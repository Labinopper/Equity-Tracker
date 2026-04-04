"""Evaluate execution signal outcomes and position execution quality."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionLabelValue,
    BetaExecutionSignal,
    BetaMinuteBar,
    BetaPositionState,
    BetaSignalCandidate,
    BetaSignalObservation,
)
from .execution_economic_annotation_service import BetaExecutionEconomicAnnotationService
from .prediction_accuracy_service import BetaPredictionAccuracyService


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BetaExecutionOutcomeService:
    """Compute intraday execution labels from aggregated minute bars."""

    @staticmethod
    def update_execution_outcomes() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"labels_written": 0, "signals_evaluated": 0, "observations_updated": 0}

        with BetaContext.write_session() as sess:
            existing_labels = {
                row.execution_signal_id: row
                for row in sess.scalars(select(BetaExecutionLabelValue)).all()
            }
            signals = list(
                sess.scalars(
                    select(BetaExecutionSignal)
                    .order_by(BetaExecutionSignal.signal_time.asc())
                ).all()
            )
            labels_written = 0
            for signal in signals:
                minute_rows = list(
                    sess.scalars(
                        select(BetaMinuteBar)
                        .where(
                            BetaMinuteBar.instrument_id == signal.instrument_id,
                            BetaMinuteBar.session_date == signal.session_date,
                            BetaMinuteBar.minute_ts >= signal.signal_time.replace(second=0, microsecond=0),
                        )
                        .order_by(BetaMinuteBar.minute_ts.asc())
                    ).all()
                )
                if not minute_rows:
                    continue
                label = existing_labels.get(signal.id)
                if label is None:
                    label = BetaExecutionLabelValue(
                        execution_signal_id=signal.id,
                        position_state_id=signal.position_state_id,
                        instrument_id=signal.instrument_id,
                        symbol=signal.symbol,
                        session_date=signal.session_date,
                        signal_time=signal.signal_time,
                    )
                    sess.add(label)
                    existing_labels[signal.id] = label

                base_price = _safe_float(minute_rows[0].close_price_gbp)
                if base_price is None or abs(base_price) < 1e-9:
                    continue
                label.future_30m_return_pct = BetaExecutionOutcomeService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal_time=signal.signal_time,
                    horizon_minutes=30,
                )
                label.future_60m_return_pct = BetaExecutionOutcomeService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal_time=signal.signal_time,
                    horizon_minutes=60,
                )
                label.future_120m_return_pct = BetaExecutionOutcomeService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal_time=signal.signal_time,
                    horizon_minutes=120,
                )
                session_close_price = _safe_float(minute_rows[-1].close_price_gbp)
                if session_close_price is not None and abs(base_price) > 1e-9:
                    label.close_return_from_signal_pct = round(
                        ((session_close_price / base_price) - 1.0) * 100.0,
                        6,
                    )
                lows = [_safe_float(row.low_price_gbp) for row in minute_rows if _safe_float(row.low_price_gbp) is not None]
                highs = [_safe_float(row.high_price_gbp) for row in minute_rows if _safe_float(row.high_price_gbp) is not None]
                if lows:
                    label.max_adverse_move_after_signal_pct = round(
                        ((min(lows) / base_price) - 1.0) * 100.0,
                        6,
                    )
                if highs:
                    label.max_favorable_move_after_signal_pct = round(
                        ((max(highs) / base_price) - 1.0) * 100.0,
                        6,
                    )
                bounded_outcome = BetaExecutionOutcomeService._bounded_action_aligned_outcome(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal=signal,
                )
                label.action_aligned_return_pct = bounded_outcome["action_aligned_return_pct"]
                # This field now records the realized evaluation horizon used for the bounded outcome.
                label.time_to_peak_minutes = bounded_outcome["time_to_peak_minutes"]
                last_minute_ts = minute_rows[-1].minute_ts
                label.evaluation_complete = bool(
                    label.close_return_from_signal_pct is not None
                    and (
                        label.future_120m_return_pct is not None
                        or last_minute_ts >= signal.signal_time + timedelta(minutes=120)
                    )
                )
                labels_written += 1
                
                # Update prediction accuracy with realized outcome for execution signals
                if label.evaluation_complete and signal.execution_hypothesis_definition_id is not None:
                    try:
                        # Use the most complete realized return available
                        realized_return = (
                            label.close_return_from_signal_pct
                            if label.close_return_from_signal_pct is not None
                            else label.future_120m_return_pct
                            if label.future_120m_return_pct is not None
                            else label.future_60m_return_pct
                        )
                        if realized_return is not None:
                            # Find prediction log entry for this execution signal
                            from ..db.models import BetaPredictionAccuracyLog
                            prediction_log = sess.scalar(
                                select(BetaPredictionAccuracyLog)
                                .where(BetaPredictionAccuracyLog.execution_signal_id == signal.id)
                                .limit(1)
                            )
                            if prediction_log is not None:
                                BetaPredictionAccuracyService.update_realized_outcome(
                                    prediction_log_id=prediction_log.id,
                                    realized_return_pct=realized_return,
                                    realization_time=label.updated_at,
                                )
                    except Exception:
                        # Don't fail outcome computation if prediction update fails
                        pass

            observations_updated = BetaExecutionOutcomeService._sync_realized_observations(sess)
            
            # Also update prediction accuracy for daily hypothesis signals
            BetaExecutionOutcomeService._update_hypothesis_prediction_outcomes(sess)
            
            return {
                "labels_written": labels_written,
                "signals_evaluated": len(signals),
                "observations_updated": observations_updated,
            }

    @staticmethod
    def _future_return_pct(
        *,
        minute_rows: list[BetaMinuteBar],
        base_price: float,
        signal_time,
        horizon_minutes: int,
    ) -> float | None:
        target_time = signal_time + timedelta(minutes=horizon_minutes)
        future_row = next((row for row in minute_rows if row.minute_ts >= target_time), None)
        if future_row is None:
            return None
        future_price = _safe_float(future_row.close_price_gbp)
        if future_price is None or abs(base_price) < 1e-9:
            return None
        return round(((future_price / base_price) - 1.0) * 100.0, 6)

    @staticmethod
    def _bounded_action_aligned_outcome(
        *,
        minute_rows: list[BetaMinuteBar],
        base_price: float,
        signal: BetaExecutionSignal,
    ) -> dict[str, float | int | None]:
        base_time = signal.signal_time.replace(second=0, microsecond=0)
        relevant_rows = [row for row in minute_rows if row.minute_ts >= base_time]
        if signal.expected_hold_max_minutes is not None:
            max_minutes = max(1, int(signal.expected_hold_max_minutes))
            evaluation_end = base_time + timedelta(minutes=max_minutes)
            bounded_rows = [row for row in relevant_rows if row.minute_ts <= evaluation_end]
            if bounded_rows:
                relevant_rows = bounded_rows
        if not relevant_rows:
            return {
                "action_aligned_return_pct": None,
                "time_to_peak_minutes": None,
            }

        best_return: float | None = None
        best_minutes: int | None = None
        for minute_row in relevant_rows:
            close_price = _safe_float(minute_row.close_price_gbp)
            if close_price is None or abs(base_price) < 1e-9:
                continue
            raw_return_pct = ((close_price / base_price) - 1.0) * 100.0
            aligned_return_pct = BetaExecutionEconomicAnnotationService.action_aligned_return(
                raw_return_pct,
                signal.signal_type,
            )
            if aligned_return_pct is None:
                continue
            elapsed_minutes = max(
                0,
                int((minute_row.minute_ts - base_time).total_seconds() // 60),
            )
            if best_return is None or aligned_return_pct > best_return:
                best_return = aligned_return_pct
                best_minutes = elapsed_minutes
        return {
            "action_aligned_return_pct": round(best_return, 6) if best_return is not None else None,
            "time_to_peak_minutes": best_minutes,
        }

    @staticmethod
    def _sync_realized_observations(sess) -> int:
        position_states = {
            row.id: row
            for row in sess.scalars(
                select(BetaPositionState).where(BetaPositionState.thesis_hypothesis_definition_id.is_not(None))
            ).all()
        }
        if not position_states:
            return 0
        candidates = {
            row.id: row
            for row in sess.scalars(
                select(BetaSignalCandidate).where(BetaSignalCandidate.id.in_(list({
                    row.thesis_candidate_id for row in position_states.values() if row.thesis_candidate_id
                }) or [""]))
            ).all()
        }
        observations = {
            row.id: row
            for row in sess.scalars(
                select(BetaSignalObservation).where(
                    BetaSignalObservation.id.in_(list({
                        candidate.signal_observation_id
                        for candidate in candidates.values()
                        if candidate.signal_observation_id
                    }) or [""])
                )
            ).all()
        }

        updated = 0
        for label in sess.scalars(select(BetaExecutionLabelValue)).all():
            position = position_states.get(label.position_state_id or "")
            if position is None or position.thesis_hypothesis_definition_id is None:
                continue
            candidate = candidates.get(position.thesis_candidate_id or "")
            if candidate is None or candidate.signal_observation_id is None:
                continue
            observation = observations.get(candidate.signal_observation_id)
            if observation is None:
                continue
            realized_return = BetaExecutionOutcomeService._realized_return_pct(label, position)
            if realized_return is None:
                continue
            if observation.realized_at is not None and observation.realized_at >= label.updated_at:
                continue

            observation.realized_return_pct = realized_return
            observation.realized_at = label.updated_at
            context = BetaExecutionOutcomeService._observation_context(observation.regime_context_json)
            context["execution_feedback"] = {
                "position_state_id": position.id,
                "candidate_id": candidate.id,
                "execution_signal_id": label.execution_signal_id,
                "evaluation_complete": bool(label.evaluation_complete),
                "source": (
                    "close_return_from_signal_pct"
                    if label.close_return_from_signal_pct is not None
                    else "future_120m_return_pct"
                    if label.future_120m_return_pct is not None
                    else "future_60m_return_pct"
                    if label.future_60m_return_pct is not None
                    else "future_30m_return_pct"
                ),
            }
            observation.regime_context_json = json.dumps(context, sort_keys=True)
            updated += 1
        return updated

    @staticmethod
    def _realized_return_pct(
        label: BetaExecutionLabelValue,
        position: BetaPositionState,
    ) -> float | None:
        for value in (
            label.close_return_from_signal_pct,
            label.future_120m_return_pct,
            label.future_60m_return_pct,
            label.future_30m_return_pct,
            position.realized_return_pct,
            position.unrealized_return_pct,
        ):
            if value is None:
                continue
            return round(float(value), 6)
        return None

    @staticmethod
    def _observation_context(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _update_hypothesis_prediction_outcomes(sess) -> int:
        """Update prediction accuracy for daily hypothesis signal observations."""
        from ..db.models import BetaPredictionAccuracyLog

        updated = 0

        pending_logs = list(
            sess.scalars(
                select(BetaPredictionAccuracyLog).where(
                    BetaPredictionAccuracyLog.signal_observation_id.is_not(None),
                    BetaPredictionAccuracyLog.realized_return_pct.is_(None),
                )
            ).all()
        )
        if not pending_logs:
            return 0

        observation_ids = sorted(
            {
                str(log.signal_observation_id)
                for log in pending_logs
                if isinstance(log.signal_observation_id, str) and log.signal_observation_id
            }
        )
        if not observation_ids:
            return 0

        realized_observations = {
            observation.id: observation
            for observation in sess.scalars(
                select(BetaSignalObservation).where(
                    BetaSignalObservation.id.in_(observation_ids),
                    BetaSignalObservation.realized_return_pct.is_not(None),
                    BetaSignalObservation.realized_at.is_not(None),
                )
            ).all()
        }

        for prediction_log in pending_logs:
            observation = realized_observations.get(str(prediction_log.signal_observation_id))
            if observation is None or observation.realized_return_pct is None or observation.realized_at is None:
                continue

            try:
                BetaPredictionAccuracyService.update_realized_outcome(
                    prediction_log_id=prediction_log.id,
                    realized_return_pct=observation.realized_return_pct,
                    realization_time=observation.realized_at,
                )
                updated += 1
            except Exception:
                # Continue processing other observations if one fails
                continue

        return updated
