"""Reference-domain sync from the core app into the beta database."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from ...db.models import PriceHistory, Security, SecurityCatalog
from ..context import BetaContext
from ..core_access import core_read_session
from ..db.models import BetaDailyBar, BetaInstrument, BetaUniverseMembership

_UK_EXCHANGES = {"LSE", "XLON", "LON"}
_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "IEX", "XNYS", "XNAS"}
_INITIAL_TARGET = 50
_INITIAL_UK_TARGET = 35
_EXPANSION_STEP = 25
_MAX_AUTO_TARGET = 500
_MIN_EXPANSION_COVERAGE_BARS = 30
_RECENCY_DAYS = 21
_SECTOR_KEYWORDS = (
    ("FINANCIALS", "Financials", ("bank", "financial", "insurance", "capital", "asset management", "holdings")),
    ("ENERGY_MATERIALS", "Energy & Materials", ("oil", "gas", "mining", "metals", "resources", "energy")),
    ("CONSUMER", "Consumer", ("retail", "stores", "foods", "food", "consumer", "brands", "leisure")),
    ("HEALTHCARE", "Healthcare", ("pharma", "therapeutics", "health", "medical", "biotech", "diagnostic")),
    ("INDUSTRIALS", "Industrials", ("engineering", "industrial", "transport", "logistics", "aerospace", "defence", "defense")),
    ("TECHNOLOGY", "Technology", ("software", "technology", "tech", "semiconductor", "systems", "digital", "micro")),
    ("UTILITIES_TELECOM", "Utilities & Telecom", ("telecom", "communications", "utility", "utilities", "electric", "water")),
    ("REAL_ESTATE", "Real Estate", ("reit", "real estate", "property", "homes", "land")),
)


def _market_for(exchange: str | None, currency: str | None) -> str:
    exch = str(exchange or "").upper()
    ccy = str(currency or "").upper()
    if exch in _UK_EXCHANGES or ccy == "GBP":
        return "UK"
    if exch in _US_EXCHANGES or ccy == "USD":
        return "US"
    return "OTHER"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _benchmark_key_for_market(market: str) -> str | None:
    if market == "UK":
        return "UK_MKT"
    if market == "US":
        return "US_MKT"
    return None


def _sector_for_name(name: str) -> tuple[str, str]:
    lowered = str(name or "").lower()
    for sector_key, sector_label, keywords in _SECTOR_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return sector_key, sector_label
    return "GENERAL", "General"


class BetaReferenceService:
    """Sync holdings/catalog references into the beta DB and seed the starter universe."""

    @staticmethod
    def sync_seed_universe(*, target_total: int = _INITIAL_TARGET, uk_target: int = _INITIAL_UK_TARGET) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"instruments_added": 0, "memberships_added": 0, "selected_total": 0}

        with core_read_session() as core_sess:
            holdings = list(core_sess.scalars(select(Security).order_by(Security.ticker)).all())
            catalog = list(core_sess.scalars(select(SecurityCatalog).order_by(SecurityCatalog.symbol)).all())
            price_coverage = {
                row.security_id: {
                    "price_count": int(row.price_count or 0),
                    "latest_price_date": row.latest_price_date,
                }
                for row in core_sess.execute(
                    select(
                        PriceHistory.security_id.label("security_id"),
                        func.count(PriceHistory.id).label("price_count"),
                        func.max(PriceHistory.price_date).label("latest_price_date"),
                    )
                    .group_by(PriceHistory.security_id)
                )
            }

        selected_symbols: set[tuple[str, str]] = set()
        selected_rows: list[dict[str, str | int | None]] = []

        with BetaContext.read_session() as beta_sess:
            active_memberships = list(
                beta_sess.scalars(
                    select(BetaUniverseMembership).where(
                        BetaUniverseMembership.effective_to.is_(None),
                        BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
                    )
                ).all()
            )
            active_instrument_ids = [row.instrument_id for row in active_memberships]
            coverage_by_instrument = {
                row.instrument_id: int(row.bar_count or 0)
                for row in beta_sess.execute(
                    select(
                        BetaDailyBar.instrument_id,
                        func.count().label("bar_count"),
                    )
                    .where(BetaDailyBar.instrument_id.in_(active_instrument_ids))
                    .group_by(BetaDailyBar.instrument_id)
                )
            } if active_instrument_ids else {}

        active_count = len(active_memberships)
        covered_active = len(
            [instrument_id for instrument_id in active_instrument_ids if coverage_by_instrument.get(instrument_id, 0) >= _MIN_EXPANSION_COVERAGE_BARS]
        )
        coverage_ratio = (covered_active / active_count) if active_count else 0.0

        def latest_is_recent(latest_price_date) -> bool:
            if latest_price_date is None:
                return False
            if isinstance(latest_price_date, str):
                try:
                    latest_dt = date.fromisoformat(latest_price_date)
                except ValueError:
                    return False
            else:
                latest_dt = latest_price_date
            return (date.today() - latest_dt).days <= _RECENCY_DAYS

        eligible_core_rows = [
            security
            for security in holdings
            if price_coverage.get(security.id, {}).get("price_count", 0) >= _MIN_EXPANSION_COVERAGE_BARS
            and latest_is_recent(price_coverage.get(security.id, {}).get("latest_price_date"))
        ]
        dynamic_target_total = max(target_total, active_count or 0)
        if active_count >= _INITIAL_TARGET and coverage_ratio >= 0.75 and len(eligible_core_rows) > active_count:
            dynamic_target_total = min(
                _MAX_AUTO_TARGET,
                min(active_count + _EXPANSION_STEP, len(eligible_core_rows)),
            )
        dynamic_uk_target = max(_INITIAL_UK_TARGET, round(dynamic_target_total * 0.7))

        def add_candidate(
            *,
            symbol: str,
            name: str,
            exchange: str | None,
            currency: str,
            core_security_id: str | None,
            reason_code: str,
            status: str,
            priority_score: int,
        ) -> None:
            key = (symbol.upper(), str(exchange or "").upper())
            if key in selected_symbols:
                return
            selected_symbols.add(key)
            selected_rows.append(
                {
                    "symbol": symbol.upper(),
                    "name": name,
                    "exchange": str(exchange or "").upper() or None,
                    "currency": currency.upper(),
                    "core_security_id": core_security_id,
                    "market": _market_for(exchange, currency),
                    "reason_code": reason_code,
                    "status": status,
                    "priority_score": priority_score,
                }
            )

        core_candidates = []
        for security in holdings:
            coverage = price_coverage.get(security.id, {})
            price_count = int(coverage.get("price_count", 0))
            latest_price_date = coverage.get("latest_price_date")
            market = _market_for(security.exchange, security.currency)
            freshness_bonus = 0 if latest_is_recent(latest_price_date) else 100000
            market_bias = 0 if market == "UK" else 1000 if market == "US" else 10000
            core_candidates.append(
                {
                    "symbol": security.ticker,
                    "name": security.name,
                    "exchange": security.exchange,
                    "currency": security.currency,
                    "core_security_id": security.id,
                    "market": market,
                    "price_count": price_count,
                    "status": "ACTIVE" if price_count >= _MIN_EXPANSION_COVERAGE_BARS else "SEED",
                    "reason_code": "TRACKED_SECURITY" if price_count >= _MIN_EXPANSION_COVERAGE_BARS else "TRACKED_SECURITY_LOW_COVERAGE",
                    "priority_score": freshness_bonus + market_bias - price_count,
                }
            )

        core_candidates.sort(key=lambda row: (int(row["priority_score"]), str(row["symbol"])))
        for row in core_candidates:
            if len(selected_rows) >= dynamic_target_total:
                break
            add_candidate(
                symbol=str(row["symbol"]),
                name=str(row["name"]),
                exchange=row["exchange"],  # type: ignore[arg-type]
                currency=str(row["currency"]),
                core_security_id=str(row["core_security_id"]),
                reason_code=str(row["reason_code"]),
                status=str(row["status"]),
                priority_score=int(row["priority_score"]),
            )

        uk_needed = max(0, dynamic_uk_target - sum(1 for row in selected_rows if row["market"] == "UK"))
        for row in catalog:
            if len(selected_rows) >= dynamic_target_total:
                break
            if _market_for(row.exchange, row.currency) != "UK":
                continue
            if uk_needed <= 0 and len(selected_rows) >= min(dynamic_target_total, dynamic_uk_target):
                break
            add_candidate(
                symbol=row.symbol,
                name=row.name,
                exchange=row.exchange,
                currency=row.currency,
                core_security_id=None,
                reason_code="UK_CATALOG_SEED",
                status="SEED",
                priority_score=900000,
            )
            uk_needed = max(0, uk_needed - 1)

        for row in catalog:
            if len(selected_rows) >= dynamic_target_total:
                break
            if _market_for(row.exchange, row.currency) != "US":
                continue
            add_candidate(
                symbol=row.symbol,
                name=row.name,
                exchange=row.exchange,
                currency=row.currency,
                core_security_id=None,
                reason_code="US_CATALOG_SEED",
                status="SEED",
                priority_score=910000,
            )

        instruments_added = 0
        memberships_added = 0
        memberships_removed = 0
        with BetaContext.write_session() as beta_sess:
            selected_instrument_ids: set[str] = set()
            for index, row in enumerate(selected_rows, start=1):
                instrument = beta_sess.scalar(
                    select(BetaInstrument).where(
                        BetaInstrument.symbol == row["symbol"],
                        BetaInstrument.exchange == row["exchange"],
                    )
                )
                if instrument is None:
                    instrument = BetaInstrument(
                        symbol=str(row["symbol"]),
                        name=str(row["name"]),
                        exchange=row["exchange"],  # type: ignore[arg-type]
                        currency=str(row["currency"]),
                        market=str(row["market"]),
                        benchmark_key=_benchmark_key_for_market(str(row["market"])),
                        sector_key=_sector_for_name(str(row["name"]))[0],
                        sector_label=_sector_for_name(str(row["name"]))[1],
                        metadata_json=json.dumps(
                            {
                                "reason_code": row["reason_code"],
                                "priority_score": row["priority_score"],
                            },
                            sort_keys=True,
                        ),
                        core_security_id=row["core_security_id"],  # type: ignore[arg-type]
                        is_active=True,
                    )
                    beta_sess.add(instrument)
                    beta_sess.flush()
                    instruments_added += 1
                else:
                    instrument.name = str(row["name"])
                    instrument.currency = str(row["currency"])
                    instrument.market = str(row["market"])
                    instrument.benchmark_key = _benchmark_key_for_market(str(row["market"]))
                    sector_key, sector_label = _sector_for_name(str(row["name"]))
                    instrument.sector_key = sector_key
                    instrument.sector_label = sector_label
                    instrument.metadata_json = json.dumps(
                        {
                            "reason_code": row["reason_code"],
                            "priority_score": row["priority_score"],
                        },
                        sort_keys=True,
                    )
                    if row["core_security_id"] is not None:
                        instrument.core_security_id = str(row["core_security_id"])
                selected_instrument_ids.add(instrument.id)

                active_membership = beta_sess.scalar(
                    select(BetaUniverseMembership).where(
                        BetaUniverseMembership.instrument_id == instrument.id,
                        BetaUniverseMembership.effective_to.is_(None),
                        BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
                    )
                )
                if active_membership is None:
                    beta_sess.add(
                        BetaUniverseMembership(
                            instrument_id=instrument.id,
                            status=str(row["status"]),
                            priority_rank=index,
                            reason_code=str(row["reason_code"]),
                            reason_text="Initial app-selected beta seed universe.",
                        )
                    )
                    memberships_added += 1
                else:
                    active_membership.priority_rank = index
                    active_membership.status = str(row["status"])
                    active_membership.reason_code = str(row["reason_code"])
                    active_membership.reason_text = "Automatically selected by beta universe sync."

            active_memberships = list(
                beta_sess.scalars(
                    select(BetaUniverseMembership).where(
                        BetaUniverseMembership.effective_to.is_(None),
                        BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
                    )
                ).all()
            )
            stale_rows = [
                row for row in active_memberships if row.instrument_id not in selected_instrument_ids
            ]
            for row in stale_rows:
                row.status = "REMOVED"
                row.effective_to = _utcnow()
                row.reason_code = "AUTO_REMOVED_UNIVERSE_SYNC"
                row.reason_text = "Removed automatically because higher-quality candidates displaced it."
                memberships_removed += 1

        return {
            "instruments_added": instruments_added,
            "memberships_added": memberships_added,
            "memberships_removed": memberships_removed,
            "coverage_ratio": round(coverage_ratio * 100, 1),
            "selected_total": len(selected_rows),
            "target_total": dynamic_target_total,
        }
