from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from src.app_context import AppContext
from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaDailyBar,
    BetaDemoPosition,
    BetaExecutionHypothesisBeliefState,
    BetaExecutionHypothesisDiscoveryCandidate,
    BetaExecutionHypothesisDefinition,
    BetaExecutionLabelValue,
    BetaExecutionSignal,
    BetaExecutionHypothesisTestRun,
    BetaHypothesisDefinition,
    BetaInstrument,
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradayFeatureSnapshot,
    BetaIntradaySimulatedTrade,
    BetaIntradaySimulatedTradeEvent,
    BetaIntradaySnapshot,
    BetaMinuteBar,
    BetaPositionState,
    BetaSignalCandidate,
    BetaSignalObservation,
    BetaUiNotification,
)
from src.beta.services.execution_outcome_service import BetaExecutionOutcomeService
from src.beta.services.execution_signal_service import BetaExecutionSignalService
from src.beta.services.execution_hypothesis_backtest_service import BetaExecutionHypothesisBacktestService
from src.beta.services.execution_hypothesis_belief_service import BetaExecutionHypothesisBeliefService
from src.beta.services.execution_hypothesis_discovery_service import BetaExecutionHypothesisDiscoveryService
from src.beta.services.intraday_aggregation_service import BetaIntradayAggregationService
from src.beta.services.intraday_feature_service import BetaIntradayFeatureService
from src.beta.services.intraday_focus_backfill_service import BetaIntradayFocusBackfillService
from src.beta.services.intraday_outlook_service import BetaIntradayOutlookService
from src.beta.services.intraday_priority_service import BetaIntradayPriorityService, IntradayPriorityItem
from src.beta.services.intraday_simulated_trade_service import BetaIntradaySimulatedTradeService
from src.beta.services.observation_service import BetaObservationService
from src.beta.services.position_registry import BetaPositionRegistry
from src.beta.services.session_service import BetaMarketSessionService
from src.beta.settings import BetaSettings
from src.db.models import Lot, Security


@pytest.fixture()
def beta_context():
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


def _add_minute_bar_series(
    sess,
    *,
    instrument_id: str,
    session_date: date,
    start_ts: datetime,
    close_values: list[float],
    volume_value: float,
) -> None:
    previous_close = close_values[0]
    for offset, close_price in enumerate(close_values):
        minute_ts = start_ts + timedelta(minutes=offset)
        open_price = previous_close if offset > 0 else close_values[0]
        high_price = max(open_price, close_price) + 0.2
        low_price = min(open_price, close_price) - 0.2
        sess.add(
            BetaMinuteBar(
                instrument_id=instrument_id,
                session_date=session_date,
                minute_ts=minute_ts,
                open_price_gbp=str(round(open_price, 4)),
                high_price_gbp=str(round(high_price, 4)),
                low_price_gbp=str(round(low_price, 4)),
                close_price_gbp=str(round(close_price, 4)),
                close_price_native=str(round(close_price, 4)),
                currency="USD",
                volume_native=str(round(volume_value, 4)),
                snapshot_count=1,
                first_snapshot_at=minute_ts,
                last_snapshot_at=minute_ts,
                source="test",
            )
        )
        previous_close = close_price


def test_market_session_service_supports_pre_open_regular_and_post_close():
    assert (
        BetaMarketSessionService.session_state(
            "NASDAQ",
            now_utc=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        )
        == "PRE_OPEN"
    )
    assert (
        BetaMarketSessionService.session_state(
            "NASDAQ",
            now_utc=datetime(2026, 3, 16, 14, 30, tzinfo=timezone.utc),
        )
        == "REGULAR_OPEN"
    )
    assert (
        BetaMarketSessionService.session_state(
            "NASDAQ",
            now_utc=datetime(2026, 3, 16, 21, 0, tzinfo=timezone.utc),
        )
        == "POST_CLOSE"
    )


def test_market_session_service_live_market_priority_window_tracks_regular_open():
    settings = BetaSettings()
    settings.market_hours_live_data_priority_enabled = True

    assert (
        BetaMarketSessionService.live_market_priority_window(
            settings,
            now_utc=datetime(2026, 3, 16, 14, 30, tzinfo=timezone.utc),
        )
        is True
    )
    assert (
        BetaMarketSessionService.live_market_priority_window(
            settings,
            now_utc=datetime(2026, 3, 15, 14, 30, tzinfo=timezone.utc),
        )
        is False
    )

    settings.market_hours_live_data_priority_enabled = False
    assert (
        BetaMarketSessionService.live_market_priority_window(
            settings,
            now_utc=datetime(2026, 3, 16, 14, 30, tzinfo=timezone.utc),
        )
        is False
    )


def test_market_session_clock_reports_regular_session_progress():
    clock = BetaMarketSessionService.session_clock(
        "NASDAQ",
        now_utc=datetime(2026, 3, 16, 16, 0, tzinfo=timezone.utc),
    )

    assert clock["session_state"] == "REGULAR_OPEN"
    assert clock["minutes_since_open"] == 150
    assert clock["minutes_until_close"] == 240
    assert clock["regular_session_minutes"] == 390
    assert clock["session_progress_pct"] == pytest.approx(38.4615, abs=1e-4)


def test_position_registry_syncs_demo_position_and_execution_quality(beta_context):
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        candidate = BetaSignalCandidate(
            symbol="IBM",
            title="IBM thesis",
            status="WATCHING",
            direction="BULLISH",
            confidence_score=0.6,
            expected_edge_score=0.8,
            evidence_json=json.dumps({"predicted_return_pct": 4.0}, sort_keys=True),
        )
        sess.add_all([instrument, candidate])
        sess.flush()
        position = BetaDemoPosition(
            candidate_id=candidate.id,
            symbol="IBM",
            market="US",
            status="OPEN",
            size_gbp="1000",
            units="10",
            entry_price="100",
            pnl_pct="3.0",
            planned_horizon_days=5,
            opened_at=datetime(2026, 3, 16, 14, 0, 0),
        )
        sess.add(position)

    result = BetaPositionRegistry.sync_demo_positions(
        now_utc=datetime(2026, 3, 17, 14, 0, 0, tzinfo=timezone.utc)
    )

    with BetaContext.read_session() as sess:
        state = sess.scalar(select(BetaPositionState).where(BetaPositionState.demo_position_id == position.id))

    assert result["states_upserted"] == 1
    assert state is not None
    assert state.thesis_expected_return_pct == 4.0
    assert state.unrealized_return_pct == 3.0
    assert state.execution_quality_score == 0.75


def test_position_registry_syncs_core_portfolio_holding_into_beta_state(beta_context, app_context):
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
                Lot(
                    security_id=security.id,
                    scheme_type="BROKERAGE",
                    tax_year="2025-26",
                    acquisition_date=date(2026, 1, 2),
                    quantity="10",
                    quantity_remaining="10",
                    acquisition_price_gbp="100",
                    true_cost_per_share_gbp="100",
                ),
                Lot(
                    security_id=security.id,
                    scheme_type="BROKERAGE",
                    tax_year="2025-26",
                    acquisition_date=date(2026, 2, 10),
                    quantity="5",
                    quantity_remaining="5",
                    acquisition_price_gbp="110",
                    true_cost_per_share_gbp="110",
                ),
            ]
        )

    result = BetaPositionRegistry.sync_core_portfolio_positions(
        now_utc=datetime(2026, 3, 16, 14, 0, 0, tzinfo=timezone.utc)
    )

    with BetaContext.read_session() as sess:
        state = sess.scalar(
            select(BetaPositionState).where(
                BetaPositionState.symbol == "IBM",
                BetaPositionState.position_status == "OPEN",
            )
        )

    assert result["states_upserted"] == 1
    assert result["open_positions"] == 1
    assert result["unmatched_instruments"] == 0
    assert state is not None
    assert state.position_source == "MANUAL"
    assert state.instrument_id == instrument.id
    assert Decimal(state.units or "0") == Decimal("15")
    assert float(Decimal(state.entry_price or "0")) == pytest.approx((10 * 100 + 5 * 110) / 15)
    assert state.entry_timestamp == datetime(2026, 1, 2, 0, 0, 0)
    metadata = json.loads(state.metadata_json or "{}")
    assert metadata["bridge_source"] == "CORE_PORTFOLIO"
    assert metadata["lot_count"] == 2


def test_execution_hypothesis_research_generates_discovery_backtests_and_beliefs(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_hypothesis_research_enabled = True
    settings.intraday_execution_hypothesis_history_days = 365
    settings.intraday_execution_hypothesis_template_limit = 1
    settings.intraday_execution_hypothesis_variant_cap = 2
    settings.intraday_execution_hypothesis_max_promotions_per_run = 1
    settings.intraday_execution_hypothesis_min_support = 10
    settings.intraday_execution_hypothesis_min_matched_instruments = 1

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
        template = BetaExecutionHypothesisDefinition(
            hypothesis_code="TEST_OPEN_RECOVERY_V1",
            name="Test open recovery",
            signal_type="HOLD_THROUGH_NOISE",
            entry_conditions_json=json.dumps(
                {
                    "all": [
                        {"feature": "return_since_open_pct", "op": "gt", "value": 0.5},
                        {"feature": "distance_from_vwap_pct", "op": "gt", "value": 0.0},
                    ]
                },
                sort_keys=True,
            ),
            regime_filters_json=json.dumps({}, sort_keys=True),
            feature_subset_json=json.dumps(["distance_from_vwap_pct", "return_since_open_pct"], sort_keys=True),
            rationale_text="Synthetic recovery template",
            source_type="MANUAL",
            metadata_json=json.dumps(
                {
                    "event_codes": ["EVENT_OPENING_WEAKNESS_REPAIR"],
                    "default_hold_window_minutes": [5, 30],
                    "base_confidence": 0.6,
                },
                sort_keys=True,
            ),
            provenance_json=json.dumps({"source": "MANUAL"}, sort_keys=True),
            status="ACTIVE",
        )
        sess.add(template)
        sess.flush()

        for day_offset in range(12):
            session_date = date(2026, 1, 5) + timedelta(days=day_offset)
            signal_time = datetime(2026, 1, 5, 15, 0, 0) + timedelta(days=day_offset)
            feature_snapshot = {
                "return_since_open_pct": 0.8 + (day_offset * 0.01),
                "distance_from_vwap_pct": 0.15,
                "session_progress_pct": 25.0,
                "minutes_since_open": 60.0,
            }
            signal = BetaExecutionSignal(
                execution_hypothesis_definition_id=template.id,
                instrument_id=instrument.id,
                symbol="IBM",
                session_date=session_date,
                signal_time=signal_time,
                session_state="REGULAR_OPEN",
                signal_type="HOLD_THROUGH_NOISE",
                confidence_score=0.6,
                rationale_text="synthetic execution sample",
                event_trigger_code="EVENT_OPENING_WEAKNESS_REPAIR",
                matched_conditions_json=json.dumps([], sort_keys=True),
                feature_snapshot_json=json.dumps(feature_snapshot, sort_keys=True),
            )
            sess.add(signal)
            sess.flush()
            sess.add(
                BetaExecutionLabelValue(
                    execution_signal_id=signal.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    session_date=session_date,
                    signal_time=signal_time,
                    future_30m_return_pct=1.0,
                    future_60m_return_pct=1.2,
                    future_120m_return_pct=1.3,
                    close_return_from_signal_pct=1.1,
                    max_adverse_move_after_signal_pct=-0.2,
                    max_favorable_move_after_signal_pct=1.4,
                    action_aligned_return_pct=1.2,
                    time_to_peak_minutes=18,
                    evaluation_complete=True,
                )
            )
            _add_minute_bar_series(
                sess,
                instrument_id=instrument.id,
                session_date=session_date,
                start_ts=signal_time,
                close_values=[100.0, 100.3, 100.7, 101.0, 101.2, 101.3],
                volume_value=1000.0,
            )

    discovery = BetaExecutionHypothesisDiscoveryService.run_discovery(settings)
    backtests = BetaExecutionHypothesisBacktestService.refresh_backtests(settings)
    beliefs = BetaExecutionHypothesisBeliefService.refresh_belief_states(settings)

    with BetaContext.read_session() as sess:
        latest_test_run = sess.scalar(
            select(BetaExecutionHypothesisTestRun)
            .where(BetaExecutionHypothesisTestRun.execution_hypothesis_definition_id == template.id)
            .order_by(BetaExecutionHypothesisTestRun.created_at.desc())
            .limit(1)
        )
        belief = sess.get(BetaExecutionHypothesisBeliefState, template.id)
        generated_candidate = sess.scalar(
            select(BetaExecutionHypothesisDiscoveryCandidate)
            .where(BetaExecutionHypothesisDiscoveryCandidate.template_execution_hypothesis_definition_id == template.id)
            .order_by(BetaExecutionHypothesisDiscoveryCandidate.created_at.desc())
            .limit(1)
        )

    assert discovery["candidates_generated"] >= 1
    assert backtests["test_runs_written"] >= 1
    assert beliefs["beliefs_written"] >= 1
    assert latest_test_run is not None
    assert latest_test_run.support_count >= 10
    assert belief is not None
    assert belief.status in {"CANDIDATE", "PROMISING", "VALIDATED"}
    assert generated_candidate is not None


def test_execution_prepare_context_includes_core_portfolio_holdings(beta_context, app_context, monkeypatch):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        sess.add(instrument)

    with AppContext.write_session() as sess:
        security = Security(
            ticker="IBM",
            name="IBM",
            currency="USD",
            exchange="NASDAQ",
        )
        sess.add(security)
        sess.flush()
        sess.add(
            Lot(
                security_id=security.id,
                scheme_type="BROKERAGE",
                tax_year="2025-26",
                acquisition_date=date(2026, 3, 2),
                quantity="7.5",
                quantity_remaining="7.5",
                acquisition_price_gbp="101.25",
                true_cost_per_share_gbp="101.25",
            )
        )

    monkeypatch.setattr(BetaObservationService, "sync_intraday_snapshots", lambda instrument_ids: None)
    monkeypatch.setattr(
        BetaIntradayAggregationService,
        "aggregate_minute_bars",
        lambda instrument_ids, lookback_minutes: None,
    )
    monkeypatch.setattr(
        BetaIntradayFeatureService,
        "refresh_feature_snapshots",
        lambda priority_items, now_utc: None,
    )

    result = BetaExecutionSignalService.prepare_execution_context(
        settings,
        now_utc=datetime(2026, 3, 16, 14, 30, 0, tzinfo=timezone.utc),
    )

    assert result["held"] == 1
    assert result["watchlist_items"] >= 1
    assert result["core_position_states_upserted"] == 1
    assert result["position_states_upserted"] == 1


def test_intraday_aggregation_and_feature_snapshot_are_incremental(beta_context):
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
        sess.add(
            BetaDailyBar(
                instrument_id=instrument.id,
                bar_date=date(2026, 3, 13),
                close_price_gbp="100",
                close_price_native="100",
                currency="USD",
                source="test",
            )
        )
        prices = [103, 104, 105, 104, 105, 106]
        for offset, price in enumerate(prices):
            sess.add(
                BetaIntradaySnapshot(
                    instrument_id=instrument.id,
                    price_date=date(2026, 3, 16),
                    price_gbp=str(price),
                    price_native=str(price),
                    currency="USD",
                    observed_at=datetime(2026, 3, 16, 14, 30 + offset, 0),
                    source="test",
                )
            )

    BetaIntradayAggregationService.aggregate_minute_bars(
        instrument_ids=[instrument.id],
        lookback_minutes=240,
    )
    BetaIntradayFeatureService.refresh_feature_snapshots(
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instrument.id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="HELD",
                cadence_minutes=3,
                priority_score=1.0,
                session_state="REGULAR_OPEN",
            )
        ],
        now_utc=datetime(2026, 3, 16, 14, 36, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        minute_bars = list(sess.scalars(select(BetaMinuteBar)).all())
        snapshot = sess.scalar(select(BetaIntradayFeatureSnapshot).where(BetaIntradayFeatureSnapshot.instrument_id == instrument.id))

    assert len(minute_bars) == 6
    assert snapshot is not None
    features = json.loads(snapshot.feature_snapshot_json)
    assert round(features["gap_from_prev_close_pct"], 2) == 3.00
    assert round(features["return_since_open_pct"], 2) == 2.91
    assert features["return_last_5m_pct"] is not None


def test_intraday_feature_snapshot_populates_vwap_and_expected_volume_profiles(beta_context):
    session_date = date(2026, 3, 17)

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
        sess.add(
            BetaDailyBar(
                instrument_id=instrument.id,
                bar_date=date(2026, 3, 16),
                close_price_gbp="100",
                close_price_native="100",
                currency="USD",
                source="test",
            )
        )

        prior_sessions = [date(2026, 3, 12), date(2026, 3, 13), date(2026, 3, 14)]
        for prior_session in prior_sessions:
            for minute_offset, (price, volume) in enumerate(((100, 100), (101, 120), (102, 140))):
                ts = datetime.combine(prior_session, datetime.min.time()).replace(hour=14, minute=30 + minute_offset)
                sess.add(
                    BetaMinuteBar(
                        instrument_id=instrument.id,
                        session_date=prior_session,
                        minute_ts=ts,
                        open_price_gbp=str(price),
                        high_price_gbp=str(price + 0.2),
                        low_price_gbp=str(price - 0.2),
                        close_price_gbp=str(price),
                        close_price_native=str(price),
                        currency="USD",
                        volume_native=str(volume),
                        snapshot_count=1,
                        first_snapshot_at=ts,
                        last_snapshot_at=ts,
                        source="test",
                    )
                )

        for minute_offset, (price, volume) in enumerate(((101, 150), (102, 150), (103, 150))):
            ts = datetime.combine(session_date, datetime.min.time()).replace(hour=14, minute=30 + minute_offset)
            sess.add(
                BetaMinuteBar(
                    instrument_id=instrument.id,
                    session_date=session_date,
                    minute_ts=ts,
                    open_price_gbp=str(price),
                    high_price_gbp=str(price + 0.2),
                    low_price_gbp=str(price - 0.2),
                    close_price_gbp=str(price),
                    close_price_native=str(price),
                    currency="USD",
                    volume_native=str(volume),
                    snapshot_count=1,
                    first_snapshot_at=ts,
                    last_snapshot_at=ts,
                    source="test",
                )
            )

    BetaIntradayFeatureService.refresh_feature_snapshots(
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instrument.id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="ACTIVE_THESIS",
                cadence_minutes=5,
                priority_score=0.8,
                session_state="REGULAR_OPEN",
            )
        ],
        now_utc=datetime(2026, 3, 17, 14, 34, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        snapshot = sess.scalar(
            select(BetaIntradayFeatureSnapshot).where(BetaIntradayFeatureSnapshot.instrument_id == instrument.id)
        )

    assert snapshot is not None
    features = json.loads(snapshot.feature_snapshot_json)
    assert features["cumulative_volume_vs_expected"] == pytest.approx(1.25, abs=1e-6)
    assert features["volume_last_15m_vs_expected"] == pytest.approx(1.25, abs=1e-6)
    assert features["distance_from_vwap_pct"] is not None


def test_intraday_feature_snapshot_adds_time_of_day_and_historical_bias_features(beta_context):
    session_date = date(2026, 3, 18)

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
        sess.add(
            BetaDailyBar(
                instrument_id=instrument.id,
                bar_date=date(2026, 3, 17),
                close_price_gbp="100",
                close_price_native="100",
                currency="USD",
                source="test",
            )
        )

        prior_sessions = [
            date(2026, 3, 11),
            date(2026, 3, 12),
            date(2026, 3, 13),
            date(2026, 3, 16),
            date(2026, 3, 17),
        ]
        prior_closes = [
            (100.0 - ((idx + 1) * 0.07)) if idx < 15 else (98.95 + ((idx - 14) * 0.1))
            for idx in range(40)
        ]
        for prior_session in prior_sessions:
            _add_minute_bar_series(
                sess,
                instrument_id=instrument.id,
                session_date=prior_session,
                start_ts=datetime.combine(prior_session, datetime.min.time()).replace(hour=13, minute=30),
                close_values=prior_closes,
                volume_value=100.0,
            )

        current_closes = [
            (101.0 - ((idx + 1) * 0.08)) if idx < 15 else (99.8 + ((idx - 14) * 0.12))
            for idx in range(40)
        ]
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=session_date,
            start_ts=datetime.combine(session_date, datetime.min.time()).replace(hour=13, minute=30),
            close_values=current_closes,
            volume_value=125.0,
        )

    BetaIntradayFeatureService.refresh_feature_snapshots(
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instrument.id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="ACTIVE_THESIS",
                cadence_minutes=5,
                priority_score=0.8,
                session_state="REGULAR_OPEN",
            )
        ],
        now_utc=datetime(2026, 3, 18, 14, 10, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        snapshot = sess.scalar(
            select(BetaIntradayFeatureSnapshot).where(BetaIntradayFeatureSnapshot.instrument_id == instrument.id)
        )

    assert snapshot is not None
    features = json.loads(snapshot.feature_snapshot_json)
    assert features["minutes_since_open"] == 39
    assert features["minutes_until_close"] == 351
    assert features["session_progress_pct"] == pytest.approx(10.0, abs=1e-6)
    assert features["return_last_30m_pct"] is not None
    assert features["volume_last_30m_vs_expected"] is not None
    assert features["typical_opening_15m_return_pct"] < 0
    assert features["typical_opening_30m_return_pct"] > 0
    assert features["typical_closing_30m_return_pct"] > 0
    assert features["historical_opening_bias_sessions"] == 5
    assert features["historical_closing_bias_sessions"] == 5


def test_execution_signal_service_evaluates_active_thesis_state(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        candidate = BetaSignalCandidate(
            instrument_id=instrument.id,
            symbol="IBM",
            title="IBM daily thesis",
            status="WATCHING",
            direction="BULLISH",
            confidence_score=0.66,
            expected_edge_score=0.52,
            market="US",
            evidence_json=json.dumps(
                {
                    "predicted_return_pct": 3.4,
                    "matched_target_metric": "fwd_3d_excess_return_pct",
                    "trade_expression_plan": {"planned_horizon_days": 3},
                },
                sort_keys=True,
            ),
        )
        sess.add(candidate)
        sess.flush()
        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instrument.id,
                session_date=date(2026, 3, 17),
                session_state="REGULAR_OPEN",
                priority_tier="ACTIVE_THESIS",
                feature_snapshot_json=json.dumps(
                    {
                        "intraday_range_pct": 0.6,
                        "return_since_open_pct": 0.8,
                        "volume_last_15m_vs_expected": 0.7,
                    },
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    sync_result = BetaPositionRegistry.sync_candidate_theses(
        now_utc=datetime(2026, 3, 17, 14, 35, 0, tzinfo=timezone.utc)
    )
    result = BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 17, 14, 36, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        states = list(
            sess.scalars(
                select(BetaPositionState).where(BetaPositionState.position_source == "SIMULATED")
            ).all()
        )
        signals = list(sess.scalars(select(BetaExecutionSignal)).all())

    assert sync_result["states_upserted"] == 1
    assert len(states) == 1
    assert result["positions_evaluated"] == 1
    assert len(signals) == 1
    assert signals[0].signal_type == "WAIT_FOR_CLOSE_CONFIRMATION"


def test_execution_signal_service_emits_execution_only_guidance(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        position_state = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
            thesis_expected_return_pct=4.0,
        )
        snapshot = BetaIntradayFeatureSnapshot(
            instrument_id=instrument.id,
            session_date=date(2026, 3, 16),
            session_state="REGULAR_OPEN",
            priority_tier="HELD",
            feature_snapshot_json=json.dumps(
                {
                    "gap_from_prev_close_pct": 3.0,
                    "return_since_open_pct": 1.0,
                    "return_last_15m_pct": -0.2,
                    "rolling_intraday_vol_15m_pct": 0.8,
                    "distance_from_session_high_pct": 0.4,
                    "distance_from_session_low_pct": 1.6,
                    "reversal_from_low_15m_pct": 0.6,
                    "reversal_from_high_15m_pct": 0.4,
                },
                sort_keys=True,
            ),
            accumulator_state_json=json.dumps({}, sort_keys=True),
        )
        sess.add_all([position_state, snapshot])

    result = BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 0, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        signals = list(sess.scalars(select(BetaExecutionSignal)).all())

    assert result["positions_evaluated"] == 1
    assert len(signals) == 1
    assert signals[0].signal_type == "TRIM_ON_STRENGTH"


def test_execution_signal_service_records_meaningful_state_change_notification(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        sess.add(
            BetaPositionState(
                instrument_id=instrument.id,
                symbol="IBM",
                market="US",
                position_source="DEMO",
                position_status="OPEN",
                thesis_expected_return_pct=4.0,
            )
        )
        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instrument.id,
                session_date=date(2026, 3, 16),
                session_state="REGULAR_OPEN",
                priority_tier="HELD",
                feature_snapshot_json=json.dumps(
                    {
                        "gap_from_prev_close_pct": 3.0,
                        "return_since_open_pct": 1.0,
                        "return_last_15m_pct": -0.2,
                        "rolling_intraday_vol_15m_pct": 0.8,
                        "distance_from_session_high_pct": 0.4,
                        "distance_from_session_low_pct": 1.6,
                        "reversal_from_low_15m_pct": 0.6,
                        "reversal_from_high_15m_pct": 0.4,
                    },
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 0, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        notifications = list(sess.scalars(select(BetaUiNotification)).all())

    assert len(notifications) == 1
    assert notifications[0].notification_type == "execution_signal_state_change"
    assert notifications[0].severity == "WARNING"
    assert notifications[0].target_table == "beta_execution_signals"


def test_execution_signal_service_recognizes_opening_weakness_repair(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        position_state = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
            thesis_expected_return_pct=4.0,
        )
        snapshot = BetaIntradayFeatureSnapshot(
            instrument_id=instrument.id,
            session_date=date(2026, 3, 18),
            session_state="REGULAR_OPEN",
            priority_tier="HELD",
            feature_snapshot_json=json.dumps(
                {
                    "minutes_since_open": 55,
                    "first_15m_return_pct": -1.4,
                    "return_from_first_15m_close_pct": 1.5,
                    "distance_from_vwap_pct": 0.12,
                },
                sort_keys=True,
            ),
            accumulator_state_json=json.dumps({}, sort_keys=True),
        )
        sess.add_all([position_state, snapshot])

    result = BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 18, 14, 40, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        signal = sess.scalar(select(BetaExecutionSignal))

    assert result["positions_evaluated"] == 1
    assert signal is not None
    assert signal.signal_type == "AVOID_SELLING_INTO_PANIC"
    assert signal.recommended_action_side == "BUY"
    assert signal.recommended_action_code == "ADD"


def test_execution_signal_service_recognizes_close_ramp_confirmation(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        position_state = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
            thesis_expected_return_pct=4.0,
        )
        snapshot = BetaIntradayFeatureSnapshot(
            instrument_id=instrument.id,
            session_date=date(2026, 3, 18),
            session_state="REGULAR_OPEN",
            priority_tier="HELD",
            feature_snapshot_json=json.dumps(
                {
                    "minutes_until_close": 20,
                    "historical_closing_bias_sessions": 8,
                    "typical_closing_30m_return_pct": 0.42,
                    "return_last_30m_pct": 1.1,
                    "distance_from_vwap_pct": 0.18,
                    "distance_from_session_high_pct": 0.25,
                },
                sort_keys=True,
            ),
            accumulator_state_json=json.dumps({}, sort_keys=True),
        )
        sess.add_all([position_state, snapshot])

    result = BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 18, 19, 40, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        signal = sess.scalar(select(BetaExecutionSignal))

    assert result["positions_evaluated"] == 1
    assert signal is not None
    assert signal.signal_type == "WAIT_FOR_CLOSE_CONFIRMATION"
    assert signal.recommended_action_side == "WAIT"
    assert signal.recommended_action_code == "CONFIRM"


def test_execution_signal_service_populates_actionable_economic_annotation(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        execution_definition = BetaExecutionHypothesisDefinition(
            hypothesis_code="ECONOMIC_EXIT_TEST",
            name="Economic exit test",
            signal_type="TRIM_ON_STRENGTH",
            entry_conditions_json=json.dumps(
                {"all": [{"feature": "gap_from_prev_close_pct", "op": "gt", "value": 9.0}]},
                sort_keys=True,
            ),
            regime_filters_json=json.dumps({}, sort_keys=True),
            feature_subset_json=json.dumps(["gap_from_prev_close_pct"], sort_keys=True),
            rationale_text="Exit timing should only be actionable with positive avoided-loss history.",
            source_type="MANUAL",
            metadata_json=json.dumps(
                {
                    "base_confidence": 0.8,
                    "event_codes": ["EVENT_OPEN_GAP"],
                    "default_hold_window_minutes": [10, 40],
                },
                sort_keys=True,
            ),
            provenance_json=json.dumps({"source": "MANUAL"}, sort_keys=True),
            status="ACTIVE",
        )
        current_position = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
            thesis_expected_return_pct=4.0,
        )
        sess.add_all([execution_definition, current_position])
        sess.flush()

        for idx in range(30):
            historical_signal = BetaExecutionSignal(
                execution_hypothesis_definition_id=execution_definition.id,
                position_state_id=current_position.id,
                instrument_id=instrument.id,
                symbol="IBM",
                session_date=date(2026, 3, 15),
                signal_time=datetime(2026, 3, 15, 9, 0, 0) + timedelta(minutes=idx),
                session_state="REGULAR_OPEN",
                signal_type="TRIM_ON_STRENGTH",
                confidence_score=0.7,
                event_trigger_code="EVENT_OPEN_GAP",
                matched_conditions_json="[]",
                feature_snapshot_json="{}",
            )
            sess.add(historical_signal)
            sess.flush()
            sess.add(
                BetaExecutionLabelValue(
                    execution_signal_id=historical_signal.id,
                    position_state_id=current_position.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    session_date=date(2026, 3, 15),
                    signal_time=historical_signal.signal_time,
                    action_aligned_return_pct=0.30,
                    time_to_peak_minutes=15 + idx,
                    evaluation_complete=True,
                )
            )

        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instrument.id,
                session_date=date(2026, 3, 16),
                session_state="REGULAR_OPEN",
                priority_tier="HELD",
                feature_snapshot_json=json.dumps(
                    {"gap_from_prev_close_pct": 10.0},
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    result = BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 0, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        signal = sess.scalar(
            select(BetaExecutionSignal)
            .where(BetaExecutionSignal.session_date == date(2026, 3, 16))
            .order_by(BetaExecutionSignal.signal_time.desc())
            .limit(1)
        )

    assert result["positions_evaluated"] == 1
    assert signal is not None
    assert signal.signal_type == "TRIM_ON_STRENGTH"
    assert signal.economic_opportunity_status == "ACTIONABLE"
    assert signal.expected_edge_pct == Decimal("0.3000")
    assert signal.historical_win_rate == Decimal("1.0000")
    assert signal.post_cost_edge_pct == Decimal("0.1800")
    assert signal.economic_annotation_sample_size == 30
    assert signal.expected_hold_minutes is not None
    assert signal.expected_hold_min_minutes is not None
    assert signal.expected_hold_max_minutes is not None
    assert signal.expected_hold_max_minutes >= signal.expected_hold_min_minutes
    assert signal.recommended_action_side == "SELL"
    assert signal.recommended_action_code == "TRIM"


def test_execution_signal_service_marks_insufficient_history_non_actionable(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        execution_definition = BetaExecutionHypothesisDefinition(
            hypothesis_code="ECONOMIC_HOLD_TEST",
            name="Economic hold test",
            signal_type="HOLD_THROUGH_NOISE",
            entry_conditions_json=json.dumps(
                {"all": [{"feature": "gap_from_prev_close_pct", "op": "gt", "value": 11.0}]},
                sort_keys=True,
            ),
            regime_filters_json=json.dumps({}, sort_keys=True),
            feature_subset_json=json.dumps(["gap_from_prev_close_pct"], sort_keys=True),
            rationale_text="Hold guidance needs enough same-structure history.",
            source_type="MANUAL",
            metadata_json=json.dumps(
                {
                    "base_confidence": 0.8,
                    "event_codes": ["EVENT_OPEN_GAP"],
                    "default_hold_window_minutes": [20, 60],
                },
                sort_keys=True,
            ),
            provenance_json=json.dumps({"source": "MANUAL"}, sort_keys=True),
            status="ACTIVE",
        )
        current_position = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
            thesis_expected_return_pct=4.0,
        )
        sess.add_all([execution_definition, current_position])
        sess.flush()

        for idx in range(29):
            historical_signal = BetaExecutionSignal(
                execution_hypothesis_definition_id=execution_definition.id,
                position_state_id=current_position.id,
                instrument_id=instrument.id,
                symbol="IBM",
                session_date=date(2026, 3, 15),
                signal_time=datetime(2026, 3, 15, 9, 0, 0) + timedelta(minutes=idx),
                session_state="REGULAR_OPEN",
                signal_type="HOLD_THROUGH_NOISE",
                confidence_score=0.7,
                event_trigger_code="EVENT_OPEN_GAP",
                matched_conditions_json="[]",
                feature_snapshot_json="{}",
            )
            sess.add(historical_signal)
            sess.flush()
            sess.add(
                BetaExecutionLabelValue(
                    execution_signal_id=historical_signal.id,
                    position_state_id=current_position.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    session_date=date(2026, 3, 15),
                    signal_time=historical_signal.signal_time,
                    action_aligned_return_pct=0.25,
                    time_to_peak_minutes=20 + idx,
                    evaluation_complete=True,
                )
            )

        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instrument.id,
                session_date=date(2026, 3, 16),
                session_state="REGULAR_OPEN",
                priority_tier="HELD",
                feature_snapshot_json=json.dumps(
                    {"gap_from_prev_close_pct": 12.0},
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 0, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        signal = sess.scalar(
            select(BetaExecutionSignal)
            .where(BetaExecutionSignal.session_date == date(2026, 3, 16))
            .order_by(BetaExecutionSignal.signal_time.desc())
            .limit(1)
        )

    assert signal is not None
    assert signal.signal_type == "HOLD_THROUGH_NOISE"
    assert signal.economic_opportunity_status == "NON_ACTIONABLE"
    assert signal.expected_edge_pct is None
    assert signal.historical_win_rate is None
    assert signal.post_cost_edge_pct is None
    assert signal.economic_annotation_sample_size == 29
    assert signal.economic_non_actionable_reason == "insufficient_sample_lt_30"
    assert signal.expected_hold_minutes == "[20, 60]"
    assert signal.expected_hold_min_minutes == 20
    assert signal.expected_hold_max_minutes == 60
    assert signal.recommended_action_side == "BUY"
    assert signal.recommended_action_code == "ADD"


def test_intraday_outlook_service_captures_current_state_and_annotates_expected_returns(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_annotation_min_sample_size = 30
    settings.intraday_execution_hypothesis_min_matched_instruments = 1
    settings.intraday_outlook_actionable_min_matched_instruments = 4
    settings.intraday_outlook_actionable_min_win_rate = 0.55
    settings.intraday_outlook_actionable_min_post_cost_edge_pct = 0.10
    settings.intraday_outlook_actionable_min_median_return_pct = 0.05
    settings.intraday_outlook_actionable_max_single_instrument_share = 0.35
    settings.intraday_outlook_actionable_require_exact_state_match = True

    with BetaContext.write_session() as sess:
        instruments = []
        for symbol in ["IBM", "AAPL", "MSFT", "NVDA", "META"]:
            instrument = BetaInstrument(
                symbol=symbol,
                name=symbol,
                market="US",
                exchange="NASDAQ",
                currency="USD",
            )
            sess.add(instrument)
            instruments.append(instrument)
        sess.flush()

        for idx in range(30):
            instrument = instruments[idx % len(instruments)]
            observed_at = datetime(2026, 3, 15, 14, 30, 0) + timedelta(minutes=idx)
            observation = BetaIntradayFeatureObservation(
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                session_date=date(2026, 3, 15),
                observed_at=observed_at,
                session_state="REGULAR_OPEN",
                priority_tier="BACKFILL",
                state_code="OPEN__GAP_DOWN_RECOVERY",
                state_family_code="GAP_DOWN_RECOVERY",
                state_label="Open / Gap-down recovery",
                signal_type="HOLD_THROUGH_NOISE",
                rationale_text="Historical recovery state",
                feature_snapshot_json=json.dumps(
                    {
                        "gap_from_prev_close_pct": -3.5,
                        "reversal_from_low_15m_pct": 1.2,
                        "distance_from_vwap_pct": 0.1,
                        "session_progress_pct": 10.0,
                    },
                    sort_keys=True,
                ),
            )
            sess.add(observation)
            sess.flush()
            sess.add(
                BetaIntradayFeatureLabelValue(
                    observation_id=observation.id,
                    instrument_id=instrument.id,
                    symbol=instrument.symbol,
                    session_date=date(2026, 3, 15),
                    observed_at=observed_at,
                    future_15m_return_pct=0.22,
                    future_30m_return_pct=0.35,
                    close_return_pct=0.40,
                    max_adverse_move_pct=-0.08,
                    max_favorable_move_pct=0.50,
                    time_to_peak_minutes=18,
                    evaluation_complete=True,
                )
            )

        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instruments[0].id,
                session_date=date(2026, 3, 16),
                session_state="REGULAR_OPEN",
                priority_tier="HELD",
                last_minute_ts=datetime(2026, 3, 16, 14, 45, 0),
                feature_snapshot_json=json.dumps(
                    {
                        "gap_from_prev_close_pct": -3.2,
                        "return_since_open_pct": -0.4,
                        "return_last_15m_pct": -1.0,
                        "reversal_from_low_15m_pct": 1.1,
                        "reversal_from_high_15m_pct": 0.2,
                        "distance_from_vwap_pct": 0.05,
                        "session_progress_pct": 12.0,
                        "minutes_until_close": 360,
                    },
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    result = BetaIntradayOutlookService.capture_current_observations(
        settings,
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instruments[0].id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="HELD",
                cadence_minutes=3,
                priority_score=1.0,
                session_state="REGULAR_OPEN",
            )
        ],
        now_utc=datetime(2026, 3, 16, 14, 45, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        latest = sess.scalar(
            select(BetaIntradayFeatureObservation)
            .where(BetaIntradayFeatureObservation.session_date == date(2026, 3, 16))
            .order_by(BetaIntradayFeatureObservation.observed_at.desc())
            .limit(1)
        )

    assert result["observations_written"] == 1
    assert latest is not None
    assert latest.state_family_code == "GAP_DOWN_RECOVERY"
    assert latest.opportunity_status == "ACTIONABLE"
    assert latest.expected_return_15m_pct == Decimal("0.2200")
    assert latest.expected_return_30m_pct == Decimal("0.3500")
    assert latest.post_cost_expected_return_15m_pct is not None
    assert latest.historical_win_rate == Decimal("1.0000")
    assert latest.outlook_sample_size == 30
    assert latest.matched_instrument_count == 5
    assert latest.confidence_label in {"MEDIUM", "HIGH"}
    assert latest.recommended_action_side == "BUY"
    assert latest.recommended_action_code == "ADD"


def test_intraday_outlook_service_keeps_weak_median_bias_informational(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_annotation_min_sample_size = 30
    settings.intraday_execution_hypothesis_min_matched_instruments = 1
    settings.intraday_outlook_actionable_min_matched_instruments = 4
    settings.intraday_outlook_actionable_min_win_rate = 0.55
    settings.intraday_outlook_actionable_min_post_cost_edge_pct = 0.10
    settings.intraday_outlook_actionable_min_median_return_pct = 0.05
    settings.intraday_outlook_actionable_max_single_instrument_share = 0.35
    settings.intraday_outlook_actionable_require_exact_state_match = True

    with BetaContext.write_session() as sess:
        instruments = []
        for symbol in ["IBM", "AAPL", "MSFT", "NVDA"]:
            instrument = BetaInstrument(
                symbol=symbol,
                name=symbol,
                market="US",
                exchange="NASDAQ",
                currency="USD",
            )
            sess.add(instrument)
            instruments.append(instrument)
        sess.flush()

        for idx in range(30):
            instrument = instruments[idx % len(instruments)]
            observed_at = datetime(2026, 3, 15, 14, 30, 0) + timedelta(minutes=idx)
            future_15m = 0.04 if idx < 16 else 0.45
            observation = BetaIntradayFeatureObservation(
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                session_date=date(2026, 3, 15),
                observed_at=observed_at,
                session_state="REGULAR_OPEN",
                priority_tier="BACKFILL",
                state_code="MIDDAY__OPENING_RANGE_BREAKDOWN",
                state_family_code="OPENING_RANGE_BREAKDOWN",
                state_label="Midday / Opening-range breakdown",
                signal_type="SELL_INTO_REBOUND",
                rationale_text="Historical breakdown state",
                feature_snapshot_json=json.dumps(
                    {
                        "return_since_open_pct": -0.8,
                        "return_last_15m_pct": -0.6,
                        "breakdown_below_first_30m_low_pct": 0.35,
                        "session_progress_pct": 44.0,
                    },
                    sort_keys=True,
                ),
            )
            sess.add(observation)
            sess.flush()
            sess.add(
                BetaIntradayFeatureLabelValue(
                    observation_id=observation.id,
                    instrument_id=instrument.id,
                    symbol=instrument.symbol,
                    session_date=date(2026, 3, 15),
                    observed_at=observed_at,
                    future_15m_return_pct=future_15m,
                    future_30m_return_pct=future_15m,
                    close_return_pct=future_15m,
                    max_adverse_move_pct=-0.10,
                    max_favorable_move_pct=0.55,
                    time_to_peak_minutes=20,
                    evaluation_complete=True,
                )
            )

        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instruments[0].id,
                session_date=date(2026, 3, 16),
                session_state="REGULAR_OPEN",
                priority_tier="HELD",
                last_minute_ts=datetime(2026, 3, 16, 16, 15, 0),
                feature_snapshot_json=json.dumps(
                    {
                        "return_since_open_pct": -0.7,
                        "return_last_15m_pct": -0.5,
                        "breakdown_below_first_30m_low_pct": 0.30,
                        "session_progress_pct": 45.0,
                        "minutes_until_close": 225,
                    },
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    BetaIntradayOutlookService.capture_current_observations(
        settings,
        priority_items=[
            IntradayPriorityItem(
                instrument_id=instruments[0].id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="HELD",
                cadence_minutes=3,
                priority_score=1.0,
                session_state="REGULAR_OPEN",
            )
        ],
        now_utc=datetime(2026, 3, 16, 16, 15, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        latest = sess.scalar(
            select(BetaIntradayFeatureObservation)
            .where(BetaIntradayFeatureObservation.session_date == date(2026, 3, 16))
            .order_by(BetaIntradayFeatureObservation.observed_at.desc())
            .limit(1)
        )

    assert latest is not None
    assert latest.opportunity_status == "INFORMATIONAL"
    assert latest.non_actionable_reason == "median_edge_below_floor"
    assert latest.post_cost_expected_return_15m_pct == Decimal("0.1113")
    assert latest.recommended_action_side == "HOLD"
    assert latest.recommended_action_code == "NO_ACTION"


def test_execution_signal_service_only_alerts_on_state_change_or_band_change(beta_context):
    settings = BetaSettings()
    settings.intraday_execution_enabled = True
    settings.intraday_event_trigger_enabled = True

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
        sess.add(
            BetaPositionState(
                instrument_id=instrument.id,
                symbol="IBM",
                market="US",
                position_source="DEMO",
                position_status="OPEN",
                thesis_expected_return_pct=4.0,
            )
        )
        sess.add(
            BetaIntradayFeatureSnapshot(
                instrument_id=instrument.id,
                session_date=date(2026, 3, 16),
                session_state="REGULAR_OPEN",
                priority_tier="HELD",
                feature_snapshot_json=json.dumps(
                    {
                        "gap_from_prev_close_pct": 3.0,
                        "return_since_open_pct": 1.0,
                        "return_last_15m_pct": -0.2,
                        "rolling_intraday_vol_15m_pct": 0.8,
                        "distance_from_session_high_pct": 0.4,
                        "distance_from_session_low_pct": 1.6,
                        "reversal_from_low_15m_pct": 0.6,
                        "reversal_from_high_15m_pct": 0.4,
                    },
                    sort_keys=True,
                ),
                accumulator_state_json=json.dumps({}, sort_keys=True),
            )
        )

    BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 0, 0, tzinfo=timezone.utc),
    )
    BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 4, 0, tzinfo=timezone.utc),
    )

    with BetaContext.write_session() as sess:
        snapshot = sess.scalar(select(BetaIntradayFeatureSnapshot))
        assert snapshot is not None
        snapshot.feature_snapshot_json = json.dumps(
            {
                "gap_from_prev_close_pct": 0.5,
                "return_since_open_pct": 2.2,
                "return_last_15m_pct": 0.5,
                "rolling_intraday_vol_15m_pct": 0.7,
                "distance_from_session_high_pct": 0.1,
                "distance_from_session_low_pct": 0.8,
                "reversal_from_low_15m_pct": 0.4,
                "reversal_from_high_15m_pct": 0.2,
            },
            sort_keys=True,
        )

    BetaExecutionSignalService.evaluate_execution_signals(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 8, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        notifications = list(
            sess.scalars(
                select(BetaUiNotification).order_by(BetaUiNotification.created_at.asc())
            ).all()
        )
        signals = list(
            sess.scalars(
                select(BetaExecutionSignal).order_by(BetaExecutionSignal.signal_time.asc())
            ).all()
        )

    assert len(signals) == 3
    assert len(notifications) == 2
    assert notifications[0].title.endswith("TRIM_ON_STRENGTH")
    assert notifications[1].title.endswith("HOLD_THROUGH_NOISE")


def test_execution_outcome_service_writes_future_return_labels(beta_context):
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        position_state = BetaPositionState(
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
        )
        sess.add_all([instrument, position_state])
        sess.flush()
        signal = BetaExecutionSignal(
            position_state_id=position_state.id,
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 16),
            signal_time=datetime(2026, 3, 16, 14, 30, 0),
            session_state="REGULAR_OPEN",
            signal_type="HOLD_THROUGH_NOISE",
            confidence_score=0.6,
            matched_conditions_json="[]",
            feature_snapshot_json="{}",
        )
        sess.add(signal)
        sess.flush()
        prices = [100, 101, 102, 104]
        times = [datetime(2026, 3, 16, 14, 30), datetime(2026, 3, 16, 15, 0), datetime(2026, 3, 16, 15, 30), datetime(2026, 3, 16, 16, 30)]
        for ts, price in zip(times, prices):
            sess.add(
                BetaMinuteBar(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 16),
                    minute_ts=ts,
                    open_price_gbp=str(price),
                    high_price_gbp=str(price),
                    low_price_gbp=str(price),
                    close_price_gbp=str(price),
                    close_price_native=str(price),
                    currency="USD",
                    snapshot_count=1,
                    first_snapshot_at=ts,
                    last_snapshot_at=ts,
                    source="test",
                )
            )

    result = BetaExecutionOutcomeService.update_execution_outcomes()

    with BetaContext.read_session() as sess:
        label = sess.scalar(select(BetaExecutionLabelValue))

    assert result["labels_written"] == 1
    assert label is not None
    assert round(label.future_30m_return_pct or 0.0, 2) == 1.00
    assert round(label.future_60m_return_pct or 0.0, 2) == 2.00
    assert round(label.future_120m_return_pct or 0.0, 2) == 4.00
    assert round(label.action_aligned_return_pct or 0.0, 2) == 4.00
    assert label.time_to_peak_minutes == 120


def test_execution_outcome_service_records_action_aligned_peak_for_exit_signals(beta_context):
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        position_state = BetaPositionState(
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
        )
        sess.add_all([instrument, position_state])
        sess.flush()
        signal = BetaExecutionSignal(
            position_state_id=position_state.id,
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 16),
            signal_time=datetime(2026, 3, 16, 14, 30, 0),
            session_state="REGULAR_OPEN",
            signal_type="TRIM_ON_STRENGTH",
            confidence_score=0.6,
            matched_conditions_json="[]",
            feature_snapshot_json="{}",
        )
        sess.add(signal)
        sess.flush()
        for ts, price in zip(
            [
                datetime(2026, 3, 16, 14, 30),
                datetime(2026, 3, 16, 15, 0),
                datetime(2026, 3, 16, 15, 30),
                datetime(2026, 3, 16, 16, 0),
            ],
            [100, 101, 99, 97],
        ):
            sess.add(
                BetaMinuteBar(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 16),
                    minute_ts=ts,
                    open_price_gbp=str(price),
                    high_price_gbp=str(price),
                    low_price_gbp=str(price),
                    close_price_gbp=str(price),
                    close_price_native=str(price),
                    currency="USD",
                    snapshot_count=1,
                    first_snapshot_at=ts,
                    last_snapshot_at=ts,
                    source="test",
                )
            )

    BetaExecutionOutcomeService.update_execution_outcomes()

    with BetaContext.read_session() as sess:
        label = sess.scalar(select(BetaExecutionLabelValue))

    assert label is not None
    assert round(label.action_aligned_return_pct or 0.0, 2) == 3.00
    assert label.time_to_peak_minutes == 90


def test_execution_outcome_service_syncs_realized_return_back_to_signal_observation(beta_context):
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        definition = BetaHypothesisDefinition(
            hypothesis_code="IBM_TEST_HYPOTHESIS",
            name="IBM Test Hypothesis",
            universe_json=json.dumps({"markets": ["US"]}, sort_keys=True),
            entry_conditions_json=json.dumps({}, sort_keys=True),
            holding_period_days=3,
            target_metric="fwd_3d_excess_return_pct",
            expected_direction="BULLISH",
            status="VALIDATED",
        )
        sess.add_all([instrument, definition])
        sess.flush()
        observation = BetaSignalObservation(
            hypothesis_definition_id=definition.id,
            instrument_id=instrument.id,
            symbol="IBM",
            decision_date=date(2026, 3, 16),
            observation_time=datetime(2026, 3, 16, 14, 25, 0),
            matched_conditions_json=json.dumps([], sort_keys=True),
            feature_snapshot_json=json.dumps({}, sort_keys=True),
            regime_context_json=json.dumps({"market": "US"}, sort_keys=True),
            expected_direction="BULLISH",
            expected_return_pct=3.0,
            observation_status="MATCHED",
        )
        sess.add(observation)
        sess.flush()
        candidate = BetaSignalCandidate(
            instrument_id=instrument.id,
            hypothesis_definition_id=definition.id,
            signal_observation_id=observation.id,
            symbol="IBM",
            title="IBM thesis",
            status="WATCHING",
            direction="BULLISH",
            confidence_score=0.62,
            expected_edge_score=0.44,
            market="US",
            evidence_json=json.dumps({"predicted_return_pct": 3.0}, sort_keys=True),
        )
        sess.add(candidate)
        sess.flush()
        position_state = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="SIMULATED",
            position_status="OPEN",
            thesis_candidate_id=candidate.id,
            thesis_hypothesis_definition_id=definition.id,
        )
        sess.add(position_state)
        sess.flush()
        signal = BetaExecutionSignal(
            position_state_id=position_state.id,
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 16),
            signal_time=datetime(2026, 3, 16, 14, 30, 0),
            session_state="REGULAR_OPEN",
            signal_type="HOLD_THROUGH_NOISE",
            confidence_score=0.6,
            matched_conditions_json="[]",
            feature_snapshot_json="{}",
        )
        sess.add(signal)
        sess.flush()
        for ts, price in zip(
            [
                datetime(2026, 3, 16, 14, 30),
                datetime(2026, 3, 16, 15, 0),
                datetime(2026, 3, 16, 15, 30),
                datetime(2026, 3, 16, 16, 30),
            ],
            [100, 101, 102, 104],
        ):
            sess.add(
                BetaMinuteBar(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 16),
                    minute_ts=ts,
                    open_price_gbp=str(price),
                    high_price_gbp=str(price),
                    low_price_gbp=str(price),
                    close_price_gbp=str(price),
                    close_price_native=str(price),
                    currency="USD",
                    snapshot_count=1,
                    first_snapshot_at=ts,
                    last_snapshot_at=ts,
                    source="test",
                )
            )

    result = BetaExecutionOutcomeService.update_execution_outcomes()

    with BetaContext.read_session() as sess:
        refreshed = sess.scalar(select(BetaSignalObservation).where(BetaSignalObservation.id == observation.id))

    assert result["labels_written"] == 1
    assert result["observations_updated"] == 1
    assert refreshed is not None
    assert round(refreshed.realized_return_pct or 0.0, 2) == 4.00
    context = json.loads(refreshed.regime_context_json or "{}")
    assert context["execution_feedback"]["candidate_id"] == candidate.id


def test_intraday_focus_watchlist_prefers_configured_large_caps(beta_context):
    settings = BetaSettings()
    settings.intraday_focus_us_symbol_cap = 2
    settings.intraday_focus_uk_symbol_cap = 1

    with BetaContext.write_session() as sess:
        sess.add_all(
            [
                BetaInstrument(symbol="IBM", name="IBM", market="US", exchange="NYSE", currency="USD"),
                BetaInstrument(symbol="AAPL", name="Apple", market="US", exchange="NASDAQ", currency="USD"),
                BetaInstrument(symbol="AZN", name="AstraZeneca", market="UK", exchange="LSE", currency="GBP"),
                BetaInstrument(symbol="SMALL", name="Small Cap", market="US", exchange="NASDAQ", currency="USD"),
            ]
        )

    focus = BetaIntradayPriorityService.build_focus_watchlist(
        settings,
        now_utc=datetime(2026, 3, 16, 15, 0, tzinfo=timezone.utc),
    )

    symbols = [item.symbol for item in focus["items"]]
    assert "IBM" in symbols
    assert "AAPL" in symbols
    assert "AZN" in symbols
    assert all(item.tier == "FOCUS" for item in focus["items"])


def test_intraday_outlook_history_rebuild_can_filter_to_specific_instruments(beta_context):
    settings = BetaSettings()

    with BetaContext.write_session() as sess:
        ibm = BetaInstrument(symbol="IBM", name="IBM", market="US", exchange="NYSE", currency="USD")
        aapl = BetaInstrument(symbol="AAPL", name="Apple", market="US", exchange="NASDAQ", currency="USD")
        sess.add_all([ibm, aapl])
        sess.flush()
        _add_minute_bar_series(
            sess,
            instrument_id=ibm.id,
            session_date=date(2026, 3, 16),
            start_ts=datetime(2026, 3, 16, 14, 30),
            close_values=[100.0, 100.05, 100.10, 100.08, 100.12, 100.15],
            volume_value=1000.0,
        )
        _add_minute_bar_series(
            sess,
            instrument_id=aapl.id,
            session_date=date(2026, 3, 16),
            start_ts=datetime(2026, 3, 16, 14, 30),
            close_values=[200.0, 199.9, 199.8, 199.85, 199.9, 199.95],
            volume_value=1200.0,
        )

    result = BetaIntradayOutlookService.rebuild_recent_history(
        settings,
        target_days=30,
        instrument_ids=[ibm.id],
    )

    with BetaContext.read_session() as sess:
        observations = list(
            sess.scalars(
                select(BetaIntradayFeatureObservation).order_by(BetaIntradayFeatureObservation.symbol.asc())
            ).all()
        )

    assert result["sessions_backfilled"] == 1
    assert observations
    assert {row.symbol for row in observations} == {"IBM"}


def test_intraday_outlook_label_updates_only_incomplete_rows_by_default(beta_context):
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        complete_observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 15),
            observed_at=datetime(2026, 3, 15, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="BACKFILL",
            state_code="COMPLETE_STATE",
            state_family_code="COMPLETE_STATE",
            state_label="Complete state",
            rationale_text="existing complete observation",
            feature_snapshot_json=json.dumps({}, sort_keys=True),
        )
        pending_observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 16),
            observed_at=datetime(2026, 3, 16, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="BACKFILL",
            state_code="PENDING_STATE",
            state_family_code="PENDING_STATE",
            state_label="Pending state",
            rationale_text="pending observation",
            feature_snapshot_json=json.dumps({}, sort_keys=True),
        )
        sess.add_all([complete_observation, pending_observation])
        sess.flush()
        sess.add(
            BetaIntradayFeatureLabelValue(
                observation_id=complete_observation.id,
                instrument_id=instrument.id,
                symbol="IBM",
                session_date=complete_observation.session_date,
                observed_at=complete_observation.observed_at,
                future_15m_return_pct=9.99,
                close_return_pct=9.99,
                evaluation_complete=True,
            )
        )
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=pending_observation.session_date,
            start_ts=pending_observation.observed_at,
            close_values=[100.0, 100.2, 100.4, 100.5, 100.6, 100.7],
            volume_value=1000.0,
        )

    result = BetaIntradayOutlookService.update_outcome_labels(instrument_ids=[instrument.id])

    with BetaContext.read_session() as sess:
        labels = list(
            sess.scalars(
                select(BetaIntradayFeatureLabelValue).order_by(BetaIntradayFeatureLabelValue.observed_at.asc())
            ).all()
        )

    assert result["observations_evaluated"] == 1
    assert result["labels_written"] == 1
    assert len(labels) == 2
    assert round(labels[0].future_15m_return_pct or 0.0, 2) == 9.99
    assert labels[0].evaluation_complete is True
    assert labels[1].observation_id == pending_observation.id
    assert labels[1].future_5m_return_pct is not None
    assert labels[1].close_return_pct is not None


def test_intraday_outlook_history_rebuild_refreshes_dependent_later_observations(beta_context, monkeypatch):
    settings = BetaSettings()

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        existing_later_observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 17),
            observed_at=datetime(2026, 3, 17, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="BACKFILL",
            state_code="LATER_STATE",
            state_family_code="LATER_STATE",
            state_label="Later state",
            rationale_text="later observation",
            feature_snapshot_json=json.dumps({}, sort_keys=True),
        )
        sess.add(existing_later_observation)
        sess.flush()
        later_observation_id = existing_later_observation.id
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=date(2026, 3, 16),
            start_ts=datetime(2026, 3, 16, 14, 30),
            close_values=[100.0, 100.05, 100.1, 100.15, 100.2, 100.25],
            volume_value=1000.0,
        )

    refresh_calls: dict[str, object] = {}
    original_refresh = BetaIntradayOutlookService.refresh_outlook_annotations

    def _spy_refresh(settings_arg, *, observation_ids=None, instrument_ids=None):
        refresh_calls["observation_ids"] = list(observation_ids or [])
        refresh_calls["instrument_ids"] = instrument_ids
        return original_refresh(
            settings_arg,
            observation_ids=observation_ids,
            instrument_ids=instrument_ids,
        )

    monkeypatch.setattr(
        BetaIntradayOutlookService,
        "refresh_outlook_annotations",
        staticmethod(_spy_refresh),
    )

    result = BetaIntradayOutlookService.rebuild_recent_history(
        settings,
        target_days=30,
        instrument_ids=[instrument.id],
    )

    assert refresh_calls["observation_ids"]
    assert refresh_calls["instrument_ids"] is None
    assert later_observation_id in refresh_calls["observation_ids"]
    assert result["observations_updated"] == len(refresh_calls["observation_ids"])


def test_execution_signal_service_limits_outlook_label_updates_to_watchlist(beta_context, monkeypatch):
    settings = BetaSettings()
    settings.intraday_bar_fetch_enabled = False

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        other = BetaInstrument(
            symbol="AAPL",
            name="Apple",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        sess.add_all([instrument, other])
        sess.flush()

    watch_item = IntradayPriorityItem(
        instrument_id=instrument.id,
        symbol="IBM",
        market="US",
        exchange="NYSE",
        tier="HELD",
        cadence_minutes=3,
        priority_score=1.0,
        session_state="REGULAR_OPEN",
    )
    monkeypatch.setattr(
        BetaPositionRegistry,
        "sync_core_portfolio_positions",
        staticmethod(lambda now_utc=None: {"states_upserted": 0}),
    )
    monkeypatch.setattr(
        BetaPositionRegistry,
        "sync_demo_positions",
        staticmethod(lambda now_utc=None: {"states_upserted": 0}),
    )
    monkeypatch.setattr(
        BetaPositionRegistry,
        "sync_candidate_theses",
        staticmethod(lambda now_utc=None: {"states_upserted": 0}),
    )
    monkeypatch.setattr(
        BetaIntradayPriorityService,
        "build_watchlist",
        staticmethod(lambda settings_arg, now_utc=None: {"items": [watch_item], "held": 1, "active_thesis": 0, "general": 0}),
    )
    monkeypatch.setattr(
        BetaIntradayPriorityService,
        "build_focus_watchlist",
        staticmethod(lambda settings_arg, now_utc=None: {"items": []}),
    )
    monkeypatch.setattr(
        BetaObservationService,
        "sync_intraday_snapshots",
        staticmethod(lambda instrument_ids=None: None),
    )
    monkeypatch.setattr(
        BetaIntradayAggregationService,
        "aggregate_minute_bars",
        staticmethod(lambda instrument_ids=None, lookback_minutes=None: None),
    )
    monkeypatch.setattr(
        BetaIntradayFeatureService,
        "refresh_feature_snapshots",
        staticmethod(lambda priority_items=None, now_utc=None: None),
    )
    monkeypatch.setattr(
        BetaIntradayOutlookService,
        "capture_current_observations",
        staticmethod(lambda settings_arg, priority_items=None, now_utc=None: {"observations_written": 0, "outlooks_annotated": 0}),
    )

    captured: dict[str, object] = {}

    def _capture_update(*, observation_ids=None, instrument_ids=None):
        captured["observation_ids"] = observation_ids
        captured["instrument_ids"] = list(instrument_ids or [])
        return {"labels_written": 0, "observations_evaluated": 0}

    monkeypatch.setattr(
        BetaIntradayOutlookService,
        "update_outcome_labels",
        staticmethod(_capture_update),
    )

    result = BetaExecutionSignalService.prepare_execution_context(
        settings,
        now_utc=datetime(2026, 3, 19, 14, 30, tzinfo=timezone.utc),
    )

    assert result["prepared_items_total"] == 1
    assert captured["observation_ids"] is None
    assert captured["instrument_ids"] == [instrument.id]


def test_intraday_focus_backfill_service_builds_focus_evidence_from_existing_history(beta_context):
    settings = BetaSettings()
    settings.intraday_focus_us_symbol_cap = 1
    settings.intraday_focus_uk_symbol_cap = 1
    settings.intraday_focus_backfill_target_days = 45
    settings.intraday_focus_backfill_stage_days = 15
    settings.intraday_focus_backfill_credits_budget = 2
    settings.intraday_bar_fetch_enabled = False

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=date(2026, 3, 10),
            start_ts=datetime(2026, 3, 10, 14, 30),
            close_values=[100.0, 100.2, 100.15, 100.25, 100.3, 100.35],
            volume_value=1000.0,
        )
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=date(2026, 3, 11),
            start_ts=datetime(2026, 3, 11, 14, 30),
            close_values=[100.4, 100.35, 100.3, 100.32, 100.38, 100.45],
            volume_value=1000.0,
        )

    result = BetaIntradayFocusBackfillService.backfill_reasonable_history(
        settings,
        now_utc=datetime(2026, 3, 19, 7, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        observation_count = int(
            sess.scalar(
                select(func.count())
                .select_from(BetaIntradayFeatureObservation)
                .where(BetaIntradayFeatureObservation.symbol == "IBM")
            )
            or 0
        )

    assert result["focus_items"] == 1
    assert result["stage_target_days"] == 15
    assert result["outlook_history"]["observations_written"] > 0
    assert observation_count > 0


def test_intraday_outlook_action_guidance_downgrades_directionally_wrong_buy(beta_context):
    observation = BetaIntradayFeatureObservation(
        symbol="IBM",
        session_date=date(2026, 3, 19),
        observed_at=datetime(2026, 3, 19, 14, 30),
        session_state="REGULAR_OPEN",
        priority_tier="FOCUS",
        signal_type="AVOID_SELLING_INTO_PANIC",
        expected_return_15m_pct=Decimal("-0.2500"),
        post_cost_expected_return_15m_pct=Decimal("-0.3500"),
        opportunity_status="ACTIONABLE",
        confidence_reasons_json=json.dumps({"reason_codes": ["exact_state_match"]}, sort_keys=True),
    )

    BetaIntradayOutlookService._apply_action_guidance(observation, definition=None)

    assert observation.recommended_action_side == "WAIT"
    assert observation.recommended_action_code == "AVOID_ENTRY"
    assert observation.opportunity_status == "INFORMATIONAL"
    assert observation.non_actionable_reason == "action_direction_mismatch"
    reasons = json.loads(observation.confidence_reasons_json or "{}")
    assert "action_direction_mismatch" in reasons.get("failed_reason_codes", [])


def test_intraday_simulated_trade_history_rebuild_creates_closed_trade(beta_context):
    settings = BetaSettings()
    settings.intraday_short_trade_history_days = 365
    settings.intraday_focus_us_symbol_cap = 1
    settings.intraday_focus_uk_symbol_cap = 1

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        entry_observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 16),
            observed_at=datetime(2026, 3, 16, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="FOCUS",
            state_code="OPEN__GAP_DOWN_RECOVERY",
            state_family_code="GAP_DOWN_RECOVERY",
            state_label="Open / Gap-down recovery",
            signal_type="AVOID_SELLING_INTO_PANIC",
            recommended_action_side="BUY",
            recommended_action_code="ENTER",
            recommended_action_label="Buy panic recovery",
            feature_snapshot_json=json.dumps({"distance_from_vwap_pct": 0.2}, sort_keys=True),
            expected_return_15m_pct=Decimal("0.4500"),
            expected_return_30m_pct=Decimal("0.5200"),
            post_cost_expected_return_15m_pct=Decimal("0.3200"),
            historical_win_rate=Decimal("0.5900"),
            confidence_score=0.74,
            confidence_label="HIGH",
            confidence_reasons_json=json.dumps(
                {
                    "exact_state_match": True,
                    "top_symbol_share": 0.20,
                    "p25_15m_return_pct": -0.18,
                    "p75_15m_return_pct": 0.90,
                },
                sort_keys=True,
            ),
            outlook_sample_size=88,
            matched_instrument_count=9,
            opportunity_status="ACTIONABLE",
        )
        exit_observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 16),
            observed_at=datetime(2026, 3, 16, 14, 50),
            session_state="REGULAR_OPEN",
            priority_tier="FOCUS",
            state_code="MIDDAY__BULLISH_VWAP_HOLD",
            state_family_code="BULLISH_VWAP_HOLD",
            state_label="Midday / Bullish VWAP hold",
            signal_type="TRIM_ON_STRENGTH",
            recommended_action_side="SELL",
            recommended_action_code="EXIT",
            recommended_action_label="Sell into strength",
            feature_snapshot_json=json.dumps({"distance_from_vwap_pct": 0.4}, sort_keys=True),
            expected_return_15m_pct=Decimal("0.1200"),
            expected_return_30m_pct=Decimal("0.0500"),
            post_cost_expected_return_15m_pct=Decimal("-0.0100"),
            historical_win_rate=Decimal("0.5100"),
            confidence_score=0.52,
            confidence_label="MEDIUM",
            confidence_reasons_json=json.dumps({"exact_state_match": True, "top_symbol_share": 0.20}, sort_keys=True),
            outlook_sample_size=90,
            matched_instrument_count=9,
            opportunity_status="INFORMATIONAL",
        )
        sess.add_all([entry_observation, exit_observation])
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=date(2026, 3, 16),
            start_ts=datetime(2026, 3, 16, 14, 30),
            close_values=[
                100.0,
                100.1,
                100.2,
                100.3,
                100.5,
                100.7,
                100.85,
                100.92,
                100.95,
                100.98,
                101.0,
                101.02,
                101.05,
                101.08,
                101.1,
                101.12,
                101.1,
                101.08,
                101.05,
                101.0,
                100.98,
            ],
            volume_value=1000.0,
        )

    result = BetaIntradaySimulatedTradeService.rebuild_recent_history(settings, target_days=365)

    with BetaContext.read_session() as sess:
        trade = sess.scalar(select(BetaIntradaySimulatedTrade))
        events = list(
            sess.scalars(
                select(BetaIntradaySimulatedTradeEvent).order_by(BetaIntradaySimulatedTradeEvent.event_time.asc())
            ).all()
        )

    assert result["trades_written"] == 1
    assert trade is not None
    assert trade.symbol == "IBM"
    assert trade.simulation_source == "HISTORICAL_BACKFILL"
    assert trade.status == "CLOSED"
    assert trade.realized_return_pct is not None
    assert trade.exit_reason_code in {"TARGET_HIT", "GUIDANCE_EXIT", "WEAKENING_EXIT"}
    assert len(events) == 2
    assert events[0].event_type == "OPENED"


def test_intraday_simulated_trade_live_refresh_opens_then_closes(beta_context):
    settings = BetaSettings()
    settings.intraday_focus_us_symbol_cap = 1
    settings.intraday_focus_uk_symbol_cap = 1

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        entry_observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 19),
            observed_at=datetime(2026, 3, 19, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="FOCUS",
            state_code="OPEN__SHAKEOUT_RECOVERY",
            state_family_code="SHAKEOUT_RECOVERY",
            state_label="Open / Shakeout recovery",
            signal_type="AVOID_SELLING_INTO_PANIC",
            recommended_action_side="BUY",
            recommended_action_code="ENTER",
            recommended_action_label="Buy panic recovery",
            feature_snapshot_json=json.dumps({}, sort_keys=True),
            expected_return_15m_pct=Decimal("0.4200"),
            expected_return_30m_pct=Decimal("0.5000"),
            post_cost_expected_return_15m_pct=Decimal("0.3000"),
            historical_win_rate=Decimal("0.5700"),
            confidence_score=0.70,
            confidence_label="HIGH",
            confidence_reasons_json=json.dumps(
                {
                    "exact_state_match": True,
                    "top_symbol_share": 0.20,
                    "p25_15m_return_pct": -0.15,
                    "p75_15m_return_pct": 0.70,
                },
                sort_keys=True,
            ),
            outlook_sample_size=72,
            matched_instrument_count=8,
            opportunity_status="ACTIONABLE",
        )
        sess.add(entry_observation)
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=date(2026, 3, 19),
            start_ts=datetime(2026, 3, 19, 14, 30),
            close_values=[100.0, 100.05, 100.10, 100.16, 100.20],
            volume_value=1000.0,
        )

    first = BetaIntradaySimulatedTradeService.refresh_live_trades(
        settings,
        now_utc=datetime(2026, 3, 19, 14, 34, tzinfo=timezone.utc),
    )

    with BetaContext.write_session() as sess:
        instrument_id = sess.scalar(select(BetaInstrument.id).where(BetaInstrument.symbol == "IBM"))
        sess.add(
            BetaIntradayFeatureObservation(
                instrument_id=instrument_id,
                symbol="IBM",
                session_date=date(2026, 3, 19),
                observed_at=datetime(2026, 3, 19, 14, 40),
                session_state="REGULAR_OPEN",
                priority_tier="FOCUS",
                state_code="MIDDAY__FAILED_BREAKOUT_FADING",
                state_family_code="FAILED_BREAKOUT_FADING",
                state_label="Midday / Failed breakout fade",
                signal_type="SELL_INTO_REBOUND",
                recommended_action_side="SELL",
                recommended_action_code="EXIT",
                recommended_action_label="Sell into rebound",
                feature_snapshot_json=json.dumps({}, sort_keys=True),
                expected_return_15m_pct=Decimal("-0.1000"),
                expected_return_30m_pct=Decimal("-0.1500"),
                post_cost_expected_return_15m_pct=Decimal("-0.2200"),
                historical_win_rate=Decimal("0.5200"),
                confidence_score=0.58,
                confidence_label="MEDIUM",
                confidence_reasons_json=json.dumps({"exact_state_match": True, "top_symbol_share": 0.20}, sort_keys=True),
                outlook_sample_size=72,
                matched_instrument_count=8,
                opportunity_status="INFORMATIONAL",
            )
        )
        _add_minute_bar_series(
            sess,
            instrument_id=instrument_id,
            session_date=date(2026, 3, 19),
            start_ts=datetime(2026, 3, 19, 14, 35),
            close_values=[100.18, 100.22, 100.24, 100.20, 100.18, 100.16],
            volume_value=1000.0,
        )

    second = BetaIntradaySimulatedTradeService.refresh_live_trades(
        settings,
        now_utc=datetime(2026, 3, 19, 14, 41, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        trade = sess.scalar(select(BetaIntradaySimulatedTrade))

    assert first["trades_opened"] == 1
    assert second["trades_closed"] == 1
    assert trade is not None
    assert trade.simulation_source == "LIVE_FORWARD"
    assert trade.status == "CLOSED"
    assert trade.exit_reason_code in {"GUIDANCE_EXIT", "TARGET_HIT"}


def test_intraday_simulated_trade_history_rebuild_can_create_short_trade_from_stable_pocket(beta_context):
    settings = BetaSettings()
    settings.intraday_short_trade_history_days = 365
    settings.intraday_focus_us_symbol_cap = 1
    settings.intraday_focus_uk_symbol_cap = 1

    with BetaContext.write_session() as sess:
        focus_instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NYSE",
            currency="USD",
        )
        sess.add(focus_instrument)
        sess.flush()

        pocket_symbols = ["PCK1", "PCK2", "PCK3", "PCK4", "PCK5"]
        pocket_instruments: list[BetaInstrument] = []
        for symbol in pocket_symbols:
            instrument = BetaInstrument(
                symbol=symbol,
                name=symbol,
                market="OTHER",
                exchange=None,
                currency="USD",
            )
            sess.add(instrument)
            pocket_instruments.append(instrument)
        sess.flush()

        session_dates = [date(2026, 2, 1) + timedelta(days=offset) for offset in range(16)]
        for session_index, session_date in enumerate(session_dates):
            for symbol_index, instrument in enumerate(pocket_instruments):
                observed_at = datetime.combine(session_date, datetime.min.time()).replace(hour=14, minute=30)
                expected_15 = Decimal("-0.5200")
                expected_30 = Decimal("-0.6200")
                post_cost_15 = Decimal("-0.4000")
                observation = BetaIntradayFeatureObservation(
                    instrument_id=instrument.id,
                    symbol=instrument.symbol,
                    session_date=session_date,
                    observed_at=observed_at,
                    session_state="REGULAR_OPEN",
                    priority_tier="FOCUS",
                    state_code="MIDDAY__GAP_DOWN_RECOVERY",
                    state_family_code="GAP_DOWN_RECOVERY",
                    state_label="Midday / Gap-down recovery",
                    signal_type=None,
                    recommended_action_side="WAIT",
                    recommended_action_code="NO_ACTION",
                    recommended_action_label="No trade action",
                    feature_snapshot_json=json.dumps({}, sort_keys=True),
                    expected_return_15m_pct=expected_15,
                    expected_return_30m_pct=expected_30,
                    post_cost_expected_return_15m_pct=post_cost_15,
                    historical_win_rate=Decimal("0.6200"),
                    confidence_score=0.72,
                    confidence_label="HIGH",
                    confidence_reasons_json=json.dumps(
                        {
                            "exact_state_match": True,
                            "top_symbol_share": 0.20,
                            "p25_15m_return_pct": -1.10,
                            "p75_15m_return_pct": 0.12,
                        },
                        sort_keys=True,
                    ),
                    outlook_sample_size=90,
                    matched_instrument_count=9,
                    opportunity_status="INFORMATIONAL",
                )
                sess.add(observation)
                sess.flush()
                base_drop = 0.55 + (0.03 * float(symbol_index)) + (0.01 * float(session_index % 3))
                sess.add(
                    BetaIntradayFeatureLabelValue(
                        observation_id=observation.id,
                        instrument_id=instrument.id,
                        symbol=instrument.symbol,
                        session_date=session_date,
                        observed_at=observed_at,
                        future_15m_return_pct=-(base_drop),
                        future_30m_return_pct=-(base_drop + 0.18),
                        evaluation_complete=True,
                    )
                )

        current_observation = BetaIntradayFeatureObservation(
            instrument_id=focus_instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 19),
            observed_at=datetime(2026, 3, 19, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="FOCUS",
            state_code="MIDDAY__GAP_DOWN_RECOVERY",
            state_family_code="GAP_DOWN_RECOVERY",
            state_label="Midday / Gap-down recovery",
            signal_type=None,
            recommended_action_side="WAIT",
            recommended_action_code="NO_ACTION",
            recommended_action_label="No trade action",
            feature_snapshot_json=json.dumps({}, sort_keys=True),
            expected_return_15m_pct=Decimal("-0.5000"),
            expected_return_30m_pct=Decimal("-0.6200"),
            post_cost_expected_return_15m_pct=Decimal("-0.3800"),
            historical_win_rate=Decimal("0.6100"),
            confidence_score=0.70,
            confidence_label="HIGH",
            confidence_reasons_json=json.dumps(
                {
                    "exact_state_match": True,
                    "top_symbol_share": 0.20,
                    "p25_15m_return_pct": -1.00,
                    "p75_15m_return_pct": 0.10,
                },
                sort_keys=True,
            ),
            outlook_sample_size=84,
            matched_instrument_count=9,
            opportunity_status="INFORMATIONAL",
        )
        sess.add(current_observation)
        _add_minute_bar_series(
            sess,
            instrument_id=focus_instrument.id,
            session_date=date(2026, 3, 19),
            start_ts=datetime(2026, 3, 19, 14, 30),
            close_values=[
                100.0,
                99.85,
                99.70,
                99.55,
                99.40,
                99.25,
                99.10,
                98.95,
                98.90,
                98.88,
                98.86,
            ],
            volume_value=1000.0,
        )

    result = BetaIntradaySimulatedTradeService.rebuild_recent_history(settings, target_days=365)

    with BetaContext.read_session() as sess:
        trade = sess.scalar(
            select(BetaIntradaySimulatedTrade).where(BetaIntradaySimulatedTrade.symbol == "IBM")
        )
        events = list(
            sess.scalars(
                select(BetaIntradaySimulatedTradeEvent)
                .order_by(BetaIntradaySimulatedTradeEvent.event_time.asc())
            ).all()
        )

    assert result["trades_written"] == 1
    assert trade is not None
    assert trade.direction == "SHORT"
    assert trade.entry_action_side == "SELL"
    assert trade.status == "CLOSED"
    assert trade.realized_return_pct is not None and trade.realized_return_pct > 0
    assert trade.realized_post_cost_return_pct is not None and trade.realized_post_cost_return_pct > 0
    assert trade.exit_reason_code in {"TARGET_HIT", "TIME_EXIT", "SESSION_END"}
    assert len(events) == 2
    assert events[0].event_type == "OPENED"
