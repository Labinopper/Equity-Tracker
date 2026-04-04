"""Execution-only intraday guidance for held positions."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionHypothesisDefinition,
    BetaExecutionSignal,
    BetaIntradayFeatureObservation,
    BetaIntradayFeatureSnapshot,
    BetaPositionState,
    BetaUiNotification,
)
from ..settings import BetaSettings
from .execution_economic_annotation_service import BetaExecutionEconomicAnnotationService
from .execution_hypothesis_service import BetaExecutionHypothesisService
from .intraday_aggregation_service import BetaIntradayAggregationService
from .intraday_bar_fetch_service import BetaIntradayBarFetchService
from .intraday_feature_service import BetaIntradayFeatureService
from .intraday_outlook_service import BetaIntradayOutlookService
from .intraday_priority_service import BetaIntradayPriorityService, IntradayPriorityItem
from .observation_service import BetaObservationService
from .position_registry import BetaPositionRegistry
from .prediction_accuracy_service import BetaPredictionAccuracyService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _merge_priority_items(*groups: list[IntradayPriorityItem]) -> list[IntradayPriorityItem]:
    merged: dict[str, IntradayPriorityItem] = {}
    tier_rank = {"HELD": 0, "ACTIVE_THESIS": 1, "FOCUS": 2, "GENERAL": 3}
    for group in groups:
        for item in group:
            existing = merged.get(item.instrument_id)
            if existing is None:
                merged[item.instrument_id] = item
                continue
            prefer_existing = (
                tier_rank.get(existing.tier, 99),
                existing.cadence_minutes,
                -existing.priority_score,
            ) <= (
                tier_rank.get(item.tier, 99),
                item.cadence_minutes,
                -item.priority_score,
            )
            if prefer_existing:
                continue
            merged[item.instrument_id] = item
    return list(merged.values())


class BetaExecutionSignalService:
    """Prepare intraday state and emit execution guidance for held positions."""

    _STATE_CHANGE_NOTIFICATION_TYPE = "execution_signal_state_change"
    _ALERTABLE_SIGNAL_TYPES = {
        "HOLD_THROUGH_NOISE",
        "TRIM_ON_STRENGTH",
        "SELL_INTO_REBOUND",
        "AVOID_SELLING_INTO_PANIC",
        "WAIT_FOR_CLOSE_CONFIRMATION",
    }

    @staticmethod
    def prepare_execution_context(settings: BetaSettings, *, now_utc: datetime | None = None) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_execution_enabled:
            return {"watchlist_items": 0, "held": 0, "active_thesis": 0, "general": 0}

        now = _coerce_utc(now_utc)
        core_position_sync = BetaPositionRegistry.sync_core_portfolio_positions(now_utc=now)
        demo_position_sync = BetaPositionRegistry.sync_demo_positions(now_utc=now)
        candidate_thesis_sync = BetaPositionRegistry.sync_candidate_theses(now_utc=now)
        watchlist = BetaIntradayPriorityService.build_watchlist(settings, now_utc=now)
        focus_watchlist = BetaIntradayPriorityService.build_focus_watchlist(settings, now_utc=now)
        all_items = _merge_priority_items(list(watchlist["items"]), list(focus_watchlist["items"]))
        instrument_ids = [item.instrument_id for item in all_items]
        if instrument_ids:
            BetaObservationService.sync_intraday_snapshots(instrument_ids=set(instrument_ids))
            BetaIntradayAggregationService.aggregate_minute_bars(
                instrument_ids=instrument_ids,
                lookback_minutes=settings.intraday_history_lookback_minutes,
            )
            if settings.intraday_bar_fetch_enabled:
                BetaIntradayBarFetchService.fetch_live_bars(
                    priority_items=all_items,
                    credits_budget=settings.intraday_bar_fetch_live_credits_budget,
                )
            BetaIntradayFeatureService.refresh_feature_snapshots(
                priority_items=all_items,
                now_utc=now,
            )
            outlook_capture = BetaIntradayOutlookService.capture_current_observations(
                settings,
                priority_items=all_items,
                now_utc=now,
            )
            outlook_labels = BetaIntradayOutlookService.update_outcome_labels(instrument_ids=instrument_ids)
        else:
            outlook_capture = {"observations_written": 0, "outlooks_annotated": 0}
            outlook_labels = {"labels_written": 0, "observations_evaluated": 0}
        return {
            "watchlist_items": len(watchlist["items"]),
            "focus_items": len(focus_watchlist["items"]),
            "prepared_items_total": len(all_items),
            "held": int(watchlist["held"]),
            "active_thesis": int(watchlist["active_thesis"]),
            "general": int(watchlist["general"]),
            "position_states_upserted": int(core_position_sync.get("states_upserted", 0))
            + int(demo_position_sync.get("states_upserted", 0))
            + int(candidate_thesis_sync.get("states_upserted", 0)),
            "core_position_states_upserted": int(core_position_sync.get("states_upserted", 0)),
            "demo_position_states_upserted": int(demo_position_sync.get("states_upserted", 0)),
            "candidate_thesis_states_upserted": int(candidate_thesis_sync.get("states_upserted", 0)),
            "intraday_outlook_observations_written": int(outlook_capture.get("observations_written", 0)),
            "intraday_outlook_annotations_written": int(outlook_capture.get("outlooks_annotated", 0)),
            "intraday_outlook_labels_written": int(outlook_labels.get("labels_written", 0)),
        }

    @staticmethod
    def evaluate_execution_signals(settings: BetaSettings, *, now_utc: datetime | None = None) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_execution_enabled:
            return {"positions_evaluated": 0, "signals_created": 0, "triggered_evaluations": 0}

        now = _coerce_utc(now_utc)
        watchlist = BetaIntradayPriorityService.build_watchlist(settings, now_utc=now)
        actionable_items = [item for item in watchlist["items"] if item.tier in {"HELD", "ACTIVE_THESIS"}]
        if not actionable_items:
            return {"positions_evaluated": 0, "signals_created": 0, "triggered_evaluations": 0}

        with BetaContext.write_session() as sess:
            BetaExecutionHypothesisService.ensure_default_definitions()
            definitions = BetaExecutionHypothesisService.active_definitions(sess)
            definitions_by_id = {row.id: row for row in definitions}
            positions = list(
                sess.scalars(
                    select(BetaPositionState)
                    .where(
                        BetaPositionState.position_status == "OPEN",
                        BetaPositionState.instrument_id.in_([item.instrument_id for item in actionable_items]),
                    )
                    .order_by(desc(BetaPositionState.updated_at))
                ).all()
            )
            feature_rows: dict[str, BetaIntradayFeatureSnapshot] = {}
            for row in sess.scalars(
                select(BetaIntradayFeatureSnapshot)
                .where(BetaIntradayFeatureSnapshot.instrument_id.in_([item.instrument_id for item in actionable_items]))
                .order_by(
                    BetaIntradayFeatureSnapshot.instrument_id.asc(),
                    BetaIntradayFeatureSnapshot.session_date.desc(),
                    desc(BetaIntradayFeatureSnapshot.updated_at),
                )
            ).all():
                if row.instrument_id not in feature_rows:
                    feature_rows[row.instrument_id] = row
            latest_outlooks: dict[str, BetaIntradayFeatureObservation] = {}
            for row in sess.scalars(
                select(BetaIntradayFeatureObservation)
                .where(BetaIntradayFeatureObservation.instrument_id.in_([item.instrument_id for item in actionable_items]))
                .order_by(
                    BetaIntradayFeatureObservation.instrument_id.asc(),
                    BetaIntradayFeatureObservation.session_date.desc(),
                    desc(BetaIntradayFeatureObservation.observed_at),
                )
            ).all():
                if row.instrument_id not in latest_outlooks:
                    latest_outlooks[row.instrument_id] = row
            cadence_by_instrument = {item.instrument_id: item.cadence_minutes for item in actionable_items}
            session_by_instrument = {item.instrument_id: item.session_state for item in actionable_items}
            signals_created = 0
            positions_evaluated = 0
            triggered_evaluations = 0

            for position in positions:
                if position.instrument_id is None:
                    continue
                session_state = session_by_instrument.get(position.instrument_id, "CLOSED")
                if session_state != "REGULAR_OPEN":
                    continue
                snapshot = feature_rows.get(position.instrument_id)
                if snapshot is None:
                    continue
                feature_values = BetaExecutionHypothesisService._json_object(snapshot.feature_snapshot_json)
                if not feature_values:
                    continue
                event_codes = (
                    BetaExecutionHypothesisService.detect_event_triggers(
                        feature_values=feature_values,
                        settings=settings,
                    )
                    if settings.intraday_event_trigger_enabled
                    else []
                )
                last_signal = sess.scalar(
                    select(BetaExecutionSignal)
                    .where(BetaExecutionSignal.position_state_id == position.id)
                    .order_by(desc(BetaExecutionSignal.signal_time))
                    .limit(1)
                )
                cadence_minutes = cadence_by_instrument.get(position.instrument_id, settings.intraday_held_symbol_cadence_minutes)
                due_for_evaluation = (
                    last_signal is None
                    or (now - last_signal.signal_time).total_seconds() >= cadence_minutes * 60
                    or bool(event_codes)
                )
                if not due_for_evaluation:
                    continue
                positions_evaluated += 1
                if event_codes:
                    triggered_evaluations += 1
                matches = [
                    BetaExecutionHypothesisService.evaluate_definition(
                        definition=definition,
                        feature_values=feature_values,
                        event_codes=event_codes,
                    )
                    for definition in definitions
                ]
                matches = [row for row in matches if row is not None]
                if not matches and not event_codes:
                    continue
                best_match = None
                if matches:
                    matches.sort(
                        key=lambda item: (float(item["confidence_score"]), str(item.get("hypothesis_code", ""))),
                        reverse=True,
                    )
                    best_match = matches[0]
                signal_type = str(best_match["signal_type"]) if best_match is not None else "NO_ACTION"
                matched_definition = definitions_by_id.get(best_match["definition_id"]) if best_match is not None else None
                normalized_event_trigger_code = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(event_codes)
                economic_annotation = BetaExecutionEconomicAnnotationService.annotation_for_match(
                    sess=sess,
                    definition=matched_definition,
                    signal_time=now,
                    signal_type=signal_type,
                    event_trigger_code=normalized_event_trigger_code,
                    settings=settings,
                )
                action_guidance = BetaExecutionHypothesisService.action_guidance(
                    definition=matched_definition,
                    signal_type=signal_type,
                    position_source=position.position_source,
                )
                latest_outlook = latest_outlooks.get(position.instrument_id)
                signal = BetaExecutionSignal(
                    execution_hypothesis_definition_id=best_match["definition_id"] if best_match is not None else None,
                    position_state_id=position.id,
                    instrument_id=position.instrument_id,
                    symbol=position.symbol,
                    session_date=snapshot.session_date,
                    signal_time=now,
                    session_state=session_state,
                    signal_type=signal_type,
                    recommended_action_side=action_guidance["recommended_action_side"],
                    recommended_action_code=action_guidance["recommended_action_code"],
                    recommended_action_label=action_guidance["recommended_action_label"],
                    confidence_score=float(best_match["confidence_score"] if best_match is not None else 0.25),
                    rationale_text=(
                        str(best_match["rationale_text"])
                        if best_match is not None
                        else "No execution hypothesis matched despite an event-triggered evaluation."
                    ),
                    event_trigger_code=normalized_event_trigger_code or None,
                    matched_conditions_json=json.dumps(
                        best_match["matched_conditions"] if best_match is not None else [],
                        sort_keys=True,
                    ),
                    feature_snapshot_json=json.dumps(feature_values, sort_keys=True),
                )
                BetaExecutionEconomicAnnotationService.apply_annotation(signal, economic_annotation)
                BetaExecutionSignalService._apply_outlook_guardrail(
                    signal=signal,
                    latest_outlook=latest_outlook,
                    position_source=position.position_source,
                )
                if BetaExecutionSignalService._is_duplicate_signal(
                    sess=sess,
                    position_state_id=position.id,
                    signal_type=signal.signal_type,
                    economic_opportunity_status=str(
                        signal.economic_opportunity_status
                        or BetaExecutionEconomicAnnotationService.NON_ACTIONABLE
                    ),
                    signal_time=now,
                ):
                    continue
                sess.add(signal)
                sess.flush()
                position.last_execution_signal_type = signal.signal_type
                position.last_execution_signal_at = now
                BetaExecutionSignalService._maybe_record_state_change_notification(
                    sess=sess,
                    position=position,
                    signal=signal,
                    previous_signal=last_signal,
                )
                
                # Log prediction for accuracy tracking (intraday execution signals)
                if matched_definition is not None and signal.signal_type != "NO_ACTION":
                    try:
                        # Estimate predicted return based on signal type and confidence
                        predicted_return_pct = BetaExecutionSignalService._estimate_predicted_return(
                            signal_type=signal.signal_type,
                            confidence_score=signal.confidence_score,
                        )
                        BetaPredictionAccuracyService.log_prediction(
                            hypothesis_definition_id=matched_definition.id,
                            execution_signal_id=signal.id,
                            predicted_return_pct=predicted_return_pct,
                            confidence_score=signal.confidence_score,
                            prediction_time=now,
                            horizon_days=1,  # Intraday signals have 1-day horizon
                        )
                    except Exception:
                        # Don't fail signal generation if prediction logging fails
                        pass
                
                signals_created += 1

            return {
                "positions_evaluated": positions_evaluated,
                "signals_created": signals_created,
                "triggered_evaluations": triggered_evaluations,
            }

    @staticmethod
    def refresh_signal_actions() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"signals_updated": 0}

        with BetaContext.write_session() as sess:
            definitions = {
                row.id: row
                for row in sess.scalars(select(BetaExecutionHypothesisDefinition)).all()
            }
            positions = {
                row.id: row
                for row in sess.scalars(select(BetaPositionState)).all()
            }
            signals = list(
                sess.scalars(
                    select(BetaExecutionSignal).order_by(BetaExecutionSignal.signal_time.asc())
                ).all()
            )
            for signal in signals:
                position = positions.get(signal.position_state_id or "")
                definition = definitions.get(signal.execution_hypothesis_definition_id or "")
                latest_outlook = None
                if signal.instrument_id is not None:
                    latest_outlook = sess.scalar(
                        select(BetaIntradayFeatureObservation)
                        .where(
                            BetaIntradayFeatureObservation.instrument_id == signal.instrument_id,
                            BetaIntradayFeatureObservation.session_date == signal.session_date,
                            BetaIntradayFeatureObservation.observed_at <= signal.signal_time,
                        )
                        .order_by(desc(BetaIntradayFeatureObservation.observed_at))
                        .limit(1)
                    )
                action_guidance = BetaExecutionHypothesisService.action_guidance(
                    definition=definition,
                    signal_type=signal.signal_type,
                    position_source=position.position_source if position is not None else None,
                )
                signal.recommended_action_side = action_guidance["recommended_action_side"]
                signal.recommended_action_code = action_guidance["recommended_action_code"]
                signal.recommended_action_label = action_guidance["recommended_action_label"]
                BetaExecutionSignalService._apply_outlook_guardrail(
                    signal=signal,
                    latest_outlook=latest_outlook,
                    position_source=position.position_source if position is not None else None,
                )
            return {"signals_updated": len(signals)}

    @staticmethod
    def _is_duplicate_signal(
        *,
        sess,
        position_state_id: str,
        signal_type: str,
        economic_opportunity_status: str,
        signal_time: datetime,
    ) -> bool:
        latest = sess.scalar(
            select(BetaExecutionSignal)
            .where(BetaExecutionSignal.position_state_id == position_state_id)
            .order_by(desc(BetaExecutionSignal.signal_time))
            .limit(1)
        )
        if latest is None:
            return False
        if latest.signal_type != signal_type:
            return False
        if str(latest.economic_opportunity_status or "") != str(economic_opportunity_status or ""):
            return False
        return latest.signal_time.replace(second=0, microsecond=0) == signal_time.replace(second=0, microsecond=0)

    @staticmethod
    def _maybe_record_state_change_notification(
        *,
        sess,
        position: BetaPositionState,
        signal: BetaExecutionSignal,
        previous_signal: BetaExecutionSignal | None,
    ) -> None:
        if signal.signal_type not in BetaExecutionSignalService._ALERTABLE_SIGNAL_TYPES:
            return
        if str(signal.recommended_action_side or "").strip().upper() not in {"BUY", "SELL"}:
            return

        notify = False
        if previous_signal is None:
            notify = True
        elif previous_signal.session_date != signal.session_date:
            notify = True
        elif previous_signal.signal_type != signal.signal_type:
            notify = True
        elif (
            BetaExecutionSignalService._confidence_band(previous_signal.confidence_score)
            != BetaExecutionSignalService._confidence_band(signal.confidence_score)
        ):
            notify = True

        if not notify:
            return

        previous_label = previous_signal.signal_type if previous_signal is not None else "NONE"
        confidence_band = BetaExecutionSignalService._confidence_band(signal.confidence_score)
        trigger_text = f" Trigger: {signal.event_trigger_code}." if signal.event_trigger_code else ""
        if previous_signal is None or previous_signal.session_date != signal.session_date:
            message = (
                f"{signal.symbol} opened a new execution state of {signal.signal_type} "
                f"with guidance {signal.recommended_action_side or 'WAIT'}/{signal.recommended_action_code or 'NO_ACTION'} "
                f"at confidence {signal.confidence_score:.2f} ({confidence_band})."
                f"{trigger_text} {signal.rationale_text or ''}".strip()
            )
        else:
            message = (
                f"{signal.symbol} execution guidance changed from {previous_label} to {signal.signal_type} "
                f"with action {signal.recommended_action_side or 'WAIT'}/{signal.recommended_action_code or 'NO_ACTION'} "
                f"at confidence {signal.confidence_score:.2f} ({confidence_band})."
                f"{trigger_text} {signal.rationale_text or ''}".strip()
            )
        sess.add(
            BetaUiNotification(
                notification_type=BetaExecutionSignalService._STATE_CHANGE_NOTIFICATION_TYPE,
                severity=BetaExecutionSignalService._notification_severity(signal.signal_type),
                title=f"Execution state change: {signal.symbol} -> {signal.signal_type}",
                message_text=message,
                target_table="beta_execution_signals",
                target_id=signal.id,
            )
        )

    @staticmethod
    def _confidence_band(score: float | None) -> str:
        value = float(score or 0.0)
        if value >= 0.65:
            return "HIGH"
        if value >= 0.55:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _notification_severity(signal_type: str) -> str:
        if signal_type in {"TRIM_ON_STRENGTH", "SELL_INTO_REBOUND"}:
            return "WARNING"
        if signal_type in {"HOLD_THROUGH_NOISE", "AVOID_SELLING_INTO_PANIC"}:
            return "SUCCESS"
        return "INFO"

    @staticmethod
    def _apply_outlook_guardrail(
        *,
        signal: BetaExecutionSignal,
        latest_outlook: BetaIntradayFeatureObservation | None,
        position_source: str | None,
    ) -> None:
        side = str(signal.recommended_action_side or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            return
        if latest_outlook is None:
            return

        latest_status = str(latest_outlook.opportunity_status or "").strip().upper()
        latest_side = str(latest_outlook.recommended_action_side or "").strip().upper()
        outlook_aligned = latest_status == "ACTIONABLE" and latest_side == side
        if outlook_aligned:
            return

        held_context = str(position_source or "").strip().upper() in {"MANUAL", "DEMO", "CORE"}
        if held_context:
            signal.recommended_action_side = "HOLD"
            signal.recommended_action_code = "NO_ACTION"
            signal.recommended_action_label = (
                "Hold pending stronger exit evidence"
                if side == "SELL"
                else "Hold pending stronger buy evidence"
            )
        else:
            signal.recommended_action_side = "WAIT"
            signal.recommended_action_code = "NO_ACTION"
            signal.recommended_action_label = "No trade action"

        signal.economic_opportunity_status = BetaExecutionEconomicAnnotationService.NON_ACTIONABLE
        signal.economic_non_actionable_reason = (
            "outlook_not_actionable"
            if latest_status != "ACTIONABLE"
            else "outlook_direction_mismatch"
        )
        reason_text = (
            "latest outlook was not actionable"
            if latest_status != "ACTIONABLE"
            else f"latest outlook side was {latest_side or 'NONE'}"
        )
        base_rationale = str(signal.rationale_text or "").strip()
        if reason_text not in base_rationale:
            signal.rationale_text = (
                f"{base_rationale} Outlook guardrail suppressed action because {reason_text}."
                if base_rationale
                else f"Outlook guardrail suppressed action because {reason_text}."
            )

    @staticmethod
    def _estimate_predicted_return(signal_type: str, confidence_score: float) -> float:
        """Estimate predicted return based on signal type and confidence.
        
        This is a heuristic for intraday execution signals where we don't have
        explicit return predictions. The return estimate is based on:
        - Signal type (bullish vs bearish)
        - Confidence score (higher confidence = larger expected move)
        """
        # Base return expectations by signal type
        signal_returns = {
            "HOLD_THROUGH_NOISE": 0.5,  # Expect small positive continuation
            "TRIM_ON_STRENGTH": -0.3,  # Expect pullback after strength
            "SELL_INTO_REBOUND": -1.0,  # Expect continued weakness
            "AVOID_SELLING_INTO_PANIC": 1.5,  # Expect bounce from oversold
            "WAIT_FOR_CLOSE_CONFIRMATION": 0.0,  # Neutral, waiting for clarity
        }
        
        base_return = signal_returns.get(signal_type, 0.0)
        
        # Scale by confidence (0.5-1.0 confidence maps to 0.5-1.5x multiplier)
        confidence_multiplier = 0.5 + (confidence_score * 1.0)
        
        return base_return * confidence_multiplier
