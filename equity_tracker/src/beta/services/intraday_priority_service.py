"""Priority selection for intraday execution monitoring under tight budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case, desc, select

from ..context import BetaContext
from ..db.models import (
    BetaInstrument,
    BetaPositionState,
    BetaResearchRanking,
    BetaSignalCandidate,
    BetaUniverseMembership,
)
from ..settings import BetaSettings
from .session_service import BetaMarketSessionService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
                    .where(BetaPositionState.position_status == "OPEN")
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
