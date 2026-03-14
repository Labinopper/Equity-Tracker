from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

import src.api.app as app_module
from src.api import _state
from src.api.auth import SESSION_COOKIE_NAME, make_session_token
from src.app_context import AppContext
from src.beta.context import BetaContext
from src.beta.db.bootstrap import beta_schema_requires_reset
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaAiReviewFinding,
    BetaAiReviewRun,
    BetaBenchmarkBar,
    BetaCashLedgerEntry,
    BetaConfidenceBucketSummary,
    BetaDailyBar,
    BetaDemoPosition,
    BetaDatasetRow,
    BetaDatasetVersion,
    BetaEvaluationRun,
    BetaEvaluationSummary,
    BetaExperimentRun,
    BetaFeatureValue,
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaFilingSource,
    BetaHypothesis,
    BetaHypothesisEvent,
    BetaInstrument,
    BetaIntradaySnapshot,
    BetaLedgerState,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaModelVersion,
    BetaNewsArticle,
    BetaNewsArticleLink,
    BetaNewsSource,
    BetaRiskControlState,
    BetaSchemaMeta,
    BetaScoreTape,
    BetaSignalCandidate,
    BetaStrategyVersion,
    BetaUniverseMembership,
    BetaValidationRun,
)
from src.beta.paths import resolve_beta_artifacts_dir, resolve_beta_settings_path
from src.beta.runtime_manager import initialize_beta_runtime, shutdown_beta_runtime
from src.beta.services.corpus_service import BetaCorpusService, _HistoryPoint
from src.beta.services.filing_service import BetaFilingService
from src.beta.services.news_service import BetaNewsService
from src.beta.settings import BetaSettings
from src.beta.state import get_beta_db_path
from src.db.engine import DatabaseEngine
from src.db.models import Base, PriceHistory, PriceTickerSnapshot, Security, SecurityCatalog


def teardown_function() -> None:
    shutdown_beta_runtime()


def _write_beta_settings(beta_db_path: Path, **overrides) -> None:
    settings = BetaSettings.defaults_for(beta_db_path)
    for key, value in overrides.items():
        setattr(settings, key, value)
    settings.save()


def _create_legacy_beta_db(beta_db_path: Path) -> None:
    beta_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(beta_db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE beta_schema_meta (
                id INTEGER PRIMARY KEY,
                schema_version VARCHAR(40) NOT NULL
            );
            INSERT INTO beta_schema_meta (id, schema_version)
            VALUES (1, 'v2');

            CREATE TABLE beta_instruments (
                id VARCHAR(36) PRIMARY KEY,
                core_security_id VARCHAR(36),
                symbol VARCHAR(20) NOT NULL,
                name VARCHAR(200) NOT NULL,
                market VARCHAR(20) NOT NULL,
                exchange VARCHAR(20),
                currency VARCHAR(3) NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO beta_instruments (
                id, core_security_id, symbol, name, market, exchange, currency, is_active, created_at, updated_at
            )
            VALUES (
                'inst-1', NULL, 'LEG', 'Legacy Plc', 'UK', 'LSE', 'GBP', 1,
                '2026-03-01 10:00:00', '2026-03-01 10:00:00'
            );

            CREATE TABLE beta_score_runs (
                id VARCHAR(36) PRIMARY KEY,
                run_type VARCHAR(40) NOT NULL,
                status VARCHAR(30) NOT NULL,
                scored_at DATETIME NOT NULL,
                notes_json TEXT
            );
            INSERT INTO beta_score_runs (id, run_type, status, scored_at, notes_json)
            VALUES ('run-1', 'HEURISTIC_DAILY', 'SUCCESS', '2026-03-10 12:00:00', NULL);

            CREATE TABLE beta_score_tape (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                score_run_id VARCHAR(36) NOT NULL,
                instrument_id VARCHAR(36) NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                direction VARCHAR(20) NOT NULL,
                predicted_return_5d FLOAT,
                realized_volatility_5d FLOAT,
                confidence_score FLOAT NOT NULL,
                expected_edge_score FLOAT NOT NULL,
                recommendation_flag BOOLEAN NOT NULL,
                rejection_reason TEXT,
                evidence_json TEXT,
                scored_at DATETIME NOT NULL
            );
            INSERT INTO beta_score_tape (
                score_run_id,
                instrument_id,
                symbol,
                direction,
                predicted_return_5d,
                realized_volatility_5d,
                confidence_score,
                expected_edge_score,
                recommendation_flag,
                rejection_reason,
                evidence_json,
                scored_at
            )
            VALUES (
                'run-1',
                'inst-1',
                'LEG',
                'BULLISH',
                4.2,
                1.1,
                0.72,
                0.51,
                1,
                NULL,
                '{"source":"legacy"}',
                '2026-03-10 12:00:00'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_beta_overview_bootstraps_from_core_db(client) -> None:
    response = client.get("/paper-trading-beta")

    assert response.status_code == 200
    assert "Paper Trading Beta" in response.text
    beta_db_path = get_beta_db_path()
    assert beta_db_path is not None
    assert beta_db_path.name.endswith(".beta_research.db")
    assert BetaContext.is_initialized()
    with BetaContext.read_session() as sess:
        assert sess.query(BetaHypothesis).count() == 2


def test_beta_control_updates_settings(client) -> None:
    overview = client.get("/paper-trading-beta")
    assert overview.status_code == 200
    beta_db_path = get_beta_db_path()
    assert beta_db_path is not None

    response = client.post(
        "/paper-trading-beta/control",
        data={"action": "pause_learning"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    settings = BetaSettings.load(beta_db_path)
    assert settings.learning_enabled is False

    cycle_response = client.post(
        "/paper-trading-beta/control",
        data={"action": "run_cycle"},
        follow_redirects=False,
    )
    assert cycle_response.status_code == 303


def test_beta_runtime_stops_when_core_db_locks(client) -> None:
    response = client.get("/paper-trading-beta")
    assert response.status_code == 200
    assert get_beta_db_path() is not None

    lock_response = client.post("/admin/lock")

    assert lock_response.status_code == 200
    assert get_beta_db_path() is None
    assert _state.get_db_path() is None
    assert BetaContext.is_initialized() is False


def test_beta_bootstrap_syncs_daily_bars_and_creates_candidate_and_demo_trade(client) -> None:
    with AppContext.write_session() as sess:
        security = Security(
            ticker="TSCO",
            name="Tesco PLC",
            currency="GBP",
            exchange="LSE",
            units_precision=0,
        )
        sess.add(security)
        sess.flush()
        closes = ["100.00", "101.00", "102.00", "104.00", "105.00", "107.00"]
        dates = [
            date(2026, 3, 2),
            date(2026, 3, 3),
            date(2026, 3, 4),
            date(2026, 3, 5),
            date(2026, 3, 6),
            date(2026, 3, 9),
        ]
        for idx, close in enumerate(closes):
            sess.add(
                PriceHistory(
                    security_id=security.id,
                    price_date=dates[idx],
                    close_price_original_ccy=close,
                    close_price_gbp=close,
                    currency="GBP",
                    source="test_history",
                    fetched_at=datetime(2026, 3, 9, 16, 30, 0),
                )
            )
        sess.add(
            PriceTickerSnapshot(
                security_id=security.id,
                price_date=date(2026, 3, 9),
                price_native="108.25",
                currency="GBP",
                price_gbp="108.25",
                direction="up",
                percent_change="1.17",
                source="twelvedata:2026-03-09 15:35:00",
                observed_at=datetime(2026, 3, 9, 15, 35, 0),
            )
        )

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200
    replay = client.get("/paper-trading-beta/replay")
    assert replay.status_code == 200
    assert "Recent Score Tape" in replay.text
    assert "Recent Intraday Snapshots" in replay.text
    assert "Feature Rows" in replay.text
    assert "Label Rows" in replay.text
    assert "Latest Evaluation" in replay.text

    with BetaContext.read_session() as sess:
        assert sess.query(BetaDailyBar).count() >= 6
        assert sess.query(BetaIntradaySnapshot).count() >= 1
        assert sess.query(BetaFeatureValue).count() >= 4
        assert sess.query(BetaLabelValue).count() >= 2
        canonical_label = sess.query(BetaLabelDefinition).filter(BetaLabelDefinition.is_canonical.is_(True)).one()
        assert canonical_label.label_name == "fwd_5d_excess_return_pct"
        assert sess.query(BetaScoreTape).count() >= 1
        assert sess.query(BetaEvaluationRun).count() >= 1
        assert sess.query(BetaEvaluationSummary).count() >= 1
        assert sess.query(BetaConfidenceBucketSummary).count() >= 3
        candidate = sess.query(BetaSignalCandidate).filter(BetaSignalCandidate.symbol == "TSCO").one()
        assert candidate.status in {"WATCHING", "PROMOTED"}
        assert candidate.confidence_score > 0
        position = sess.query(BetaDemoPosition).filter(BetaDemoPosition.symbol == "TSCO").one()
        assert position.status == "OPEN"
        assert position.side == "LONG"
        assert float(position.entry_price) > 108.25
        assert position.units is not None
        ledger = sess.query(BetaLedgerState).filter(BetaLedgerState.id == 1).one()
        assert float(ledger.available_cash_gbp) < 10000.0
        assert float(ledger.deployed_capital_gbp) > 0
        assert sess.query(BetaCashLedgerEntry).count() >= 2
        candidate_id = candidate.id
        position_id = position.id

    candidate_detail = client.get(f"/paper-trading-beta/candidate/{candidate_id}")
    assert candidate_detail.status_code == 200
    assert "Evidence Summary" in candidate_detail.text
    trade_detail = client.get(f"/paper-trading-beta/trade/{position_id}")
    assert trade_detail.status_code == 200
    assert "Lifecycle Events" in trade_detail.text


def test_beta_auto_pauses_entries_on_degradation(client) -> None:
    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    with BetaContext.write_session() as sess:
        for index in range(4):
            sess.add(
                BetaDemoPosition(
                    symbol=f"LOSS{index}",
                    market="UK",
                    side="LONG",
                    status="CLOSED",
                    confidence_score=0.7,
                    expected_edge_score=0.4,
                    size_gbp="500.00",
                    entry_price="100.0000",
                    exit_price="96.0000",
                    pnl_gbp="-20.00",
                    pnl_pct="-4.00",
                    exit_reason="Synthetic losing trade for degradation test.",
                    opened_at=datetime(2026, 3, 1, 10, 0, 0),
                    closed_at=datetime(2026, 3, 5, 10, 0, 0),
                )
            )

    cycle_response = client.post(
        "/paper-trading-beta/control",
        data={"action": "run_cycle"},
        follow_redirects=False,
    )
    assert cycle_response.status_code == 303

    health = client.get("/paper-trading-beta/health")
    assert health.status_code == 200

    with BetaContext.read_session() as sess:
        risk = sess.query(BetaRiskControlState).filter(BetaRiskControlState.id == 1).one()
        assert risk.demo_entries_paused is True
        assert risk.degradation_status == "PAUSED"


def test_beta_manual_review_persists_review_run_and_findings(client) -> None:
    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    review_response = client.post(
        "/paper-trading-beta/control",
        data={"action": "run_review"},
        follow_redirects=False,
    )

    assert review_response.status_code == 303

    with BetaContext.read_session() as sess:
        assert sess.query(BetaAiReviewRun).count() >= 1
        assert sess.query(BetaAiReviewFinding).count() >= 1


def test_beta_can_build_replay_pack_artifacts(client) -> None:
    response = client.get("/paper-trading-beta")
    assert response.status_code == 200
    beta_db_path = get_beta_db_path()
    assert beta_db_path is not None

    replay_response = client.post(
        "/paper-trading-beta/control",
        data={"action": "build_replay_pack"},
        follow_redirects=False,
    )
    assert replay_response.status_code == 303

    artifacts_dir = resolve_beta_artifacts_dir(beta_db_path)
    packs = list(artifacts_dir.glob("focus_replay_*.json"))
    assert packs

    replay_page = client.get("/paper-trading-beta/replay")
    assert replay_page.status_code == 200
    assert "Recent Replay Packs" in replay_page.text


def test_beta_universe_auto_expands_when_coverage_is_healthy(client) -> None:
    with AppContext.write_session() as sess:
        start_date = date(2026, 2, 10)
        for index in range(55):
            market = "LSE" if index < 40 else "NASDAQ"
            currency = "GBP" if index < 40 else "USD"
            security = Security(
                ticker=f"SEED{index:02d}",
                name=f"Seed Security {index:02d}",
                currency=currency,
                exchange=market,
                units_precision=0,
            )
            sess.add(security)
            sess.flush()
            for offset in range(30):
                price_date = date.fromordinal(start_date.toordinal() + offset)
                close_value = 100 + index + offset
                sess.add(
                    PriceHistory(
                        security_id=security.id,
                        price_date=price_date,
                        close_price_original_ccy=f"{close_value:.2f}",
                        close_price_gbp=f"{close_value:.2f}",
                        currency=currency,
                        source="expansion_test",
                        fetched_at=datetime(2026, 3, 9, 16, 30, 0),
                    )
                )

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    with BetaContext.read_session() as sess:
        initial_active = sess.query(BetaUniverseMembership).filter(
            BetaUniverseMembership.effective_to.is_(None),
            BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
        ).count()
        assert initial_active == 50

    cycle_response = client.post(
        "/paper-trading-beta/control",
        data={"action": "run_cycle"},
        follow_redirects=False,
    )
    assert cycle_response.status_code == 303

    with BetaContext.read_session() as sess:
        expanded_active = sess.query(BetaUniverseMembership).filter(
            BetaUniverseMembership.effective_to.is_(None),
            BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
        ).count()
        assert expanded_active == 55


def test_beta_corpus_backfill_can_populate_catalog_only_names(client, monkeypatch) -> None:
    with AppContext.write_session() as sess:
        sess.add(
            SecurityCatalog(
                symbol="CATA",
                name="Catalog Alpha Bank PLC",
                exchange="LSE",
                currency="GBP",
            )
        )

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    def _fake_history(**_kwargs):
        return [
            _HistoryPoint(
                bar_date=date(2026, 3, 10),
                close_native="100.0000",
                close_gbp="100.0000",
                source="test_provider",
                fetched_at=datetime(2026, 3, 10, 16, 30, 0),
            ),
            _HistoryPoint(
                bar_date=date(2026, 3, 11),
                close_native="101.5000",
                close_gbp="101.5000",
                source="test_provider",
                fetched_at=datetime(2026, 3, 11, 16, 30, 0),
            ),
        ]

    monkeypatch.setattr(
        BetaCorpusService,
        "_fetch_history_points",
        staticmethod(_fake_history),
    )

    result = BetaCorpusService.backfill_market_corpus(batch_size=5, include_benchmarks=True)
    assert result["instrument_bars_added"] >= 2

    with BetaContext.read_session() as sess:
        catalog_bars = (
            sess.query(BetaDailyBar)
            .join(BetaInstrument, BetaInstrument.id == BetaDailyBar.instrument_id)
            .filter(BetaInstrument.symbol == "CATA")
            .all()
        )
        assert len(catalog_bars) >= 2
        assert sess.query(BetaBenchmarkBar).count() >= 2


def test_beta_training_can_store_and_activate_a_model(client) -> None:
    with AppContext.write_session() as sess:
        start_date = date(2026, 2, 1)
        specs = [
            ("ALFA", "Alpha PLC", "LSE", "GBP", 100.0, 1.2),
            ("BRAV", "Bravo PLC", "LSE", "GBP", 80.0, 0.8),
            ("CHAR", "Charlie Inc", "NASDAQ", "USD", 120.0, 1.5),
        ]
        for ticker, name, exchange, currency, base_price, slope in specs:
            security = Security(
                ticker=ticker,
                name=name,
                currency=currency,
                exchange=exchange,
                units_precision=0,
            )
            sess.add(security)
            sess.flush()
            for offset in range(20):
                price_date = date.fromordinal(start_date.toordinal() + offset)
                close_value = base_price + (offset * slope)
                sess.add(
                    PriceHistory(
                        security_id=security.id,
                        price_date=price_date,
                        close_price_original_ccy=f"{close_value:.2f}",
                        close_price_gbp=f"{close_value:.2f}",
                        currency=currency,
                        source="training_test",
                        fetched_at=datetime(2026, 3, 9, 16, 30, 0),
                    )
                )

    overview = client.get("/paper-trading-beta")
    assert overview.status_code == 200

    response = client.post(
        "/paper-trading-beta/control",
        data={"action": "run_training"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with BetaContext.read_session() as sess:
        models = sess.query(BetaModelVersion).all()
        assert len(models) >= 1
        active_models = [row for row in models if row.is_active]
        assert len(active_models) >= 1
        assert active_models[0].training_row_count >= 20
        assert active_models[0].dataset_version_id is not None
        assert sess.query(BetaDatasetVersion).count() >= 1
        assert sess.query(BetaDatasetRow).count() >= 20
        assert sess.query(BetaExperimentRun).count() >= 1
        assert sess.query(BetaValidationRun).count() >= 1
        active_strategies = sess.query(BetaStrategyVersion).filter(BetaStrategyVersion.is_active.is_(True)).all()
        assert len(active_strategies) >= 1
        assert active_strategies[0].model_version_id == active_models[0].id


def test_beta_news_ingestion_can_store_and_link_articles(client) -> None:
    with AppContext.write_session() as sess:
        security = Security(
            ticker="TSCO",
            name="Tesco PLC",
            currency="GBP",
            exchange="LSE",
            units_precision=0,
        )
        sess.add(security)

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    with BetaContext.read_session() as sess:
        source = sess.query(BetaNewsSource).first()
        assert source is not None

    xml_text = """
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <item>
          <guid>article-1</guid>
          <title>Tesco PLC beats forecasts as TSCO profit surges</title>
          <link>https://example.com/tesco-beats</link>
          <description>Positive catalyst with strong profit growth.</description>
          <pubDate>Sat, 14 Mar 2026 09:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    result = BetaNewsService.ingest_source(source.id, xml_text=xml_text)
    assert result["stored_count"] == 1
    assert result["linked_count"] >= 1

    replay = client.get("/paper-trading-beta/replay")
    assert replay.status_code == 200
    assert "Recent News Articles" in replay.text

    with BetaContext.read_session() as sess:
        article = sess.query(BetaNewsArticle).one()
        assert article.sentiment_label == "POSITIVE"
        links = sess.query(BetaNewsArticleLink).all()
        assert len(links) >= 1
        assert any(row.symbol == "TSCO" for row in links)


def test_beta_filing_ingestion_can_store_and_link_official_events(client) -> None:
    with AppContext.write_session() as sess:
        security = Security(
            ticker="TSCO",
            name="Tesco PLC",
            currency="GBP",
            exchange="LSE",
            units_precision=0,
        )
        sess.add(security)

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    with BetaContext.read_session() as sess:
        source = sess.query(BetaFilingSource).first()
        assert source is not None

    xml_text = """
    <rss version="2.0">
      <channel>
        <title>Test Official Feed</title>
        <item>
          <guid>filing-1</guid>
          <title>Tesco PLC trading update and TSCO guidance upgrade</title>
          <link>https://example.com/tesco-trading-update</link>
          <description>Official trading update with stronger profit outlook.</description>
          <pubDate>Sat, 14 Mar 2026 10:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    result = BetaFilingService.ingest_source(source.id, xml_text=xml_text)
    assert result["stored_count"] == 1
    assert result["linked_count"] >= 1

    opportunities = client.get("/paper-trading-beta/opportunities")
    assert opportunities.status_code == 200
    assert "Recent Official Releases" in opportunities.text
    replay = client.get("/paper-trading-beta/replay")
    assert replay.status_code == 200
    assert "Recent Official Releases" in replay.text

    with BetaContext.read_session() as sess:
        event = (
            sess.query(BetaFilingEvent)
            .filter(BetaFilingEvent.title.like("%Tesco PLC trading update%"))
            .one()
        )
        assert event.event_category in {"TRADING_UPDATE", "OFFICIAL_RELEASE"}
        links = sess.query(BetaFilingEventLink).filter(BetaFilingEventLink.event_id == event.id).all()
        assert len(links) >= 1
        assert any(row.symbol == "TSCO" for row in links)


def test_beta_does_not_open_demo_trade_when_market_closed_at_score_time(client) -> None:
    with AppContext.write_session() as sess:
        security = Security(
            ticker="TSCO",
            name="Tesco PLC",
            currency="GBP",
            exchange="LSE",
            units_precision=0,
        )
        sess.add(security)
        sess.flush()
        closes = ["100.00", "101.00", "102.00", "104.00", "105.00", "107.00"]
        dates = [
            date(2026, 3, 9),
            date(2026, 3, 10),
            date(2026, 3, 11),
            date(2026, 3, 12),
            date(2026, 3, 13),
            date(2026, 3, 14),
        ]
        for idx, close in enumerate(closes):
            sess.add(
                PriceHistory(
                    security_id=security.id,
                    price_date=dates[idx],
                    close_price_original_ccy=close,
                    close_price_gbp=close,
                    currency="GBP",
                    source="closed_market_test",
                    fetched_at=datetime(2026, 3, 14, 12, 0, 0),
                )
            )
        sess.add(
            PriceTickerSnapshot(
                security_id=security.id,
                price_date=date(2026, 3, 14),
                price_native="108.25",
                currency="GBP",
                price_gbp="108.25",
                direction="up",
                percent_change="1.17",
                source="twelvedata:2026-03-14 12:00:00",
                observed_at=datetime(2026, 3, 14, 12, 0, 0),
            )
        )

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    with BetaContext.read_session() as sess:
        candidate = sess.query(BetaSignalCandidate).filter(BetaSignalCandidate.symbol == "TSCO").one()
        assert candidate.status in {"WATCHING", "PROMOTED"}
        positions = sess.query(BetaDemoPosition).filter(BetaDemoPosition.symbol == "TSCO").all()
        assert positions == []


def test_beta_hypothesis_registry_tracks_catalyst_family_and_pages(client) -> None:
    with AppContext.write_session() as sess:
        security = Security(
            ticker="TSCO",
            name="Tesco PLC",
            currency="GBP",
            exchange="LSE",
            units_precision=0,
        )
        sess.add(security)
        sess.flush()
        closes = ["100.00", "101.50", "102.50", "104.00", "105.50", "107.00"]
        dates = [
            date(2026, 3, 2),
            date(2026, 3, 3),
            date(2026, 3, 4),
            date(2026, 3, 5),
            date(2026, 3, 6),
            date(2026, 3, 9),
        ]
        for idx, close in enumerate(closes):
            sess.add(
                PriceHistory(
                    security_id=security.id,
                    price_date=dates[idx],
                    close_price_original_ccy=close,
                    close_price_gbp=close,
                    currency="GBP",
                    source="hypothesis_test",
                    fetched_at=datetime(2026, 3, 9, 16, 30, 0),
                )
            )

    response = client.get("/paper-trading-beta")
    assert response.status_code == 200

    with BetaContext.read_session() as sess:
        source = sess.query(BetaNewsSource).first()
        assert source is not None

    xml_text = """
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <item>
          <guid>article-catalyst-1</guid>
          <title>Tesco PLC confirms strong trading update as TSCO sales beat expectations</title>
          <link>https://example.com/tesco-trading-update</link>
          <description>Positive trading update with strong sales and profit momentum.</description>
          <pubDate>Sat, 14 Mar 2026 09:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """
    ingest_result = BetaNewsService.ingest_source(source.id, xml_text=xml_text)
    assert ingest_result["stored_count"] == 1

    cycle_response = client.post(
        "/paper-trading-beta/control",
        data={"action": "run_cycle"},
        follow_redirects=False,
    )
    assert cycle_response.status_code == 303

    hypotheses_page = client.get("/paper-trading-beta/hypotheses")
    assert hypotheses_page.status_code == 200
    assert "Hypothesis Families" in hypotheses_page.text

    with BetaContext.read_session() as sess:
        catalyst = sess.query(BetaHypothesis).filter(BetaHypothesis.code == "CATALYST_CONFIRMATION").one()
        candidate = sess.query(BetaSignalCandidate).filter(BetaSignalCandidate.symbol == "TSCO").one()
        assert candidate.hypothesis_id == catalyst.id
        assert float(catalyst.evidence_score or "0") > 0
        assert sess.query(BetaHypothesisEvent).filter(BetaHypothesisEvent.hypothesis_id == catalyst.id).count() >= 1
        catalyst_id = catalyst.id

    detail_response = client.get(f"/paper-trading-beta/hypothesis/{catalyst_id}")
    assert detail_response.status_code == 200
    assert "Linked Candidates" in detail_response.text


def test_beta_runtime_migrates_existing_beta_db_without_reset(tmp_path, monkeypatch) -> None:
    beta_db_path = tmp_path / "legacy.beta_research.db"
    _create_legacy_beta_db(beta_db_path)
    _write_beta_settings(
        beta_db_path,
        auto_start_supervisor=False,
        observation_enabled=False,
        learning_enabled=False,
        shadow_scoring_enabled=False,
        demo_execution_enabled=False,
        news_enabled=False,
        filings_enabled=False,
    )
    monkeypatch.setenv("EQUITY_BETA_DB_PATH", str(beta_db_path))

    initialize_beta_runtime(None, allow_supervisor=False)

    with BetaContext.read_session() as sess:
        instrument = sess.query(BetaInstrument).filter(BetaInstrument.symbol == "LEG").one()
        assert instrument.name == "Legacy Plc"
        assert instrument.benchmark_key is None
        score = sess.query(BetaScoreTape).filter(BetaScoreTape.symbol == "LEG").one()
        assert score.strategy_version_id is None
        assert score.model_version_id is None
        schema_meta = sess.query(BetaSchemaMeta).filter(BetaSchemaMeta.id == 1).one()
        assert schema_meta.schema_version == "v3"

    engine = BetaDatabaseEngine.open(beta_db_path)
    try:
        requires_reset, reasons = beta_schema_requires_reset(engine)
        assert requires_reset is False, reasons
        inspector = inspect(engine.raw_engine)
        instrument_columns = {column["name"] for column in inspector.get_columns("beta_instruments")}
        assert {"benchmark_key", "sector_key", "sector_label", "metadata_json"} <= instrument_columns
        score_columns = {column["name"] for column in inspector.get_columns("beta_score_tape")}
        assert {"strategy_version_id", "model_version_id"} <= score_columns
    finally:
        engine.dispose()

    assert list(tmp_path.glob("legacy.beta_research.schema_backup_*")) == []


def test_beta_lifespan_can_bootstrap_from_env_vars(tmp_path, monkeypatch) -> None:
    core_db_path = tmp_path / "lifespan.db"
    beta_db_path = tmp_path / "lifespan.beta_research.db"

    engine = DatabaseEngine.open_unencrypted(f"sqlite:///{core_db_path}")
    try:
        Base.metadata.create_all(engine.raw_engine)
        with engine.session() as sess:
            sess.add(
                Security(
                    ticker="LGEN",
                    name="Legal & General",
                    currency="GBP",
                    exchange="LSE",
                    units_precision=0,
                )
            )
    finally:
        engine.dispose()

    _write_beta_settings(
        beta_db_path,
        auto_start_supervisor=False,
        news_enabled=False,
        filings_enabled=False,
    )

    monkeypatch.setenv("EQUITY_DB_PATH", str(core_db_path))
    monkeypatch.setenv("EQUITY_DB_ENCRYPTED", "false")
    monkeypatch.setenv("EQUITY_BETA_DB_PATH", str(beta_db_path))

    def _noop_catalog_refresh(*, force_refresh: bool = False) -> None:
        return None

    async def _idle_task() -> None:
        return None

    monkeypatch.setattr(app_module, "_ensure_security_catalog_available", _noop_catalog_refresh)
    monkeypatch.setattr(app_module, "_nightly_history_task", _idle_task)
    monkeypatch.setattr(app_module, "_intraday_quote_refresh_task", _idle_task)
    monkeypatch.setattr(app_module, "_fx_refresh_task", _idle_task)
    monkeypatch.setattr(app_module, "_weekly_catalog_sync_task", _idle_task)
    monkeypatch.setattr(app_module, "_twelve_data_stream_task", _idle_task)

    token = make_session_token()
    with TestClient(
        app_module.app,
        raise_server_exceptions=True,
        cookies={SESSION_COOKIE_NAME: token},
    ) as tc:
        response = tc.get("/paper-trading-beta")
        assert response.status_code == 200
        assert "Paper Trading Beta" in response.text
        status = tc.get("/admin/status")
        assert status.status_code == 200
        assert status.json()["locked"] is False
        assert get_beta_db_path() == beta_db_path
        assert BetaContext.is_initialized() is True

    assert BetaContext.is_initialized() is False
    assert _state.get_db_path() is None
