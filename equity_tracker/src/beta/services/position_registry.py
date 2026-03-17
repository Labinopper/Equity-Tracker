"""Persistent position-state registry for the beta execution layer."""

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, select

from ...db.models import Lot as CoreLot
from ...db.models import Security as CoreSecurity
from ..context import BetaContext
from ..core_access import core_read_session
from ..db.models import (
    BetaDemoPosition,
    BetaInstrument,
    BetaPositionState,
    BetaSignalCandidate,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _candidate_payload(candidate: BetaSignalCandidate | None) -> dict[str, object]:
    if candidate is None or not candidate.evidence_json:
        return {}
    try:
        payload = json.loads(candidate.evidence_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _metadata_payload(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _decimal_to_storage(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class BetaPositionRegistry:
    """Maintain persistent position state separate from demo-position mechanics."""

    @staticmethod
    def get_held_symbols() -> list[str]:
        if not BetaContext.is_initialized():
            return []
        with BetaContext.read_session() as sess:
            rows = list(
                sess.scalars(
                    select(BetaPositionState.symbol)
                    .where(BetaPositionState.position_status == "OPEN")
                    .order_by(BetaPositionState.updated_at.desc())
                ).all()
            )
        seen: set[str] = set()
        ordered: list[str] = []
        for symbol in rows:
            normalized = str(symbol or "").strip().upper()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    @staticmethod
    def get_position(symbol: str) -> BetaPositionState | None:
        if not BetaContext.is_initialized():
            return None
        with BetaContext.read_session() as sess:
            return sess.scalar(
                select(BetaPositionState)
                .where(
                    BetaPositionState.symbol == str(symbol).upper(),
                    BetaPositionState.position_status == "OPEN",
                )
                .order_by(desc(BetaPositionState.updated_at))
                .limit(1)
            )

    @staticmethod
    def update_position_state(
        *,
        symbol: str,
        instrument_id: str | None = None,
        demo_position_id: str | None = None,
        position_source: str = "MANUAL",
        position_status: str = "OPEN",
        position_size_gbp: str | None = None,
        units: str | None = None,
        entry_price: str | None = None,
        entry_timestamp: datetime | None = None,
        thesis_id: str | None = None,
        thesis_candidate_id: str | None = None,
        thesis_hypothesis_definition_id: str | None = None,
        thesis_expected_return_pct: float | None = None,
        thesis_horizon_days: int | None = None,
        unrealized_return_pct: float | None = None,
        realized_return_pct: float | None = None,
        execution_quality_score: float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str | None:
        if not BetaContext.is_initialized():
            return None

        normalized_symbol = str(symbol or "").upper().strip()
        if not normalized_symbol:
            return None

        with BetaContext.write_session() as sess:
            state = None
            if demo_position_id:
                state = sess.scalar(
                    select(BetaPositionState).where(BetaPositionState.demo_position_id == demo_position_id).limit(1)
                )
            if state is None:
                state = sess.scalar(
                    select(BetaPositionState)
                    .where(
                        BetaPositionState.symbol == normalized_symbol,
                        BetaPositionState.position_status == "OPEN",
                    )
                    .order_by(desc(BetaPositionState.updated_at))
                    .limit(1)
                )
            if state is None:
                state = BetaPositionState(
                    symbol=normalized_symbol,
                    position_source=position_source,
                    position_status=position_status,
                )
                sess.add(state)
                sess.flush()

            state.instrument_id = instrument_id or state.instrument_id
            state.demo_position_id = demo_position_id or state.demo_position_id
            state.symbol = normalized_symbol
            state.position_source = position_source or state.position_source
            state.position_status = position_status or state.position_status
            state.position_size_gbp = position_size_gbp or state.position_size_gbp
            state.units = units or state.units
            state.entry_price = entry_price or state.entry_price
            state.entry_timestamp = entry_timestamp or state.entry_timestamp
            state.thesis_id = thesis_id or state.thesis_id
            state.thesis_candidate_id = thesis_candidate_id or state.thesis_candidate_id
            state.thesis_hypothesis_definition_id = (
                thesis_hypothesis_definition_id or state.thesis_hypothesis_definition_id
            )
            state.thesis_expected_return_pct = (
                thesis_expected_return_pct
                if thesis_expected_return_pct is not None
                else state.thesis_expected_return_pct
            )
            state.thesis_horizon_days = thesis_horizon_days or state.thesis_horizon_days
            state.unrealized_return_pct = (
                unrealized_return_pct if unrealized_return_pct is not None else state.unrealized_return_pct
            )
            state.realized_return_pct = (
                realized_return_pct if realized_return_pct is not None else state.realized_return_pct
            )
            if unrealized_return_pct is not None:
                previous_max = float(state.max_unrealized_return_pct or unrealized_return_pct)
                state.max_unrealized_return_pct = max(previous_max, float(unrealized_return_pct))
            if execution_quality_score is not None:
                state.execution_quality_score = execution_quality_score
            if metadata:
                state.metadata_json = json.dumps(metadata, sort_keys=True)
            return state.id

    @staticmethod
    def sync_core_portfolio_positions(*, now_utc: datetime | None = None) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {
                "states_upserted": 0,
                "open_positions": 0,
                "closed_positions": 0,
                "unmatched_instruments": 0,
            }

        now = now_utc.replace(tzinfo=None) if now_utc and now_utc.tzinfo else (now_utc or _utcnow())
        with core_read_session() as core_sess:
            core_securities = {
                row.id: row
                for row in core_sess.scalars(select(CoreSecurity)).all()
            }
            core_lots = list(core_sess.scalars(select(CoreLot)).all())

        holdings_by_symbol: dict[str, dict[str, object]] = {}
        for lot in core_lots:
            quantity_remaining = _as_decimal(lot.quantity_remaining)
            if quantity_remaining is None or quantity_remaining <= 0:
                continue
            security = core_securities.get(lot.security_id)
            if security is None:
                continue
            symbol = str(security.ticker or "").strip().upper()
            if not symbol:
                continue
            acquisition_price = _as_decimal(lot.acquisition_price_gbp) or Decimal("0")
            aggregate = holdings_by_symbol.setdefault(
                symbol,
                {
                    "core_security_id": security.id,
                    "name": str(security.name or symbol),
                    "exchange": str(security.exchange or "").strip().upper() or None,
                    "currency": str(security.currency or "").strip().upper() or None,
                    "units": Decimal("0"),
                    "weighted_cost_sum": Decimal("0"),
                    "lot_count": 0,
                    "earliest_acquisition_date": lot.acquisition_date,
                },
            )
            aggregate["units"] = Decimal(str(aggregate["units"])) + quantity_remaining
            aggregate["weighted_cost_sum"] = Decimal(str(aggregate["weighted_cost_sum"])) + (
                quantity_remaining * acquisition_price
            )
            aggregate["lot_count"] = int(aggregate["lot_count"]) + 1
            earliest = aggregate.get("earliest_acquisition_date")
            if earliest is None or lot.acquisition_date < earliest:
                aggregate["earliest_acquisition_date"] = lot.acquisition_date

        with BetaContext.write_session() as sess:
            instruments_by_symbol = {
                str(row.symbol or "").upper(): row
                for row in sess.scalars(select(BetaInstrument)).all()
            }
            bridge_states: dict[str, BetaPositionState] = {}
            manual_states = list(
                sess.scalars(
                    select(BetaPositionState).where(BetaPositionState.position_source == "MANUAL")
                ).all()
            )
            for state in sorted(manual_states, key=lambda row: row.updated_at, reverse=True):
                metadata = _metadata_payload(state.metadata_json)
                if metadata.get("bridge_source") != "CORE_PORTFOLIO":
                    continue
                symbol = str(state.symbol or "").strip().upper()
                if symbol and symbol not in bridge_states:
                    bridge_states[symbol] = state

            active_symbols: set[str] = set()
            states_upserted = 0
            open_positions = 0
            closed_positions = 0
            unmatched_instruments = 0

            for symbol, aggregate in holdings_by_symbol.items():
                instrument = instruments_by_symbol.get(symbol)
                if instrument is None:
                    unmatched_instruments += 1
                    continue
                active_symbols.add(symbol)
                state = bridge_states.get(symbol)
                if state is None:
                    state = BetaPositionState(
                        symbol=symbol,
                        position_source="MANUAL",
                        position_status="OPEN",
                    )
                    sess.add(state)
                    sess.flush()
                    bridge_states[symbol] = state

                units = Decimal(str(aggregate["units"]))
                weighted_cost_sum = Decimal(str(aggregate["weighted_cost_sum"]))
                entry_price = (weighted_cost_sum / units) if units > 0 else None
                earliest_acquisition_date = aggregate.get("earliest_acquisition_date")
                entry_timestamp = (
                    datetime.combine(earliest_acquisition_date, time.min)
                    if earliest_acquisition_date is not None
                    else None
                )
                state.instrument_id = instrument.id
                state.market = instrument.market
                state.position_status = "OPEN"
                state.units = _decimal_to_storage(units)
                state.entry_price = _decimal_to_storage(entry_price)
                state.entry_timestamp = entry_timestamp
                state.metadata_json = json.dumps(
                    {
                        "bridge_source": "CORE_PORTFOLIO",
                        "bridge_type": "aggregated_open_lots",
                        "core_security_id": str(aggregate["core_security_id"]),
                        "core_exchange": aggregate["exchange"],
                        "core_currency": aggregate["currency"],
                        "core_name": aggregate["name"],
                        "lot_count": int(aggregate["lot_count"]),
                        "last_core_sync_at": now.isoformat(),
                    },
                    sort_keys=True,
                )
                states_upserted += 1
                open_positions += 1

            for symbol, state in bridge_states.items():
                if symbol in active_symbols or state.position_status != "OPEN":
                    continue
                metadata = _metadata_payload(state.metadata_json)
                metadata["bridge_source"] = "CORE_PORTFOLIO"
                metadata["bridge_type"] = "aggregated_open_lots"
                metadata["closed_by_bridge"] = True
                metadata["last_core_sync_at"] = now.isoformat()
                state.position_status = "CLOSED"
                state.metadata_json = json.dumps(metadata, sort_keys=True)
                closed_positions += 1

            return {
                "states_upserted": states_upserted,
                "open_positions": open_positions,
                "closed_positions": closed_positions,
                "unmatched_instruments": unmatched_instruments,
            }

    @staticmethod
    def sync_demo_positions(*, now_utc: datetime | None = None) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"states_upserted": 0, "open_positions": 0, "closed_positions": 0}

        now = now_utc.replace(tzinfo=None) if now_utc and now_utc.tzinfo else (now_utc or _utcnow())
        with BetaContext.write_session() as sess:
            instruments = {
                row.symbol.upper(): row
                for row in sess.scalars(select(BetaInstrument)).all()
            }
            candidates = {
                row.id: row
                for row in sess.scalars(select(BetaSignalCandidate)).all()
            }
            existing_states = {
                row.demo_position_id: row
                for row in sess.scalars(
                    select(BetaPositionState).where(BetaPositionState.demo_position_id.is_not(None))
                ).all()
            }
            positions = list(sess.scalars(select(BetaDemoPosition)).all())
            upserted = 0
            open_positions = 0
            closed_positions = 0
            for position in positions:
                normalized_symbol = str(position.symbol or "").upper()
                instrument = instruments.get(normalized_symbol)
                candidate = candidates.get(position.candidate_id) if position.candidate_id else None
                evidence = _candidate_payload(candidate)

                state = existing_states.get(position.id)
                if state is None:
                    state = BetaPositionState(
                        demo_position_id=position.id,
                        symbol=normalized_symbol,
                        position_source="DEMO",
                    )
                    sess.add(state)
                    sess.flush()
                    existing_states[position.id] = state

                entry_price = _as_decimal(position.entry_price)
                pnl_pct = _as_decimal(position.pnl_pct)
                unrealized_return_pct = float(pnl_pct) if position.status == "OPEN" and pnl_pct is not None else None
                realized_return_pct = float(pnl_pct) if position.status != "OPEN" and pnl_pct is not None else None
                thesis_expected = BetaPositionRegistry._resolve_expected_return_pct(position, candidate, evidence)
                thesis_horizon_days = int(position.planned_horizon_days or evidence.get("holding_period_days") or 5)
                remaining_days = BetaPositionRegistry._remaining_horizon_days(
                    opened_at=position.opened_at,
                    horizon_days=thesis_horizon_days,
                    now=now,
                    closed_at=position.closed_at,
                )
                quality_score = BetaPositionRegistry._execution_quality_score(
                    thesis_expected_return_pct=thesis_expected,
                    realized_return_pct=realized_return_pct,
                    unrealized_return_pct=unrealized_return_pct,
                    is_open=position.status == "OPEN",
                )

                is_stale_open = (
                    position.status == "OPEN"
                    and position.closed_at is None
                    and position.opened_at is not None
                    and (now - (position.opened_at.replace(tzinfo=None) if position.opened_at.tzinfo else position.opened_at)).days > thesis_horizon_days * 2
                )
                resolved_status = "CLOSED" if is_stale_open else ("OPEN" if position.status == "OPEN" else "CLOSED")
                state.instrument_id = instrument.id if instrument is not None else state.instrument_id
                state.market = position.market
                state.position_status = resolved_status
                state.position_size_gbp = position.size_gbp
                state.units = position.units
                state.entry_price = position.entry_price
                state.entry_timestamp = position.opened_at
                state.thesis_id = position.candidate_id or state.thesis_id
                state.thesis_candidate_id = position.candidate_id
                state.thesis_hypothesis_definition_id = (
                    candidate.hypothesis_definition_id if candidate is not None else state.thesis_hypothesis_definition_id
                )
                state.thesis_expected_return_pct = thesis_expected
                state.thesis_horizon_days = thesis_horizon_days
                state.thesis_remaining_days = remaining_days
                state.unrealized_return_pct = unrealized_return_pct
                if unrealized_return_pct is not None:
                    state.max_unrealized_return_pct = max(
                        float(state.max_unrealized_return_pct or unrealized_return_pct),
                        unrealized_return_pct,
                    )
                state.realized_return_pct = realized_return_pct
                state.execution_quality_score = quality_score
                state.metadata_json = json.dumps(
                    {
                        "entry_price": str(entry_price) if entry_price is not None else None,
                        "target_return_pct": position.target_return_pct,
                        "stop_loss_pct": position.stop_loss_pct,
                        "candidate_status": candidate.status if candidate is not None else None,
                        "stale_closed_by_registry": is_stale_open or None,
                    },
                    sort_keys=True,
                )
                upserted += 1
                if position.status == "OPEN":
                    open_positions += 1
                else:
                    closed_positions += 1

            return {
                "states_upserted": upserted,
                "open_positions": open_positions,
                "closed_positions": closed_positions,
            }

    @staticmethod
    def _resolve_expected_return_pct(
        position: BetaDemoPosition,
        candidate: BetaSignalCandidate | None,
        evidence: dict[str, object],
    ) -> float | None:
        if candidate is not None:
            try:
                predicted = float(evidence.get("predicted_return_pct"))
            except (TypeError, ValueError):
                predicted = None
            if predicted is not None:
                return predicted
        target_return = _as_decimal(position.target_return_pct)
        if target_return is not None:
            return float(target_return)
        return None

    @staticmethod
    def _remaining_horizon_days(
        *,
        opened_at: datetime | None,
        horizon_days: int | None,
        now: datetime,
        closed_at: datetime | None,
    ) -> float | None:
        if horizon_days is None or horizon_days <= 0 or opened_at is None:
            return None
        end = (closed_at or now) - opened_at
        elapsed_days = max(0.0, end.total_seconds() / 86400.0)
        return max(0.0, round(float(horizon_days) - elapsed_days, 4))

    @staticmethod
    def _execution_quality_score(
        *,
        thesis_expected_return_pct: float | None,
        realized_return_pct: float | None,
        unrealized_return_pct: float | None,
        is_open: bool,
    ) -> float | None:
        denominator = float(thesis_expected_return_pct or 0.0)
        if abs(denominator) < 0.001:
            return None
        numerator = float(unrealized_return_pct if is_open else realized_return_pct or 0.0)
        return round(numerator / denominator, 4)
