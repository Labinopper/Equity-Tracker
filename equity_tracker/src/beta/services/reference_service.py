"""Reference-domain sync from the core app into the beta database."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from ...db.models import PriceHistory, Security, SecurityCatalog
from ..context import BetaContext
from ..core_access import core_read_session
from ..db.models import (
    BetaDailyBar,
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaFeatureValue,
    BetaInstrument,
    BetaLabelValue,
    BetaNewsArticle,
    BetaNewsArticleLink,
    BetaRecommendationDecision,
    BetaResearchRanking,
    BetaSignalObservation,
    BetaUniverseMembership,
)

_UK_EXCHANGES = {"LSE", "XLON", "LON"}
_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "IEX", "XNYS", "XNAS"}
_INITIAL_TARGET = 50
_INITIAL_UK_TARGET = 35
_EXPANSION_STEP = 25
_MAX_AUTO_TARGET = 2000
_MIN_EXPANSION_COVERAGE_BARS = 30
_MIN_PROMOTION_BARS = 252
_MIN_EXPANSION_COVERAGE_RATIO = 0.30
_RECENCY_DAYS = 21
_MAX_ACTIVE_RESEARCH = 20
_NO_HISTORY_REASON_CODE = "NO_PROVIDER_HISTORY"
_UNSUPPORTED_SYMBOL_REASON_CODE = "PROVIDER_UNSUPPORTED_SYMBOL"
_RECENT_NEWS_DAYS = 14
_RECENT_FILINGS_DAYS = 30
_RECENT_SIGNAL_DAYS = 14
_RANKING_PERSIST_LIMIT = 300
_QUALITY_NAME_PENALTIES = (
    ("bond", 5000),
    ("note", 4500),
    ("notes", 4500),
    ("mtn", 4500),
    ("warrant", 5000),
    ("rights", 4000),
    ("preference", 3500),
    ("preferred", 3500),
    ("pref", 3000),
    ("fund", 2500),
    ("trust", 2500),
    ("etf", 2500),
    ("income", 2000),
    ("debenture", 4000),
    ("loan", 3000),
    ("unit", 1800),
    ("units", 1800),
)
_QUALITY_NAME_BONUSES = (
    ("plc", 160),
    ("inc", 140),
    ("corp", 120),
    ("corporation", 120),
    ("group", 90),
    ("holdings", 80),
    ("ltd", 80),
    ("limited", 80),
)
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


def _instrument_key(symbol: str | None, exchange: str | None) -> tuple[str, str]:
    return (str(symbol or "").strip().upper(), str(exchange or "").strip().upper())


def _is_supported_research_symbol(symbol: str | None) -> bool:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return False
    if normalized.startswith("!"):
        return False
    if "/" in normalized or "\\" in normalized:
        return False
    if " " in normalized:
        return False
    return True


def _catalog_symbol_quality_penalty(symbol: str | None) -> int:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return 10000
    penalty = 0
    if normalized.startswith("0"):
        penalty += 700
    if any(char.isdigit() for char in normalized):
        penalty += 500
    if not normalized.isalpha():
        penalty += 450
    if len(normalized) == 1:
        penalty += 120
    elif len(normalized) > 5:
        penalty += 100 + ((len(normalized) - 5) * 60)
    return penalty


def _catalog_name_quality_penalty(name: str | None) -> int:
    lowered = str(name or "").lower()
    penalty = 0
    for token, value in _QUALITY_NAME_PENALTIES:
        if token in lowered:
            penalty += value
    if "%" in lowered:
        penalty += 5000
    digit_count = len([char for char in lowered if char.isdigit()])
    if digit_count >= 4:
        penalty += 1800
    for token, value in _QUALITY_NAME_BONUSES:
        if token in lowered:
            penalty -= value
    return penalty


def _catalog_priority_score(row: SecurityCatalog, *, market_bias: int) -> int:
    priority = market_bias
    priority += _catalog_symbol_quality_penalty(row.symbol)
    priority += _catalog_name_quality_penalty(row.name)
    if not row.figi:
        priority += 140
    if not row.isin:
        priority += 80
    return priority


def _quality_score_for_symbol(*, symbol: str | None, name: str | None, has_figi: bool, has_isin: bool) -> float:
    quality = 45.0
    quality -= (_catalog_symbol_quality_penalty(symbol) / 30.0)
    quality -= (_catalog_name_quality_penalty(name) / 180.0)
    if has_figi:
        quality += 2.5
    if has_isin:
        quality += 1.5
    return max(0.0, min(50.0, quality))


def _build_research_context() -> dict[str, object]:
    if not BetaContext.is_initialized():
        return {
            "instrument_by_key": {},
            "bar_counts": {},
            "feature_counts": {},
            "label_counts": {},
            "latest_bar_dates": {},
            "recent_news_counts": {},
            "recent_filing_counts": {},
            "recent_signal_counts": {},
            "recent_blocked_counts": {},
            "open_sector_counts": {},
        }

    now = _utcnow()
    news_cutoff = now - timedelta(days=_RECENT_NEWS_DAYS)
    filing_cutoff = now - timedelta(days=_RECENT_FILINGS_DAYS)
    signal_cutoff = now - timedelta(days=_RECENT_SIGNAL_DAYS)

    with BetaContext.read_session() as sess:
        instruments = list(sess.scalars(select(BetaInstrument)).all())
        instrument_by_id = {row.id: row for row in instruments}
        instrument_by_key = {_instrument_key(row.symbol, row.exchange): row for row in instruments}
        instrument_ids = [row.id for row in instruments]

        bar_counts = {}
        feature_counts = {}
        label_counts = {}
        latest_bar_dates = {}
        recent_news_counts = {}
        recent_filing_counts = {}
        recent_signal_counts = {}
        recent_blocked_counts = {}
        open_sector_counts = {}

        if instrument_ids:
            bar_counts = {
                row.instrument_id: int(row.bar_count or 0)
                for row in sess.execute(
                    select(BetaDailyBar.instrument_id, func.count().label("bar_count"))
                    .where(BetaDailyBar.instrument_id.in_(instrument_ids))
                    .group_by(BetaDailyBar.instrument_id)
                )
            }
            feature_counts = {
                row.instrument_id: int(row.feature_count or 0)
                for row in sess.execute(
                    select(BetaFeatureValue.instrument_id, func.count().label("feature_count"))
                    .where(BetaFeatureValue.instrument_id.in_(instrument_ids))
                    .group_by(BetaFeatureValue.instrument_id)
                )
            }
            label_counts = {
                row.instrument_id: int(row.label_count or 0)
                for row in sess.execute(
                    select(BetaLabelValue.instrument_id, func.count().label("label_count"))
                    .where(BetaLabelValue.instrument_id.in_(instrument_ids))
                    .group_by(BetaLabelValue.instrument_id)
                )
            }
            latest_bar_dates = {
                row.instrument_id: row.latest_bar_date
                for row in sess.execute(
                    select(BetaDailyBar.instrument_id, func.max(BetaDailyBar.bar_date).label("latest_bar_date"))
                    .where(BetaDailyBar.instrument_id.in_(instrument_ids))
                    .group_by(BetaDailyBar.instrument_id)
                )
            }
            recent_news_counts = {
                row.instrument_id: int(row.link_count or 0)
                for row in sess.execute(
                    select(BetaNewsArticleLink.instrument_id, func.count().label("link_count"))
                    .join(BetaNewsArticle, BetaNewsArticle.id == BetaNewsArticleLink.article_id)
                    .where(
                        BetaNewsArticleLink.instrument_id.in_(instrument_ids),
                        func.coalesce(BetaNewsArticle.published_at, BetaNewsArticle.created_at) >= news_cutoff,
                    )
                    .group_by(BetaNewsArticleLink.instrument_id)
                )
            }
            recent_filing_counts = {
                row.instrument_id: int(row.link_count or 0)
                for row in sess.execute(
                    select(BetaFilingEventLink.instrument_id, func.count().label("link_count"))
                    .join(BetaFilingEvent, BetaFilingEvent.id == BetaFilingEventLink.event_id)
                    .where(
                        BetaFilingEventLink.instrument_id.in_(instrument_ids),
                        func.coalesce(BetaFilingEvent.published_at, BetaFilingEvent.created_at) >= filing_cutoff,
                    )
                    .group_by(BetaFilingEventLink.instrument_id)
                )
            }
            recent_signal_counts = {
                row.instrument_id: int(row.observation_count or 0)
                for row in sess.execute(
                    select(BetaSignalObservation.instrument_id, func.count().label("observation_count"))
                    .where(
                        BetaSignalObservation.instrument_id.in_(instrument_ids),
                        BetaSignalObservation.observation_time >= signal_cutoff,
                    )
                    .group_by(BetaSignalObservation.instrument_id)
                )
            }
            recent_blocked_counts = {
                row.instrument_id: int(row.blocked_count or 0)
                for row in sess.execute(
                    select(BetaRecommendationDecision.instrument_id, func.count().label("blocked_count"))
                    .where(
                        BetaRecommendationDecision.instrument_id.in_(instrument_ids),
                        BetaRecommendationDecision.created_at >= signal_cutoff,
                        BetaRecommendationDecision.decision_status == "BLOCKED",
                    )
                    .group_by(BetaRecommendationDecision.instrument_id)
                )
            }

        open_sector_counts = {}
        for row in sess.execute(
            select(BetaInstrument.sector_key, func.count().label("sector_count"))
            .join(BetaUniverseMembership, BetaUniverseMembership.instrument_id == BetaInstrument.id)
            .where(
                BetaUniverseMembership.effective_to.is_(None),
                BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
            )
            .group_by(BetaInstrument.sector_key)
        ):
            open_sector_counts[str(row.sector_key or "GENERAL")] = int(row.sector_count or 0)

    return {
        "instrument_by_key": instrument_by_key,
        "instrument_by_id": instrument_by_id,
        "bar_counts": bar_counts,
        "feature_counts": feature_counts,
        "label_counts": label_counts,
        "latest_bar_dates": latest_bar_dates,
        "recent_news_counts": recent_news_counts,
        "recent_filing_counts": recent_filing_counts,
        "recent_signal_counts": recent_signal_counts,
        "recent_blocked_counts": recent_blocked_counts,
        "open_sector_counts": open_sector_counts,
    }


def _score_research_candidate(candidate: dict[str, object], research_context: dict[str, object]) -> dict[str, object]:
    instrument_by_key = research_context.get("instrument_by_key", {})
    bar_counts = research_context.get("bar_counts", {})
    feature_counts = research_context.get("feature_counts", {})
    label_counts = research_context.get("label_counts", {})
    latest_bar_dates = research_context.get("latest_bar_dates", {})
    recent_news_counts = research_context.get("recent_news_counts", {})
    recent_filing_counts = research_context.get("recent_filing_counts", {})
    recent_signal_counts = research_context.get("recent_signal_counts", {})
    recent_blocked_counts = research_context.get("recent_blocked_counts", {})
    open_sector_counts = research_context.get("open_sector_counts", {})

    symbol = str(candidate["symbol"])
    exchange = candidate.get("exchange")
    instrument = instrument_by_key.get(_instrument_key(symbol, exchange)) if isinstance(instrument_by_key, dict) else None
    instrument_id = getattr(instrument, "id", None)
    sector_key, sector_label = _sector_for_name(str(candidate["name"]))
    if instrument is not None and getattr(instrument, "sector_key", None):
        sector_key = str(instrument.sector_key)
        sector_label = str(instrument.sector_label or sector_label)

    bar_count = int(bar_counts.get(instrument_id, 0)) if isinstance(bar_counts, dict) else 0
    feature_count = int(feature_counts.get(instrument_id, 0)) if isinstance(feature_counts, dict) else 0
    label_count = int(label_counts.get(instrument_id, 0)) if isinstance(label_counts, dict) else 0
    latest_bar_date = latest_bar_dates.get(instrument_id) if isinstance(latest_bar_dates, dict) else None
    has_recent_bars = BetaReferenceService._latest_is_recent(latest_bar_date)
    recent_news = int(recent_news_counts.get(instrument_id, 0)) if isinstance(recent_news_counts, dict) else 0
    recent_filings = int(recent_filing_counts.get(instrument_id, 0)) if isinstance(recent_filing_counts, dict) else 0
    recent_signals = int(recent_signal_counts.get(instrument_id, 0)) if isinstance(recent_signal_counts, dict) else 0
    blocked_decisions = int(recent_blocked_counts.get(instrument_id, 0)) if isinstance(recent_blocked_counts, dict) else 0
    sector_population = int(open_sector_counts.get(sector_key, 0)) if isinstance(open_sector_counts, dict) else 0

    core_price_count = int(candidate.get("price_count", 0) or 0)
    quality_score = _quality_score_for_symbol(
        symbol=symbol,
        name=str(candidate["name"]),
        has_figi=bool(candidate.get("figi")),
        has_isin=bool(candidate.get("isin")),
    )
    if candidate.get("core_security_id") is not None:
        quality_score = max(quality_score, 40.0)

    data_readiness_score = min(bar_count / 10.0, 28.0)
    data_readiness_score += min(feature_count / 80.0, 14.0)
    data_readiness_score += min(label_count / 80.0, 16.0)
    data_readiness_score += min(core_price_count / 18.0, 10.0)
    if has_recent_bars:
        data_readiness_score += 6.0
    catalyst_score = min(recent_news * 3.0, 15.0) + min(recent_filings * 4.0, 20.0)
    hypothesis_relevance_score = min(recent_signals * 2.0, 16.0) - min(blocked_decisions * 0.75, 6.0)
    diversification_score = max(0.0, 10.0 - min(float(sector_population), 10.0))
    if candidate.get("core_security_id") is not None:
        diversification_score += 4.0

    ranking_score = quality_score + data_readiness_score + catalyst_score + hypothesis_relevance_score + diversification_score
    if candidate.get("core_security_id") is not None:
        ranking_score += 120.0
    elif candidate.get("market") == "UK":
        ranking_score += 4.0
    elif candidate.get("market") == "US":
        ranking_score += 2.0
    if not has_recent_bars and instrument_id is not None:
        ranking_score -= 8.0

    scored = dict(candidate)
    scored.update(
        {
            "instrument_id": instrument_id,
            "sector_key": sector_key,
            "sector_label": sector_label,
            "bar_count": bar_count,
            "feature_count": feature_count,
            "label_count": label_count,
            "latest_bar_date": latest_bar_date.isoformat() if latest_bar_date is not None else None,
            "recent_news_count": recent_news,
            "recent_filing_count": recent_filings,
            "recent_signal_count": recent_signals,
            "recent_blocked_decisions": blocked_decisions,
            "quality_score": round(quality_score, 3),
            "data_readiness_score": round(data_readiness_score, 3),
            "catalyst_score": round(catalyst_score, 3),
            "hypothesis_relevance_score": round(hypothesis_relevance_score, 3),
            "diversification_score": round(diversification_score, 3),
            "ranking_score": round(ranking_score, 3),
            "has_recent_bars": has_recent_bars,
        }
    )
    return scored


class BetaReferenceService:
    """Sync holdings/catalog references into the beta DB and seed the starter universe."""

    @staticmethod
    def _latest_is_recent(latest_price_date) -> bool:
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

    @staticmethod
    def _load_metadata(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    @staticmethod
    def _apply_research_membership_states(*, max_active_research: int = _MAX_ACTIVE_RESEARCH) -> dict[str, int]:
        promoted = 0
        demoted = 0
        active_research = 0
        ready_research = 0
        with BetaContext.write_session() as sess:
            memberships = list(
                sess.scalars(
                    select(BetaUniverseMembership).where(
                        BetaUniverseMembership.effective_to.is_(None),
                        BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
                    )
                ).all()
            )
            if not memberships:
                return {
                    "promoted": 0,
                    "demoted": 0,
                    "active_research": 0,
                    "ready_research": 0,
                }
            instrument_ids = [row.instrument_id for row in memberships]
            instrument_by_id = {
                row.id: row
                for row in sess.scalars(select(BetaInstrument).where(BetaInstrument.id.in_(instrument_ids))).all()
            }
            bar_counts = {
                row.instrument_id: int(row.bar_count or 0)
                for row in sess.execute(
                    select(BetaDailyBar.instrument_id, func.count().label("bar_count"))
                    .where(BetaDailyBar.instrument_id.in_(instrument_ids))
                    .group_by(BetaDailyBar.instrument_id)
                )
            }
            latest_bar_dates = {
                row.instrument_id: row.latest_bar_date
                for row in sess.execute(
                    select(BetaDailyBar.instrument_id, func.max(BetaDailyBar.bar_date).label("latest_bar_date"))
                    .where(BetaDailyBar.instrument_id.in_(instrument_ids))
                    .group_by(BetaDailyBar.instrument_id)
                )
            }
            feature_counts = {
                row.instrument_id: int(row.feature_count or 0)
                for row in sess.execute(
                    select(BetaFeatureValue.instrument_id, func.count().label("feature_count"))
                    .where(BetaFeatureValue.instrument_id.in_(instrument_ids))
                    .group_by(BetaFeatureValue.instrument_id)
                )
            }
            label_counts = {
                row.instrument_id: int(row.label_count or 0)
                for row in sess.execute(
                    select(BetaLabelValue.instrument_id, func.count().label("label_count"))
                    .where(BetaLabelValue.instrument_id.in_(instrument_ids))
                    .group_by(BetaLabelValue.instrument_id)
                )
            }

            ready_seed_memberships: list[tuple[int, int, str, BetaUniverseMembership]] = []
            for membership in memberships:
                instrument = instrument_by_id.get(membership.instrument_id)
                if instrument is None:
                    continue
                if instrument.core_security_id is not None:
                    if membership.status != "ACTIVE":
                        membership.status = "ACTIVE"
                        membership.reason_code = "TRACKED_SECURITY"
                        membership.reason_text = "Tracked core security kept active in research universe."
                        promoted += 1
                    continue
                metadata = BetaReferenceService._load_metadata(instrument.metadata_json)
                has_ready_history = (
                    bar_counts.get(instrument.id, 0) >= _MIN_PROMOTION_BARS
                    and BetaReferenceService._latest_is_recent(latest_bar_dates.get(instrument.id))
                )
                has_learning_rows = (
                    feature_counts.get(instrument.id, 0) > 0
                    and label_counts.get(instrument.id, 0) > 0
                )
                if has_ready_history and has_learning_rows:
                    ready_research += 1
                    ready_seed_memberships.append(
                        (
                            int(membership.priority_rank or 999999),
                            -bar_counts.get(instrument.id, 0),
                            str(instrument.symbol),
                            membership,
                        )
                    )
                elif membership.status == "ACTIVE":
                    membership.status = "SEED"
                    membership.reason_code = "RESEARCH_PENDING_DATA"
                    membership.reason_text = "Demoted to seed because trainable coverage is no longer sufficient."
                    demoted += 1
                else:
                    membership.reason_code = (
                        metadata.get("last_history_error_code")
                        or "RESEARCH_PENDING_DATA"
                    )
                    membership.reason_text = "Awaiting sufficient history, features, and labels for activation."

            ready_seed_memberships.sort()
            for _rank, _neg_bars, _symbol, membership in ready_seed_memberships:
                if active_research >= max_active_research:
                    if membership.status == "ACTIVE":
                        membership.status = "SEED"
                        membership.reason_code = "RESEARCH_ACTIVE_CAP_REACHED"
                        membership.reason_text = "Left in seed pool because active research cap is full."
                        demoted += 1
                    continue
                if membership.status != "ACTIVE":
                    membership.status = "ACTIVE"
                    membership.reason_code = "RESEARCH_READY"
                    membership.reason_text = "Promoted automatically after history, feature, and label coverage became sufficient."
                    promoted += 1
                active_research += 1

        return {
            "promoted": promoted,
            "demoted": demoted,
            "active_research": active_research,
            "ready_research": ready_research,
        }

    @staticmethod
    def refresh_research_membership_states(*, refill_if_needed: bool = False) -> dict[str, int]:
        sync_result: dict[str, int] = {}
        if refill_if_needed:
            sync_result = BetaReferenceService.sync_seed_universe()
        state_result = BetaReferenceService._apply_research_membership_states()
        if sync_result:
            state_result.update(
                {
                    "refilled_instruments_added": int(sync_result.get("instruments_added", 0)),
                    "refilled_memberships_added": int(sync_result.get("memberships_added", 0)),
                    "refilled_memberships_removed": int(sync_result.get("memberships_removed", 0)),
                }
            )
        return state_result

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
        selected_rows: list[dict[str, object]] = []
        ranked_candidates: list[dict[str, object]] = []
        ranking_run_code = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        research_context = _build_research_context()

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

        eligible_core_rows = [
            security
            for security in holdings
            if price_coverage.get(security.id, {}).get("price_count", 0) >= _MIN_EXPANSION_COVERAGE_BARS
            and BetaReferenceService._latest_is_recent(price_coverage.get(security.id, {}).get("latest_price_date"))
        ]
        dynamic_target_total = max(target_total, active_count or 0)

        def add_candidate(candidate: dict[str, object]) -> None:
            key = _instrument_key(str(candidate["symbol"]), candidate.get("exchange"))
            if key in selected_symbols:
                return
            selected_symbols.add(key)
            candidate["selection_status"] = "SELECTED"
            selected_rows.append(candidate)

        core_candidates: list[dict[str, object]] = []
        for security in holdings:
            coverage = price_coverage.get(security.id, {})
            price_count = int(coverage.get("price_count", 0))
            latest_price_date = coverage.get("latest_price_date")
            market = _market_for(security.exchange, security.currency)
            core_candidates.append(
                _score_research_candidate(
                    {
                        "symbol": security.ticker.upper(),
                        "name": security.name,
                        "exchange": security.exchange,
                        "currency": security.currency.upper(),
                        "core_security_id": security.id,
                        "market": market,
                        "price_count": price_count,
                        "latest_price_date": latest_price_date.isoformat() if latest_price_date is not None else None,
                        "status": "ACTIVE" if price_count >= _MIN_EXPANSION_COVERAGE_BARS else "SEED",
                        "reason_code": "TRACKED_SECURITY" if price_count >= _MIN_EXPANSION_COVERAGE_BARS else "TRACKED_SECURITY_LOW_COVERAGE",
                        "figi": None,
                        "isin": None,
                    },
                    research_context,
                )
            )

        core_candidates.sort(
            key=lambda row: (-float(row["ranking_score"]), -int(row.get("price_count", 0) or 0), str(row["symbol"]))
        )
        catalog_candidate_capacity = len(catalog) + len(core_candidates)
        if active_count >= _INITIAL_TARGET and coverage_ratio >= _MIN_EXPANSION_COVERAGE_RATIO and catalog_candidate_capacity > active_count:
            dynamic_target_total = min(
                _MAX_AUTO_TARGET,
                min(active_count + _EXPANSION_STEP, catalog_candidate_capacity),
            )
        dynamic_uk_target = max(_INITIAL_UK_TARGET, round(dynamic_target_total * 0.7))
        for row in core_candidates:
            if len(selected_rows) >= dynamic_target_total:
                break
            add_candidate(dict(row))

        excluded_catalog_keys: set[tuple[str, str]] = set()
        with BetaContext.read_session() as beta_sess:
            for row in beta_sess.execute(
                select(BetaInstrument.symbol, BetaInstrument.exchange)
                .join(BetaUniverseMembership, BetaUniverseMembership.instrument_id == BetaInstrument.id)
                .where(
                    BetaUniverseMembership.status == "REMOVED",
                    BetaUniverseMembership.reason_code.in_(
                        (_NO_HISTORY_REASON_CODE, _UNSUPPORTED_SYMBOL_REASON_CODE)
                    ),
                )
            ):
                excluded_catalog_keys.add(_instrument_key(row.symbol, row.exchange))

        uk_catalog_candidates = sorted(
            [
                _score_research_candidate(
                    {
                        "symbol": row.symbol.upper(),
                        "name": row.name,
                        "exchange": row.exchange,
                        "currency": row.currency.upper(),
                        "core_security_id": None,
                        "market": "UK",
                        "status": "SEED",
                        "reason_code": "UK_CATALOG_SEED",
                        "figi": row.figi,
                        "isin": row.isin,
                    },
                    research_context,
                )
                for row in catalog
                if _market_for(row.exchange, row.currency) == "UK"
                and _is_supported_research_symbol(row.symbol)
                and _instrument_key(row.symbol, row.exchange) not in excluded_catalog_keys
            ],
            key=lambda row: (-float(row["ranking_score"]), str(row["name"]), str(row["symbol"])),
        )
        us_catalog_candidates = sorted(
            [
                _score_research_candidate(
                    {
                        "symbol": row.symbol.upper(),
                        "name": row.name,
                        "exchange": row.exchange,
                        "currency": row.currency.upper(),
                        "core_security_id": None,
                        "market": "US",
                        "status": "SEED",
                        "reason_code": "US_CATALOG_SEED",
                        "figi": row.figi,
                        "isin": row.isin,
                    },
                    research_context,
                )
                for row in catalog
                if _market_for(row.exchange, row.currency) == "US"
                and _is_supported_research_symbol(row.symbol)
                and _instrument_key(row.symbol, row.exchange) not in excluded_catalog_keys
            ],
            key=lambda row: (-float(row["ranking_score"]), str(row["name"]), str(row["symbol"])),
        )
        ranked_candidates = sorted(
            core_candidates + uk_catalog_candidates + us_catalog_candidates,
            key=lambda row: (-float(row["ranking_score"]), str(row["name"]), str(row["symbol"])),
        )

        uk_needed = max(0, dynamic_uk_target - sum(1 for row in selected_rows if row["market"] == "UK"))
        for row in uk_catalog_candidates:
            if len(selected_rows) >= dynamic_target_total:
                break
            if uk_needed <= 0 and len(selected_rows) >= min(dynamic_target_total, dynamic_uk_target):
                break
            add_candidate(dict(row))
            uk_needed = max(0, uk_needed - 1)

        for row in us_catalog_candidates:
            if len(selected_rows) >= dynamic_target_total:
                break
            add_candidate(dict(row))

        instruments_added = 0
        memberships_added = 0
        memberships_removed = 0
        unsupported_removed = 0
        with BetaContext.write_session() as beta_sess:
            unsupported_memberships = list(
                beta_sess.scalars(
                    select(BetaUniverseMembership)
                    .join(BetaInstrument, BetaInstrument.id == BetaUniverseMembership.instrument_id)
                    .where(
                        BetaUniverseMembership.effective_to.is_(None),
                        BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
                        BetaInstrument.core_security_id.is_(None),
                    )
                ).all()
            )
            for membership in unsupported_memberships:
                instrument = beta_sess.get(BetaInstrument, membership.instrument_id)
                if instrument is None or _is_supported_research_symbol(instrument.symbol):
                    continue
                membership.status = "REMOVED"
                membership.effective_to = _utcnow()
                membership.reason_code = _UNSUPPORTED_SYMBOL_REASON_CODE
                membership.reason_text = "Removed automatically because the symbol is not supported by the market-data provider."
                memberships_removed += 1
                unsupported_removed += 1

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
                        sector_key=str(row.get("sector_key") or _sector_for_name(str(row["name"]))[0]),
                        sector_label=str(row.get("sector_label") or _sector_for_name(str(row["name"]))[1]),
                        metadata_json=json.dumps(
                            {
                                "reason_code": row["reason_code"],
                                "ranking_score": row.get("ranking_score"),
                                "quality_score": row.get("quality_score"),
                                "data_readiness_score": row.get("data_readiness_score"),
                                "catalyst_score": row.get("catalyst_score"),
                                "hypothesis_relevance_score": row.get("hypothesis_relevance_score"),
                                "diversification_score": row.get("diversification_score"),
                                "ranking_run_code": ranking_run_code,
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
                    instrument.sector_key = str(row.get("sector_key") or _sector_for_name(str(row["name"]))[0])
                    instrument.sector_label = str(row.get("sector_label") or _sector_for_name(str(row["name"]))[1])
                    instrument.metadata_json = json.dumps(
                        {
                            "reason_code": row["reason_code"],
                            "ranking_score": row.get("ranking_score"),
                            "quality_score": row.get("quality_score"),
                            "data_readiness_score": row.get("data_readiness_score"),
                            "catalyst_score": row.get("catalyst_score"),
                            "hypothesis_relevance_score": row.get("hypothesis_relevance_score"),
                            "diversification_score": row.get("diversification_score"),
                            "ranking_run_code": ranking_run_code,
                        },
                        sort_keys=True,
                    )
                    if row["core_security_id"] is not None:
                        instrument.core_security_id = str(row["core_security_id"])
                row["instrument_id"] = instrument.id
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

            deferred_budget = max(50, min(_RANKING_PERSIST_LIMIT, dynamic_target_total + 50) - len(selected_rows))
            selected_keys = {_instrument_key(str(row["symbol"]), row.get("exchange")) for row in selected_rows}
            selected_rankings: list[tuple[int, dict[str, object]]] = []
            deferred_rankings: list[tuple[int, dict[str, object]]] = []
            seen_ranking_keys: set[tuple[str, str]] = set()
            for overall_rank, row in enumerate(ranked_candidates, start=1):
                key = _instrument_key(str(row["symbol"]), row.get("exchange"))
                if key in seen_ranking_keys:
                    continue
                seen_ranking_keys.add(key)
                if key in selected_keys:
                    selected_rankings.append((overall_rank, row))
                elif len(deferred_rankings) < deferred_budget:
                    deferred_rankings.append((overall_rank, row))

            ranking_window = sorted(selected_rankings + deferred_rankings, key=lambda item: item[0])
            ranking_symbols = {str(row["symbol"]) for _rank, row in ranking_window}
            instrument_rows = list(
                beta_sess.scalars(select(BetaInstrument).where(BetaInstrument.symbol.in_(ranking_symbols))).all()
            ) if ranking_symbols else []
            instrument_by_key = {_instrument_key(row.symbol, row.exchange): row for row in instrument_rows}
            for rank_position, row in ranking_window:
                key = _instrument_key(str(row["symbol"]), row.get("exchange"))
                instrument = instrument_by_key.get(key)
                beta_sess.add(
                    BetaResearchRanking(
                        ranking_run_code=ranking_run_code,
                        ranking_scope="UNIVERSE_SYNC",
                        instrument_id=instrument.id if instrument is not None else None,
                        symbol=str(row["symbol"]),
                        exchange=row.get("exchange"),  # type: ignore[arg-type]
                        market=row.get("market"),  # type: ignore[arg-type]
                        sector_key=row.get("sector_key"),  # type: ignore[arg-type]
                        selection_status="SELECTED" if key in selected_keys else "DEFERRED",
                        rank_position=rank_position,
                        ranking_score=float(row.get("ranking_score") or 0.0),
                        data_readiness_score=float(row.get("data_readiness_score") or 0.0),
                        catalyst_score=float(row.get("catalyst_score") or 0.0),
                        hypothesis_relevance_score=float(row.get("hypothesis_relevance_score") or 0.0),
                        quality_score=float(row.get("quality_score") or 0.0),
                        diversification_score=float(row.get("diversification_score") or 0.0),
                        notes_json=json.dumps(
                            {
                                "reason_code": row.get("reason_code"),
                                "status": row.get("status"),
                                "core_security_id": row.get("core_security_id"),
                                "bar_count": row.get("bar_count", 0),
                                "feature_count": row.get("feature_count", 0),
                                "label_count": row.get("label_count", 0),
                                "recent_news_count": row.get("recent_news_count", 0),
                                "recent_filing_count": row.get("recent_filing_count", 0),
                                "recent_signal_count": row.get("recent_signal_count", 0),
                                "recent_blocked_decisions": row.get("recent_blocked_decisions", 0),
                                "has_recent_bars": row.get("has_recent_bars", False),
                                "latest_bar_date": row.get("latest_bar_date"),
                            },
                            sort_keys=True,
                        ),
                    )
                )

        state_result = BetaReferenceService._apply_research_membership_states()
        return {
            "instruments_added": instruments_added,
            "memberships_added": memberships_added,
            "memberships_removed": memberships_removed,
            "coverage_ratio": round(coverage_ratio * 100, 1),
            "selected_total": len(selected_rows),
            "target_total": dynamic_target_total,
            "unsupported_removed": unsupported_removed,
            "ranking_run_code": ranking_run_code,
            "promoted": int(state_result.get("promoted", 0)),
            "demoted": int(state_result.get("demoted", 0)),
            "active_research": int(state_result.get("active_research", 0)),
        }
