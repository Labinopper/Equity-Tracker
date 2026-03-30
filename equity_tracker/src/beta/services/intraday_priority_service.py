"""Priority selection for intraday execution monitoring under tight budgets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, desc, select

from ..context import BetaContext
from ..db.models import (
    BetaInstrument,
    BetaInstrumentStatistics,
    BetaIntradaySimulatedTrade,
    BetaPositionState,
    BetaResearchRanking,
    BetaSignalCandidate,
    BetaUniverseMembership,
)
from ..settings import BetaSettings
from .session_service import BetaMarketSessionService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_NATIVE_EXCHANGES_BY_MARKET: dict[str, set[str]] = {
    "US": {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "CBOE"},
    "UK": {"LSE", "LON", "XLON"},
}


@dataclass(frozen=True)
class IntradayPriorityItem:
    instrument_id: str
    symbol: str
    market: str
    exchange: str | None
    tier: str
    cadence_minutes: int
    priority_score: float
    session_state: str


class BetaIntradayPriorityService:
    """Allocate limited intraday compute to held positions first."""

    @staticmethod
    def build_focus_watchlist(settings: BetaSettings, *, now_utc: datetime | None = None) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"items": [], "us_focus": 0, "uk_focus": 0, "open_trade_focus": 0}

        now = now_utc or _utcnow()
        focus_symbols = BetaIntradayPriorityService._load_focus_symbols()
        with BetaContext.read_session() as sess:
            instruments = list(
                sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all()
            )
            stats_by_instrument = {
                row.instrument_id: row
                for row in sess.scalars(select(BetaInstrumentStatistics)).all()
            }
            simulated_instrument_ids = {
                str(instrument_id)
                for instrument_id in sess.scalars(
                    select(BetaIntradaySimulatedTrade.instrument_id).where(
                        BetaIntradaySimulatedTrade.status == "OPEN",
                        BetaIntradaySimulatedTrade.instrument_id.is_not(None),
                    )
                ).all()
                if instrument_id
            }

        instruments_by_key: dict[tuple[str, str], BetaInstrument] = {}
        instruments_by_id = {row.id: row for row in instruments}
        for instrument in instruments:
            if not BetaIntradayPriorityService._native_exchange_ok(
                market=instrument.market,
                exchange=instrument.exchange,
            ):
                continue
            key = (str(instrument.symbol or "").strip().upper(), str(instrument.market or "").strip().upper())
            instruments_by_key.setdefault(key, instrument)

        items: list[IntradayPriorityItem] = []
        seen_instrument_ids: set[str] = set()
        us_focus = 0
        uk_focus = 0

        for market, cap in (
            ("US", max(1, int(settings.intraday_focus_us_symbol_cap))),
            ("UK", max(1, int(settings.intraday_focus_uk_symbol_cap))),
        ):
            configured_symbols = [str(symbol).strip().upper() for symbol in focus_symbols.get(market, [])]
            added_for_market = 0
            for symbol in configured_symbols:
                instrument = instruments_by_key.get((symbol, market))
                if instrument is None or instrument.id in seen_instrument_ids:
                    continue
                items.append(
                    BetaIntradayPriorityService._focus_item(
                        instrument=instrument,
                        priority_score=1.0,
                        now_utc=now,
                        cadence_minutes=settings.intraday_focus_symbol_cadence_minutes,
                    )
                )
                seen_instrument_ids.add(instrument.id)
                added_for_market += 1
                if market == "US":
                    us_focus += 1
                else:
                    uk_focus += 1
                if added_for_market >= cap:
                    break

            if added_for_market >= cap:
                continue

            ranked_candidates: list[tuple[bool, float, str, BetaInstrument]] = []
            for instrument in instruments:
                if instrument.id in seen_instrument_ids:
                    continue
                if str(instrument.market or "").strip().upper() != market:
                    continue
                if not BetaIntradayPriorityService._native_exchange_ok(
                    market=instrument.market,
                    exchange=instrument.exchange,
                ):
                    continue
                market_cap = float(stats_by_instrument.get(instrument.id).market_cap or 0.0) if instrument.id in stats_by_instrument else 0.0
                ranked_candidates.append(
                    (
                        market_cap > 0.0,
                        market_cap,
                        str(instrument.symbol or ""),
                        instrument,
                    )
                )
            ranked_candidates.sort(
                key=lambda item: (item[0], item[1], item[2]),
                reverse=True,
            )
            for has_market_cap, market_cap, _symbol, instrument in ranked_candidates:
                items.append(
                    BetaIntradayPriorityService._focus_item(
                        instrument=instrument,
                        priority_score=market_cap if has_market_cap else 0.1,
                        now_utc=now,
                        cadence_minutes=settings.intraday_focus_symbol_cadence_minutes,
                    )
                )
                seen_instrument_ids.add(instrument.id)
                if market == "US":
                    us_focus += 1
                else:
                    uk_focus += 1
                added_for_market += 1
                if added_for_market >= cap:
                    break

        open_trade_focus = 0
        for instrument_id in simulated_instrument_ids:
            if instrument_id in seen_instrument_ids:
                continue
            instrument = instruments_by_id.get(instrument_id)
            if instrument is None:
                continue
            items.append(
                BetaIntradayPriorityService._focus_item(
                    instrument=instrument,
                    priority_score=2.0,
                    now_utc=now,
                    cadence_minutes=max(1, settings.intraday_focus_symbol_cadence_minutes),
                )
            )
            seen_instrument_ids.add(instrument_id)
            open_trade_focus += 1

        return {
            "items": items,
            "us_focus": us_focus,
            "uk_focus": uk_focus,
            "open_trade_focus": open_trade_focus,
        }

    @staticmethod
    def build_watchlist(settings: BetaSettings, *, now_utc: datetime | None = None) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"items": [], "held": 0, "active_thesis": 0, "general": 0}

        now = now_utc or _utcnow()
        with BetaContext.read_session() as sess:
            instruments = {
                row.id: row
                for row in sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all()
            }
            held_rows = list(
                sess.scalars(
                    select(BetaPositionState)
                    .where(
                        BetaPositionState.position_status == "OPEN",
                        BetaPositionState.position_source.in_(("DEMO", "MANUAL")),
                    )
                    .order_by(desc(BetaPositionState.updated_at))
                ).all()
            )
            held_items: list[IntradayPriorityItem] = []
            seen_symbols: set[str] = set()
            for row in held_rows:
                instrument = instruments.get(row.instrument_id or "")
                if instrument is None:
                    instrument = sess.scalar(
                        select(BetaInstrument).where(BetaInstrument.symbol == row.symbol).limit(1)
                    )
                if instrument is None:
                    continue
                seen_symbols.add(str(instrument.symbol).upper())
                held_items.append(
                    IntradayPriorityItem(
                        instrument_id=instrument.id,
                        symbol=instrument.symbol,
                        market=str(instrument.market or "OTHER"),
                        exchange=instrument.exchange,
                        tier="HELD",
                        cadence_minutes=max(1, settings.intraday_held_symbol_cadence_minutes),
                        priority_score=1.0,
                        session_state=BetaMarketSessionService.session_state(instrument.exchange, now_utc=now),
                    )
                )

            active_target = BetaIntradayPriorityService._budget_slice(
                total_budget=settings.intraday_learning_symbol_budget,
                pct=settings.intraday_priority_active_thesis_weight_pct,
            )
            general_target = BetaIntradayPriorityService._budget_slice(
                total_budget=settings.intraday_learning_symbol_budget,
                pct=settings.intraday_priority_general_weight_pct,
            )
            active_candidates = list(
                sess.execute(
                    select(BetaSignalCandidate, BetaInstrument)
                    .join(
                        BetaInstrument,
                        BetaInstrument.id == BetaSignalCandidate.instrument_id,
                        isouter=True,
                    )
                    .where(BetaSignalCandidate.status.in_(("WATCHING", "PROMOTED")))
                    .order_by(
                        desc(BetaSignalCandidate.updated_at),
                        desc(BetaSignalCandidate.confidence_score),
                        desc(BetaSignalCandidate.expected_edge_score),
                    )
                    .limit(max(10, active_target * 3))
                ).all()
            )
            active_items: list[IntradayPriorityItem] = []
            for candidate, instrument in active_candidates:
                if instrument is None or instrument.id is None:
                    continue
                if instrument.symbol.upper() in seen_symbols:
                    continue
                session_state = BetaMarketSessionService.session_state(instrument.exchange, now_utc=now)
                active_items.append(
                    IntradayPriorityItem(
                        instrument_id=instrument.id,
                        symbol=instrument.symbol,
                        market=str(instrument.market or "OTHER"),
                        exchange=instrument.exchange,
                        tier="ACTIVE_THESIS",
                        cadence_minutes=max(
                            settings.intraday_held_symbol_cadence_minutes,
                            settings.intraday_active_thesis_cadence_minutes,
                        ),
                        priority_score=round(
                            float(candidate.confidence_score or 0.0)
                            + float(candidate.expected_edge_score or 0.0),
                            4,
                        ),
                        session_state=session_state,
                    )
                )
                seen_symbols.add(instrument.symbol.upper())
                if len(active_items) >= active_target:
                    break

            general_items: list[IntradayPriorityItem] = []
            if settings.intraday_watchlist_general_cap > 0 and general_target > 0:
                latest_ranking_run = sess.scalar(
                    select(BetaResearchRanking.ranking_run_code)
                    .order_by(desc(BetaResearchRanking.created_at))
                    .limit(1)
                )
                ranking_rows = []
                if latest_ranking_run:
                    ranking_rows = list(
                        sess.scalars(
                            select(BetaResearchRanking)
                            .where(
                                BetaResearchRanking.ranking_run_code == latest_ranking_run,
                                BetaResearchRanking.selection_status.in_(("SELECTED", "ACTIVE", "DEFERRED")),
                            )
                            .order_by(BetaResearchRanking.rank_position.asc())
                            .limit(max(general_target * 4, settings.intraday_watchlist_general_cap))
                        ).all()
                    )
                if not ranking_rows:
                    ranking_rows = list(
                        sess.scalars(
                            select(BetaUniverseMembership)
                            .where(
                                BetaUniverseMembership.effective_to.is_(None),
                                BetaUniverseMembership.status.in_(("ACTIVE", "SEED")),
                            )
                            .order_by(
                                case((BetaUniverseMembership.status == "ACTIVE", 0), else_=1),
                                BetaUniverseMembership.priority_rank.asc(),
                            )
                            .limit(max(general_target * 3, settings.intraday_watchlist_general_cap))
                        ).all()
                    )
                for row in ranking_rows:
                    instrument = instruments.get(getattr(row, "instrument_id", None) or "")
                    if instrument is None:
                        continue
                    if instrument.symbol.upper() in seen_symbols:
                        continue
                    general_items.append(
                        IntradayPriorityItem(
                            instrument_id=instrument.id,
                            symbol=instrument.symbol,
                            market=str(instrument.market or "OTHER"),
                            exchange=instrument.exchange,
                            tier="GENERAL",
                            cadence_minutes=max(15, settings.intraday_active_thesis_cadence_minutes),
                            priority_score=float(getattr(row, "ranking_score", 0.0) or 0.0),
                            session_state=BetaMarketSessionService.session_state(instrument.exchange, now_utc=now),
                        )
                    )
                    seen_symbols.add(instrument.symbol.upper())
                    if len(general_items) >= min(general_target, settings.intraday_watchlist_general_cap):
                        break

        items = held_items + active_items + general_items
        return {
            "items": items,
            "held": len(held_items),
            "active_thesis": len(active_items),
            "general": len(general_items),
        }

    @staticmethod
    def _budget_slice(*, total_budget: int, pct: int) -> int:
        if total_budget <= 0 or pct <= 0:
            return 0
        return max(1, int(round(total_budget * (pct / 100.0))))

    @staticmethod
    def _focus_item(
        *,
        instrument: BetaInstrument,
        priority_score: float,
        now_utc: datetime,
        cadence_minutes: int,
    ) -> IntradayPriorityItem:
        return IntradayPriorityItem(
            instrument_id=instrument.id,
            symbol=instrument.symbol,
            market=str(instrument.market or "OTHER"),
            exchange=instrument.exchange,
            tier="FOCUS",
            cadence_minutes=max(1, int(cadence_minutes)),
            priority_score=float(priority_score),
            session_state=BetaMarketSessionService.session_state(instrument.exchange, now_utc=now_utc),
        )

    @staticmethod
    def _native_exchange_ok(*, market: str | None, exchange: str | None) -> bool:
        market_key = str(market or "").strip().upper()
        allowed = _NATIVE_EXCHANGES_BY_MARKET.get(market_key)
        if not allowed:
            return True
        return str(exchange or "").strip().upper() in allowed

    @staticmethod
    def _load_focus_symbols() -> dict[str, list[str]]:
        config_path = Path(__file__).resolve().parents[1] / "config" / "intraday_focus_symbols.json"
        if not config_path.exists():
            return {"US": ["IBM"], "UK": []}
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"US": ["IBM"], "UK": []}
        result: dict[str, list[str]] = {"US": [], "UK": []}
        for market in ("US", "UK"):
            raw_symbols = payload.get(market) or []
            result[market] = [str(symbol).strip().upper() for symbol in raw_symbols if str(symbol).strip()]
        if "IBM" not in result["US"]:
            result["US"].insert(0, "IBM")
        return result
