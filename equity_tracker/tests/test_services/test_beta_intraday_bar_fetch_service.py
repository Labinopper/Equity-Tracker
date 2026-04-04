from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from src.app_context import AppContext
from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import BetaInstrument, BetaMinuteBar
from src.beta.services.intraday_bar_fetch_service import BetaIntradayBarFetchService
from src.beta.services.intraday_priority_service import IntradayPriorityItem
from src.beta.settings import BetaSettings
from src.beta import supervisor_process
from src.db.models import PriceHistory, Security


@pytest.fixture()
def beta_context():
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


def test_backfill_historical_bars_uses_bar_date_fx_rates(beta_context, app_context, monkeypatch):
    today = date.today()
    newer_date = today - timedelta(days=1)
    older_date = today - timedelta(days=6)

    with AppContext.write_session() as sess:
        security = Security(
            ticker="IBM",
            name="IBM",
            currency="USD",
            exchange="NASDAQ",
        )
        sess.add(security)
        sess.flush()
        sess.add_all(
            [
                PriceHistory(
                    security_id=security.id,
                    price_date=older_date,
                    close_price_original_ccy="100",
                    close_price_gbp="79",
                    currency="USD",
                    source="test",
                ),
                PriceHistory(
                    security_id=security.id,
                    price_date=newer_date,
                    close_price_original_ccy="100",
                    close_price_gbp="81",
                    currency="USD",
                    source="test",
                ),
            ]
        )

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        instrument_id = instrument.id

    calls = {"count": 0}

    def _fake_fetch(_params: dict[str, str]) -> list[dict]:
        calls["count"] += 1
        if calls["count"] == 1:
            return [
                {
                    "datetime": f"{newer_date.isoformat()} 15:59:00",
                    "open": "100",
                    "high": "100",
                    "low": "100",
                    "close": "100",
                    "volume": "1",
                },
                {
                    "datetime": f"{older_date.isoformat()} 15:59:00",
                    "open": "100",
                    "high": "100",
                    "low": "100",
                    "close": "100",
                    "volume": "1",
                },
            ]
        return []

    monkeypatch.setattr(
        "src.beta.services.intraday_bar_fetch_service._api_key",
        lambda: "test-key",
    )
    monkeypatch.setattr(BetaIntradayBarFetchService, "_fetch_bars_with_params", staticmethod(_fake_fetch))

    result = BetaIntradayBarFetchService.backfill_historical_bars(
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instrument_id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="HELD",
                cadence_minutes=1,
                priority_score=1.0,
                session_state="REGULAR_OPEN",
            )
        ],
        target_days=30,
        credits_budget=3,
    )

    with BetaContext.read_session() as sess:
        rows = list(
            sess.scalars(
                select(BetaMinuteBar)
                .where(BetaMinuteBar.instrument_id == instrument_id)
                .order_by(BetaMinuteBar.minute_ts.asc())
            ).all()
        )

    assert result["bars_written"] == 2
    assert len(rows) == 2
    assert rows[0].close_price_gbp == "79.0000"
    assert rows[1].close_price_gbp == "81.0000"


def test_backfill_historical_bars_returns_error_details(beta_context, monkeypatch):
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        instrument_id = instrument.id

    monkeypatch.setattr(
        "src.beta.services.intraday_bar_fetch_service._api_key",
        lambda: "test-key",
    )
    monkeypatch.setattr(
        BetaIntradayBarFetchService,
        "_fetch_bars_with_params",
        staticmethod(lambda _params: (_ for _ in ()).throw(RuntimeError("boom"))),
    )

    result = BetaIntradayBarFetchService.backfill_historical_bars(
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instrument_id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="HELD",
                cadence_minutes=1,
                priority_score=1.0,
                session_state="REGULAR_OPEN",
            )
        ],
        target_days=30,
        credits_budget=1,
    )

    assert result["bars_written"] == 0
    assert result["credits_used"] == 0
    assert result["errors_count"] == 1
    assert "IBM:NASDAQ" in result["errors"][0]
    assert "boom" in result["errors"][0]


def test_run_supervisor_cycle_returns_next_bar_backfill_at(monkeypatch):
    settings = BetaSettings()
    now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
    future = now + timedelta(hours=1)

    monkeypatch.setattr(supervisor_process, "_maybe_pause_for_memory", lambda **_kwargs: False)
    monkeypatch.setattr(
        supervisor_process.BetaMarketSessionService,
        "live_market_priority_window",
        lambda _settings, now_utc=None: False,
    )
    monkeypatch.setattr(supervisor_process.BetaReplayService, "ensure_daily_dashboard_pack", lambda: None)
    monkeypatch.setattr(supervisor_process.BetaRuntimeService, "ensure_daily_snapshot", lambda _settings: None)
    monkeypatch.setattr(supervisor_process.BetaRuntimeService, "record_notification", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_process.BetaPipelineAssessmentService,
        "record_snapshot",
        lambda **_kwargs: None,
    )

    result = supervisor_process._run_supervisor_cycle(
        core_db_path=None,
        beta_db_path=Path("beta.db"),
        settings=settings,
        now=now,
        next_reference_sync_at=future,
        next_news_sync_at=future,
        next_filing_sync_at=future,
        next_observation_at=future,
        next_intraday_execution_at=future,
        next_hypothesis_research_at=future,
        next_core_scoring_at=future,
        next_scoring_at=future,
        next_eod_bar_fetch_at=future,
        next_statistics_refresh_at=future,
        next_bar_backfill_at=future,
        next_storage_cleanup_at=future,
    )

    assert result["next_bar_backfill_at"] == future


def test_run_supervisor_cycle_skips_long_horizon_jobs_in_intraday_only_mode(monkeypatch):
    settings = BetaSettings()
    settings.intraday_only_mode = True
    settings.learning_enabled = True
    settings.shadow_scoring_enabled = True
    settings.observation_enabled = True
    settings.intraday_execution_hypothesis_research_enabled = False
    settings.intraday_pattern_exploration_enabled = True

    now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(supervisor_process, "_maybe_pause_for_memory", lambda **_kwargs: False)
    monkeypatch.setattr(
        supervisor_process.BetaMarketSessionService,
        "live_market_priority_window",
        lambda _settings, now_utc=None: False,
    )
    monkeypatch.setattr(supervisor_process.BetaReplayService, "ensure_daily_dashboard_pack", lambda: None)
    monkeypatch.setattr(supervisor_process.BetaRuntimeService, "ensure_daily_snapshot", lambda _settings: None)
    monkeypatch.setattr(supervisor_process.BetaRuntimeService, "record_notification", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_process.BetaPipelineAssessmentService,
        "record_snapshot",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        supervisor_process.BetaIntradayPatternExplorationService,
        "run_exploration",
        lambda _settings: {
            "patterns_generated": 4,
            "patterns_screened_in": 1,
            "labeled_observations": 6,
        },
    )
    monkeypatch.setattr(
        supervisor_process.BetaPredictionAccuracyService,
        "compute_calibration_metrics",
        lambda lookback_days=30: {"overall": {}, "by_confidence_band": {}},
    )

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("long-horizon supervisor job should not run in intraday-only mode")

    monkeypatch.setattr(supervisor_process.BetaObservationService, "sync_daily_bars", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaCorpusService, "backfill_market_corpus", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaFeatureService, "generate_core_tracked_features", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaLabelService, "generate_core_tracked_labels", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaScoringService, "run_daily_shadow_cycle", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaTrainingService, "ensure_daily_training", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaReviewService, "ensure_daily_potential_gains_review", _unexpected)
    monkeypatch.setattr(supervisor_process.BetaHypothesisDiscoveryService, "run_discovery", _unexpected)

    result = supervisor_process._run_supervisor_cycle(
        core_db_path=None,
        beta_db_path=Path("beta.db"),
        settings=settings,
        now=now,
        next_reference_sync_at=now + timedelta(hours=1),
        next_news_sync_at=now + timedelta(hours=1),
        next_filing_sync_at=now + timedelta(hours=1),
        next_observation_at=now,
        next_intraday_execution_at=now + timedelta(hours=1),
        next_hypothesis_research_at=now,
        next_core_scoring_at=now,
        next_scoring_at=now,
        next_eod_bar_fetch_at=now + timedelta(hours=1),
        next_statistics_refresh_at=now + timedelta(hours=1),
        next_bar_backfill_at=now + timedelta(hours=1),
        next_storage_cleanup_at=now + timedelta(hours=1),
    )

    assert result["next_observation_at"] > now
    assert result["next_hypothesis_research_at"] > now
