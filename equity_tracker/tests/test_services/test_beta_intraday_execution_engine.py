from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.app_context import AppContext
from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaDailyBar,
    BetaDemoPosition,
    BetaExecutionLabelValue,
    BetaExecutionSignal,
    BetaInstrument,
    BetaIntradayFeatureSnapshot,
    BetaIntradaySnapshot,
    BetaMinuteBar,
    BetaPositionState,
    BetaSignalCandidate,
    BetaUiNotification,
)
from src.beta.services.execution_outcome_service import BetaExecutionOutcomeService
from src.beta.services.execution_signal_service import BetaExecutionSignalService
from src.beta.services.intraday_aggregation_service import BetaIntradayAggregationService
from src.beta.services.intraday_feature_service import BetaIntradayFeatureService
from src.beta.services.intraday_priority_service import IntradayPriorityItem
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
