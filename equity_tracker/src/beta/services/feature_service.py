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
    ("ret_1d_pct", "v1", "price_momentum", "One-day close-to-close percent return in GBP terms."),
    ("ret_3d_pct", "v1", "price_momentum", "Three-day close-to-close percent return in GBP terms."),
    ("ret_5d_pct", "v1", "price_momentum", "Five-day close-to-close percent return in GBP terms."),
    ("ret_10d_pct", "v1", "price_momentum", "Ten-day close-to-close percent return in GBP terms."),
    ("ret_20d_pct", "v1", "price_momentum", "Twenty-day close-to-close percent return in GBP terms."),
    ("realized_vol_5d_pct", "v1", "volatility", "Population standard deviation of daily returns over the last five closes."),
    ("realized_vol_10d_pct", "v1", "volatility", "Population standard deviation of daily returns over the last ten closes."),
    ("realized_vol_20d_pct", "v1", "volatility", "Population standard deviation of daily returns over the last twenty closes."),
    ("distance_from_5d_mean_pct", "v1", "mean_reversion", "Current close distance from the trailing five-day average close."),
    ("distance_from_10d_mean_pct", "v1", "mean_reversion", "Current close distance from the trailing ten-day average close."),
    ("distance_from_20d_mean_pct", "v1", "mean_reversion", "Current close distance from the trailing twenty-day average close."),
    ("drawdown_from_20d_high_pct", "v1", "mean_reversion", "Current close distance below the trailing twenty-day high."),
    ("rebound_from_20d_low_pct", "v1", "mean_reversion", "Current close distance above the trailing twenty-day low."),
    ("market_ret_1d_pct", "v1", "benchmark_context", "Average same-market one-day percent return across the beta universe."),
    ("market_ret_5d_pct", "v1", "benchmark_context", "Average same-market five-day percent return across the beta universe."),
    ("market_ret_10d_pct", "v1", "benchmark_context", "Average same-market ten-day percent return across the beta universe."),
    ("market_excess_1d_pct", "v1", "benchmark_context", "Instrument one-day return minus same-market average one-day return."),
    ("market_excess_5d_pct", "v1", "benchmark_context", "Instrument five-day return minus same-market average five-day return."),
    ("market_excess_10d_pct", "v1", "benchmark_context", "Instrument ten-day return minus same-market average ten-day return."),
    ("benchmark_ret_1d_pct", "v1", "benchmark_context", "One-day percent return of the mapped market benchmark series."),
    ("benchmark_ret_5d_pct", "v1", "benchmark_context", "Five-day percent return of the mapped market benchmark series."),
    ("benchmark_ret_10d_pct", "v1", "benchmark_context", "Ten-day percent return of the mapped market benchmark series."),
    ("benchmark_excess_1d_pct", "v1", "benchmark_context", "Instrument one-day return minus mapped benchmark one-day return."),
    ("benchmark_excess_5d_pct", "v1", "benchmark_context", "Instrument five-day return minus mapped benchmark five-day return."),
    ("benchmark_excess_10d_pct", "v1", "benchmark_context", "Instrument ten-day return minus mapped benchmark ten-day return."),
    ("sector_ret_1d_pct", "v1", "sector_context", "Average one-day return across the instrument's heuristic sector cohort."),
    ("sector_ret_5d_pct", "v1", "sector_context", "Average five-day return across the instrument's heuristic sector cohort."),
    ("sector_ret_10d_pct", "v1", "sector_context", "Average ten-day return across the instrument's heuristic sector cohort."),
    ("sector_excess_1d_pct", "v1", "sector_context", "Instrument one-day return minus heuristic sector cohort one-day return."),
    ("sector_excess_5d_pct", "v1", "sector_context", "Instrument five-day return minus heuristic sector cohort five-day return."),
    ("sector_excess_10d_pct", "v1", "sector_context", "Instrument ten-day return minus heuristic sector cohort ten-day return."),
    ("intraday_pct_change", "v1", "intraday_context", "Latest same-day intraday percent change where available, otherwise zero."),
    ("news_sentiment_1d", "v1", "news_context", "Average linked news sentiment score over the current calendar day."),
    ("news_count_1d", "v1", "news_context", "Count of linked news articles over the current calendar day."),
    ("news_sentiment_3d", "v1", "news_context", "Average linked news sentiment score over the trailing three calendar days."),
    ("news_count_3d", "v1", "news_context", "Count of linked news articles over the trailing three calendar days."),
    ("news_sentiment_7d", "v1", "news_context", "Average linked news sentiment score over the trailing seven calendar days."),
    ("news_count_7d", "v1", "news_context", "Count of linked news articles over the trailing seven calendar days."),
    ("days_since_latest_news", "v1", "news_context", "Days since the most recent linked news article up to the feature date."),
    ("official_sentiment_1d", "v1", "official_context", "Average linked official-release sentiment score over the current calendar day."),
    ("official_count_1d", "v1", "official_context", "Count of linked official releases over the current calendar day."),
    ("official_sentiment_7d", "v1", "official_context", "Average linked official-release sentiment score over the trailing seven calendar days."),
    ("official_count_7d", "v1", "official_context", "Count of linked official releases over the trailing seven calendar days."),
    ("official_sentiment_14d", "v1", "official_context", "Average linked official-release sentiment score over the trailing fourteen calendar days."),
    ("official_count_14d", "v1", "official_context", "Count of linked official releases over the trailing fourteen calendar days."),
    ("days_since_latest_official_release", "v1", "official_context", "Days since the most recent linked official release up to the feature date."),
)
_MIN_FEATURE_BACKLOG_BARS = 30
_RETURN_LOOKBACKS = (1, 3, 5, 10)


def _d(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _trailing_return(closes: list[Decimal | None], idx: int, lookback: int) -> float | None:
    if idx < lookback:
        return None
    current_close = closes[idx]
    previous_close = closes[idx - lookback]
    if current_close is None or previous_close is None or previous_close <= 0:
        return None
    return float(((current_close / previous_close) - Decimal("1")) * Decimal("100"))


def _trailing_closes(closes: list[Decimal | None], idx: int, window: int) -> list[Decimal] | None:
    if idx < window - 1:
        return None
    trailing = [closes[position] for position in range(idx - window + 1, idx + 1)]
    if any(value is None or value <= 0 for value in trailing):
        return None
    return [value for value in trailing if value is not None]


def _rolling_volatility(closes: list[Decimal | None], idx: int, window: int) -> float | None:
    trailing = _trailing_closes(closes, idx, window)
    if trailing is None or len(trailing) < 2:
        return None
    returns = [
        float(((trailing[position] / trailing[position - 1]) - Decimal("1")) * Decimal("100"))
        for position in range(1, len(trailing))
    ]
    if len(returns) < 2:
        return 0.0
    return float(pstdev(returns))


def _distance_from_mean(closes: list[Decimal | None], idx: int, window: int) -> float | None:
    trailing = _trailing_closes(closes, idx, window)
    if trailing is None:
        return None
    current_close = trailing[-1]
    trailing_mean = sum(trailing) / Decimal(len(trailing))
    if trailing_mean <= 0:
        return None
    return float(((current_close / trailing_mean) - Decimal("1")) * Decimal("100"))


def _average_window_sentiment(rows: list[tuple[object, float]], *, bar_date, lookback_days: int) -> tuple[float, float]:
    window_start = bar_date.toordinal() - (lookback_days - 1)
    matching = [
        sentiment
        for event_date, sentiment in rows
        if event_date.toordinal() >= window_start and event_date <= bar_date
    ]
    return float(len(matching)), (float(sum(matching) / len(matching)) if matching else 0.0)


def _days_since_latest(rows: list[tuple[object, float]], *, bar_date) -> float:
    prior_dates = [event_date for event_date, _sentiment in rows if event_date <= bar_date]
    if not prior_dates:
        return 999.0
    latest_date = max(prior_dates)
    return float((bar_date - latest_date).days)


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
            else:
                existing.feature_family = family
                existing.timeframe = "1D"
                existing.definition_text = definition
                existing.is_active = True
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
            target_ids = [row.id for row in target_instruments]

            bars_by_instrument: dict[str, list[BetaDailyBar]] = {}
            closes_by_instrument: dict[str, list[Decimal | None]] = {}
            market_returns: dict[int, dict[tuple[str, object], list[tuple[str, float]]]] = {
                1: defaultdict(list),
                5: defaultdict(list),
                10: defaultdict(list),
            }
            sector_returns: dict[int, dict[tuple[str, str, object], list[tuple[str, float]]]] = {
                1: defaultdict(list),
                5: defaultdict(list),
                10: defaultdict(list),
            }

            benchmark_rows = list(
                sess.scalars(select(BetaBenchmarkBar).order_by(BetaBenchmarkBar.benchmark_key.asc(), BetaBenchmarkBar.bar_date.asc())).all()
            )
            benchmark_close_map: dict[tuple[str, object], Decimal] = {}
            benchmark_returns: dict[int, dict[tuple[str, object], float]] = {
                1: {},
                5: {},
                10: {},
            }
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
                    for lookback in (1, 5, 10):
                        if idx < lookback:
                            continue
                        previous_close = benchmark_close_map.get((benchmark_key, ordered_dates[idx - lookback]))
                        if previous_close is None or previous_close <= 0:
                            continue
                        benchmark_returns[lookback][(benchmark_key, current_date)] = float(
                            ((close / previous_close) - Decimal("1")) * Decimal("100")
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
                    for lookback in (1, 5, 10):
                        value = _trailing_return(closes, idx, lookback)
                        if value is None:
                            continue
                        market_returns[lookback][(market, bar.bar_date)].append((instrument.id, value))
                        sector_returns[lookback][(market, sector_key, bar.bar_date)].append((instrument.id, value))

            written = 0
            existing_keys = (
                {
                    (row.feature_definition_id, row.instrument_id, row.feature_date): row
                    for row in sess.scalars(
                        select(BetaFeatureValue).where(BetaFeatureValue.instrument_id.in_(target_ids if target_ids else [""]))
                    ).all()
                }
                if target_ids
                else {}
            )
            for instrument in target_instruments:
                bars = bars_by_instrument.get(instrument.id, [])
                closes = closes_by_instrument.get(instrument.id, [])
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
                news_events = [
                    ((row.published_at or row.created_at).date(), float(row.sentiment_score or 0.0))
                    for row in news_rows
                ]
                filing_events = [
                    ((row.published_at or row.created_at).date(), float(row.sentiment_score or 0.0))
                    for row in filing_rows
                ]
                market = str(instrument.market or "OTHER")
                sector_key = str(instrument.sector_key or "GENERAL")
                benchmark_key = str(instrument.benchmark_key or "")
                for idx, bar in enumerate(bars):
                    close = closes[idx]
                    if close is None or close <= 0:
                        continue

                    values: dict[str, float] = {}
                    current_returns = {lookback: _trailing_return(closes, idx, lookback) for lookback in (1, 3, 5, 10, 20)}
                    for lookback, value in current_returns.items():
                        if value is not None:
                            values[f"ret_{lookback}d_pct"] = value

                    for window in (5, 10, 20):
                        vol_value = _rolling_volatility(closes, idx, window)
                        if vol_value is not None:
                            values[f"realized_vol_{window}d_pct"] = vol_value
                        mean_distance = _distance_from_mean(closes, idx, window)
                        if mean_distance is not None:
                            values[f"distance_from_{window}d_mean_pct"] = mean_distance

                    trailing_20 = _trailing_closes(closes, idx, 20)
                    if trailing_20 is not None:
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

                    for lookback in (1, 5, 10):
                        market_rows = market_returns[lookback].get((market, bar.bar_date), [])
                        comparison = [value for instrument_id, value in market_rows if instrument_id != instrument.id]
                        if not comparison:
                            comparison = [value for _instrument_id, value in market_rows]
                        market_value = float(sum(comparison) / len(comparison)) if comparison else 0.0
                        values[f"market_ret_{lookback}d_pct"] = market_value
                        current_return = current_returns.get(lookback)
                        if current_return is not None:
                            values[f"market_excess_{lookback}d_pct"] = current_return - market_value

                        benchmark_value = benchmark_returns[lookback].get((benchmark_key, bar.bar_date), market_value)
                        values[f"benchmark_ret_{lookback}d_pct"] = benchmark_value
                        if current_return is not None:
                            values[f"benchmark_excess_{lookback}d_pct"] = current_return - benchmark_value

                        sector_rows = sector_returns[lookback].get((market, sector_key, bar.bar_date), [])
                        sector_comparison = [value for instrument_id, value in sector_rows if instrument_id != instrument.id]
                        if not sector_comparison:
                            sector_comparison = [value for _instrument_id, value in sector_rows]
                        sector_value = (
                            float(sum(sector_comparison) / len(sector_comparison))
                            if sector_comparison
                            else market_value
                        )
                        values[f"sector_ret_{lookback}d_pct"] = sector_value
                        if current_return is not None:
                            values[f"sector_excess_{lookback}d_pct"] = current_return - sector_value

                    intraday_candidates = [row for row in intraday_rows if row.price_date == bar.bar_date]
                    if intraday_candidates:
                        latest_intraday = intraday_candidates[-1]
                        try:
                            values["intraday_pct_change"] = float(latest_intraday.percent_change or 0.0)
                        except (TypeError, ValueError):
                            values["intraday_pct_change"] = 0.0
                    else:
                        values["intraday_pct_change"] = 0.0

                    for lookback in (1, 3, 7):
                        count, sentiment = _average_window_sentiment(news_events, bar_date=bar.bar_date, lookback_days=lookback)
                        values[f"news_count_{lookback}d"] = count
                        values[f"news_sentiment_{lookback}d"] = sentiment
                    values["days_since_latest_news"] = _days_since_latest(news_events, bar_date=bar.bar_date)

                    for lookback in (1, 7, 14):
                        count, sentiment = _average_window_sentiment(filing_events, bar_date=bar.bar_date, lookback_days=lookback)
                        values[f"official_count_{lookback}d"] = count
                        values[f"official_sentiment_{lookback}d"] = sentiment
                    values["days_since_latest_official_release"] = _days_since_latest(filing_events, bar_date=bar.bar_date)

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
