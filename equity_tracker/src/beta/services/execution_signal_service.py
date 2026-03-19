"""Execution-only intraday guidance for held positions."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import BetaExecutionSignal, BetaIntradayFeatureSnapshot, BetaPositionState, BetaUiNotification
from ..settings import BetaSettings
from .execution_hypothesis_service import BetaExecutionHypothesisService
from .intraday_aggregation_service import BetaIntradayAggregationService
from .intraday_bar_fetch_service import BetaIntradayBarFetchService
from .intraday_feature_service import BetaIntradayFeatureService
from .intraday_priority_service import BetaIntradayPriorityService
from .observation_service import BetaObservationService
from .position_registry import BetaPositionRegistry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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
        instrument_ids = [item.instrument_id for item in watchlist["items"]]
        if instrument_ids:
            BetaObservationService.sync_intraday_snapshots(instrument_ids=set(instrument_ids))
            BetaIntradayAggregationService.aggregate_minute_bars(
                instrument_ids=instrument_ids,
                lookback_minutes=settings.intraday_history_lookback_minutes,
            )
            if settings.intraday_bar_fetch_enabled:
                BetaIntradayBarFetchService.fetch_live_bars(
                    priority_items=list(watchlist["items"]),
                    credits_budget=settings.intraday_bar_fetch_live_credits_budget,
                )
            BetaIntradayFeatureService.refresh_feature_snapshots(
                priority_items=list(watchlist["items"]),
                now_utc=now,
            )
        return {
            "watchlist_items": len(watchlist["items"]),
            "held": int(watchlist["held"]),
            "active_thesis": int(watchlist["active_thesis"]),
            "general": int(watchlist["general"]),
            "position_states_upserted": int(core_position_sync.get("states_upserted", 0))
            + int(demo_position_sync.get("states_upserted", 0))
            + int(candidate_thesis_sync.get("states_upserted", 0)),
            "core_position_states_upserted": int(core_position_sync.get("states_upserted", 0)),
            "demo_position_states_upserted": int(demo_position_sync.get("states_upserted", 0)),
            "candidate_thesis_states_upserted": int(candidate_thesis_sync.get("states_upserted", 0)),
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
                if BetaExecutionSignalService._is_duplicate_signal(
                    sess=sess,
                    position_state_id=position.id,
                    signal_type=signal_type,
                    signal_time=now,
                ):
                    continue
                signal = BetaExecutionSignal(
                    execution_hypothesis_definition_id=best_match["definition_id"] if best_match is not None else None,
                    position_state_id=position.id,
                    instrument_id=position.instrument_id,
                    symbol=position.symbol,
                    session_date=snapshot.session_date,
                    signal_time=now,
                    session_state=session_state,
                    signal_type=signal_type,
                    confidence_score=float(best_match["confidence_score"] if best_match is not None else 0.25),
                    rationale_text=(
                        str(best_match["rationale_text"])
                        if best_match is not None
                        else "No execution hypothesis matched despite an event-triggered evaluation."
                    ),
                    event_trigger_code="|".join(event_codes) if event_codes else None,
                    matched_conditions_json=json.dumps(
                        best_match["matched_conditions"] if best_match is not None else [],
                        sort_keys=True,
                    ),
                    feature_snapshot_json=json.dumps(feature_values, sort_keys=True),
                )
                sess.add(signal)
                position.last_execution_signal_type = signal.signal_type
                position.last_execution_signal_at = now
                BetaExecutionSignalService._maybe_record_state_change_notification(
                    sess=sess,
                    position=position,
                    signal=signal,
                    previous_signal=last_signal,
                )
                signals_created += 1

            return {
                "positions_evaluated": positions_evaluated,
                "signals_created": signals_created,
                "triggered_evaluations": triggered_evaluations,
            }

    @staticmethod
    def _is_duplicate_signal(
        *,
        sess,
        position_state_id: str,
        signal_type: str,
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
                f"at confidence {signal.confidence_score:.2f} ({confidence_band})."
                f"{trigger_text} {signal.rationale_text or ''}".strip()
            )
        else:
            message = (
                f"{signal.symbol} execution guidance changed from {previous_label} to {signal.signal_type} "
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
