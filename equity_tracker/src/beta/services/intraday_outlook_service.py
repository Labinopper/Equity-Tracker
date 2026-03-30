"""Persist intraday state observations and derive compact next-window outlooks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from statistics import median, pstdev

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionHypothesisDefinition,
    BetaInstrument,
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradayFeatureSnapshot,
    BetaMinuteBar,
)
from ..settings import BetaSettings
from .execution_economic_annotation_service import BetaExecutionEconomicAnnotationService
from .execution_hypothesis_service import BetaExecutionHypothesisService
from .intraday_feature_service import BetaIntradayFeatureService
from .intraday_priority_service import BetaIntradayPriorityService, IntradayPriorityItem

_DECIMAL_QUANT = Decimal("0.0001")
_BACKFILL_PRIORITY_TIER = "BACKFILL"
_BACKFILL_CHECKPOINT_MINUTES = {1, 5, 15, 30}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _quantize_4dp(value: float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP)


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    clipped_pct = min(1.0, max(0.0, pct))
    index = (len(ordered) - 1) * clipped_pct
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    weight = index - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _winsorize(values: list[float], *, tail_pct: float) -> list[float]:
    if not values:
        return []
    clipped_tail = min(0.20, max(0.0, float(tail_pct)))
    if clipped_tail <= 0.0 or len(values) < 2:
        return [float(value) for value in values]
    lower = _percentile(values, clipped_tail)
    upper = _percentile(values, 1.0 - clipped_tail)
    if lower is None or upper is None:
        return [float(value) for value in values]
    return [min(max(float(value), lower), upper) for value in values]


class BetaIntradayOutlookService:
    """Append intraday feature observations and annotate them with historical expectancy."""

    @staticmethod
    def capture_current_observations(
        settings: BetaSettings,
        *,
        priority_items: list[IntradayPriorityItem] | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"observations_written": 0, "outlooks_annotated": 0}

        now = _coerce_utc(now_utc)
        items = priority_items or list(BetaIntradayPriorityService.build_watchlist(settings, now_utc=now)["items"])
        if not items:
            return {"observations_written": 0, "outlooks_annotated": 0}

        instrument_ids = [item.instrument_id for item in items]
        with BetaContext.write_session() as sess:
            BetaExecutionHypothesisService.ensure_default_definitions()
            definitions = BetaExecutionHypothesisService.active_definitions(sess)
            definitions_by_id = {row.id: row for row in definitions}
            latest_snapshots: dict[str, BetaIntradayFeatureSnapshot] = {}
            for row in sess.scalars(
                select(BetaIntradayFeatureSnapshot)
                .where(BetaIntradayFeatureSnapshot.instrument_id.in_(instrument_ids))
                .order_by(
                    BetaIntradayFeatureSnapshot.instrument_id.asc(),
                    BetaIntradayFeatureSnapshot.session_date.desc(),
                    BetaIntradayFeatureSnapshot.updated_at.desc(),
                )
            ).all():
                if row.instrument_id not in latest_snapshots:
                    latest_snapshots[row.instrument_id] = row

            latest_observations: dict[str, BetaIntradayFeatureObservation] = {}
            for row in sess.scalars(
                select(BetaIntradayFeatureObservation)
                .where(BetaIntradayFeatureObservation.instrument_id.in_(instrument_ids))
                .order_by(
                    BetaIntradayFeatureObservation.instrument_id.asc(),
                    BetaIntradayFeatureObservation.session_date.desc(),
                    BetaIntradayFeatureObservation.observed_at.desc(),
                )
            ).all():
                if row.instrument_id not in latest_observations:
                    latest_observations[row.instrument_id] = row

            written = 0
            annotated = 0
            for item in items:
                snapshot = latest_snapshots.get(item.instrument_id)
                if snapshot is None:
                    continue
                feature_values = {
                    str(key): _safe_float(value)
                    for key, value in _json_object(snapshot.feature_snapshot_json).items()
                }
                if not feature_values:
                    continue
                observed_at = snapshot.last_minute_ts or snapshot.updated_at or now
                event_codes = (
                    BetaExecutionHypothesisService.detect_event_triggers(
                        feature_values=feature_values,
                        settings=settings,
                    )
                    if settings.intraday_event_trigger_enabled
                    else []
                )
                best_match = BetaIntradayOutlookService._best_execution_match(
                    definitions=definitions,
                    feature_values=feature_values,
                    event_codes=event_codes,
                )
                state = BetaIntradayOutlookService._classify_state(
                    feature_values=feature_values,
                    event_codes=event_codes,
                    best_match=best_match,
                )
                normalized_event_code = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(event_codes)
                latest_observation = latest_observations.get(item.instrument_id)
                if not BetaIntradayOutlookService._should_record_observation(
                    latest_observation=latest_observation,
                    session_date=snapshot.session_date,
                    observed_at=observed_at,
                    state_code=state["state_code"],
                    event_trigger_code=normalized_event_code,
                    cadence_minutes=item.cadence_minutes,
                ):
                    continue

                observation = BetaIntradayFeatureObservation(
                    instrument_id=item.instrument_id,
                    execution_hypothesis_definition_id=best_match["definition_id"] if best_match is not None else None,
                    symbol=item.symbol,
                    session_date=snapshot.session_date,
                    observed_at=observed_at,
                    session_state=snapshot.session_state,
                    priority_tier=item.tier,
                    last_minute_ts=snapshot.last_minute_ts,
                    state_code=state["state_code"],
                    state_family_code=state["state_family_code"],
                    state_label=state["state_label"],
                    event_trigger_code=normalized_event_code or None,
                    signal_type=str(best_match["signal_type"]) if best_match is not None else None,
                    rationale_text=str(state["rationale_text"]),
                    feature_snapshot_json=json.dumps(feature_values, sort_keys=True),
                )
                sess.add(observation)
                sess.flush()
                annotation = BetaIntradayOutlookService._annotation_for_observation(
                    sess=sess,
                    observation=observation,
                    settings=settings,
                )
                BetaIntradayOutlookService._apply_annotation(observation, annotation)
                BetaIntradayOutlookService._apply_action_guidance(
                    observation,
                    definition=definitions_by_id.get(observation.execution_hypothesis_definition_id or ""),
                )
                latest_observations[item.instrument_id] = observation
                written += 1
                annotated += 1

            return {"observations_written": written, "outlooks_annotated": annotated}

    @staticmethod
    def backfill_recent_history(
        settings: BetaSettings,
        *,
        target_days: int | None = None,
        instrument_ids: list[str] | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"sessions_backfilled": 0, "observations_written": 0}

        history_days = max(1, int(target_days or settings.intraday_execution_hypothesis_history_days))
        cutoff_date = (_coerce_utc(now_utc) - timedelta(days=history_days)).date()
        instrument_filter = [str(value) for value in (instrument_ids or []) if str(value).strip()]

        with BetaContext.write_session() as sess:
            BetaExecutionHypothesisService.ensure_default_definitions()
            definitions = BetaExecutionHypothesisService.active_definitions(sess)
            existing_query = select(BetaIntradayFeatureObservation).where(
                BetaIntradayFeatureObservation.session_date >= cutoff_date,
            )
            if instrument_filter:
                existing_query = existing_query.where(
                    BetaIntradayFeatureObservation.instrument_id.in_(instrument_filter)
                )
            existing_sessions = {
                (row.instrument_id, row.session_date)
                for row in sess.scalars(
                    existing_query
                ).all()
            }
            instrument_query = select(BetaInstrument).where(BetaInstrument.is_active.is_(True))
            if instrument_filter:
                instrument_query = instrument_query.where(BetaInstrument.id.in_(instrument_filter))
            instruments = {
                row.id: row
                for row in sess.scalars(instrument_query).all()
            }
            session_query = (
                select(BetaMinuteBar.instrument_id, BetaMinuteBar.session_date)
                .where(BetaMinuteBar.session_date >= cutoff_date)
                .group_by(BetaMinuteBar.instrument_id, BetaMinuteBar.session_date)
                .order_by(BetaMinuteBar.session_date.asc(), BetaMinuteBar.instrument_id.asc())
            )
            if instrument_filter:
                session_query = session_query.where(BetaMinuteBar.instrument_id.in_(instrument_filter))
            session_keys = list(sess.execute(session_query).all())

            sessions_backfilled = 0
            observations_written = 0
            for instrument_id, session_date in session_keys:
                if (instrument_id, session_date) in existing_sessions:
                    continue
                instrument = instruments.get(instrument_id)
                if instrument is None:
                    continue
                minute_rows = list(
                    sess.scalars(
                        select(BetaMinuteBar)
                        .where(
                            BetaMinuteBar.instrument_id == instrument_id,
                            BetaMinuteBar.session_date == session_date,
                        )
                        .order_by(BetaMinuteBar.minute_ts.asc())
                    ).all()
                )
                if not minute_rows:
                    continue

                state = BetaIntradayFeatureService._historical_intraday_profile(
                    sess,
                    instrument_id=instrument_id,
                    session_date=session_date,
                )
                previous_close = BetaIntradayFeatureService._previous_close_price(
                    sess,
                    instrument_id=instrument_id,
                    session_date=session_date,
                )
                if previous_close is not None:
                    state["previous_close_price"] = previous_close

                latest_captured: BetaIntradayFeatureObservation | None = None
                total_rows = len(minute_rows)
                for minute_index, row in enumerate(minute_rows, start=1):
                    state = BetaIntradayFeatureService._consume_minute_bar(state, row)
                    feature_values = BetaIntradayFeatureService._feature_view(
                        state,
                        exchange=instrument.exchange,
                    )
                    event_codes = BetaExecutionHypothesisService.detect_event_triggers(
                        feature_values=feature_values,
                        settings=settings,
                    )
                    best_match = BetaIntradayOutlookService._best_execution_match(
                        definitions=definitions,
                        feature_values=feature_values,
                        event_codes=event_codes,
                    )
                    state_summary = BetaIntradayOutlookService._classify_state(
                        feature_values=feature_values,
                        event_codes=event_codes,
                        best_match=best_match,
                    )
                    normalized_event_code = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(event_codes)
                    if not BetaIntradayOutlookService._should_record_backfill_observation(
                        latest_observation=latest_captured,
                        observed_at=row.minute_ts,
                        minute_index=minute_index,
                        total_rows=total_rows,
                        state_code=state_summary["state_code"],
                        event_trigger_code=normalized_event_code,
                    ):
                        continue

                    observation = BetaIntradayFeatureObservation(
                        instrument_id=instrument_id,
                        execution_hypothesis_definition_id=best_match["definition_id"] if best_match is not None else None,
                        symbol=instrument.symbol,
                        session_date=session_date,
                        observed_at=row.minute_ts,
                        session_state="REGULAR_OPEN",
                        priority_tier=_BACKFILL_PRIORITY_TIER,
                        last_minute_ts=row.minute_ts,
                        state_code=state_summary["state_code"],
                        state_family_code=state_summary["state_family_code"],
                        state_label=state_summary["state_label"],
                        event_trigger_code=normalized_event_code or None,
                        signal_type=str(best_match["signal_type"]) if best_match is not None else None,
                        rationale_text=str(state_summary["rationale_text"]),
                        feature_snapshot_json=json.dumps(feature_values, sort_keys=True),
                    )
                    sess.add(observation)
                    sess.flush()
                    latest_captured = observation
                    observations_written += 1

                sessions_backfilled += 1
                existing_sessions.add((instrument_id, session_date))

        return {
            "sessions_backfilled": sessions_backfilled,
            "observations_written": observations_written,
        }

    @staticmethod
    def update_outcome_labels(
        *,
        observation_ids: list[str] | None = None,
        instrument_ids: list[str] | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"labels_written": 0, "observations_evaluated": 0}

        with BetaContext.write_session() as sess:
            existing_labels = {
                row.observation_id: row
                for row in sess.scalars(select(BetaIntradayFeatureLabelValue)).all()
            }
            query = select(BetaIntradayFeatureObservation).order_by(
                BetaIntradayFeatureObservation.observed_at.asc(),
                BetaIntradayFeatureObservation.created_at.asc(),
            )
            if observation_ids:
                query = query.where(BetaIntradayFeatureObservation.id.in_(observation_ids))
            if instrument_ids:
                query = query.where(BetaIntradayFeatureObservation.instrument_id.in_(instrument_ids))
            observations = list(sess.scalars(query).all())
            labels_written = 0
            for observation in observations:
                label = existing_labels.get(observation.id)
                if label is not None and label.evaluation_complete and not observation_ids:
                    continue

                minute_rows = list(
                    sess.scalars(
                        select(BetaMinuteBar)
                        .where(
                            BetaMinuteBar.instrument_id == observation.instrument_id,
                            BetaMinuteBar.session_date == observation.session_date,
                            BetaMinuteBar.minute_ts >= observation.observed_at.replace(second=0, microsecond=0),
                        )
                        .order_by(BetaMinuteBar.minute_ts.asc())
                    ).all()
                )
                if not minute_rows:
                    continue
                if label is None:
                    label = BetaIntradayFeatureLabelValue(
                        observation_id=observation.id,
                        instrument_id=observation.instrument_id,
                        symbol=observation.symbol,
                        session_date=observation.session_date,
                        observed_at=observation.observed_at,
                    )
                    sess.add(label)
                    existing_labels[observation.id] = label

                base_price = _safe_float(minute_rows[0].close_price_gbp)
                if base_price is None or abs(base_price) < 1e-9:
                    continue

                label.future_5m_return_pct = BetaIntradayOutlookService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    observed_at=observation.observed_at,
                    horizon_minutes=5,
                )
                label.future_15m_return_pct = BetaIntradayOutlookService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    observed_at=observation.observed_at,
                    horizon_minutes=15,
                )
                label.future_30m_return_pct = BetaIntradayOutlookService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    observed_at=observation.observed_at,
                    horizon_minutes=30,
                )
                label.future_60m_return_pct = BetaIntradayOutlookService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    observed_at=observation.observed_at,
                    horizon_minutes=60,
                )
                label.future_120m_return_pct = BetaIntradayOutlookService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    observed_at=observation.observed_at,
                    horizon_minutes=120,
                )
                session_close_price = _safe_float(minute_rows[-1].close_price_gbp)
                if session_close_price is not None and abs(base_price) > 1e-9:
                    label.close_return_pct = round(((session_close_price / base_price) - 1.0) * 100.0, 6)

                lows = [_safe_float(row.low_price_gbp) for row in minute_rows if _safe_float(row.low_price_gbp) is not None]
                highs = [_safe_float(row.high_price_gbp) for row in minute_rows if _safe_float(row.high_price_gbp) is not None]
                if lows:
                    label.max_adverse_move_pct = round(((min(lows) / base_price) - 1.0) * 100.0, 6)
                if highs:
                    label.max_favorable_move_pct = round(((max(highs) / base_price) - 1.0) * 100.0, 6)

                peak_return = None
                peak_minutes = None
                for row in minute_rows:
                    close_price = _safe_float(row.close_price_gbp)
                    if close_price is None or abs(base_price) < 1e-9:
                        continue
                    raw_return_pct = ((close_price / base_price) - 1.0) * 100.0
                    elapsed_minutes = max(
                        0,
                        int((row.minute_ts - observation.observed_at.replace(second=0, microsecond=0)).total_seconds() // 60),
                    )
                    if peak_return is None or raw_return_pct > peak_return:
                        peak_return = raw_return_pct
                        peak_minutes = elapsed_minutes
                label.time_to_peak_minutes = peak_minutes
                last_minute_ts = minute_rows[-1].minute_ts
                label.evaluation_complete = bool(
                    label.close_return_pct is not None
                    and (
                        label.future_120m_return_pct is not None
                        or last_minute_ts >= observation.observed_at + timedelta(minutes=120)
                    )
                )
                labels_written += 1

            return {
                "labels_written": labels_written,
                "observations_evaluated": len(observations),
            }

    @staticmethod
    def refresh_outlook_annotations(
        settings: BetaSettings,
        *,
        observation_ids: list[str] | None = None,
        instrument_ids: list[str] | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"observations_updated": 0}

        with BetaContext.write_session() as sess:
            definitions = {
                row.id: row
                for row in sess.scalars(select(BetaExecutionHypothesisDefinition)).all()
            }
            query = select(BetaIntradayFeatureObservation).order_by(
                BetaIntradayFeatureObservation.observed_at.asc(),
                BetaIntradayFeatureObservation.created_at.asc(),
            )
            if observation_ids:
                query = query.where(BetaIntradayFeatureObservation.id.in_(observation_ids))
            if instrument_ids:
                query = query.where(BetaIntradayFeatureObservation.instrument_id.in_(instrument_ids))
            observations = list(sess.scalars(query).all())
            for observation in observations:
                annotation = BetaIntradayOutlookService._annotation_for_observation(
                    sess=sess,
                    observation=observation,
                    settings=settings,
                )
                BetaIntradayOutlookService._apply_annotation(observation, annotation)
                BetaIntradayOutlookService._apply_action_guidance(
                    observation,
                    definition=definitions.get(observation.execution_hypothesis_definition_id or ""),
                )
            return {"observations_updated": len(observations)}

    @staticmethod
    def rebuild_recent_history(
        settings: BetaSettings,
        *,
        target_days: int | None = None,
        instrument_ids: list[str] | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, int]:
        backfill = BetaIntradayOutlookService.backfill_recent_history(
            settings,
            target_days=target_days,
            instrument_ids=instrument_ids,
            now_utc=now_utc,
        )
        labels = BetaIntradayOutlookService.update_outcome_labels(instrument_ids=instrument_ids)
        refresh = BetaIntradayOutlookService.refresh_outlook_annotations(
            settings,
            instrument_ids=instrument_ids,
        )
        return {
            "sessions_backfilled": int(backfill.get("sessions_backfilled") or 0),
            "observations_written": int(backfill.get("observations_written") or 0),
            "labels_written": int(labels.get("labels_written") or 0),
            "observations_updated": int(refresh.get("observations_updated") or 0),
        }

    @staticmethod
    def _best_execution_match(
        *,
        definitions: list[BetaExecutionHypothesisDefinition],
        feature_values: dict[str, float | None],
        event_codes: list[str],
    ) -> dict[str, object] | None:
        matches = [
            BetaExecutionHypothesisService.evaluate_definition(
                definition=definition,
                feature_values=feature_values,
                event_codes=event_codes,
            )
            for definition in definitions
        ]
        matches = [row for row in matches if row is not None]
        if not matches:
            return None
        matches.sort(
            key=lambda item: (float(item["confidence_score"]), str(item.get("hypothesis_code", ""))),
            reverse=True,
        )
        return matches[0]

    @staticmethod
    def _classify_state(
        *,
        feature_values: dict[str, float | None],
        event_codes: list[str],
        best_match: dict[str, object] | None,
    ) -> dict[str, str]:
        minutes_until_close = _safe_float(feature_values.get("minutes_until_close"))
        session_progress = _safe_float(feature_values.get("session_progress_pct"))
        gap = _safe_float(feature_values.get("gap_from_prev_close_pct")) or 0.0
        ret_15 = _safe_float(feature_values.get("return_last_15m_pct")) or 0.0
        ret_30 = _safe_float(feature_values.get("return_last_30m_pct")) or 0.0
        ret_open = _safe_float(feature_values.get("return_since_open_pct")) or 0.0
        vwap = _safe_float(feature_values.get("distance_from_vwap_pct")) or 0.0
        reversal_low = _safe_float(feature_values.get("reversal_from_low_15m_pct")) or 0.0
        reversal_high = _safe_float(feature_values.get("reversal_from_high_15m_pct")) or 0.0
        breakout_30 = _safe_float(feature_values.get("breakout_above_first_30m_high_pct")) or 0.0
        breakdown_30 = _safe_float(feature_values.get("breakdown_below_first_30m_low_pct")) or 0.0
        gap_fill = _safe_float(feature_values.get("gap_fill_pct")) or 0.0
        volume_30 = _safe_float(feature_values.get("volume_last_30m_vs_expected")) or 0.0
        intraday_vol = _safe_float(feature_values.get("rolling_intraday_vol_15m_pct")) or 0.0

        phase = "MIDDAY"
        if session_progress is not None and session_progress < 22.0:
            phase = "OPEN"
        elif minutes_until_close is not None and minutes_until_close <= 45:
            phase = "CLOSE"
        elif session_progress is not None and session_progress >= 70.0:
            phase = "LATE"

        family_code = "RANGE_DRIFT"
        label = "Range-bound drift"
        rationale = "The tape is moving without a strong directional structure."

        if breakout_30 > 0.20 and ret_15 > 0.0:
            family_code = "OPENING_RANGE_BREAKOUT"
            label = "Opening-range breakout"
            rationale = "Price has pushed above the first 30-minute range and is still pressing higher."
        elif breakdown_30 > 0.20 and ret_15 < 0.0:
            family_code = "OPENING_RANGE_BREAKDOWN"
            label = "Opening-range breakdown"
            rationale = "Price has broken below the first 30-minute range and is still pressing lower."
        elif gap <= -2.0 and reversal_low >= 0.8 and vwap >= 0.0:
            family_code = "GAP_DOWN_RECOVERY"
            label = "Gap-down recovery"
            rationale = "The name opened weak, then reclaimed the intraday tape and is recovering."
        elif gap >= 2.0 and gap_fill >= 40.0 and ret_15 < 0.0:
            family_code = "GAP_FADE"
            label = "Gap fade"
            rationale = "The opening gap is being given back rather than extended."
        elif ret_15 <= -0.8 and reversal_low >= 0.8:
            family_code = "SHAKEOUT_RECOVERY"
            label = "Shakeout recovery"
            rationale = "A fast drop was met by a meaningful rebound off the low."
        elif ret_15 >= 0.8 and reversal_high >= 0.8:
            family_code = "FAILED_BREAKOUT_FADING"
            label = "Failed breakout fade"
            rationale = "Recent strength is fading quickly from the intraday high."
        elif phase == "CLOSE" and ret_30 >= 0.5 and volume_30 >= 1.0:
            family_code = "CLOSE_RAMP"
            label = "Close ramp"
            rationale = "Late-session buying pressure is building into the close."
        elif phase == "CLOSE" and ret_30 <= -0.5:
            family_code = "CLOSE_FADE"
            label = "Close fade"
            rationale = "Late-session pressure is weak and fading into the close."
        elif vwap >= 0.15 and ret_open >= 0.5:
            family_code = "BULLISH_VWAP_HOLD"
            label = "Bullish VWAP hold"
            rationale = "Price is holding above VWAP and staying above the opening anchor."
        elif vwap <= -0.15 and ret_open <= -0.5:
            family_code = "BEARISH_VWAP_PRESSURE"
            label = "Bearish VWAP pressure"
            rationale = "Price is staying below VWAP and under pressure versus the open."
        elif intraday_vol >= 1.4 and abs(ret_15) >= 0.8:
            family_code = "VOLATILITY_EXPANSION"
            label = "Volatility expansion"
            rationale = "The tape is moving quickly enough that continuation and reversal risk are both elevated."
        elif ret_open >= 0.4:
            family_code = "BULLISH_DRIFT"
            label = "Bullish drift"
            rationale = "Price is grinding higher without a sharper regime change."
        elif ret_open <= -0.4:
            family_code = "BEARISH_DRIFT"
            label = "Bearish drift"
            rationale = "Price is grinding lower without a sharper regime change."

        if best_match is not None and str(best_match.get("signal_type") or "").strip():
            rationale = (
                f"{rationale} Best matched execution thesis: "
                f"{best_match.get('name') or best_match.get('hypothesis_code') or best_match.get('signal_type')}."
            )
        elif event_codes:
            rationale = f"{rationale} Event context: {', '.join(sorted(event_codes))}."

        return {
            "state_code": f"{phase}__{family_code}",
            "state_family_code": family_code,
            "state_label": f"{phase.title()} / {label}",
            "rationale_text": rationale,
        }

    @staticmethod
    def _should_record_observation(
        *,
        latest_observation: BetaIntradayFeatureObservation | None,
        session_date,
        observed_at: datetime,
        state_code: str,
        event_trigger_code: str,
        cadence_minutes: int,
    ) -> bool:
        if latest_observation is None:
            return True
        if latest_observation.session_date != session_date:
            return True
        if observed_at <= latest_observation.observed_at:
            return False
        if str(latest_observation.state_code or "") != str(state_code or ""):
            return True
        if BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(
            latest_observation.event_trigger_code
        ) != BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(event_trigger_code):
            return True
        return (observed_at - latest_observation.observed_at).total_seconds() >= max(1, cadence_minutes) * 60

    @staticmethod
    def _should_record_backfill_observation(
        *,
        latest_observation: BetaIntradayFeatureObservation | None,
        observed_at: datetime,
        minute_index: int,
        total_rows: int,
        state_code: str,
        event_trigger_code: str,
    ) -> bool:
        if latest_observation is None:
            return True
        if str(latest_observation.state_code or "") != str(state_code or "") and (
            observed_at - latest_observation.observed_at
        ).total_seconds() >= 5 * 60:
            return True
        normalized_previous = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(
            latest_observation.event_trigger_code
        )
        normalized_current = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(event_trigger_code)
        if normalized_previous != normalized_current and (
            observed_at - latest_observation.observed_at
        ).total_seconds() >= 5 * 60:
            return True
        if minute_index in _BACKFILL_CHECKPOINT_MINUTES or minute_index == total_rows or minute_index % 15 == 0:
            return (observed_at - latest_observation.observed_at).total_seconds() >= 5 * 60
        return False

    @staticmethod
    def _future_return_pct(
        *,
        minute_rows: list[BetaMinuteBar],
        base_price: float,
        observed_at: datetime,
        horizon_minutes: int,
    ) -> float | None:
        target_time = observed_at + timedelta(minutes=horizon_minutes)
        future_row = next((row for row in minute_rows if row.minute_ts >= target_time), None)
        if future_row is None:
            return None
        future_price = _safe_float(future_row.close_price_gbp)
        if future_price is None or abs(base_price) < 1e-9:
            return None
        return round(((future_price / base_price) - 1.0) * 100.0, 6)

    @staticmethod
    def _annotation_for_observation(
        *,
        sess,
        observation: BetaIntradayFeatureObservation,
        settings: BetaSettings,
    ) -> dict[str, object]:
        min_sample_size = int(settings.intraday_execution_annotation_min_sample_size)
        min_instruments = max(1, int(settings.intraday_execution_hypothesis_min_matched_instruments))
        if not observation.state_family_code:
            return {
                "expected_return_15m_pct": None,
                "expected_return_30m_pct": None,
                "post_cost_expected_return_15m_pct": None,
                "historical_win_rate": None,
                "confidence_score": 0.1,
                "confidence_label": "LOW",
                "confidence_reasons_json": json.dumps({"reason_codes": ["missing_state_code"]}, sort_keys=True),
                "outlook_sample_size": 0,
                "matched_instrument_count": 0,
                "opportunity_status": "INSUFFICIENT_EVIDENCE",
                "non_actionable_reason": "missing_state_code",
            }

        exact_rows = list(
            sess.execute(
                select(
                    BetaIntradayFeatureObservation.instrument_id,
                    BetaIntradayFeatureLabelValue.future_15m_return_pct,
                    BetaIntradayFeatureLabelValue.future_30m_return_pct,
                )
                .join(
                    BetaIntradayFeatureLabelValue,
                    BetaIntradayFeatureLabelValue.observation_id == BetaIntradayFeatureObservation.id,
                )
                .where(
                    BetaIntradayFeatureObservation.state_code == observation.state_code,
                    BetaIntradayFeatureObservation.observed_at < observation.observed_at,
                    BetaIntradayFeatureObservation.session_state == observation.session_state,
                    BetaIntradayFeatureLabelValue.evaluation_complete.is_(True),
                    BetaIntradayFeatureLabelValue.future_15m_return_pct.is_not(None),
                )
                .order_by(BetaIntradayFeatureObservation.observed_at.asc())
            ).all()
        )

        sample_rows = exact_rows
        used_family_fallback = False
        if len(sample_rows) < min_sample_size:
            sample_rows = list(
                sess.execute(
                    select(
                        BetaIntradayFeatureObservation.instrument_id,
                        BetaIntradayFeatureLabelValue.future_15m_return_pct,
                        BetaIntradayFeatureLabelValue.future_30m_return_pct,
                    )
                    .join(
                        BetaIntradayFeatureLabelValue,
                        BetaIntradayFeatureLabelValue.observation_id == BetaIntradayFeatureObservation.id,
                    )
                    .where(
                        BetaIntradayFeatureObservation.state_family_code == observation.state_family_code,
                        BetaIntradayFeatureObservation.observed_at < observation.observed_at,
                        BetaIntradayFeatureObservation.session_state == observation.session_state,
                        BetaIntradayFeatureLabelValue.evaluation_complete.is_(True),
                        BetaIntradayFeatureLabelValue.future_15m_return_pct.is_not(None),
                    )
                    .order_by(BetaIntradayFeatureObservation.observed_at.asc())
                ).all()
            )
            used_family_fallback = True

        sample_size = len(sample_rows)
        matched_instruments = len({str(row.instrument_id or "") for row in sample_rows if row.instrument_id})
        actionable_min_instruments = max(
            min_instruments,
            int(settings.intraday_outlook_actionable_min_matched_instruments),
        )
        actionable_min_win_rate = float(settings.intraday_outlook_actionable_min_win_rate)
        actionable_min_post_cost_edge = float(settings.intraday_outlook_actionable_min_post_cost_edge_pct)
        actionable_min_median_return = float(settings.intraday_outlook_actionable_min_median_return_pct)
        actionable_max_single_instrument_share = float(settings.intraday_outlook_actionable_max_single_instrument_share)
        require_exact_state_match = bool(settings.intraday_outlook_actionable_require_exact_state_match)
        if sample_size < min_sample_size:
            return {
                "expected_return_15m_pct": None,
                "expected_return_30m_pct": None,
                "post_cost_expected_return_15m_pct": None,
                "historical_win_rate": None,
                "confidence_score": round(min(0.45, sample_size / max(1.0, float(min_sample_size))), 4),
                "confidence_label": "LOW",
                "confidence_reasons_json": json.dumps(
                    {
                        "sample_size": sample_size,
                        "matched_instruments": matched_instruments,
                        "exact_state_match": not used_family_fallback,
                        "reason_codes": ["insufficient_sample"],
                    },
                    sort_keys=True,
                ),
                "outlook_sample_size": sample_size,
                "matched_instrument_count": matched_instruments,
                "opportunity_status": "INSUFFICIENT_EVIDENCE",
                "non_actionable_reason": f"insufficient_sample_lt_{min_sample_size}",
            }

        returns_15 = [float(row.future_15m_return_pct) for row in sample_rows if row.future_15m_return_pct is not None]
        returns_30 = [float(row.future_30m_return_pct) for row in sample_rows if row.future_30m_return_pct is not None]
        winsorized_15 = _winsorize(
            returns_15,
            tail_pct=float(settings.intraday_execution_annotation_winsorize_tail_pct),
        )
        winsorized_30 = _winsorize(
            returns_30,
            tail_pct=float(settings.intraday_execution_annotation_winsorize_tail_pct),
        )
        if not winsorized_15:
            return {
                "expected_return_15m_pct": None,
                "expected_return_30m_pct": None,
                "post_cost_expected_return_15m_pct": None,
                "historical_win_rate": None,
                "confidence_score": 0.15,
                "confidence_label": "LOW",
                "confidence_reasons_json": json.dumps({"reason_codes": ["no_valid_historical_returns"]}, sort_keys=True),
                "outlook_sample_size": sample_size,
                "matched_instrument_count": matched_instruments,
                "opportunity_status": "INSUFFICIENT_EVIDENCE",
                "non_actionable_reason": "no_valid_historical_returns",
            }

        cost_drag_pct = float(BetaExecutionEconomicAnnotationService.estimated_cost_drag_pct(settings))
        expected_15 = sum(winsorized_15) / len(winsorized_15)
        expected_30 = (sum(winsorized_30) / len(winsorized_30)) if winsorized_30 else None
        median_15 = float(median(winsorized_15))
        p25_15 = float(_percentile(winsorized_15, 0.25) or 0.0)
        p75_15 = float(_percentile(winsorized_15, 0.75) or 0.0)
        direction_positive = expected_15 >= 0.0
        directional_wins = sum(
            1 for value in winsorized_15 if (value > 0.0 if direction_positive else value < 0.0)
        )
        historical_win_rate = directional_wins / len(winsorized_15)
        post_cost_expected_15 = expected_15 - cost_drag_pct
        volatility_15 = float(pstdev(winsorized_15)) if len(winsorized_15) > 1 else 0.0
        direction_consistent = (expected_15 >= 0.0 and median_15 >= 0.0) or (expected_15 < 0.0 and median_15 < 0.0)
        instrument_counts: dict[str, int] = {}
        for row in sample_rows:
            instrument_key = str(row.instrument_id or "").strip()
            if not instrument_key:
                continue
            instrument_counts[instrument_key] = instrument_counts.get(instrument_key, 0) + 1
        top_symbol_share = (
            max(instrument_counts.values()) / float(sample_size)
            if instrument_counts and sample_size > 0
            else 1.0
        )

        support_factor = min(1.0, sample_size / max(60.0, float(min_sample_size * 2)))
        instrument_factor = min(1.0, matched_instruments / max(1.0, float(actionable_min_instruments)))
        win_factor = min(
            1.0,
            max(
                0.0,
                (historical_win_rate - 0.5) / max(0.01, actionable_min_win_rate - 0.5),
            ),
        )
        edge_factor = min(
            1.0,
            max(
                0.0,
                abs(post_cost_expected_15) / max(0.05, actionable_min_post_cost_edge * 2.0),
            ),
        )
        median_factor = min(
            1.0,
            max(
                0.0,
                abs(median_15) / max(0.01, actionable_min_median_return * 2.0),
            ),
        )
        stability_penalty = min(0.30, max(0.0, volatility_15 / 2.0) * 0.15)
        concentration_penalty = min(
            0.18,
            max(0.0, top_symbol_share - actionable_max_single_instrument_share) * 0.60,
        )
        exactness_penalty = (
            0.12
            if used_family_fallback and require_exact_state_match
            else 0.05
            if used_family_fallback
            else 0.0
        )
        confidence_score = round(
            max(
                0.0,
                min(
                    1.0,
                    (
                        0.08
                        + (support_factor * 0.25)
                        + (instrument_factor * 0.15)
                        + (win_factor * 0.15)
                        + (edge_factor * 0.15)
                        + (median_factor * 0.12)
                        - stability_penalty
                        - concentration_penalty
                        - exactness_penalty
                    ),
                ),
            ),
            4,
        )
        confidence_label = "HIGH" if confidence_score >= 0.72 else "MEDIUM" if confidence_score >= 0.52 else "LOW"

        reason_codes: list[str] = []
        failed_reason_codes: list[str] = []
        if used_family_fallback:
            reason_codes.append("state_family_fallback")
            if require_exact_state_match:
                failed_reason_codes.append("state_family_fallback")
        else:
            reason_codes.append("exact_state_match")
        if matched_instruments >= actionable_min_instruments:
            reason_codes.append("actionable_breadth_met")
        elif matched_instruments >= min_instruments:
            reason_codes.append("minimum_breadth_only")
            failed_reason_codes.append("actionable_breadth_below_floor")
        else:
            failed_reason_codes.append("insufficient_breadth")
        if direction_consistent:
            reason_codes.append("median_confirms_mean")
        else:
            failed_reason_codes.append("median_mean_direction_mismatch")
        if abs(median_15) >= actionable_min_median_return:
            reason_codes.append("median_edge_above_floor")
        else:
            failed_reason_codes.append("median_edge_below_floor")
        if top_symbol_share <= actionable_max_single_instrument_share:
            reason_codes.append("sample_concentration_acceptable")
        else:
            failed_reason_codes.append("sample_concentration_too_high")
        if abs(post_cost_expected_15) >= actionable_min_post_cost_edge:
            reason_codes.append("post_cost_edge_above_floor")
        else:
            failed_reason_codes.append("post_cost_edge_below_floor")
        if historical_win_rate >= actionable_min_win_rate:
            reason_codes.append("win_rate_above_floor")
        else:
            failed_reason_codes.append("win_rate_below_floor")
        if abs(post_cost_expected_15) > 0.0:
            reason_codes.append("post_cost_edge_non_zero")
        if volatility_15 <= 0.6:
            reason_codes.append("stable_distribution")

        opportunity_status = "INFORMATIONAL"
        non_actionable_reason = None
        if matched_instruments < min_instruments:
            opportunity_status = "INSUFFICIENT_EVIDENCE"
            non_actionable_reason = f"insufficient_breadth_lt_{min_instruments}"
        elif require_exact_state_match and used_family_fallback:
            non_actionable_reason = "state_family_fallback"
        elif matched_instruments < actionable_min_instruments:
            non_actionable_reason = "actionable_breadth_below_floor"
        elif top_symbol_share > actionable_max_single_instrument_share:
            non_actionable_reason = "sample_concentration_too_high"
        elif not direction_consistent:
            non_actionable_reason = "median_mean_direction_mismatch"
        elif abs(median_15) < actionable_min_median_return:
            non_actionable_reason = "median_edge_below_floor"
        elif historical_win_rate < actionable_min_win_rate:
            non_actionable_reason = "win_rate_below_floor"
        elif abs(post_cost_expected_15) < actionable_min_post_cost_edge:
            non_actionable_reason = "post_cost_edge_below_floor"
        else:
            opportunity_status = "ACTIONABLE"

        return {
            "expected_return_15m_pct": _quantize_4dp(expected_15),
            "expected_return_30m_pct": _quantize_4dp(expected_30),
            "post_cost_expected_return_15m_pct": _quantize_4dp(post_cost_expected_15),
            "historical_win_rate": _quantize_4dp(historical_win_rate),
            "confidence_score": confidence_score,
            "confidence_label": confidence_label,
            "confidence_reasons_json": json.dumps(
                {
                    "sample_size": sample_size,
                    "exact_state_sample_size": len(exact_rows),
                    "matched_instruments": matched_instruments,
                    "exact_state_match": not used_family_fallback,
                    "expected_15m_return_pct": round(expected_15, 4),
                    "expected_30m_return_pct": round(expected_30, 4) if expected_30 is not None else None,
                    "post_cost_expected_15m_pct": round(post_cost_expected_15, 4),
                    "historical_win_rate": round(historical_win_rate, 4),
                    "median_15m_return_pct": round(median_15, 4),
                    "p25_15m_return_pct": round(p25_15, 4),
                    "p75_15m_return_pct": round(p75_15, 4),
                    "volatility_15m_pct": round(volatility_15, 4),
                    "top_symbol_share": round(top_symbol_share, 4),
                    "actionable_min_instruments": actionable_min_instruments,
                    "actionable_min_win_rate": round(actionable_min_win_rate, 4),
                    "actionable_min_post_cost_edge_pct": round(actionable_min_post_cost_edge, 4),
                    "actionable_min_median_return_pct": round(actionable_min_median_return, 4),
                    "actionable_max_single_instrument_share": round(actionable_max_single_instrument_share, 4),
                    "reason_codes": reason_codes,
                    "failed_reason_codes": failed_reason_codes,
                },
                sort_keys=True,
            ),
            "outlook_sample_size": sample_size,
            "matched_instrument_count": matched_instruments,
            "opportunity_status": opportunity_status,
            "non_actionable_reason": non_actionable_reason,
        }

    @staticmethod
    def _apply_annotation(observation: BetaIntradayFeatureObservation, annotation: dict[str, object]) -> None:
        observation.expected_return_15m_pct = annotation.get("expected_return_15m_pct")  # type: ignore[assignment]
        observation.expected_return_30m_pct = annotation.get("expected_return_30m_pct")  # type: ignore[assignment]
        observation.post_cost_expected_return_15m_pct = annotation.get("post_cost_expected_return_15m_pct")  # type: ignore[assignment]
        observation.historical_win_rate = annotation.get("historical_win_rate")  # type: ignore[assignment]
        observation.confidence_score = _safe_float(annotation.get("confidence_score"))
        observation.confidence_label = str(annotation.get("confidence_label")) if annotation.get("confidence_label") else None
        observation.confidence_reasons_json = (
            str(annotation.get("confidence_reasons_json"))
            if annotation.get("confidence_reasons_json") is not None
            else None
        )
        observation.outlook_sample_size = int(annotation.get("outlook_sample_size") or 0)
        observation.matched_instrument_count = int(annotation.get("matched_instrument_count") or 0)
        observation.opportunity_status = str(annotation.get("opportunity_status") or "INSUFFICIENT_EVIDENCE")
        observation.non_actionable_reason = (
            str(annotation.get("non_actionable_reason"))
            if annotation.get("non_actionable_reason")
            else None
        )

    @staticmethod
    def _apply_action_guidance(
        observation: BetaIntradayFeatureObservation,
        *,
        definition: BetaExecutionHypothesisDefinition | None,
    ) -> None:
        guidance = BetaExecutionHypothesisService.action_guidance(
            definition=definition,
            signal_type=observation.signal_type,
            priority_tier=observation.priority_tier,
        )
        observation.recommended_action_side = guidance["recommended_action_side"]
        observation.recommended_action_code = guidance["recommended_action_code"]
        observation.recommended_action_label = guidance["recommended_action_label"]
        BetaIntradayOutlookService._enforce_action_alignment(observation)

    @staticmethod
    def _enforce_action_alignment(observation: BetaIntradayFeatureObservation) -> None:
        side = str(observation.recommended_action_side or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            return

        expected_15 = _safe_float(observation.expected_return_15m_pct)
        post_cost_15 = _safe_float(observation.post_cost_expected_return_15m_pct)
        mismatch = False
        if side == "BUY":
            mismatch = (
                expected_15 is None
                or post_cost_15 is None
                or expected_15 <= 0.0
                or post_cost_15 <= 0.0
            )
        elif side == "SELL":
            mismatch = (
                expected_15 is None
                or post_cost_15 is None
                or expected_15 >= 0.0
                or post_cost_15 >= 0.0
            )
        if not mismatch:
            return

        held_context = str(observation.priority_tier or "").strip().upper() == "HELD"
        if side == "BUY":
            observation.recommended_action_side = "HOLD" if held_context else "WAIT"
            observation.recommended_action_code = "NO_ACTION" if held_context else "AVOID_ENTRY"
            observation.recommended_action_label = (
                "Hold pending stronger long evidence"
                if held_context
                else "Avoid long entry"
            )
        else:
            observation.recommended_action_side = "HOLD" if held_context else "WAIT"
            observation.recommended_action_code = "NO_ACTION"
            observation.recommended_action_label = (
                "Hold pending clearer exit evidence"
                if held_context
                else "No short-side action"
            )

        if str(observation.opportunity_status or "").strip().upper() == "ACTIONABLE":
            observation.opportunity_status = "INFORMATIONAL"
        if not observation.non_actionable_reason:
            observation.non_actionable_reason = "action_direction_mismatch"
        elif observation.non_actionable_reason != "action_direction_mismatch":
            observation.non_actionable_reason = "action_direction_mismatch"

        payload = _json_object(observation.confidence_reasons_json)
        failed_codes = [str(code) for code in payload.get("failed_reason_codes", []) if str(code).strip()]
        reason_codes = [str(code) for code in payload.get("reason_codes", []) if str(code).strip()]
        if "action_direction_mismatch" not in failed_codes:
            failed_codes.append("action_direction_mismatch")
        if "action_direction_mismatch" not in reason_codes:
            reason_codes.append("action_direction_mismatch")
        payload["failed_reason_codes"] = failed_codes
        payload["reason_codes"] = reason_codes
        payload["aligned_action_side"] = observation.recommended_action_side
        payload["aligned_action_code"] = observation.recommended_action_code
        observation.confidence_reasons_json = json.dumps(payload, sort_keys=True)
