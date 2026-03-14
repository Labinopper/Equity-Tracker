"""Reusable feature generation over beta daily bars."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from statistics import pstdev

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..context import BetaContext
from ..db.models import (
    BetaBenchmarkBar,
    BetaDailyBar,
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaInstrument,
    BetaIntradaySnapshot,
    BetaNewsArticle,
    BetaNewsArticleLink,
)

_FEATURES = (
    (
        "ret_1d_pct",
        "v1",
        "price_momentum",
        "One-day close-to-close percent return in GBP terms.",
    ),
    (
        "ret_5d_pct",
        "v1",
        "price_momentum",
        "Five-day close-to-close percent return in GBP terms.",
    ),
    (
        "ret_10d_pct",
        "v1",
        "price_momentum",
        "Ten-day close-to-close percent return in GBP terms.",
    ),
    (
        "ret_20d_pct",
        "v1",
        "price_momentum",
        "Twenty-day close-to-close percent return in GBP terms.",
    ),
    (
        "realized_vol_5d_pct",
        "v1",
        "volatility",
        "Population standard deviation of daily returns over the last five closes.",
    ),
    (
        "realized_vol_20d_pct",
        "v1",
        "volatility",
        "Population standard deviation of daily returns over the last twenty closes.",
    ),
    (
        "distance_from_5d_mean_pct",
        "v1",
        "mean_reversion",
        "Current close distance from the trailing five-day average close.",
    ),
    (
        "distance_from_20d_mean_pct",
        "v1",
        "mean_reversion",
        "Current close distance from the trailing twenty-day average close.",
    ),
    (
        "drawdown_from_20d_high_pct",
        "v1",
        "mean_reversion",
        "Current close distance below the trailing twenty-day high.",
    ),
    (
        "rebound_from_20d_low_pct",
        "v1",
        "mean_reversion",
        "Current close distance above the trailing twenty-day low.",
    ),
    (
        "market_ret_1d_pct",
        "v1",
        "benchmark_context",
        "Average same-market one-day percent return across the beta universe.",
    ),
    (
        "market_ret_5d_pct",
        "v1",
        "benchmark_context",
        "Average same-market five-day percent return across the beta universe.",
    ),
    (
        "market_excess_1d_pct",
        "v1",
        "benchmark_context",
        "Instrument one-day return minus same-market average one-day return.",
    ),
    (
        "market_excess_5d_pct",
        "v1",
        "benchmark_context",
        "Instrument five-day return minus same-market average five-day return.",
    ),
    (
        "benchmark_ret_1d_pct",
        "v1",
        "benchmark_context",
        "One-day percent return of the mapped market benchmark series.",
    ),
    (
        "benchmark_ret_5d_pct",
        "v1",
        "benchmark_context",
        "Five-day percent return of the mapped market benchmark series.",
    ),
    (
        "benchmark_excess_1d_pct",
        "v1",
        "benchmark_context",
        "Instrument one-day return minus mapped benchmark one-day return.",
    ),
    (
        "benchmark_excess_5d_pct",
        "v1",
        "benchmark_context",
        "Instrument five-day return minus mapped benchmark five-day return.",
    ),
    (
        "sector_ret_1d_pct",
        "v1",
        "sector_context",
        "Average one-day return across the instrument's heuristic sector cohort.",
    ),
    (
        "sector_ret_5d_pct",
        "v1",
        "sector_context",
        "Average five-day return across the instrument's heuristic sector cohort.",
    ),
    (
        "sector_excess_1d_pct",
        "v1",
        "sector_context",
        "Instrument one-day return minus heuristic sector cohort one-day return.",
    ),
    (
        "sector_excess_5d_pct",
        "v1",
        "sector_context",
        "Instrument five-day return minus heuristic sector cohort five-day return.",
    ),
    (
        "intraday_pct_change",
        "v1",
        "intraday_context",
        "Latest same-day intraday percent change where available, otherwise zero.",
    ),
    (
        "news_sentiment_3d",
        "v1",
        "news_context",
        "Average linked news sentiment score over the trailing three calendar days.",
    ),
    (
        "news_count_3d",
        "v1",
        "news_context",
        "Count of linked news articles over the trailing three calendar days.",
    ),
    (
        "news_sentiment_7d",
        "v1",
        "news_context",
        "Average linked news sentiment score over the trailing seven calendar days.",
    ),
    (
        "news_count_7d",
        "v1",
        "news_context",
        "Count of linked news articles over the trailing seven calendar days.",
    ),
    (
        "official_sentiment_7d",
        "v1",
        "official_context",
        "Average linked official-release sentiment score over the trailing seven calendar days.",
    ),
    (
        "official_count_7d",
        "v1",
        "official_context",
        "Count of linked official releases over the trailing seven calendar days.",
    ),
    (
        "official_sentiment_14d",
        "v1",
        "official_context",
        "Average linked official-release sentiment score over the trailing fourteen calendar days.",
    ),
    (
        "official_count_14d",
        "v1",
        "official_context",
        "Count of linked official releases over the trailing fourteen calendar days.",
    ),
)
_MIN_FEATURE_BACKLOG_BARS = 30


def _d(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class BetaFeatureService:
    """Build compact reusable feature rows from beta daily bars."""

    @staticmethod
    def ensure_feature_definitions(sess: Session) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for feature_name, version_code, family, definition in _FEATURES:
            existing = sess.scalar(
                select(BetaFeatureDefinition).where(
                    BetaFeatureDefinition.feature_name == feature_name,
                    BetaFeatureDefinition.version_code == version_code,
                )
            )
            if existing is None:
                existing = BetaFeatureDefinition(
                    feature_name=feature_name,
                    version_code=version_code,
                    feature_family=family,
                    timeframe="1D",
                    definition_text=definition,
                    is_active=True,
                )
                sess.add(existing)
                sess.flush()
            mapping[feature_name] = existing.id
        return mapping

    @staticmethod
    def _resolve_target_instruments(
        sess: Session,
        *,
        instrument_ids: list[str] | None = None,
        core_only: bool = False,
    ) -> list[BetaInstrument]:
        stmt = select(BetaInstrument)
        if core_only:
            stmt = stmt.where(BetaInstrument.core_security_id.is_not(None))
        rows = list(sess.scalars(stmt).all())
        if instrument_ids is None:
            return rows
        allowed = set(instrument_ids)
        return [row for row in rows if row.id in allowed]

    @staticmethod
    def _feature_backlog_instrument_ids(sess: Session, *, batch_size: int) -> list[str]:
        instruments = list(sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all())
        expected_feature_count_per_bar = len(_FEATURES)
        candidates: list[tuple[int, float, int, str]] = []
        for instrument in instruments:
            bar_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaDailyBar).where(BetaDailyBar.instrument_id == instrument.id)
                )
                or 0
            )
            if bar_count < _MIN_FEATURE_BACKLOG_BARS:
                continue
            feature_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaFeatureValue).where(BetaFeatureValue.instrument_id == instrument.id)
                )
                or 0
            )
            expected_feature_count = bar_count * expected_feature_count_per_bar
            if feature_count >= expected_feature_count:
                continue
            priority = 0 if instrument.core_security_id else 1
            coverage_ratio = (feature_count / expected_feature_count) if expected_feature_count else 0.0
            candidates.append((priority, coverage_ratio, -bar_count, instrument.id))
        candidates.sort()
        return [instrument_id for _priority, _coverage_ratio, _neg_bar_count, instrument_id in candidates[:batch_size]]

    @staticmethod
    def generate_daily_features(*, instrument_ids: list[str] | None = None, core_only: bool = False) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"features_written": 0}

        with BetaContext.write_session() as sess:
            feature_ids = BetaFeatureService.ensure_feature_definitions(sess)
            instruments = list(sess.scalars(select(BetaInstrument)).all())
            target_instruments = BetaFeatureService._resolve_target_instruments(
                sess,
                instrument_ids=instrument_ids,
                core_only=core_only,
            )
            bars_by_instrument: dict[str, list[BetaDailyBar]] = {}
            closes_by_instrument: dict[str, list[Decimal | None]] = {}
            target_ids = [row.id for row in target_instruments]
            market_ret_1d: dict[tuple[str, object], list[tuple[str, float]]] = defaultdict(list)
            market_ret_5d: dict[tuple[str, object], list[tuple[str, float]]] = defaultdict(list)
            sector_ret_1d: dict[tuple[str, str, object], list[tuple[str, float]]] = defaultdict(list)
            sector_ret_5d: dict[tuple[str, str, object], list[tuple[str, float]]] = defaultdict(list)
            benchmark_rows = list(
                sess.scalars(select(BetaBenchmarkBar).order_by(BetaBenchmarkBar.benchmark_key.asc(), BetaBenchmarkBar.bar_date.asc())).all()
            )
            benchmark_close_map: dict[tuple[str, object], Decimal] = {}
            benchmark_ret_1d: dict[tuple[str, object], float] = {}
            benchmark_ret_5d: dict[tuple[str, object], float] = {}
            benchmark_dates_by_key: dict[str, list[object]] = defaultdict(list)
            for row in benchmark_rows:
                close = _d(row.close_price_gbp)
                if close is None or close <= 0:
                    continue
                benchmark_close_map[(row.benchmark_key, row.bar_date)] = close
                benchmark_dates_by_key[row.benchmark_key].append(row.bar_date)
            for benchmark_key, dates in benchmark_dates_by_key.items():
                ordered_dates = sorted(set(dates))
                for idx, current_date in enumerate(ordered_dates):
                    close = benchmark_close_map.get((benchmark_key, current_date))
                    if close is None or close <= 0:
                        continue
                    if idx >= 1:
                        prev_close = benchmark_close_map.get((benchmark_key, ordered_dates[idx - 1]))
                        if prev_close is not None and prev_close > 0:
                            benchmark_ret_1d[(benchmark_key, current_date)] = float(
                                ((close / prev_close) - Decimal("1")) * Decimal("100")
                            )
                    if idx >= 5:
                        prev_close = benchmark_close_map.get((benchmark_key, ordered_dates[idx - 5]))
                        if prev_close is not None and prev_close > 0:
                            benchmark_ret_5d[(benchmark_key, current_date)] = float(
                                ((close / prev_close) - Decimal("1")) * Decimal("100")
                            )
            for instrument in instruments:
                bars = list(
                    sess.scalars(
                        select(BetaDailyBar)
                        .where(BetaDailyBar.instrument_id == instrument.id)
                        .order_by(BetaDailyBar.bar_date)
                    ).all()
                )
                closes = [_d(row.close_price_gbp) for row in bars]
                bars_by_instrument[instrument.id] = bars
                closes_by_instrument[instrument.id] = closes
                market = str(instrument.market or "OTHER")
                sector_key = str(instrument.sector_key or "GENERAL")
                for idx, bar in enumerate(bars):
                    close = closes[idx]
                    if close is None or close <= 0:
                        continue
                    if idx >= 1 and closes[idx - 1] and closes[idx - 1] > 0:
                        ret_1d = float(((close / closes[idx - 1]) - Decimal("1")) * Decimal("100"))
                        market_ret_1d[(market, bar.bar_date)].append((instrument.id, ret_1d))
                        sector_ret_1d[(market, sector_key, bar.bar_date)].append((instrument.id, ret_1d))
                    if idx >= 5 and closes[idx - 5] and closes[idx - 5] > 0:
                        ret_5d = float(((close / closes[idx - 5]) - Decimal("1")) * Decimal("100"))
                        market_ret_5d[(market, bar.bar_date)].append((instrument.id, ret_5d))
                        sector_ret_5d[(market, sector_key, bar.bar_date)].append((instrument.id, ret_5d))
            written = 0
            existing_keys = {
                (row.feature_definition_id, row.instrument_id, row.feature_date): row
                for row in sess.scalars(
                    select(BetaFeatureValue).where(BetaFeatureValue.instrument_id.in_(target_ids if target_ids else [""]))
                ).all()
            } if target_ids else {}
            for instrument in target_instruments:
                bars = bars_by_instrument.get(instrument.id, [])
                intraday_rows = list(
                    sess.scalars(
                        select(BetaIntradaySnapshot)
                        .where(BetaIntradaySnapshot.instrument_id == instrument.id)
                        .order_by(BetaIntradaySnapshot.price_date.asc(), BetaIntradaySnapshot.observed_at.asc())
                    ).all()
                )
                news_rows = list(
                    sess.execute(
                        select(BetaNewsArticle)
                        .join(BetaNewsArticleLink, BetaNewsArticleLink.article_id == BetaNewsArticle.id)
                        .where(BetaNewsArticleLink.instrument_id == instrument.id)
                        .order_by(BetaNewsArticle.published_at.asc(), BetaNewsArticle.created_at.asc())
                    ).scalars().all()
                )
                filing_rows = list(
                    sess.execute(
                        select(BetaFilingEvent)
                        .join(BetaFilingEventLink, BetaFilingEventLink.event_id == BetaFilingEvent.id)
                        .where(BetaFilingEventLink.instrument_id == instrument.id)
                        .order_by(BetaFilingEvent.published_at.asc(), BetaFilingEvent.created_at.asc())
                    ).scalars().all()
                )
                closes = closes_by_instrument.get(instrument.id, [])
                market = str(instrument.market or "OTHER")
                sector_key = str(instrument.sector_key or "GENERAL")
                benchmark_key = str(instrument.benchmark_key or "")
                for idx, bar in enumerate(bars):
                    close = closes[idx]
                    if close is None or close <= 0:
                        continue
                    values: dict[str, float] = {}
                    current_ret_1d = None
                    current_ret_5d = None
                    if idx >= 1 and closes[idx - 1] and closes[idx - 1] > 0:
                        current_ret_1d = float(((close / closes[idx - 1]) - Decimal("1")) * Decimal("100"))
                        values["ret_1d_pct"] = current_ret_1d
                    if idx >= 5 and closes[idx - 5] and closes[idx - 5] > 0:
                        current_ret_5d = float(((close / closes[idx - 5]) - Decimal("1")) * Decimal("100"))
                        values["ret_5d_pct"] = current_ret_5d
                    if idx >= 10 and closes[idx - 10] and closes[idx - 10] > 0:
                        values["ret_10d_pct"] = float(((close / closes[idx - 10]) - Decimal("1")) * Decimal("100"))
                    if idx >= 20 and closes[idx - 20] and closes[idx - 20] > 0:
                        values["ret_20d_pct"] = float(((close / closes[idx - 20]) - Decimal("1")) * Decimal("100"))
                    if idx >= 4:
                        trailing = [closes[i] for i in range(idx - 4, idx + 1) if closes[i] is not None and closes[i] > 0]
                        if len(trailing) == 5:
                            daily_returns = [
                                float(((trailing[i] / trailing[i - 1]) - Decimal("1")) * Decimal("100"))
                                for i in range(1, len(trailing))
                            ]
                            values["realized_vol_5d_pct"] = float(pstdev(daily_returns)) if len(daily_returns) >= 2 else 0.0
                            trailing_mean = sum(trailing) / Decimal(len(trailing))
                            if trailing_mean > 0:
                                values["distance_from_5d_mean_pct"] = float(((close / trailing_mean) - Decimal("1")) * Decimal("100"))
                    if idx >= 19:
                        trailing_20 = [closes[i] for i in range(idx - 19, idx + 1) if closes[i] is not None and closes[i] > 0]
                        if len(trailing_20) == 20:
                            daily_returns_20 = [
                                float(((trailing_20[i] / trailing_20[i - 1]) - Decimal("1")) * Decimal("100"))
                                for i in range(1, len(trailing_20))
                            ]
                            values["realized_vol_20d_pct"] = (
                                float(pstdev(daily_returns_20)) if len(daily_returns_20) >= 2 else 0.0
                            )
                            trailing_mean_20 = sum(trailing_20) / Decimal(len(trailing_20))
                            if trailing_mean_20 > 0:
                                values["distance_from_20d_mean_pct"] = float(
                                    ((close / trailing_mean_20) - Decimal("1")) * Decimal("100")
                                )
                            trailing_high_20 = max(trailing_20)
                            trailing_low_20 = min(trailing_20)
                            if trailing_high_20 > 0:
                                values["drawdown_from_20d_high_pct"] = float(
                                    ((close / trailing_high_20) - Decimal("1")) * Decimal("100")
                                )
                            if trailing_low_20 > 0:
                                values["rebound_from_20d_low_pct"] = float(
                                    ((close / trailing_low_20) - Decimal("1")) * Decimal("100")
                                )
                    market_1d_rows = market_ret_1d.get((market, bar.bar_date), [])
                    comparison_1d = [value for instrument_id, value in market_1d_rows if instrument_id != instrument.id]
                    if not comparison_1d:
                        comparison_1d = [value for _, value in market_1d_rows]
                    market_1d_value = (
                        float(sum(comparison_1d) / len(comparison_1d)) if comparison_1d else 0.0
                    )
                    values["market_ret_1d_pct"] = market_1d_value
                    if current_ret_1d is not None:
                        values["market_excess_1d_pct"] = current_ret_1d - market_1d_value

                    market_5d_rows = market_ret_5d.get((market, bar.bar_date), [])
                    comparison_5d = [value for instrument_id, value in market_5d_rows if instrument_id != instrument.id]
                    if not comparison_5d:
                        comparison_5d = [value for _, value in market_5d_rows]
                    market_5d_value = (
                        float(sum(comparison_5d) / len(comparison_5d)) if comparison_5d else 0.0
                    )
                    values["market_ret_5d_pct"] = market_5d_value
                    if current_ret_5d is not None:
                        values["market_excess_5d_pct"] = current_ret_5d - market_5d_value
                    benchmark_1d_value = benchmark_ret_1d.get((benchmark_key, bar.bar_date), market_1d_value)
                    benchmark_5d_value = benchmark_ret_5d.get((benchmark_key, bar.bar_date), market_5d_value)
                    values["benchmark_ret_1d_pct"] = benchmark_1d_value
                    values["benchmark_ret_5d_pct"] = benchmark_5d_value
                    if current_ret_1d is not None:
                        values["benchmark_excess_1d_pct"] = current_ret_1d - benchmark_1d_value
                    if current_ret_5d is not None:
                        values["benchmark_excess_5d_pct"] = current_ret_5d - benchmark_5d_value

                    sector_1d_rows = sector_ret_1d.get((market, sector_key, bar.bar_date), [])
                    sector_comparison_1d = [value for instrument_id, value in sector_1d_rows if instrument_id != instrument.id]
                    if not sector_comparison_1d:
                        sector_comparison_1d = [value for _, value in sector_1d_rows]
                    sector_1d_value = (
                        float(sum(sector_comparison_1d) / len(sector_comparison_1d)) if sector_comparison_1d else market_1d_value
                    )
                    values["sector_ret_1d_pct"] = sector_1d_value
                    if current_ret_1d is not None:
                        values["sector_excess_1d_pct"] = current_ret_1d - sector_1d_value

                    sector_5d_rows = sector_ret_5d.get((market, sector_key, bar.bar_date), [])
                    sector_comparison_5d = [value for instrument_id, value in sector_5d_rows if instrument_id != instrument.id]
                    if not sector_comparison_5d:
                        sector_comparison_5d = [value for _, value in sector_5d_rows]
                    sector_5d_value = (
                        float(sum(sector_comparison_5d) / len(sector_comparison_5d)) if sector_comparison_5d else market_5d_value
                    )
                    values["sector_ret_5d_pct"] = sector_5d_value
                    if current_ret_5d is not None:
                        values["sector_excess_5d_pct"] = current_ret_5d - sector_5d_value
                    intraday_candidates = [row for row in intraday_rows if row.price_date == bar.bar_date]
                    if intraday_candidates:
                        latest_intraday = intraday_candidates[-1]
                        try:
                            values["intraday_pct_change"] = float(latest_intraday.percent_change or 0.0)
                        except (TypeError, ValueError):
                            values["intraday_pct_change"] = 0.0
                    else:
                        values["intraday_pct_change"] = 0.0

                    news_window_start = bar.bar_date.toordinal() - 2
                    recent_news = [
                        row for row in news_rows
                        if (row.published_at or row.created_at).date().toordinal() >= news_window_start
                        and (row.published_at or row.created_at).date() <= bar.bar_date
                    ]
                    values["news_count_3d"] = float(len(recent_news))
                    values["news_sentiment_3d"] = (
                        float(sum(float(row.sentiment_score or 0.0) for row in recent_news) / len(recent_news))
                        if recent_news
                        else 0.0
                    )
                    news_window_start_7d = bar.bar_date.toordinal() - 6
                    recent_news_7d = [
                        row for row in news_rows
                        if (row.published_at or row.created_at).date().toordinal() >= news_window_start_7d
                        and (row.published_at or row.created_at).date() <= bar.bar_date
                    ]
                    values["news_count_7d"] = float(len(recent_news_7d))
                    values["news_sentiment_7d"] = (
                        float(sum(float(row.sentiment_score or 0.0) for row in recent_news_7d) / len(recent_news_7d))
                        if recent_news_7d
                        else 0.0
                    )

                    filing_window_start = bar.bar_date.toordinal() - 6
                    recent_filings = [
                        row for row in filing_rows
                        if (row.published_at or row.created_at).date().toordinal() >= filing_window_start
                        and (row.published_at or row.created_at).date() <= bar.bar_date
                    ]
                    values["official_count_7d"] = float(len(recent_filings))
                    values["official_sentiment_7d"] = (
                        float(sum(float(row.sentiment_score or 0.0) for row in recent_filings) / len(recent_filings))
                        if recent_filings
                        else 0.0
                    )
                    filing_window_start_14d = bar.bar_date.toordinal() - 13
                    recent_filings_14d = [
                        row for row in filing_rows
                        if (row.published_at or row.created_at).date().toordinal() >= filing_window_start_14d
                        and (row.published_at or row.created_at).date() <= bar.bar_date
                    ]
                    values["official_count_14d"] = float(len(recent_filings_14d))
                    values["official_sentiment_14d"] = (
                        float(sum(float(row.sentiment_score or 0.0) for row in recent_filings_14d) / len(recent_filings_14d))
                        if recent_filings_14d
                        else 0.0
                    )

                    for feature_name, numeric_value in values.items():
                        key = (feature_ids[feature_name], instrument.id, bar.bar_date)
                        existing = existing_keys.get(key)
                        if existing is None:
                            existing = BetaFeatureValue(
                                feature_definition_id=feature_ids[feature_name],
                                instrument_id=instrument.id,
                                feature_date=bar.bar_date,
                                value_numeric=numeric_value,
                            )
                            sess.add(existing)
                            existing_keys[key] = existing
                            written += 1
                        else:
                            existing.value_numeric = numeric_value
            return {
                "features_written": written,
                "target_instruments": len(target_instruments),
                "scope": "CORE_ONLY" if core_only else ("SELECTED" if instrument_ids is not None else "FULL"),
            }

    @staticmethod
    def generate_core_tracked_features() -> dict[str, int]:
        return BetaFeatureService.generate_daily_features(core_only=True)

    @staticmethod
    def generate_feature_backlog(*, batch_size: int = 3) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"features_written": 0, "selected_instruments": 0}
        with BetaContext.write_session() as sess:
            target_ids = BetaFeatureService._feature_backlog_instrument_ids(sess, batch_size=batch_size)
        result = BetaFeatureService.generate_daily_features(instrument_ids=target_ids)
        result["selected_instruments"] = len(target_ids)
        return result
