"""Focused short-horizon intraday paper trades for secondary evidence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, func, select, text

from ..context import BetaContext
from ..db.models import (
    BetaInstrument,
    BetaIntradayFeatureObservation,
    BetaIntradaySimulatedTrade,
    BetaIntradaySimulatedTradeEvent,
    BetaMinuteBar,
)
from ..settings import BetaSettings
from .execution_economic_annotation_service import BetaExecutionEconomicAnnotationService
from .intraday_priority_service import BetaIntradayPriorityService

_DECIMAL_QUANT = Decimal("0.0001")
_PROFIT_POCKET_MIN_ROWS = 80
_PROFIT_POCKET_MIN_SYMBOLS = 5
_PROFIT_POCKET_MIN_MEAN_EDGE_PCT = 0.10
_PROFIT_POCKET_MIN_WIN_RATE = 0.52
_PROFIT_POCKET_MAX_SINGLE_SYMBOL_SHARE = 0.45
_PROFIT_POCKET_MAX_TOP_TWO_SYMBOL_SHARE = 0.65


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    clipped_pct = min(1.0, max(0.0, float(pct)))
    index = (len(ordered) - 1) * clipped_pct
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    weight = index - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _price_string(value: float | None) -> str | None:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP))


class BetaIntradaySimulatedTradeService:
    """Run a focused short-horizon long/short paper-trade lane on liquid large caps."""

    @staticmethod
    def refresh_live_trades(
        settings: BetaSettings,
        *,
        now_utc: datetime | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized() or not settings.intraday_short_trade_simulation_enabled:
            return {"focus_items": 0, "trades_opened": 0, "trades_closed": 0, "open_trades": 0}

        now = _coerce_utc(now_utc)
        focus_watchlist = BetaIntradayPriorityService.build_focus_watchlist(settings, now_utc=now)
        focus_items = list(focus_watchlist["items"])
        focus_ids = {item.instrument_id for item in focus_items if item.instrument_id}
        if not focus_ids:
            return {"focus_items": 0, "trades_opened": 0, "trades_closed": 0, "open_trades": 0}

        trades_opened = 0
        trades_closed = 0
        with BetaContext.write_session() as sess:
            open_trades = list(
                sess.scalars(
                    select(BetaIntradaySimulatedTrade)
                    .where(BetaIntradaySimulatedTrade.status == "OPEN")
                    .order_by(BetaIntradaySimulatedTrade.entry_observed_at.asc())
                ).all()
            )
            focus_ids.update({trade.instrument_id for trade in open_trades if trade.instrument_id})
            instruments = {
                row.id: row
                for row in sess.scalars(select(BetaInstrument).where(BetaInstrument.id.in_(list(focus_ids)))).all()
            }
            latest_observations = BetaIntradaySimulatedTradeService._latest_observations(
                sess,
                instrument_ids=list(focus_ids),
            )
            profit_pockets = BetaIntradaySimulatedTradeService._profit_pocket_lookup(
                sess,
                cutoff_date=now.date() - timedelta(days=max(7, int(settings.intraday_short_trade_history_days)) - 1),
                state_codes={
                    str(row.state_code or "").strip().upper()
                    for row in latest_observations.values()
                    if str(row.state_code or "").strip()
                },
                cost_drag_pct=float(BetaExecutionEconomicAnnotationService.estimated_cost_drag_pct(settings)),
            )

            for trade in open_trades:
                if not trade.instrument_id:
                    continue
                entry_observation = sess.get(BetaIntradayFeatureObservation, trade.entry_observation_id or "")
                if entry_observation is None:
                    continue
                session_observations = BetaIntradaySimulatedTradeService._session_observations(
                    sess,
                    instrument_id=trade.instrument_id,
                    session_date=trade.session_date,
                    up_to=now,
                )
                minute_rows = BetaIntradaySimulatedTradeService._minute_rows(
                    sess,
                    instrument_id=trade.instrument_id,
                    session_date=trade.session_date,
                    start_at=trade.entry_observed_at,
                    up_to=now,
                )
                if not minute_rows:
                    continue
                entry_plan = BetaIntradaySimulatedTradeService._entry_plan_from_trade(trade)
                simulation = BetaIntradaySimulatedTradeService._simulate_trade_path(
                    entry_observation=entry_observation,
                    session_observations=session_observations,
                    minute_rows=minute_rows,
                    settings=settings,
                    as_of=now,
                    entry_plan=entry_plan,
                )
                was_open = trade.status == "OPEN"
                BetaIntradaySimulatedTradeService._apply_trade_simulation(trade, simulation)
                if was_open and trade.status == "CLOSED":
                    trades_closed += 1
                    BetaIntradaySimulatedTradeService._record_trade_event(
                        sess=sess,
                        trade=trade,
                        event_type=str(trade.exit_reason_code or "CLOSED"),
                        event_time=trade.exit_observed_at or trade.latest_observed_at or now,
                        price_gbp=trade.exit_price_gbp,
                        return_pct=trade.realized_return_pct,
                        message_text=str(trade.exit_reason_text or "Closed simulated trade."),
                        payload={
                            "current_action_code": trade.current_action_code,
                            "current_action_label": trade.current_action_label,
                        },
                    )

            existing_sessions = {
                (trade.instrument_id, trade.session_date)
                for trade in sess.scalars(
                    select(BetaIntradaySimulatedTrade).where(
                        BetaIntradaySimulatedTrade.instrument_id.in_(list(focus_ids)),
                        BetaIntradaySimulatedTrade.session_date >= now.date() - timedelta(days=1),
                        BetaIntradaySimulatedTrade.simulation_source == "LIVE_FORWARD",
                    )
                ).all()
                if trade.instrument_id
            }

            for item in focus_items:
                observation = latest_observations.get(item.instrument_id)
                if observation is None:
                    continue
                if (observation.instrument_id, observation.session_date) in existing_sessions:
                    continue
                entry_plan = BetaIntradaySimulatedTradeService._entry_plan(
                    observation,
                    settings,
                    profit_pockets=profit_pockets,
                )
                if entry_plan is None:
                    continue
                minute_rows = BetaIntradaySimulatedTradeService._minute_rows(
                    sess,
                    instrument_id=observation.instrument_id,
                    session_date=observation.session_date,
                    start_at=observation.observed_at,
                    up_to=now,
                )
                if not minute_rows:
                    continue
                session_observations = BetaIntradaySimulatedTradeService._session_observations(
                    sess,
                    instrument_id=observation.instrument_id,
                    session_date=observation.session_date,
                    up_to=now,
                )
                simulation = BetaIntradaySimulatedTradeService._simulate_trade_path(
                    entry_observation=observation,
                    session_observations=session_observations,
                    minute_rows=minute_rows,
                    settings=settings,
                    as_of=now,
                    entry_plan=entry_plan,
                )
                instrument = instruments.get(observation.instrument_id)
                trade = BetaIntradaySimulatedTradeService._build_trade_row(
                    observation=observation,
                    instrument=instrument,
                    simulation=simulation,
                    focus_bucket=BetaIntradaySimulatedTradeService._focus_bucket(item.market),
                    simulation_source="LIVE_FORWARD",
                )
                sess.add(trade)
                sess.flush()
                trades_opened += 1
                existing_sessions.add((observation.instrument_id, observation.session_date))
                BetaIntradaySimulatedTradeService._record_trade_event(
                    sess=sess,
                    trade=trade,
                    event_type="OPENED",
                    event_time=trade.entry_observed_at,
                    price_gbp=trade.entry_price_gbp,
                    return_pct=0.0,
                    message_text=(
                        f"Opened simulated {str(trade.direction or 'LONG').lower()} trade from "
                        f"{trade.entry_action_label or 'trade guidance'}."
                    ),
                    payload=_json_object(trade.notes_json),
                )
                if trade.status == "CLOSED":
                    trades_closed += 1
                    BetaIntradaySimulatedTradeService._record_trade_event(
                        sess=sess,
                        trade=trade,
                        event_type=str(trade.exit_reason_code or "CLOSED"),
                        event_time=trade.exit_observed_at or trade.latest_observed_at or now,
                        price_gbp=trade.exit_price_gbp,
                        return_pct=trade.realized_return_pct,
                        message_text=str(trade.exit_reason_text or "Closed simulated trade."),
                        payload={
                            "current_action_code": trade.current_action_code,
                            "current_action_label": trade.current_action_label,
                        },
                    )

            open_trade_count = int(
                sess.scalar(
                    select(func.count())
                    .select_from(BetaIntradaySimulatedTrade)
                    .where(BetaIntradaySimulatedTrade.status == "OPEN")
                )
                or 0
            )

        return {
            "focus_items": len(focus_items),
            "trades_opened": trades_opened,
            "trades_closed": trades_closed,
            "open_trades": open_trade_count,
        }

    @staticmethod
    def rebuild_recent_history(
        settings: BetaSettings,
        *,
        target_days: int | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized() or not settings.intraday_short_trade_simulation_enabled:
            return {"focus_items": 0, "trades_written": 0, "sessions_simulated": 0}

        now = _utcnow()
        focus_watchlist = BetaIntradayPriorityService.build_focus_watchlist(settings, now_utc=now)
        focus_items = list(focus_watchlist["items"])
        focus_ids = {item.instrument_id for item in focus_items if item.instrument_id}
        if not focus_ids:
            return {"focus_items": 0, "trades_written": 0, "sessions_simulated": 0}

        history_days = max(7, int(target_days or settings.intraday_short_trade_history_days))
        cutoff_date = (now - timedelta(days=history_days)).date()
        trades_written = 0
        sessions_simulated = 0

        with BetaContext.write_session() as sess:
            existing_trade_ids = list(
                sess.scalars(
                    select(BetaIntradaySimulatedTrade.id).where(
                        BetaIntradaySimulatedTrade.instrument_id.in_(list(focus_ids)),
                        BetaIntradaySimulatedTrade.session_date >= cutoff_date,
                    )
                ).all()
            )
            if existing_trade_ids:
                sess.execute(
                    delete(BetaIntradaySimulatedTradeEvent).where(
                        BetaIntradaySimulatedTradeEvent.simulated_trade_id.in_(existing_trade_ids)
                    )
                )
                sess.execute(
                    delete(BetaIntradaySimulatedTrade).where(BetaIntradaySimulatedTrade.id.in_(existing_trade_ids))
                )

            instruments = {
                row.id: row
                for row in sess.scalars(select(BetaInstrument).where(BetaInstrument.id.in_(list(focus_ids)))).all()
            }
            grouped_observations: dict[tuple[str, object], list[BetaIntradayFeatureObservation]] = {}
            for observation in sess.scalars(
                select(BetaIntradayFeatureObservation)
                .where(
                    BetaIntradayFeatureObservation.instrument_id.in_(list(focus_ids)),
                    BetaIntradayFeatureObservation.session_date >= cutoff_date,
                    BetaIntradayFeatureObservation.session_state == "REGULAR_OPEN",
                )
                .order_by(
                    BetaIntradayFeatureObservation.instrument_id.asc(),
                    BetaIntradayFeatureObservation.session_date.asc(),
                    BetaIntradayFeatureObservation.observed_at.asc(),
                )
            ).all():
                grouped_observations.setdefault(
                    (observation.instrument_id, observation.session_date),
                    [],
                ).append(observation)
            profit_pockets = BetaIntradaySimulatedTradeService._profit_pocket_lookup(
                sess,
                cutoff_date=cutoff_date,
                state_codes={
                    str(observation.state_code or "").strip().upper()
                    for observations in grouped_observations.values()
                    for observation in observations
                    if str(observation.state_code or "").strip()
                },
                cost_drag_pct=float(BetaExecutionEconomicAnnotationService.estimated_cost_drag_pct(settings)),
            )

            for (instrument_id, session_date), observations in grouped_observations.items():
                entry_pair = next(
                    (
                        (
                            observation,
                            entry_plan,
                        )
                        for observation in observations
                        for entry_plan in [
                            BetaIntradaySimulatedTradeService._entry_plan(
                                observation,
                                settings,
                                profit_pockets=profit_pockets,
                            )
                        ]
                        if entry_plan is not None
                    ),
                    (None, None),
                )
                entry_observation, entry_plan = entry_pair
                if entry_observation is None or entry_plan is None:
                    continue
                minute_rows = BetaIntradaySimulatedTradeService._minute_rows(
                    sess,
                    instrument_id=instrument_id,
                    session_date=session_date,
                    start_at=entry_observation.observed_at,
                    up_to=None,
                )
                if not minute_rows:
                    continue
                simulation = BetaIntradaySimulatedTradeService._simulate_trade_path(
                    entry_observation=entry_observation,
                    session_observations=observations,
                    minute_rows=minute_rows,
                    settings=settings,
                    as_of=None,
                    entry_plan=entry_plan,
                )
                instrument = instruments.get(instrument_id)
                focus_bucket = BetaIntradaySimulatedTradeService._focus_bucket(
                    str(instrument.market or "OTHER") if instrument is not None else "OTHER"
                )
                trade = BetaIntradaySimulatedTradeService._build_trade_row(
                    observation=entry_observation,
                    instrument=instrument,
                    simulation=simulation,
                    focus_bucket=focus_bucket,
                    simulation_source="HISTORICAL_BACKFILL",
                )
                sess.add(trade)
                sess.flush()
                BetaIntradaySimulatedTradeService._record_trade_event(
                    sess=sess,
                    trade=trade,
                    event_type="OPENED",
                    event_time=trade.entry_observed_at,
                    price_gbp=trade.entry_price_gbp,
                    return_pct=0.0,
                    message_text=(
                        f"Opened simulated {str(trade.direction or 'LONG').lower()} trade from "
                        f"{trade.entry_action_label or 'trade guidance'}."
                    ),
                    payload=_json_object(trade.notes_json),
                )
                BetaIntradaySimulatedTradeService._record_trade_event(
                    sess=sess,
                    trade=trade,
                    event_type=str(trade.exit_reason_code or "SESSION_END"),
                    event_time=trade.exit_observed_at or trade.latest_observed_at or trade.entry_observed_at,
                    price_gbp=trade.exit_price_gbp,
                    return_pct=trade.realized_return_pct,
                    message_text=str(trade.exit_reason_text or "Closed simulated trade."),
                    payload={
                        "current_action_code": trade.current_action_code,
                        "current_action_label": trade.current_action_label,
                    },
                )
                trades_written += 1
                sessions_simulated += 1

        return {
            "focus_items": len(focus_items),
            "trades_written": trades_written,
            "sessions_simulated": sessions_simulated,
        }

    @staticmethod
    def _build_trade_row(
        *,
        observation: BetaIntradayFeatureObservation,
        instrument: BetaInstrument | None,
        simulation: dict[str, object],
        focus_bucket: str,
        simulation_source: str,
    ) -> BetaIntradaySimulatedTrade:
        return BetaIntradaySimulatedTrade(
            entry_observation_id=observation.id,
            current_observation_id=(simulation.get("current_observation_id") or observation.id),
            instrument_id=observation.instrument_id,
            symbol=observation.symbol,
            market=str(instrument.market or "OTHER") if instrument is not None else None,
            exchange=instrument.exchange if instrument is not None else None,
            focus_bucket=focus_bucket,
            direction=str(simulation.get("direction") or "LONG"),
            simulation_source=simulation_source,
            status=str(simulation.get("status") or "OPEN"),
            session_date=observation.session_date,
            state_code=observation.state_code,
            state_label=observation.state_label,
            entry_action_side=simulation.get("entry_action_side") or observation.recommended_action_side,
            entry_action_code=simulation.get("entry_action_code") or observation.recommended_action_code,
            entry_action_label=simulation.get("entry_action_label") or observation.recommended_action_label,
            current_action_side=simulation.get("current_action_side"),
            current_action_code=simulation.get("current_action_code"),
            current_action_label=simulation.get("current_action_label"),
            entry_observed_at=observation.observed_at,
            latest_observed_at=simulation.get("latest_observed_at"),
            exit_observed_at=simulation.get("exit_observed_at"),
            entry_price_gbp=simulation.get("entry_price_gbp"),
            latest_price_gbp=simulation.get("latest_price_gbp"),
            exit_price_gbp=simulation.get("exit_price_gbp"),
            expected_return_15m_pct=observation.expected_return_15m_pct,
            expected_return_30m_pct=observation.expected_return_30m_pct,
            post_cost_expected_return_15m_pct=observation.post_cost_expected_return_15m_pct,
            historical_win_rate=observation.historical_win_rate,
            confidence_score=observation.confidence_score,
            outlook_sample_size=observation.outlook_sample_size,
            matched_instrument_count=observation.matched_instrument_count,
            target_return_pct=simulation.get("target_return_pct"),
            stop_loss_pct=simulation.get("stop_loss_pct"),
            max_hold_minutes=simulation.get("max_hold_minutes"),
            early_bail_minutes=simulation.get("early_bail_minutes"),
            latest_return_pct=simulation.get("latest_return_pct"),
            max_return_pct=simulation.get("max_return_pct"),
            max_drawdown_pct=simulation.get("max_drawdown_pct"),
            realized_return_pct=simulation.get("realized_return_pct"),
            realized_post_cost_return_pct=simulation.get("realized_post_cost_return_pct"),
            hold_minutes=simulation.get("hold_minutes"),
            exit_reason_code=simulation.get("exit_reason_code"),
            exit_reason_text=simulation.get("exit_reason_text"),
            notes_json=json.dumps(simulation.get("notes") or {}, sort_keys=True),
        )

    @staticmethod
    def _apply_trade_simulation(trade: BetaIntradaySimulatedTrade, simulation: dict[str, object]) -> None:
        trade.current_observation_id = simulation.get("current_observation_id") or trade.current_observation_id
        trade.current_action_side = simulation.get("current_action_side")
        trade.current_action_code = simulation.get("current_action_code")
        trade.current_action_label = simulation.get("current_action_label")
        trade.status = str(simulation.get("status") or trade.status)
        trade.latest_observed_at = simulation.get("latest_observed_at")
        trade.exit_observed_at = simulation.get("exit_observed_at")
        trade.latest_price_gbp = simulation.get("latest_price_gbp")
        trade.exit_price_gbp = simulation.get("exit_price_gbp")
        trade.latest_return_pct = simulation.get("latest_return_pct")
        trade.max_return_pct = simulation.get("max_return_pct")
        trade.max_drawdown_pct = simulation.get("max_drawdown_pct")
        trade.realized_return_pct = simulation.get("realized_return_pct")
        trade.realized_post_cost_return_pct = simulation.get("realized_post_cost_return_pct")
        trade.hold_minutes = simulation.get("hold_minutes")
        trade.exit_reason_code = simulation.get("exit_reason_code")
        trade.exit_reason_text = simulation.get("exit_reason_text")
        trade.notes_json = json.dumps(simulation.get("notes") or {}, sort_keys=True)

    @staticmethod
    def _entry_plan_from_trade(trade: BetaIntradaySimulatedTrade) -> dict[str, object]:
        notes = _json_object(trade.notes_json)
        direction = str(trade.direction or notes.get("direction") or "LONG").strip().upper() or "LONG"
        return {
            "direction": direction,
            "entry_source": str(notes.get("entry_source") or "DIRECT"),
            "entry_action_side": str(trade.entry_action_side or ("BUY" if direction == "LONG" else "SELL")),
            "entry_action_code": str(trade.entry_action_code or ("ENTER" if direction == "LONG" else "SELL_SHORT")),
            "entry_action_label": str(
                trade.entry_action_label or ("Long trade" if direction == "LONG" else "Short trade")
            ),
            "target_return_pct": float(trade.target_return_pct or 0.0),
            "stop_loss_pct": float(trade.stop_loss_pct or 0.0),
            "max_hold_minutes": int(trade.max_hold_minutes or 0),
            "early_bail_minutes": int(trade.early_bail_minutes or 0),
            "notes": notes,
        }

    @staticmethod
    def _aligned_return(raw_return_pct: float | None, direction: str) -> float | None:
        if raw_return_pct is None:
            return None
        return raw_return_pct if str(direction or "").strip().upper() == "LONG" else -raw_return_pct

    @staticmethod
    def _profit_pocket_lookup(
        sess,
        *,
        cutoff_date,
        state_codes: set[str],
        cost_drag_pct: float,
    ) -> dict[str, dict[str, object]]:
        normalized_state_codes = sorted({str(code or "").strip().upper() for code in state_codes if str(code or "").strip()})
        if not normalized_state_codes:
            return {}

        placeholders = ", ".join(f":state_{idx}" for idx, _ in enumerate(normalized_state_codes))
        params: dict[str, object] = {"cutoff_date": cutoff_date}
        params.update({f"state_{idx}": code for idx, code in enumerate(normalized_state_codes)})
        rows = list(
            sess.execute(
                text(
                    f"""
                    WITH base AS (
                        SELECT
                            o.instrument_id,
                            o.symbol,
                            o.session_date,
                            o.observed_at,
                            o.state_code,
                            l.future_15m_return_pct,
                            l.future_30m_return_pct,
                            CAST(
                                (
                                    (CAST(strftime('%H', o.observed_at) AS INTEGER) * 60)
                                    + CAST(strftime('%M', o.observed_at) AS INTEGER)
                                ) / 15 AS INTEGER
                            ) AS audit_bucket
                        FROM beta_intraday_feature_observations o
                        JOIN beta_intraday_feature_label_values l
                          ON l.observation_id = o.id
                        WHERE o.session_date >= :cutoff_date
                          AND o.session_state = 'REGULAR_OPEN'
                          AND l.evaluation_complete = 1
                          AND l.future_15m_return_pct IS NOT NULL
                          AND l.future_30m_return_pct IS NOT NULL
                          AND UPPER(COALESCE(o.state_code, '')) IN ({placeholders})
                    ),
                    canonical AS (
                        SELECT
                            *,
                            ROW_NUMBER() OVER (
                                PARTITION BY instrument_id, session_date, audit_bucket
                                ORDER BY observed_at DESC
                            ) AS rn
                        FROM base
                    )
                    SELECT
                        state_code,
                        symbol,
                        session_date,
                        future_15m_return_pct,
                        future_30m_return_pct
                    FROM canonical
                    WHERE rn = 1
                    """
                ),
                params,
            ).mappings()
        )

        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            state_code = str(row["state_code"] or "").strip().upper()
            if not state_code:
                continue
            grouped.setdefault(state_code, []).append(dict(row))

        lookup: dict[str, dict[str, object]] = {}
        for state_code, state_rows in grouped.items():
            candidates: list[dict[str, object]] = []
            for horizon_minutes in (15, 30):
                value_key = f"future_{horizon_minutes}m_return_pct"
                for direction in ("LONG", "SHORT"):
                    returns: list[float] = []
                    symbol_counts: dict[str, int] = {}
                    sessions: set[str] = set()
                    for row in state_rows:
                        raw_return = _safe_float(row.get(value_key))
                        if raw_return is None:
                            continue
                        aligned_post_cost = (
                            raw_return - cost_drag_pct
                            if direction == "LONG"
                            else (-raw_return) - cost_drag_pct
                        )
                        returns.append(aligned_post_cost)
                        symbol = str(row["symbol"] or "").strip().upper()
                        session_date = str(row["session_date"] or "").strip()
                        if symbol:
                            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
                        if session_date:
                            sessions.add(session_date)
                    if not returns or len(symbol_counts) < _PROFIT_POCKET_MIN_SYMBOLS or len(returns) < _PROFIT_POCKET_MIN_ROWS:
                        continue
                    ordered_symbol_counts = sorted(symbol_counts.values(), reverse=True)
                    top_symbol_share = float(ordered_symbol_counts[0]) / float(len(returns))
                    top_two_symbol_share = float(sum(ordered_symbol_counts[:2])) / float(len(returns))
                    mean_edge = _safe_float(sum(returns) / len(returns))
                    median_edge = _percentile(returns, 0.50)
                    win_rate = _mean([1.0 if value > 0.0 else 0.0 for value in returns])
                    if (
                        mean_edge is None
                        or median_edge is None
                        or win_rate is None
                        or mean_edge < _PROFIT_POCKET_MIN_MEAN_EDGE_PCT
                        or median_edge <= 0.0
                        or win_rate < _PROFIT_POCKET_MIN_WIN_RATE
                        or top_symbol_share > _PROFIT_POCKET_MAX_SINGLE_SYMBOL_SHARE
                        or top_two_symbol_share > _PROFIT_POCKET_MAX_TOP_TWO_SYMBOL_SHARE
                    ):
                        continue
                    candidates.append(
                        {
                            "state_code": state_code,
                            "direction": direction,
                            "horizon_minutes": horizon_minutes,
                            "row_count": len(returns),
                            "symbol_count": len(symbol_counts),
                            "session_count": len(sessions),
                            "avg_post_cost_return_pct": mean_edge,
                            "median_post_cost_return_pct": median_edge,
                            "win_rate": win_rate,
                            "top_symbol_share": top_symbol_share,
                            "top_two_symbol_share": top_two_symbol_share,
                            "status": "STABLE",
                        }
                    )
            if not candidates:
                continue
            lookup[state_code] = max(
                candidates,
                key=lambda item: (
                    float(item["avg_post_cost_return_pct"]),
                    float(item["median_post_cost_return_pct"]),
                    float(item["win_rate"]),
                    int(item["row_count"]),
                ),
            )
        return lookup

    @staticmethod
    def _entry_plan(
        observation: BetaIntradayFeatureObservation,
        settings: BetaSettings,
        *,
        profit_pockets: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object] | None:
        if str(observation.session_state or "") != "REGULAR_OPEN":
            return None

        current_side = str(observation.recommended_action_side or "").strip().upper()
        state_code = str(observation.state_code or "").strip().upper()
        pocket = (profit_pockets or {}).get(state_code)
        candidate_directions: list[tuple[str, str]] = []
        if current_side == "BUY":
            candidate_directions.append(("DIRECT", "LONG"))
        elif current_side == "SELL":
            candidate_directions.append(("DIRECT", "SHORT"))
        if pocket is not None:
            pocket_direction = str(pocket.get("direction") or "").strip().upper()
            if pocket_direction in {"LONG", "SHORT"} and pocket_direction not in {direction for _, direction in candidate_directions}:
                candidate_directions.append(("POCKET", pocket_direction))
        if not candidate_directions:
            return None

        expected_15 = _safe_float(observation.expected_return_15m_pct)
        expected_30 = _safe_float(observation.expected_return_30m_pct)
        post_cost_15 = _safe_float(observation.post_cost_expected_return_15m_pct)
        historical_win_rate = _safe_float(observation.historical_win_rate)
        confidence_score = _safe_float(observation.confidence_score)
        sample_size = int(observation.outlook_sample_size or 0)
        matched_instruments = int(observation.matched_instrument_count or 0)
        if (
            expected_15 is None
            or post_cost_15 is None
            or historical_win_rate is None
            or confidence_score is None
        ):
            return None

        reasons = _json_object(observation.confidence_reasons_json)
        exact_state_match = bool(reasons.get("exact_state_match"))
        top_symbol_share = float(reasons.get("top_symbol_share") or 1.0)
        if bool(settings.intraday_short_trade_require_exact_state_match) and not exact_state_match:
            return None
        if top_symbol_share > float(settings.intraday_short_trade_entry_max_single_instrument_share):
            return None

        raw_p25 = float(reasons.get("p25_15m_return_pct") or 0.0)
        raw_p75 = float(reasons.get("p75_15m_return_pct") or expected_15)
        for entry_source, direction in candidate_directions:
            directional_expected_15 = BetaIntradaySimulatedTradeService._aligned_return(expected_15, direction)
            directional_expected_30 = BetaIntradaySimulatedTradeService._aligned_return(expected_30, direction)
            directional_post_cost_15 = BetaIntradaySimulatedTradeService._aligned_return(post_cost_15, direction)
            if (
                directional_expected_15 is None
                or directional_post_cost_15 is None
                or directional_expected_15 < float(settings.intraday_short_trade_entry_min_expected_return_15m_pct)
                or directional_post_cost_15 < float(settings.intraday_short_trade_entry_min_post_cost_edge_pct)
                or historical_win_rate < float(settings.intraday_short_trade_entry_min_win_rate)
                or confidence_score < float(settings.intraday_short_trade_entry_min_confidence_score)
                or sample_size < int(settings.intraday_short_trade_entry_min_sample_size)
                or matched_instruments < int(settings.intraday_short_trade_entry_min_matched_instruments)
            ):
                continue

            favorable_tail = max(0.0, raw_p75) if direction == "LONG" else abs(min(0.0, raw_p25))
            adverse_tail = abs(min(0.0, raw_p25)) if direction == "LONG" else max(0.0, raw_p75)
            target_return_pct = max(
                float(settings.intraday_short_trade_min_target_return_pct),
                min(
                    float(settings.intraday_short_trade_max_target_return_pct),
                    max(directional_expected_15, favorable_tail * 0.9, directional_post_cost_15 + 0.10),
                ),
            )
            stop_loss_pct = max(
                float(settings.intraday_short_trade_min_stop_loss_pct),
                min(
                    float(settings.intraday_short_trade_max_stop_loss_pct),
                    max(adverse_tail, target_return_pct * 0.55),
                ),
            )
            max_hold_minutes = min(
                int(settings.intraday_short_trade_max_hold_minutes),
                30
                if directional_expected_30 is not None and directional_expected_30 >= directional_expected_15 * 0.85
                else 20,
            )
            if pocket is not None and entry_source == "POCKET":
                max_hold_minutes = min(max_hold_minutes, int(pocket.get("horizon_minutes") or max_hold_minutes))
            max_hold_minutes = max(10, max_hold_minutes)
            early_bail_minutes = min(
                max_hold_minutes,
                max(1, int(settings.intraday_short_trade_bail_after_minutes)),
            )
            entry_action_side = "BUY" if direction == "LONG" else "SELL"
            entry_action_code = (
                str(observation.recommended_action_code or "").strip()
                if entry_source == "DIRECT" and current_side in {"BUY", "SELL"}
                else ("ENTER" if direction == "LONG" else "SELL_SHORT")
            )
            entry_action_label = (
                str(observation.recommended_action_label or "").strip()
                if entry_source == "DIRECT" and current_side in {"BUY", "SELL"}
                else ("Long profit pocket test" if direction == "LONG" else "Short profit pocket test")
            )
            return {
                "direction": direction,
                "entry_source": entry_source,
                "entry_action_side": entry_action_side,
                "entry_action_code": entry_action_code or ("ENTER" if direction == "LONG" else "SELL_SHORT"),
                "entry_action_label": entry_action_label or ("Long trade" if direction == "LONG" else "Short trade"),
                "target_return_pct": round(target_return_pct, 4),
                "stop_loss_pct": round(stop_loss_pct, 4),
                "max_hold_minutes": max_hold_minutes,
                "early_bail_minutes": early_bail_minutes,
                "notes": {
                    "direction": direction,
                    "entry_source": entry_source,
                    "entry_sample_size": sample_size,
                    "entry_matched_instruments": matched_instruments,
                    "entry_expected_return_15m_pct": round(directional_expected_15, 4),
                    "entry_post_cost_edge_15m_pct": round(directional_post_cost_15, 4),
                    "entry_historical_win_rate": round(historical_win_rate, 4),
                    "entry_confidence_score": round(confidence_score, 4),
                    "entry_top_symbol_share": round(top_symbol_share, 4),
                    "exact_state_match": exact_state_match,
                    "p25_15m_return_pct": round(raw_p25, 4),
                    "p75_15m_return_pct": round(raw_p75, 4),
                    "pocket_direction": str(pocket.get("direction")) if pocket is not None else None,
                    "pocket_horizon_minutes": int(pocket.get("horizon_minutes") or 0) if pocket is not None else None,
                    "pocket_row_count": int(pocket.get("row_count") or 0) if pocket is not None else None,
                },
            }
        return None

    @staticmethod
    def _simulate_trade_path(
        *,
        entry_observation: BetaIntradayFeatureObservation,
        session_observations: list[BetaIntradayFeatureObservation],
        minute_rows: list[BetaMinuteBar],
        settings: BetaSettings,
        as_of: datetime | None,
        entry_plan: dict[str, object] | None = None,
    ) -> dict[str, object]:
        plan = entry_plan or BetaIntradaySimulatedTradeService._entry_plan(entry_observation, settings)
        if plan is None:
            return {
                "status": "CANCELLED",
                "exit_reason_code": "ENTRY_FILTER_FAILED",
                "exit_reason_text": "Observation no longer meets simulated trade entry criteria.",
                "notes": {},
            }
        direction = str(plan.get("direction") or "LONG").strip().upper() or "LONG"
        entry_action_side = str(
            plan.get("entry_action_side")
            or ("BUY" if direction == "LONG" else "SELL")
        ).strip().upper()
        entry_action_code = str(plan.get("entry_action_code") or ("ENTER" if direction == "LONG" else "SELL_SHORT")).strip()
        entry_action_label = str(
            plan.get("entry_action_label") or ("Long trade" if direction == "LONG" else "Short trade")
        ).strip()

        entry_row = next((row for row in minute_rows if row.minute_ts >= entry_observation.observed_at), None)
        if entry_row is None:
            return {
                "status": "CANCELLED",
                "exit_reason_code": "MISSING_ENTRY_PRICE",
                "exit_reason_text": "No minute bar was available at or after the entry observation.",
                "notes": plan.get("notes") or {},
            }

        entry_price = _safe_float(entry_row.close_price_gbp)
        if entry_price is None or abs(entry_price) < 1e-9:
            return {
                "status": "CANCELLED",
                "exit_reason_code": "INVALID_ENTRY_PRICE",
                "exit_reason_text": "Entry minute bar did not have a usable close price.",
                "notes": plan.get("notes") or {},
            }

        target_return_pct = float(plan["target_return_pct"])
        stop_loss_pct = float(plan["stop_loss_pct"])
        max_hold_minutes = int(plan["max_hold_minutes"])
        early_bail_minutes = int(plan["early_bail_minutes"])
        cost_drag_pct = float(BetaExecutionEconomicAnnotationService.estimated_cost_drag_pct(settings))

        current_observation = entry_observation
        current_observation_id = entry_observation.id
        current_action_side = entry_observation.recommended_action_side
        current_action_code = entry_observation.recommended_action_code
        current_action_label = entry_observation.recommended_action_label
        latest_price = entry_price
        latest_time = entry_observation.observed_at
        latest_return = 0.0
        max_return = 0.0
        max_drawdown = 0.0
        hold_minutes = 0
        exit_time: datetime | None = None
        exit_price: float | None = None
        exit_reason_code: str | None = None
        exit_reason_text: str | None = None

        later_observations = [row for row in session_observations if row.observed_at >= entry_observation.observed_at]
        observation_index = 0
        for row in minute_rows:
            if row.minute_ts < entry_observation.observed_at:
                continue
            if as_of is not None and row.minute_ts > as_of:
                break
            while (
                observation_index + 1 < len(later_observations)
                and later_observations[observation_index + 1].observed_at <= row.minute_ts
            ):
                observation_index += 1
                current_observation = later_observations[observation_index]
                current_observation_id = current_observation.id
                current_action_side = current_observation.recommended_action_side
                current_action_code = current_observation.recommended_action_code
                current_action_label = current_observation.recommended_action_label

            latest_price = _safe_float(row.close_price_gbp) or latest_price
            latest_time = row.minute_ts
            raw_return_pct = ((latest_price / entry_price) - 1.0) * 100.0
            aligned_return_pct = BetaIntradaySimulatedTradeService._aligned_return(raw_return_pct, direction)
            latest_return = round(float(aligned_return_pct or 0.0), 6)
            max_return = max(max_return, latest_return)
            max_drawdown = min(max_drawdown, latest_return)
            hold_minutes = max(
                0,
                int((row.minute_ts - entry_observation.observed_at).total_seconds() // 60),
            )

            if hold_minutes >= 1 and latest_return >= target_return_pct:
                exit_reason_code = "TARGET_HIT"
                exit_reason_text = "The simulated trade reached its target return."
            elif hold_minutes >= 1 and latest_return <= -stop_loss_pct:
                exit_reason_code = "STOP_HIT"
                exit_reason_text = "The simulated trade breached its stop-loss guardrail."
            elif (
                hold_minutes >= early_bail_minutes
                and latest_return <= -max(0.12, stop_loss_pct * 0.60)
            ):
                exit_reason_code = "EARLY_BAIL"
                exit_reason_text = "Early price action failed quickly enough to trigger a bail-out."
            elif hold_minutes >= 1 and (
                (direction == "LONG" and str(current_action_side or "").strip().upper() == "SELL")
                or (direction == "SHORT" and str(current_action_side or "").strip().upper() == "BUY")
            ):
                exit_reason_code = "GUIDANCE_EXIT"
                exit_reason_text = (
                    "Later intraday guidance flipped to sell."
                    if direction == "LONG"
                    else "Later intraday guidance flipped to buy."
                )
            elif (
                hold_minutes >= max(10, early_bail_minutes)
                and str(current_action_side or "").strip().upper() == "WAIT"
                and (
                    BetaIntradaySimulatedTradeService._aligned_return(
                        _safe_float(current_observation.post_cost_expected_return_15m_pct),
                        direction,
                    )
                    or 0.0
                )
                <= 0.0
            ):
                exit_reason_code = "WEAKENING_EXIT"
                exit_reason_text = (
                    "The follow-on outlook lost positive post-cost edge."
                    if direction == "LONG"
                    else "The follow-on outlook lost negative post-cost edge."
                )
            elif (
                hold_minutes >= early_bail_minutes
                and str(current_observation.opportunity_status or "").strip().upper() == "INSUFFICIENT_EVIDENCE"
                and latest_return <= 0.0
            ):
                exit_reason_code = "EVIDENCE_FADED"
                exit_reason_text = "The follow-on outlook lost evidence before the trade improved."
            elif hold_minutes >= max_hold_minutes:
                exit_reason_code = "TIME_EXIT"
                exit_reason_text = "The trade reached its maximum hold window."

            if exit_reason_code is not None:
                exit_time = row.minute_ts
                exit_price = latest_price
                break

        status = "OPEN"
        realized_return_pct = None
        realized_post_cost_return_pct = None
        if exit_reason_code is not None and exit_price is not None and exit_time is not None:
            status = "CLOSED"
            realized_return_pct = round(
                float(
                    BetaIntradaySimulatedTradeService._aligned_return(
                        ((exit_price / entry_price) - 1.0) * 100.0,
                        direction,
                    )
                    or 0.0
                ),
                6,
            )
            realized_post_cost_return_pct = round(realized_return_pct - cost_drag_pct, 6)
        elif as_of is None:
            status = "CLOSED"
            exit_reason_code = "SESSION_END"
            exit_reason_text = "The simulated trade was closed at the session end."
            exit_time = latest_time
            exit_price = latest_price
            realized_return_pct = round(
                float(
                    BetaIntradaySimulatedTradeService._aligned_return(
                        ((exit_price / entry_price) - 1.0) * 100.0,
                        direction,
                    )
                    or 0.0
                ),
                6,
            )
            realized_post_cost_return_pct = round(realized_return_pct - cost_drag_pct, 6)

        notes = dict(plan.get("notes") or {})
        notes["direction"] = direction
        notes["latest_action_side"] = current_action_side
        notes["latest_action_code"] = current_action_code
        notes["latest_action_label"] = current_action_label

        return {
            "direction": direction,
            "entry_action_side": entry_action_side,
            "entry_action_code": entry_action_code,
            "entry_action_label": entry_action_label,
            "status": status,
            "current_observation_id": current_observation_id,
            "current_action_side": current_action_side,
            "current_action_code": current_action_code,
            "current_action_label": current_action_label,
            "entry_price_gbp": _price_string(entry_price),
            "latest_observed_at": latest_time,
            "latest_price_gbp": _price_string(latest_price),
            "latest_return_pct": latest_return,
            "max_return_pct": max_return,
            "max_drawdown_pct": max_drawdown,
            "target_return_pct": target_return_pct,
            "stop_loss_pct": stop_loss_pct,
            "max_hold_minutes": max_hold_minutes,
            "early_bail_minutes": early_bail_minutes,
            "hold_minutes": hold_minutes,
            "exit_observed_at": exit_time,
            "exit_price_gbp": _price_string(exit_price),
            "realized_return_pct": realized_return_pct,
            "realized_post_cost_return_pct": realized_post_cost_return_pct,
            "exit_reason_code": exit_reason_code,
            "exit_reason_text": exit_reason_text,
            "notes": notes,
        }

    @staticmethod
    def _minute_rows(
        sess,
        *,
        instrument_id: str,
        session_date,
        start_at: datetime | None,
        up_to: datetime | None,
    ) -> list[BetaMinuteBar]:
        stmt = (
            select(BetaMinuteBar)
            .where(
                BetaMinuteBar.instrument_id == instrument_id,
                BetaMinuteBar.session_date == session_date,
            )
            .order_by(BetaMinuteBar.minute_ts.asc())
        )
        if start_at is not None:
            stmt = stmt.where(BetaMinuteBar.minute_ts >= start_at.replace(second=0, microsecond=0))
        if up_to is not None:
            stmt = stmt.where(BetaMinuteBar.minute_ts <= up_to.replace(second=0, microsecond=0))
        return list(sess.scalars(stmt).all())

    @staticmethod
    def _session_observations(
        sess,
        *,
        instrument_id: str,
        session_date,
        up_to: datetime | None,
    ) -> list[BetaIntradayFeatureObservation]:
        stmt = (
            select(BetaIntradayFeatureObservation)
            .where(
                BetaIntradayFeatureObservation.instrument_id == instrument_id,
                BetaIntradayFeatureObservation.session_date == session_date,
                BetaIntradayFeatureObservation.session_state == "REGULAR_OPEN",
            )
            .order_by(BetaIntradayFeatureObservation.observed_at.asc())
        )
        if up_to is not None:
            stmt = stmt.where(BetaIntradayFeatureObservation.observed_at <= up_to)
        return list(sess.scalars(stmt).all())

    @staticmethod
    def _latest_observations(sess, *, instrument_ids: list[str]) -> dict[str, BetaIntradayFeatureObservation]:
        latest: dict[str, BetaIntradayFeatureObservation] = {}
        for row in sess.scalars(
            select(BetaIntradayFeatureObservation)
            .where(BetaIntradayFeatureObservation.instrument_id.in_(instrument_ids))
            .order_by(
                BetaIntradayFeatureObservation.instrument_id.asc(),
                BetaIntradayFeatureObservation.session_date.desc(),
                BetaIntradayFeatureObservation.observed_at.desc(),
            )
        ).all():
            if row.instrument_id not in latest:
                latest[row.instrument_id] = row
        return latest

    @staticmethod
    def _focus_bucket(market: str) -> str:
        market_key = str(market or "").strip().upper()
        if market_key == "UK":
            return "UK_LARGE_CAP"
        if market_key == "US":
            return "US_LARGE_CAP"
        return "OTHER_FOCUS"

    @staticmethod
    def _record_trade_event(
        *,
        sess,
        trade: BetaIntradaySimulatedTrade,
        event_type: str,
        event_time: datetime,
        price_gbp: str | None,
        return_pct: float | None,
        message_text: str,
        payload: dict[str, object],
    ) -> None:
        sess.add(
            BetaIntradaySimulatedTradeEvent(
                simulated_trade_id=trade.id,
                event_time=event_time,
                event_type=event_type,
                price_gbp=price_gbp,
                return_pct=return_pct,
                message_text=message_text,
                payload_json=json.dumps(payload, sort_keys=True) if payload else None,
            )
        )
