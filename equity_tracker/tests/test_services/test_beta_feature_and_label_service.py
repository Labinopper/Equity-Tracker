from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaBenchmarkBar,
    BetaDailyBar,
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaInstrument,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaNewsArticle,
    BetaNewsArticleLink,
)
from src.beta.services.feature_service import BetaFeatureService
from src.beta.services.label_service import BetaLabelService


@pytest.fixture()
def beta_context():
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


def _seed_instrument_with_bars(
    *,
    sess,
    symbol: str,
    closes: list[float],
    start_date: date,
) -> str:
    instrument = BetaInstrument(
        symbol=symbol,
        name=symbol,
        market="US",
        exchange="NASDAQ",
        currency="USD",
        sector_key="TECH",
        sector_label="Technology",
        benchmark_key="SPY",
        is_active=True,
    )
    sess.add(instrument)
    sess.flush()
    for offset, close in enumerate(closes):
        sess.add(
            BetaDailyBar(
                instrument_id=instrument.id,
                bar_date=start_date + timedelta(days=offset),
                close_price_gbp=str(close),
                close_price_native=str(close),
                currency="USD",
                source="test",
            )
        )
    return instrument.id


def test_generate_daily_labels_writes_multi_horizon_metrics(beta_context):
    start_date = date(2026, 1, 1)
    dates = [start_date + timedelta(days=offset) for offset in range(12)]

    with BetaContext.write_session() as sess:
        instrument_id = _seed_instrument_with_bars(
            sess=sess,
            symbol="AAA",
            closes=[100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
            start_date=start_date,
        )
        _seed_instrument_with_bars(
            sess=sess,
            symbol="BBB",
            closes=[100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            start_date=start_date,
        )
        for offset, bar_date in enumerate(dates):
            benchmark_close = 100 + (offset * 0.5)
            sess.add(
                BetaBenchmarkBar(
                    benchmark_key="SPY",
                    market="US",
                    symbol="SPY",
                    name="SPY",
                    bar_date=bar_date,
                    close_price_gbp=str(benchmark_close),
                    close_price_native=str(benchmark_close),
                    currency="USD",
                    source="test",
                )
            )

    result = BetaLabelService.generate_daily_labels()

    with BetaContext.read_session() as sess:
        label_defs = {
            row.label_name: row
            for row in sess.scalars(select(BetaLabelDefinition)).all()
        }
        label_values = {
            row.label_definition_id: row
            for row in sess.scalars(
                select(BetaLabelValue).where(
                    BetaLabelValue.instrument_id == instrument_id,
                    BetaLabelValue.decision_date == start_date,
                )
            ).all()
        }

    assert result["labels_written"] > 0
    assert "fwd_3d_excess_return_pct" in label_defs
    assert "fwd_10d_excess_return_pct" in label_defs
    assert label_defs["fwd_5d_excess_return_pct"].is_canonical is True

    label_3d = label_values[label_defs["fwd_3d_return_pct"].id]
    label_10d = label_values[label_defs["fwd_10d_return_pct"].id]
    assert label_3d.horizon_end_date == start_date + timedelta(days=3)
    assert label_10d.horizon_end_date == start_date + timedelta(days=10)
    assert label_3d.value_numeric == pytest.approx(3.0)
    assert label_10d.value_numeric == pytest.approx(10.0)


def test_generate_daily_features_writes_new_horizon_and_event_freshness_features(beta_context):
    start_date = date(2026, 2, 1)
    target_date = start_date + timedelta(days=24)

    with BetaContext.write_session() as sess:
        instrument_id = _seed_instrument_with_bars(
            sess=sess,
            symbol="AAA",
            closes=[100 + offset for offset in range(25)],
            start_date=start_date,
        )
        _seed_instrument_with_bars(
            sess=sess,
            symbol="BBB",
            closes=[100 + (offset * 0.5) for offset in range(25)],
            start_date=start_date,
        )
        for offset in range(25):
            benchmark_close = 100 + (offset * 0.4)
            sess.add(
                BetaBenchmarkBar(
                    benchmark_key="SPY",
                    market="US",
                    symbol="SPY",
                    name="SPY",
                    bar_date=start_date + timedelta(days=offset),
                    close_price_gbp=str(benchmark_close),
                    close_price_native=str(benchmark_close),
                    currency="USD",
                    source="test",
                )
            )

        news_article = BetaNewsArticle(
            article_guid="news-1",
            title="Fresh catalyst",
            published_at=datetime(2026, 2, 25, 9, 0, 0),
            sentiment_label="POSITIVE",
            sentiment_score=0.7,
        )
        filing_event = BetaFilingEvent(
            event_guid="filing-1",
            title="Company filing",
            published_at=datetime(2026, 2, 25, 7, 0, 0),
            event_category="OFFICIAL_RELEASE",
            sentiment_label="POSITIVE",
            sentiment_score=0.4,
        )
        sess.add_all([news_article, filing_event])
        sess.flush()
        sess.add(
            BetaNewsArticleLink(
                article_id=news_article.id,
                instrument_id=instrument_id,
                symbol="AAA",
            )
        )
        sess.add(
            BetaFilingEventLink(
                event_id=filing_event.id,
                instrument_id=instrument_id,
                symbol="AAA",
            )
        )

    result = BetaFeatureService.generate_daily_features()

    with BetaContext.read_session() as sess:
        feature_defs = {
            row.feature_name: row
            for row in sess.scalars(select(BetaFeatureDefinition)).all()
        }
        feature_values = {
            row.feature_definition_id: row.value_numeric
            for row in sess.scalars(
                select(BetaFeatureValue).where(
                    BetaFeatureValue.instrument_id == instrument_id,
                    BetaFeatureValue.feature_date == target_date,
                )
            ).all()
        }

    assert result["features_written"] > 0
    assert feature_values[feature_defs["ret_3d_pct"].id] is not None
    assert feature_values[feature_defs["market_ret_10d_pct"].id] is not None
    assert feature_values[feature_defs["benchmark_excess_10d_pct"].id] is not None
    assert feature_values[feature_defs["news_count_1d"].id] == pytest.approx(1.0)
    assert feature_values[feature_defs["days_since_latest_news"].id] == pytest.approx(0.0)
    assert feature_values[feature_defs["official_count_1d"].id] == pytest.approx(1.0)
    assert feature_values[feature_defs["days_since_latest_official_release"].id] == pytest.approx(0.0)
