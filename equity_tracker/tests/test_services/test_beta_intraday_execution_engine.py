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
    BetaExecutionHypothesisDiscoveryRun,
    BetaExecutionHypothesisDefinition,
    BetaExecutionLabelValue,
    BetaExecutionSignal,
    BetaExecutionHypothesisTestRun,
    BetaHypothesisDefinition,
    BetaInstrument,
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradayPatternCandidate,
    BetaIntradayPatternDiscoveryRun,
    BetaIntradayPatternExecutionProfile,
    BetaIntradayPatternExplorationProfile,
    BetaIntradayPatternPolicyProfile,
    BetaIntradayPatternThresholdProfile,
    BetaIntradayFeatureSnapshot,
    BetaIntradaySimulatedTrade,
    BetaIntradaySimulatedTradeEvent,
    BetaIntradaySnapshot,
    BetaMinuteBar,
    BetaPositionState,
    BetaPredictionAccuracyLog,
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
from src.beta.services.intraday_pattern_exploration_service import BetaIntradayPatternExplorationService
from src.beta.services.intraday_pattern_execution_learning_service import BetaIntradayPatternExecutionLearningService
from src.beta.services.intraday_pattern_exploration_learning_service import BetaIntradayPatternExplorationLearningService
from src.beta.services.intraday_pattern_parameter_learning_service import BetaIntradayPatternParameterLearningService
from src.beta.services.intraday_pattern_review_service import BetaIntradayPatternReviewService
from src.beta.services.intraday_pattern_threshold_learning_service import BetaIntradayPatternThresholdLearningService
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
    repeated_discovery = BetaExecutionHypothesisDiscoveryService.run_discovery(settings)
    backtests = BetaExecutionHypothesisBacktestService.refresh_backtests(settings)
    beliefs = BetaExecutionHypothesisBeliefService.refresh_belief_states(settings)

    with BetaContext.read_session() as sess:
        discovery_run_count = sess.scalar(select(func.count()).select_from(BetaExecutionHypothesisDiscoveryRun)) or 0
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
    assert repeated_discovery["job_status"] == "SKIPPED"
    assert backtests["test_runs_written"] >= 1
    assert beliefs["beliefs_written"] >= 1
    assert discovery_run_count == 1
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


def test_intraday_aggregation_uses_observed_minute_date_when_snapshot_price_date_is_stale(beta_context):
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
        for minute_offset, price in enumerate((178.5, 178.6)):
            observed_at = datetime(2026, 3, 30, 14, 30 + minute_offset, 0)
            sess.add(
                BetaIntradaySnapshot(
                    instrument_id=instrument.id,
                    price_date=date(2026, 3, 27),
                    price_gbp=str(price),
                    price_native=str(price),
                    currency="USD",
                    observed_at=observed_at,
                    source="test",
                )
            )

    BetaIntradayAggregationService.aggregate_minute_bars(
        instrument_ids=[instrument.id],
        lookback_minutes=240,
    )

    with BetaContext.read_session() as sess:
        minute_bars = list(
            sess.scalars(
                select(BetaMinuteBar)
                .where(BetaMinuteBar.instrument_id == instrument.id)
                .order_by(BetaMinuteBar.minute_ts.asc())
            ).all()
        )

    assert len(minute_bars) == 2
    assert {row.session_date for row in minute_bars} == {date(2026, 3, 30)}


def test_intraday_feature_snapshot_keeps_last_bar_session_state_anchor(beta_context):
    session_date = date(2026, 3, 16)

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
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=session_date,
            start_ts=datetime(2026, 3, 16, 14, 30, 0),
            close_values=[101.0, 101.2, 101.4],
            volume_value=1000.0,
        )

    priority_item = IntradayPriorityItem(
        instrument_id=instrument.id,
        symbol="IBM",
        market="US",
        exchange="NASDAQ",
        tier="HELD",
        cadence_minutes=3,
        priority_score=1.0,
        session_state="REGULAR_OPEN",
    )
    BetaIntradayFeatureService.refresh_feature_snapshots(
        priority_items=[priority_item],
        now_utc=datetime(2026, 3, 16, 14, 33, tzinfo=timezone.utc),
    )
    BetaIntradayFeatureService.refresh_feature_snapshots(
        priority_items=[priority_item],
        now_utc=datetime(2026, 3, 16, 22, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        snapshot = sess.scalar(
            select(BetaIntradayFeatureSnapshot).where(BetaIntradayFeatureSnapshot.instrument_id == instrument.id)
        )

    assert snapshot is not None
    assert snapshot.last_minute_ts == datetime(2026, 3, 16, 14, 32, 0)
    assert snapshot.session_state == "REGULAR_OPEN"


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


def test_execution_signal_prediction_log_reconciles_to_realized_outcome(beta_context):
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
        execution_definition = BetaExecutionHypothesisDefinition(
            hypothesis_code="EXECUTION_ACCURACY_TEST",
            name="Execution accuracy test",
            signal_type="TRIM_ON_STRENGTH",
            entry_conditions_json=json.dumps(
                {"all": [{"feature": "gap_from_prev_close_pct", "op": "gt", "value": 9.0}]},
                sort_keys=True,
            ),
            regime_filters_json=json.dumps({}, sort_keys=True),
            feature_subset_json=json.dumps(["gap_from_prev_close_pct"], sort_keys=True),
            rationale_text="Execution accuracy regression test.",
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
        sess.add_all([instrument, execution_definition])
        sess.flush()
        current_position = BetaPositionState(
            instrument_id=instrument.id,
            symbol="IBM",
            market="US",
            position_source="DEMO",
            position_status="OPEN",
            thesis_expected_return_pct=4.0,
        )
        sess.add(current_position)
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
        for ts, price in zip(
            [
                datetime(2026, 3, 16, 15, 0),
                datetime(2026, 3, 16, 15, 30),
                datetime(2026, 3, 16, 16, 0),
                datetime(2026, 3, 16, 16, 30),
                datetime(2026, 3, 16, 17, 0),
            ],
            [100, 101, 99, 97, 96],
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
        prediction_log = sess.scalar(
            select(BetaPredictionAccuracyLog)
            .where(BetaPredictionAccuracyLog.execution_signal_id == signal.id)
            .limit(1)
        )

    assert prediction_log is not None
    assert prediction_log.signal_observation_id is None
    assert prediction_log.realized_return_pct is None

    BetaExecutionOutcomeService.update_execution_outcomes()

    with BetaContext.read_session() as sess:
        refreshed_log = sess.get(BetaPredictionAccuracyLog, prediction_log.id)

    assert refreshed_log is not None
    assert refreshed_log.realized_return_pct is not None
    assert refreshed_log.realization_time is not None


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


def test_intraday_outlook_service_skips_non_regular_open_snapshots(beta_context):
    settings = BetaSettings()

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
            BetaIntradayFeatureSnapshot(
                instrument_id=instrument.id,
                session_date=date(2026, 3, 30),
                session_state="CLOSED",
                priority_tier="HELD",
                last_minute_ts=datetime(2026, 3, 30, 10, 46, 0),
                feature_snapshot_json=json.dumps(
                    {
                        "gap_from_prev_close_pct": 0.1,
                        "return_since_open_pct": 0.0,
                        "return_last_15m_pct": 0.0,
                        "distance_from_vwap_pct": 0.0,
                        "session_progress_pct": 0.0,
                        "minutes_until_close": 390,
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
                instrument_id=instrument.id,
                symbol="IBM",
                market="US",
                exchange="NASDAQ",
                tier="HELD",
                cadence_minutes=3,
                priority_score=1.0,
                session_state="CLOSED",
            )
        ],
        now_utc=datetime(2026, 3, 30, 10, 46, 0, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        observation_count = sess.scalar(select(func.count()).select_from(BetaIntradayFeatureObservation))

    assert result["observations_written"] == 0
    assert observation_count == 0


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
    settings.intraday_pattern_live_forward_enabled = False
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


def test_intraday_simulated_trade_live_refresh_uses_approved_pattern_whitelist(beta_context, monkeypatch):
    settings = BetaSettings()
    settings.intraday_pattern_live_forward_enabled = True
    settings.intraday_pattern_live_forward_top_n = 1
    settings.intraday_pattern_live_forward_min_quality_score = 0.20
    settings.intraday_pattern_live_forward_max_open_trades = 1
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
        observation = BetaIntradayFeatureObservation(
            instrument_id=instrument.id,
            symbol="IBM",
            session_date=date(2026, 3, 19),
            observed_at=datetime(2026, 3, 19, 14, 30),
            session_state="REGULAR_OPEN",
            priority_tier="FOCUS",
            state_code="OPEN__SHAKEOUT_RECOVERY",
            state_family_code="SHAKEOUT_RECOVERY",
            state_label="Open / Shakeout recovery",
            signal_type="WAIT_FOR_CONFIRMATION",
            recommended_action_side="WAIT",
            recommended_action_code="NO_ACTION",
            recommended_action_label="Wait for confirmation",
            feature_snapshot_json=json.dumps(
                {
                    "minutes_since_open": 10.0,
                    "session_progress_pct": 5.0,
                    "volume_last_15m_vs_expected": 1.10,
                    "intraday_range_pct": 1.35,
                    "return_last_15m_pct": 0.42,
                    "distance_from_vwap_pct": 0.03,
                },
                sort_keys=True,
            ),
            expected_return_15m_pct=Decimal("0.1800"),
            expected_return_30m_pct=Decimal("0.2400"),
            post_cost_expected_return_15m_pct=Decimal("0.1200"),
            historical_win_rate=Decimal("0.5400"),
            confidence_score=0.48,
            confidence_label="MEDIUM",
            confidence_reasons_json=json.dumps({"exact_state_match": False, "top_symbol_share": 0.20}, sort_keys=True),
            outlook_sample_size=14,
            matched_instrument_count=2,
            opportunity_status="ACTIONABLE",
        )
        sess.add(observation)
        sess.flush()
        spec = next(
            row
            for row in BetaIntradayPatternExplorationService.pattern_specs_for_observation(
                observation,
                settings=settings,
            )
            if row["anchor_family_code"] == "STATE_FAMILY"
            and row["anchor_code"] == "SHAKEOUT_RECOVERY"
            and not row["context_tags"]
        )
        run = BetaIntradayPatternDiscoveryRun(
            run_code="20260319143000",
            status="SUCCESS",
            lookback_days=30,
            observations_considered=42,
            labeled_observations=40,
            patterns_generated=18,
            patterns_screened_in=1,
            window_start=datetime(2026, 2, 1, 14, 30),
            window_end=datetime(2026, 3, 18, 15, 30),
        )
        sess.add(run)
        sess.flush()
        sess.add(
            BetaIntradayPatternCandidate(
                discovery_run_id=run.id,
                pattern_hash=str(spec["pattern_hash"]),
                pattern_code=str(spec["pattern_code"]),
                anchor_family_code="STATE_FAMILY",
                anchor_code="SHAKEOUT_RECOVERY",
                symbol=None,
                session_segment=None,
                context_tags_json="[]",
                action_bias="LONG",
                sample_size=24,
                matched_instruments=3,
                average_return_15m_pct=0.3100,
                average_return_30m_pct=0.3600,
                median_return_15m_pct=0.2700,
                win_rate_pct=59.0,
                mean_max_adverse_move_pct=0.1200,
                mean_max_favorable_move_pct=0.5200,
                post_cost_edge_15m_pct=0.2200,
                reliability_score=0.4200,
                status="SCREENED_IN",
            )
        )
        _add_minute_bar_series(
            sess,
            instrument_id=instrument.id,
            session_date=date(2026, 3, 19),
            start_ts=datetime(2026, 3, 19, 14, 30),
            close_values=[100.0, 100.05, 100.12, 100.18, 100.24],
            volume_value=1000.0,
        )

    focus_item = IntradayPriorityItem(
        instrument_id=instrument.id,
        symbol="IBM",
        market="US",
        exchange="NYSE",
        tier="FOCUS",
        cadence_minutes=5,
        priority_score=1.0,
        session_state="REGULAR_OPEN",
    )
    monkeypatch.setattr(
        BetaIntradayPriorityService,
        "build_focus_watchlist",
        staticmethod(lambda settings_arg, now_utc=None: {"items": [focus_item]}),
    )

    result = BetaIntradaySimulatedTradeService.refresh_live_trades(
        settings,
        now_utc=datetime(2026, 3, 19, 14, 34, tzinfo=timezone.utc),
    )

    with BetaContext.read_session() as sess:
        trade = sess.scalar(select(BetaIntradaySimulatedTrade))

    assert result["trades_opened"] == 1
    assert trade is not None
    assert trade.simulation_source == "LIVE_FORWARD"
    assert trade.status in {"OPEN", "CLOSED"}
    notes = json.loads(trade.notes_json or "{}")
    assert notes["entry_source"] == "PATTERN"
    assert notes["pattern_hash"] == spec["pattern_hash"]
    assert notes["pattern_family_code"] == "STATE_FAMILY:SHAKEOUT_RECOVERY"


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


def test_intraday_pattern_exploration_persists_screened_in_derived_patterns(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_exploration_enabled = True
    settings.intraday_pattern_history_days = 365
    settings.intraday_pattern_min_sample_size = 3
    settings.intraday_pattern_min_matched_instruments = 1
    settings.intraday_pattern_max_context_depth = 1
    settings.intraday_pattern_max_patterns_per_observation = 16
    settings.intraday_execution_commission_bps = 1.0
    settings.intraday_execution_spread_bps = 1.0
    settings.intraday_execution_slippage_bps = 1.0

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

        for offset, session_date in enumerate(
            [
                date(2026, 3, 24),
                date(2026, 3, 25),
                date(2026, 3, 26),
                date(2026, 3, 27),
            ]
        ):
            observed_at = datetime(2026, 3, 24 + offset, 16, 0)
            observation = BetaIntradayFeatureObservation(
                instrument_id=instrument.id,
                symbol="IBM",
                session_date=session_date,
                observed_at=observed_at,
                session_state="REGULAR_OPEN",
                priority_tier="FOCUS",
                state_code="MIDDAY__RANGE_DRIFT",
                state_family_code="RANGE_DRIFT",
                state_label="Midday / Range drift",
                event_trigger_code="EVENT_LOW_VOLUME_DRIFT",
                feature_snapshot_json=json.dumps(
                    {
                        "minutes_since_open": 90,
                        "session_progress_pct": 38.0,
                        "gap_from_prev_close_pct": -0.2,
                        "distance_from_vwap_pct": 0.18,
                        "volume_last_15m_vs_expected": 0.52,
                        "rolling_intraday_vol_15m_pct": 0.31,
                        "intraday_range_pct": 0.95,
                        "return_last_15m_pct": 0.22,
                    },
                    sort_keys=True,
                ),
                opportunity_status="INFORMATIONAL",
            )
            sess.add(observation)
            sess.flush()
            sess.add(
                BetaIntradayFeatureLabelValue(
                    observation_id=observation.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    session_date=session_date,
                    observed_at=observed_at,
                    future_15m_return_pct=0.46 + (0.03 * offset),
                    future_30m_return_pct=0.62 + (0.02 * offset),
                    max_adverse_move_pct=-0.18,
                    max_favorable_move_pct=0.78,
                    evaluation_complete=True,
                )
            )

    result = BetaIntradayPatternExplorationService.run_exploration(settings)
    repeated_result = BetaIntradayPatternExplorationService.run_exploration(settings)

    with BetaContext.read_session() as sess:
        discovery_run = sess.scalar(select(BetaIntradayPatternDiscoveryRun))
        discovery_run_count = sess.scalar(select(func.count()).select_from(BetaIntradayPatternDiscoveryRun)) or 0
        dry_period_candidate = sess.scalar(
            select(BetaIntradayPatternCandidate).where(
                BetaIntradayPatternCandidate.anchor_family_code == "DERIVED",
                BetaIntradayPatternCandidate.anchor_code == "DRY_PERIOD",
                BetaIntradayPatternCandidate.status == "SCREENED_IN",
            )
        )

    assert discovery_run is not None
    assert result["labeled_observations"] == 4
    assert repeated_result["job_status"] == "SKIPPED"
    assert result["patterns_generated"] > 0
    assert result["patterns_screened_in"] > 0
    assert discovery_run_count == 1
    assert dry_period_candidate is not None
    assert dry_period_candidate.action_bias == "LONG"
    assert dry_period_candidate.sample_size >= 3
    assert dry_period_candidate.post_cost_edge_15m_pct is not None
    assert dry_period_candidate.post_cost_edge_15m_pct > 0
    notes = json.loads(dry_period_candidate.notes_json or "{}")
    assert notes["best_horizon_minutes"] == 30
    assert "15" in notes["horizon_profile"]
    assert "30" in notes["horizon_profile"]
    assert notes["best_horizon_post_cost_edge_pct"] > 0


def test_intraday_pattern_threshold_learning_persists_active_profile(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_threshold_learning_enabled = True
    settings.intraday_pattern_threshold_learning_window_days = 45
    settings.intraday_pattern_threshold_learning_min_observations = 6

    with BetaContext.write_session() as sess:
        instruments = []
        for symbol in ("IBM", "MSFT"):
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

        feature_rows = [
            (0.52, 0.28, 0.40, -1.30, -0.90, -1.80, -0.55, -0.14, 32.0),
            (0.68, 0.34, 0.85, -0.95, -0.60, -1.25, -0.35, -0.09, 38.0),
            (1.05, 0.60, 1.15, 0.18, 0.22, 0.45, 0.10, 0.03, 48.0),
            (1.48, 1.05, 1.90, 0.82, 0.88, 1.55, 0.42, 0.12, 72.0),
            (1.92, 1.58, 2.60, 1.22, 1.10, 2.10, 0.78, 0.18, 78.0),
            (2.18, 2.05, 3.10, 1.65, 1.38, 2.80, 1.05, 0.25, 84.0),
        ]
        for index, (
            volume_ratio,
            volatility,
            intraday_range,
            ret_15,
            ret_30,
            ret_open,
            gap,
            vwap_distance,
            session_progress,
        ) in enumerate(feature_rows):
            instrument = instruments[index % len(instruments)]
            observed_at = datetime(2026, 4, 3, 14, 30) + timedelta(minutes=15 * index)
            observation = BetaIntradayFeatureObservation(
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                session_date=date(2026, 4, 3),
                observed_at=observed_at,
                session_state="REGULAR_OPEN",
                priority_tier="FOCUS",
                state_code="MIDDAY__RANGE_DRIFT",
                state_family_code="RANGE_DRIFT",
                state_label="Midday / Range drift",
                feature_snapshot_json=json.dumps(
                    {
                        "minutes_since_open": 30 + (15 * index),
                        "minutes_until_close": max(20, 330 - (15 * index)),
                        "session_progress_pct": session_progress,
                        "gap_from_prev_close_pct": gap,
                        "distance_from_vwap_pct": vwap_distance,
                        "volume_last_15m_vs_expected": volume_ratio,
                        "rolling_intraday_vol_15m_pct": volatility,
                        "intraday_range_pct": intraday_range,
                        "return_last_15m_pct": ret_15,
                        "return_last_30m_pct": ret_30,
                        "return_since_open_pct": ret_open,
                    },
                    sort_keys=True,
                ),
                opportunity_status="INFORMATIONAL",
            )
            sess.add(observation)
            sess.flush()
            sess.add(
                BetaIntradayFeatureLabelValue(
                    observation_id=observation.id,
                    instrument_id=instrument.id,
                    symbol=instrument.symbol,
                    session_date=date(2026, 4, 3),
                    observed_at=observed_at,
                    future_15m_return_pct=0.15 + (0.02 * index),
                    future_30m_return_pct=0.20 + (0.03 * index),
                    evaluation_complete=True,
                )
            )

    result = BetaIntradayPatternThresholdLearningService.learn_threshold_profile(settings)
    repeated_result = BetaIntradayPatternThresholdLearningService.learn_threshold_profile(settings)

    with BetaContext.read_session() as sess:
        profile = sess.scalar(select(BetaIntradayPatternThresholdProfile))
        profile_count = sess.scalar(select(func.count()).select_from(BetaIntradayPatternThresholdProfile)) or 0

    assert result["profile_created"] is True
    assert repeated_result["job_status"] == "SKIPPED"
    assert profile is not None
    assert profile_count == 1
    assert profile.source_mode == "OBSERVATION_DISTRIBUTION"
    assert profile.observation_count == 6
    assert profile.distinct_instrument_count == 2
    assert profile.confidence_score >= 0.25
    thresholds = json.loads(profile.thresholds_json or "{}")
    notes = json.loads(profile.notes_json or "{}")
    assert thresholds["volume_high_ratio"] > thresholds["volume_low_ratio"]
    assert thresholds["volatility_high_pct"] > thresholds["volatility_low_pct"]
    assert thresholds["range_expanded_pct"] > thresholds["range_compressed_pct"]
    assert thresholds["close_drive_abs_return_30m_pct"] >= 0.30
    assert notes["input_fingerprint"]
    assert notes["input_summary"]["observation_count"] == 6
    assert repeated_result["input_fingerprint"] == notes["input_fingerprint"]


def test_intraday_pattern_exploration_uses_learned_threshold_profile(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_threshold_learning_enabled = True
    settings.intraday_pattern_threshold_learning_min_observations = 10

    with BetaContext.write_session() as sess:
        sess.add(
            BetaIntradayPatternThresholdProfile(
                profile_code="20260403191500",
                source_mode="OBSERVATION_DISTRIBUTION",
                evaluation_window_days=45,
                observation_count=24,
                distinct_instrument_count=4,
                confidence_score=0.68,
                thresholds_json=json.dumps(
                    {
                        "volume_low_ratio": 0.90,
                        "volume_high_ratio": 1.55,
                        "volatility_low_pct": 0.80,
                        "volatility_high_pct": 1.60,
                        "momentum_up_pct": 0.45,
                        "momentum_down_pct": -0.45,
                        "range_compressed_pct": 1.40,
                        "range_expanded_pct": 2.80,
                        "sell_pressure_return_15m_pct": -0.60,
                        "sell_pressure_return_since_open_pct": -1.10,
                        "buy_pressure_return_15m_pct": 0.60,
                        "buy_pressure_return_since_open_pct": 1.10,
                        "close_drive_abs_return_30m_pct": 0.55,
                    },
                    sort_keys=True,
                ),
                notes_json=json.dumps({}, sort_keys=True),
            )
        )

    observation = BetaIntradayFeatureObservation(
        instrument_id="instrument-1",
        symbol="IBM",
        session_date=date(2026, 4, 3),
        observed_at=datetime(2026, 4, 3, 15, 10),
        session_state="REGULAR_OPEN",
        priority_tier="FOCUS",
        state_code="MIDDAY__RANGE_DRIFT",
        state_family_code="RANGE_DRIFT",
        state_label="Midday / Range drift",
        feature_snapshot_json=json.dumps(
            {
                "minutes_since_open": 100,
                "minutes_until_close": 290,
                "session_progress_pct": 46.0,
                "gap_from_prev_close_pct": 0.18,
                "distance_from_vwap_pct": 0.06,
                "volume_last_15m_vs_expected": 0.82,
                "rolling_intraday_vol_15m_pct": 0.72,
                "intraday_range_pct": 1.28,
                "return_last_15m_pct": 0.32,
                "return_last_30m_pct": 0.42,
                "return_since_open_pct": 0.58,
            },
            sort_keys=True,
        ),
        opportunity_status="INFORMATIONAL",
    )

    static_specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
        observation,
        settings=settings,
        threshold_profile=BetaIntradayPatternThresholdLearningService.static_threshold_snapshot(settings),
    )
    learned_specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
        observation,
        settings=settings,
    )

    assert not any(row["anchor_code"] == "DRY_PERIOD" for row in static_specs)
    assert any(row["anchor_code"] == "DRY_PERIOD" for row in learned_specs)
    assert not any("VOLUME:LOW" in (row.get("context_tags") or []) for row in static_specs)
    assert any("VOLUME:LOW" in (row.get("context_tags") or []) for row in learned_specs)


def test_intraday_pattern_review_service_builds_leaderboard_and_family_rollups(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_live_forward_enabled = True
    settings.intraday_pattern_live_forward_top_n = 2
    settings.intraday_pattern_live_forward_min_quality_score = 0.20

    with BetaContext.write_session() as sess:
        run = BetaIntradayPatternDiscoveryRun(
            run_code="20260403123000",
            status="SUCCESS",
            lookback_days=30,
            observations_considered=120,
            labeled_observations=96,
            patterns_generated=40,
            patterns_screened_in=2,
            window_start=datetime(2026, 3, 3, 14, 30, 0),
            window_end=datetime(2026, 4, 3, 14, 30, 0),
        )
        sess.add(run)
        sess.flush()
        sess.add_all(
            [
                BetaIntradayPatternCandidate(
                    discovery_run_id=run.id,
                    pattern_hash="pattern-a",
                    pattern_code="STATE_FAMILY:SHAKEOUT_RECOVERY",
                    anchor_family_code="STATE_FAMILY",
                    anchor_code="SHAKEOUT_RECOVERY",
                    context_tags_json="[]",
                    action_bias="LONG",
                    sample_size=28,
                    matched_instruments=3,
                    average_return_15m_pct=0.3200,
                    average_return_30m_pct=0.3800,
                    median_return_15m_pct=0.2900,
                    win_rate_pct=60.0,
                    mean_max_adverse_move_pct=0.1200,
                    mean_max_favorable_move_pct=0.5400,
                    post_cost_edge_15m_pct=0.2300,
                    reliability_score=0.4500,
                    status="SCREENED_IN",
                    notes_json=json.dumps(
                        {
                            "best_horizon_minutes": 60,
                            "best_horizon_return_pct": 0.4100,
                            "best_horizon_median_return_pct": 0.3600,
                            "best_horizon_win_rate_pct": 63.0,
                            "best_horizon_post_cost_edge_pct": 0.3000,
                            "horizon_stability_score": 0.2800,
                            "horizon_profile": {
                                "15": {"post_cost_edge_pct": 0.2300},
                                "30": {"post_cost_edge_pct": 0.2600},
                                "60": {"post_cost_edge_pct": 0.3000},
                            },
                        },
                        sort_keys=True,
                    ),
                ),
                BetaIntradayPatternCandidate(
                    discovery_run_id=run.id,
                    pattern_hash="pattern-b",
                    pattern_code="DERIVED:DRY_PERIOD|SEGMENT:MIDDAY",
                    anchor_family_code="DERIVED",
                    anchor_code="DRY_PERIOD",
                    context_tags_json='["SEGMENT:MIDDAY"]',
                    action_bias="LONG",
                    sample_size=18,
                    matched_instruments=2,
                    average_return_15m_pct=0.1800,
                    average_return_30m_pct=0.2400,
                    median_return_15m_pct=0.1500,
                    win_rate_pct=57.0,
                    mean_max_adverse_move_pct=0.0900,
                    mean_max_favorable_move_pct=0.3000,
                    post_cost_edge_15m_pct=0.1200,
                    reliability_score=0.3300,
                    status="SCREENED_IN",
                    notes_json=json.dumps(
                        {
                            "best_horizon_minutes": 30,
                            "best_horizon_return_pct": 0.2400,
                            "best_horizon_median_return_pct": 0.1900,
                            "best_horizon_win_rate_pct": 57.0,
                            "best_horizon_post_cost_edge_pct": 0.1200,
                            "horizon_stability_score": 0.1800,
                            "horizon_profile": {
                                "15": {"post_cost_edge_pct": 0.1200},
                                "30": {"post_cost_edge_pct": 0.1200},
                            },
                        },
                        sort_keys=True,
                    ),
                ),
            ]
        )

        summary = BetaIntradayPatternReviewService.latest_summary_in_session(sess, settings)

    assert summary["available"] is True
    assert summary["counts"]["approved_count"] == 2
    assert summary["leaderboard"]
    assert summary["leaderboard"][0]["pattern_hash"] == "pattern-a"
    assert summary["leaderboard"][0]["best_horizon_minutes"] == 60
    assert summary["leaderboard"][0]["approval_status"] == "APPROVED"
    assert summary["family_rollups"]
    assert summary["family_rollups"][0]["best_horizon_label"] == "60m"
    assert summary["family_rollups"][0]["verdict"] in {"WORKING", "PROMISING"}
    assert {row["pattern_hash"] for row in summary["approved_patterns"]} == {"pattern-a", "pattern-b"}


def test_intraday_pattern_parameter_learning_persists_outcome_driven_profile(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_parameter_learning_enabled = True
    settings.intraday_pattern_parameter_learning_window_days = 30
    settings.intraday_pattern_parameter_learning_min_closed_trades = 4

    with BetaContext.write_session() as sess:
        run = BetaIntradayPatternDiscoveryRun(
            run_code="20260403153000",
            status="SUCCESS",
            lookback_days=30,
            observations_considered=150,
            labeled_observations=120,
            patterns_generated=44,
            patterns_screened_in=5,
            window_start=datetime(2026, 3, 3, 14, 30, 0),
            window_end=datetime(2026, 4, 3, 15, 30, 0),
        )
        sess.add(run)
        sess.flush()

        trade_specs = [
            ("pattern-a", "LIVE_FORWARD", 0.18, 0.46, 0.44, 0.55, 0.42),
            ("pattern-b", "LIVE_FORWARD", 0.12, 0.43, 0.40, 0.52, 0.39),
            ("pattern-a", "LIVE_FORWARD", 0.09, 0.41, 0.38, 0.50, 0.36),
            ("pattern-c", "HISTORICAL_BACKFILL", 0.06, 0.39, 0.35, 0.47, 0.34),
            ("pattern-d", "LIVE_FORWARD", -0.05, 0.30, 0.22, 0.36, 0.18),
            ("pattern-e", "HISTORICAL_BACKFILL", -0.03, 0.28, 0.20, 0.34, 0.16),
        ]
        for offset, (pattern_hash, source, realized_post_cost, quality, stability, sample_quality, reliability) in enumerate(trade_specs):
            sess.add(
                BetaIntradaySimulatedTrade(
                    symbol=f"SYM{offset}",
                    market="US",
                    direction="LONG",
                    simulation_source=source,
                    status="CLOSED",
                    session_date=date(2026, 4, 3),
                    entry_observed_at=datetime(2026, 4, 3, 14, 30) + timedelta(minutes=offset),
                    latest_observed_at=datetime(2026, 4, 3, 15, 0) + timedelta(minutes=offset),
                    exit_observed_at=datetime(2026, 4, 3, 15, 10) + timedelta(minutes=offset),
                    realized_post_cost_return_pct=realized_post_cost,
                    notes_json=json.dumps(
                        {
                            "entry_source": "PATTERN",
                            "pattern_hash": pattern_hash,
                            "pattern_quality_score": quality,
                            "pattern_stability_score": stability,
                            "pattern_sample_quality_score": sample_quality,
                            "pattern_reliability_score": reliability,
                            "pattern_best_horizon_post_cost_edge_pct": max(0.0, realized_post_cost + 0.12),
                        },
                        sort_keys=True,
                    ),
                )
            )

    result = BetaIntradayPatternParameterLearningService.learn_policy_profile(settings)
    repeated_result = BetaIntradayPatternParameterLearningService.learn_policy_profile(settings)

    with BetaContext.read_session() as sess:
        profile = sess.scalar(select(BetaIntradayPatternPolicyProfile))
        profile_count = sess.scalar(select(func.count()).select_from(BetaIntradayPatternPolicyProfile)) or 0

    assert result["profile_created"] is True
    assert repeated_result["job_status"] == "SKIPPED"
    assert profile is not None
    assert profile_count == 1
    assert profile.source_mode == "OUTCOME_DRIVEN"
    assert profile.trade_count == 6
    assert profile.recommended_top_n == 2
    assert profile.recommended_max_open_trades == 2
    assert profile.recommended_min_quality_score is not None
    assert profile.confidence_score > 0.25
    notes = json.loads(profile.notes_json or "{}")
    assert notes["input_fingerprint"]
    assert notes["input_summary"]["trade_count"] == 6
    assert repeated_result["input_fingerprint"] == notes["input_fingerprint"]


def test_intraday_pattern_execution_learning_persists_outcome_driven_profile(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_execution_learning_enabled = True
    settings.intraday_pattern_execution_learning_window_days = 30
    settings.intraday_pattern_execution_learning_min_closed_trades = 4

    with BetaContext.write_session() as sess:
        trade_specs = [
            ("pattern-a", "LIVE_FORWARD", 0.18, 0.22, 0.16, 60, 35, 0.26, -0.06, "TARGET_HIT"),
            ("pattern-b", "LIVE_FORWARD", 0.12, 0.20, 0.14, 60, 28, 0.24, -0.05, "TARGET_HIT"),
            ("pattern-c", "HISTORICAL_BACKFILL", 0.08, 0.18, 0.15, 45, 22, 0.19, -0.04, "GUIDANCE_EXIT"),
            ("pattern-d", "LIVE_FORWARD", -0.05, 0.20, 0.14, 60, 18, 0.04, -0.09, "EARLY_BAIL"),
            ("pattern-e", "LIVE_FORWARD", -0.08, 0.22, 0.16, 60, 26, 0.02, -0.12, "STOP_HIT"),
            ("pattern-f", "HISTORICAL_BACKFILL", 0.04, 0.18, 0.14, 45, 20, 0.17, -0.03, "TIME_EXIT"),
        ]
        for offset, (
            pattern_hash,
            source,
            realized_post_cost,
            target_return,
            stop_loss,
            max_hold,
            hold_minutes,
            max_return,
            max_drawdown,
            exit_reason,
        ) in enumerate(trade_specs):
            sess.add(
                BetaIntradaySimulatedTrade(
                    symbol=f"PTN{offset}",
                    market="US",
                    direction="LONG",
                    simulation_source=source,
                    status="CLOSED",
                    session_date=date(2026, 4, 3),
                    entry_observed_at=datetime(2026, 4, 3, 14, 30) + timedelta(minutes=offset),
                    latest_observed_at=datetime(2026, 4, 3, 15, 0) + timedelta(minutes=offset),
                    exit_observed_at=datetime(2026, 4, 3, 15, 10) + timedelta(minutes=offset),
                    target_return_pct=target_return,
                    stop_loss_pct=stop_loss,
                    max_hold_minutes=max_hold,
                    hold_minutes=hold_minutes,
                    max_return_pct=max_return,
                    max_drawdown_pct=max_drawdown,
                    realized_post_cost_return_pct=realized_post_cost,
                    exit_reason_code=exit_reason,
                    notes_json=json.dumps(
                        {
                            "entry_source": "PATTERN",
                            "pattern_hash": pattern_hash,
                        },
                        sort_keys=True,
                    ),
                )
            )

    result = BetaIntradayPatternExecutionLearningService.learn_execution_profile(settings)
    repeated_result = BetaIntradayPatternExecutionLearningService.learn_execution_profile(settings)

    with BetaContext.read_session() as sess:
        profile = sess.scalar(select(BetaIntradayPatternExecutionProfile))
        profile_count = sess.scalar(select(func.count()).select_from(BetaIntradayPatternExecutionProfile)) or 0

    assert result["profile_created"] is True
    assert repeated_result["job_status"] == "SKIPPED"
    assert profile is not None
    assert profile_count == 1
    assert profile.source_mode == "OUTCOME_DRIVEN"
    assert profile.trade_count == 6
    assert profile.live_forward_trade_count == 4
    assert profile.recommended_target_capture_ratio is not None
    assert 0.65 <= float(profile.recommended_target_capture_ratio) <= 1.10
    assert profile.recommended_stop_loss_ratio is not None
    assert 0.70 <= float(profile.recommended_stop_loss_ratio) <= 1.15
    assert profile.recommended_max_hold_ratio is not None
    assert 0.55 <= float(profile.recommended_max_hold_ratio) <= 1.10
    assert profile.recommended_early_bail_ratio is not None
    assert 0.15 <= float(profile.recommended_early_bail_ratio) <= 0.70
    assert profile.confidence_score > 0.25
    notes = json.loads(profile.notes_json or "{}")
    assert notes["input_fingerprint"]
    assert notes["input_summary"]["trade_count"] == 6
    assert repeated_result["input_fingerprint"] == notes["input_fingerprint"]


def test_intraday_pattern_exploration_learning_persists_family_budget_profile(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_exploration_learning_enabled = True
    settings.intraday_pattern_exploration_learning_window_days = 30
    settings.intraday_pattern_exploration_learning_min_closed_trades = 4

    with BetaContext.write_session() as sess:
        run = BetaIntradayPatternDiscoveryRun(
            run_code="20260403193000",
            status="SUCCESS",
            lookback_days=30,
            observations_considered=180,
            labeled_observations=140,
            patterns_generated=60,
            patterns_screened_in=6,
            window_start=datetime(2026, 3, 4, 14, 30, 0),
            window_end=datetime(2026, 4, 3, 19, 30, 0),
        )
        sess.add(run)
        sess.flush()
        sess.add_all(
            [
                BetaIntradayPatternCandidate(
                    discovery_run_id=run.id,
                    pattern_hash="dry-a",
                    pattern_code="DERIVED:DRY_PERIOD|SEGMENT:MIDDAY",
                    anchor_family_code="DERIVED",
                    anchor_code="DRY_PERIOD",
                    context_tags_json='["SEGMENT:MIDDAY","VOLUME:LOW"]',
                    action_bias="LONG",
                    sample_size=18,
                    matched_instruments=2,
                    average_return_15m_pct=0.18,
                    average_return_30m_pct=0.24,
                    median_return_15m_pct=0.15,
                    win_rate_pct=57.0,
                    mean_max_adverse_move_pct=0.09,
                    mean_max_favorable_move_pct=0.30,
                    post_cost_edge_15m_pct=0.12,
                    reliability_score=0.33,
                    status="SCREENED_IN",
                ),
                BetaIntradayPatternCandidate(
                    discovery_run_id=run.id,
                    pattern_hash="shake-a",
                    pattern_code="STATE_FAMILY:SHAKEOUT_RECOVERY|MOMENTUM:UP",
                    anchor_family_code="STATE_FAMILY",
                    anchor_code="SHAKEOUT_RECOVERY",
                    context_tags_json='["MOMENTUM:UP"]',
                    action_bias="LONG",
                    sample_size=28,
                    matched_instruments=3,
                    average_return_15m_pct=0.32,
                    average_return_30m_pct=0.38,
                    median_return_15m_pct=0.29,
                    win_rate_pct=60.0,
                    mean_max_adverse_move_pct=0.12,
                    mean_max_favorable_move_pct=0.54,
                    post_cost_edge_15m_pct=0.23,
                    reliability_score=0.45,
                    status="SCREENED_IN",
                ),
            ]
        )
        trade_specs = [
            ("DERIVED:DRY_PERIOD", "LIVE_FORWARD", 0.14, ["VOLUME:LOW", "SEGMENT:MIDDAY"]),
            ("DERIVED:DRY_PERIOD", "LIVE_FORWARD", 0.10, ["VOLUME:LOW", "RANGE:COMPRESSED"]),
            ("DERIVED:DRY_PERIOD", "HISTORICAL_BACKFILL", 0.06, ["VOLUME:LOW"]),
            ("STATE_FAMILY:RANGE_DRIFT", "LIVE_FORWARD", -0.05, ["MOMENTUM:UP"]),
            ("STATE_FAMILY:RANGE_DRIFT", "HISTORICAL_BACKFILL", -0.03, ["SEGMENT:MIDDAY"]),
            ("EVENT:EVENT_LOW_VOLUME_DRIFT", "HISTORICAL_BACKFILL", 0.02, ["SEGMENT:MIDDAY"]),
        ]
        for offset, (family_code, source, realized_post_cost, context_tags) in enumerate(trade_specs):
            sess.add(
                BetaIntradaySimulatedTrade(
                    symbol=f"EXP{offset}",
                    market="US",
                    direction="LONG",
                    simulation_source=source,
                    status="CLOSED",
                    session_date=date(2026, 4, 3),
                    entry_observed_at=datetime(2026, 4, 3, 14, 30) + timedelta(minutes=offset),
                    latest_observed_at=datetime(2026, 4, 3, 15, 0) + timedelta(minutes=offset),
                    exit_observed_at=datetime(2026, 4, 3, 15, 10) + timedelta(minutes=offset),
                    realized_post_cost_return_pct=realized_post_cost,
                    notes_json=json.dumps(
                        {
                            "entry_source": "PATTERN",
                            "pattern_hash": f"hash-{offset}",
                            "pattern_family_code": family_code,
                            "pattern_context_tags": context_tags,
                        },
                        sort_keys=True,
                    ),
                )
            )

    result = BetaIntradayPatternExplorationLearningService.learn_exploration_profile(settings)
    repeated_result = BetaIntradayPatternExplorationLearningService.learn_exploration_profile(settings)

    with BetaContext.read_session() as sess:
        profile = sess.scalar(select(BetaIntradayPatternExplorationProfile))
        profile_count = sess.scalar(select(func.count()).select_from(BetaIntradayPatternExplorationProfile)) or 0

    assert result["profile_created"] is True
    assert repeated_result["job_status"] == "SKIPPED"
    assert profile is not None
    assert profile_count == 1
    assert profile.source_mode == "OUTCOME_MIXED"
    assert profile.distinct_family_count >= 3
    assert profile.recommended_max_patterns_per_observation is not None
    notes = json.loads(profile.notes_json or "{}")
    assert notes["input_fingerprint"]
    assert notes["input_summary"]["candidate_count"] >= 1
    assert repeated_result["input_fingerprint"] == notes["input_fingerprint"]
    family_scores = notes.get("family_scores") or {}
    family_allowlists = notes.get("family_context_prefix_allowlists") or {}
    assert family_scores["DERIVED:DRY_PERIOD"] > family_scores["STATE_FAMILY:RANGE_DRIFT"]
    assert "VOLUME" in family_allowlists["DERIVED:DRY_PERIOD"]
    assert profile.confidence_score > 0.20


def test_intraday_pattern_review_service_uses_learned_policy_profile_for_approval(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_live_forward_enabled = True
    settings.intraday_pattern_live_forward_top_n = 3
    settings.intraday_pattern_live_forward_min_quality_score = 0.20
    settings.intraday_pattern_parameter_learning_enabled = True
    settings.intraday_pattern_parameter_learning_min_closed_trades = 4

    with BetaContext.write_session() as sess:
        run = BetaIntradayPatternDiscoveryRun(
            run_code="20260403183000",
            status="SUCCESS",
            lookback_days=30,
            observations_considered=140,
            labeled_observations=120,
            patterns_generated=30,
            patterns_screened_in=2,
            window_start=datetime(2026, 3, 4, 14, 30, 0),
            window_end=datetime(2026, 4, 3, 18, 30, 0),
        )
        sess.add(run)
        sess.flush()
        sess.add_all(
            [
                BetaIntradayPatternCandidate(
                    discovery_run_id=run.id,
                    pattern_hash="pattern-strong",
                    pattern_code="STATE_FAMILY:SHAKEOUT_RECOVERY",
                    anchor_family_code="STATE_FAMILY",
                    anchor_code="SHAKEOUT_RECOVERY",
                    context_tags_json="[]",
                    action_bias="LONG",
                    sample_size=26,
                    matched_instruments=3,
                    average_return_15m_pct=0.2800,
                    average_return_30m_pct=0.3400,
                    median_return_15m_pct=0.2500,
                    win_rate_pct=58.0,
                    mean_max_adverse_move_pct=0.1200,
                    mean_max_favorable_move_pct=0.4800,
                    post_cost_edge_15m_pct=0.2100,
                    reliability_score=0.4200,
                    status="SCREENED_IN",
                    notes_json=json.dumps(
                        {
                            "best_horizon_minutes": 60,
                            "best_horizon_return_pct": 0.3900,
                            "best_horizon_median_return_pct": 0.3400,
                            "best_horizon_win_rate_pct": 62.0,
                            "best_horizon_post_cost_edge_pct": 0.2900,
                            "horizon_stability_score": 0.3100,
                        },
                        sort_keys=True,
                    ),
                ),
                BetaIntradayPatternCandidate(
                    discovery_run_id=run.id,
                    pattern_hash="pattern-weak",
                    pattern_code="DERIVED:DRY_PERIOD|SEGMENT:MIDDAY",
                    anchor_family_code="DERIVED",
                    anchor_code="DRY_PERIOD",
                    context_tags_json='["SEGMENT:MIDDAY"]',
                    action_bias="LONG",
                    sample_size=18,
                    matched_instruments=2,
                    average_return_15m_pct=0.1800,
                    average_return_30m_pct=0.2100,
                    median_return_15m_pct=0.1500,
                    win_rate_pct=56.0,
                    mean_max_adverse_move_pct=0.1000,
                    mean_max_favorable_move_pct=0.2800,
                    post_cost_edge_15m_pct=0.1200,
                    reliability_score=0.2500,
                    status="SCREENED_IN",
                    notes_json=json.dumps(
                        {
                            "best_horizon_minutes": 30,
                            "best_horizon_return_pct": 0.2200,
                            "best_horizon_median_return_pct": 0.1800,
                            "best_horizon_win_rate_pct": 56.0,
                            "best_horizon_post_cost_edge_pct": 0.1200,
                            "horizon_stability_score": 0.1400,
                        },
                        sort_keys=True,
                    ),
                ),
                BetaIntradayPatternPolicyProfile(
                    profile_code="20260403183100",
                    discovery_run_id=run.id,
                    source_mode="OUTCOME_DRIVEN",
                    evaluation_window_days=30,
                    trade_count=8,
                    live_forward_trade_count=6,
                    historical_backfill_trade_count=2,
                    winner_count=5,
                    distinct_pattern_count=4,
                    confidence_score=0.52,
                    recommended_min_quality_score=0.35,
                    recommended_min_stability_score=0.28,
                    recommended_min_sample_quality_score=0.45,
                    recommended_min_reliability_score=0.30,
                    recommended_top_n=1,
                    recommended_max_open_trades=1,
                    notes_json=json.dumps({}, sort_keys=True),
                ),
            ]
        )

        summary = BetaIntradayPatternReviewService.latest_summary_in_session(sess, settings)

    assert summary["adaptive_policy"]["source_mode"] == "OUTCOME_DRIVEN"
    assert summary["adaptive_policy"]["active_for_runtime"] is True
    assert summary["approval_mode"] == "ADAPTIVE_AUTO_TOP_N"
    assert [row["pattern_hash"] for row in summary["approved_patterns"]] == ["pattern-strong"]


def test_intraday_pattern_entry_plan_uses_best_horizon_metadata(beta_context):
    settings = BetaSettings()
    settings.intraday_short_trade_max_hold_minutes = 90
    settings.intraday_pattern_min_sample_size = 12
    settings.intraday_pattern_min_matched_instruments = 1

    observation = BetaIntradayFeatureObservation(
        instrument_id="instrument-1",
        symbol="IBM",
        session_date=date(2026, 4, 3),
        observed_at=datetime(2026, 4, 3, 15, 0),
        session_state="REGULAR_OPEN",
        priority_tier="FOCUS",
        state_code="MIDDAY__RANGE_DRIFT",
        state_family_code="RANGE_DRIFT",
        state_label="Midday / Range drift",
        feature_snapshot_json=json.dumps({}, sort_keys=True),
        confidence_score=0.62,
        outlook_sample_size=18,
        matched_instrument_count=3,
        opportunity_status="ACTIONABLE",
    )
    approved_pattern = {
        "pattern_hash": "pattern-60m",
        "pattern_code": "DERIVED:DRY_PERIOD|SEGMENT:MIDDAY",
        "family_code": "DERIVED:DRY_PERIOD",
        "anchor_family_code": "DERIVED",
        "anchor_code": "DRY_PERIOD",
        "action_bias": "LONG",
        "quality_score": 0.52,
        "stability_score": 0.48,
        "horizon_stability_score": 0.30,
        "sample_quality_score": 0.58,
        "reliability_score": 0.41,
        "sample_size": 18,
        "matched_instruments": 3,
        "aligned_average_return_15m_pct": 0.18,
        "aligned_average_return_30m_pct": 0.26,
        "best_horizon_minutes": 60,
        "aligned_best_horizon_return_pct": 0.42,
        "best_horizon_post_cost_edge_pct": 0.31,
        "best_horizon_win_rate_pct": 61.0,
        "win_rate_decimal": 0.58,
        "mean_max_adverse_move_pct": -0.14,
        "mean_max_favorable_move_pct": 0.60,
        "context_tags": ["SEGMENT:MIDDAY"],
        "context_depth": 1,
        "matched_context_depth": 1,
        "matched_context_tags": ["SEGMENT:MIDDAY"],
        "post_cost_edge_15m_pct": 0.12,
    }

    plan = BetaIntradaySimulatedTradeService._pattern_entry_plan(
        observation,
        settings,
        approved_pattern=approved_pattern,
    )

    assert plan is not None
    assert plan["max_hold_minutes"] == 60
    assert plan["notes"]["pattern_best_horizon_minutes"] == 60
    assert plan["notes"]["pattern_best_horizon_expected_return_pct"] == 0.42
    assert plan["notes"]["pattern_best_horizon_post_cost_edge_pct"] == 0.31


def test_intraday_pattern_exploration_respects_learned_family_priority_when_budget_is_tight(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_max_context_depth = 0
    settings.intraday_pattern_max_patterns_per_observation = 1

    observation = BetaIntradayFeatureObservation(
        instrument_id="instrument-1",
        symbol="IBM",
        session_date=date(2026, 4, 3),
        observed_at=datetime(2026, 4, 3, 15, 10),
        session_state="REGULAR_OPEN",
        priority_tier="FOCUS",
        state_code="MIDDAY__RANGE_DRIFT",
        state_family_code="RANGE_DRIFT",
        state_label="Midday / Range drift",
        event_trigger_code="EVENT_LOW_VOLUME_DRIFT",
        feature_snapshot_json=json.dumps(
            {
                "minutes_since_open": 100,
                "minutes_until_close": 290,
                "session_progress_pct": 46.0,
                "volume_last_15m_vs_expected": 0.52,
                "rolling_intraday_vol_15m_pct": 0.31,
                "intraday_range_pct": 0.95,
                "return_last_15m_pct": 0.22,
                "return_last_30m_pct": 0.28,
                "return_since_open_pct": 0.35,
            },
            sort_keys=True,
        ),
        opportunity_status="INFORMATIONAL",
    )

    static_specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
        observation,
        settings=settings,
    )
    learned_specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
        observation,
        settings=settings,
        exploration_profile={
            "recommended_max_context_depth": 0,
            "recommended_max_patterns_per_observation": 1,
            "family_scores": {
                "DERIVED:DRY_PERIOD": 0.82,
                "STATE_FAMILY:RANGE_DRIFT": 0.18,
                "EVENT:EVENT_LOW_VOLUME_DRIFT": 0.10,
            },
            "family_depth_caps": {
                "DERIVED:DRY_PERIOD": 0,
                "STATE_FAMILY:RANGE_DRIFT": 0,
                "EVENT:EVENT_LOW_VOLUME_DRIFT": 0,
            },
        },
    )

    assert static_specs
    assert learned_specs
    assert static_specs[0]["pattern_code"] == "STATE_FAMILY:RANGE_DRIFT"
    assert learned_specs[0]["pattern_code"] == "DERIVED:DRY_PERIOD"


def test_intraday_pattern_exploration_respects_learned_family_context_preferences(beta_context):
    settings = BetaSettings()
    settings.intraday_pattern_max_context_depth = 1
    settings.intraday_pattern_max_patterns_per_observation = 12

    observation = BetaIntradayFeatureObservation(
        instrument_id="instrument-1",
        symbol="IBM",
        session_date=date(2026, 4, 3),
        observed_at=datetime(2026, 4, 3, 16, 10),
        session_state="REGULAR_OPEN",
        priority_tier="FOCUS",
        state_code=None,
        state_family_code=None,
        state_label=None,
        event_trigger_code=None,
        feature_snapshot_json=json.dumps(
            {
                "minutes_since_open": 120,
                "minutes_until_close": 210,
                "session_progress_pct": 46.0,
                "gap_from_prev_close_pct": 1.25,
                "distance_from_vwap_pct": 0.18,
                "volume_last_15m_vs_expected": 0.52,
                "rolling_intraday_vol_15m_pct": 0.31,
                "intraday_range_pct": 0.95,
                "return_last_15m_pct": 0.18,
                "return_last_30m_pct": 0.22,
                "return_since_open_pct": 0.35,
            },
            sort_keys=True,
        ),
        opportunity_status="INFORMATIONAL",
    )

    static_specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
        observation,
        settings=settings,
    )
    learned_specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
        observation,
        settings=settings,
        exploration_profile={
            "recommended_max_context_depth": 1,
            "recommended_max_patterns_per_observation": 12,
            "family_scores": {
                "DERIVED:DRY_PERIOD": 0.82,
            },
            "family_depth_caps": {
                "DERIVED:DRY_PERIOD": 1,
            },
            "family_context_prefix_scores": {
                "DERIVED:DRY_PERIOD": {
                    "VOLUME": 0.80,
                    "RANGE": 0.72,
                    "SEGMENT": 0.10,
                    "SYMBOL": 0.05,
                }
            },
            "family_context_prefix_allowlists": {
                "DERIVED:DRY_PERIOD": ["VOLUME", "RANGE"]
            },
        },
    )

    static_dry_period_specs = [
        row for row in static_specs if row["pattern_code"].startswith("DERIVED:DRY_PERIOD")
    ]
    learned_dry_period_specs = [
        row for row in learned_specs if row["pattern_code"].startswith("DERIVED:DRY_PERIOD")
    ]

    assert static_dry_period_specs
    assert learned_dry_period_specs
    assert any("SEGMENT:MIDDAY" in row["context_tags"] for row in static_dry_period_specs)
    assert any("SYMBOL:IBM" in row["context_tags"] for row in static_dry_period_specs)
    assert all(
        {
            tag.split(":", 1)[0]
            for tag in row["context_tags"]
        }.issubset({"VOLUME", "RANGE"})
        for row in learned_dry_period_specs
    )
    assert any("VOLUME:LOW" in row["context_tags"] for row in learned_dry_period_specs)
    assert any("RANGE:COMPRESSED" in row["context_tags"] for row in learned_dry_period_specs)


def test_intraday_pattern_entry_plan_uses_learned_execution_profile(beta_context):
    settings = BetaSettings()
    settings.intraday_short_trade_max_hold_minutes = 90
    settings.intraday_pattern_min_sample_size = 12
    settings.intraday_pattern_min_matched_instruments = 1

    observation = BetaIntradayFeatureObservation(
        instrument_id="instrument-1",
        symbol="IBM",
        session_date=date(2026, 4, 3),
        observed_at=datetime(2026, 4, 3, 15, 0),
        session_state="REGULAR_OPEN",
        priority_tier="FOCUS",
        state_code="MIDDAY__RANGE_DRIFT",
        state_family_code="RANGE_DRIFT",
        state_label="Midday / Range drift",
        feature_snapshot_json=json.dumps({}, sort_keys=True),
        confidence_score=0.62,
        outlook_sample_size=18,
        matched_instrument_count=3,
        opportunity_status="ACTIONABLE",
    )
    approved_pattern = {
        "pattern_hash": "pattern-60m",
        "pattern_code": "DERIVED:DRY_PERIOD|SEGMENT:MIDDAY",
        "family_code": "DERIVED:DRY_PERIOD",
        "anchor_family_code": "DERIVED",
        "anchor_code": "DRY_PERIOD",
        "action_bias": "LONG",
        "quality_score": 0.52,
        "stability_score": 0.48,
        "horizon_stability_score": 0.30,
        "sample_quality_score": 0.58,
        "reliability_score": 0.41,
        "sample_size": 18,
        "matched_instruments": 3,
        "aligned_average_return_15m_pct": 0.18,
        "aligned_average_return_30m_pct": 0.26,
        "best_horizon_minutes": 60,
        "aligned_best_horizon_return_pct": 0.42,
        "best_horizon_post_cost_edge_pct": 0.31,
        "best_horizon_win_rate_pct": 61.0,
        "win_rate_decimal": 0.58,
        "mean_max_adverse_move_pct": -0.14,
        "mean_max_favorable_move_pct": 0.60,
        "context_tags": ["SEGMENT:MIDDAY"],
        "context_depth": 1,
        "matched_context_depth": 1,
        "matched_context_tags": ["SEGMENT:MIDDAY"],
        "post_cost_edge_15m_pct": 0.12,
    }

    static_plan = BetaIntradaySimulatedTradeService._pattern_entry_plan(
        observation,
        settings,
        approved_pattern=approved_pattern,
    )
    learned_plan = BetaIntradaySimulatedTradeService._pattern_entry_plan(
        observation,
        settings,
        approved_pattern=approved_pattern,
        execution_profile={
            "source_mode": "OUTCOME_DRIVEN",
            "recommended_target_capture_ratio": 0.75,
            "recommended_stop_loss_ratio": 0.80,
            "recommended_max_hold_ratio": 0.60,
            "recommended_early_bail_ratio": 0.25,
        },
    )

    assert static_plan is not None
    assert learned_plan is not None
    assert learned_plan["target_return_pct"] < static_plan["target_return_pct"]
    assert learned_plan["stop_loss_pct"] < static_plan["stop_loss_pct"]
    assert learned_plan["max_hold_minutes"] < static_plan["max_hold_minutes"]
    assert learned_plan["early_bail_minutes"] == round(learned_plan["max_hold_minutes"] * 0.25)
    assert learned_plan["notes"]["pattern_execution_profile_source"] == "OUTCOME_DRIVEN"
    assert learned_plan["notes"]["pattern_execution_target_capture_ratio"] == 0.75
