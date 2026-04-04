"""Unit tests for BetaPredictionAccuracyService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaPredictionAccuracyLog,
    BetaCalibrationMetrics,
    BetaHypothesisDefinition,
    BetaInstrument,
    BetaSignalObservation,
)
from src.beta.services.prediction_accuracy_service import BetaPredictionAccuracyService


@pytest.fixture()
def beta_context():
    """Create an in-memory Beta database for testing."""
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


@pytest.fixture()
def sample_hypothesis_definition(beta_context):
    """Create a sample hypothesis definition for testing."""
    with BetaContext.write_session() as sess:
        definition = BetaHypothesisDefinition(
            hypothesis_code="TEST_HYPOTHESIS_V1",
            name="Test Hypothesis",
            entry_conditions_json='{"all": [{"feature": "ret_5d_pct", "op": "lt", "value": -2.0}]}',
            regime_filters_json='{}',
            holding_period_days=5,
            status="CANDIDATE",
        )
        sess.add(definition)
        sess.flush()
        return definition.id


@pytest.fixture()
def sample_instrument(beta_context):
    """Create a sample instrument for signal-observation tests."""
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="TEST",
            name="Test Instrument",
            market="US",
            exchange="TESTX",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()
        return instrument.id


def _utcnow() -> datetime:
    """Get current UTC time without timezone info."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_log_prediction_creates_entry_with_correct_confidence_band(beta_context, sample_hypothesis_definition):
    """Test that log_prediction creates a prediction log entry with correct confidence band."""
    prediction_time = _utcnow()
    
    # Test HIGH confidence
    log_id_high = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=3.5,
        confidence_score=0.85,
        prediction_time=prediction_time,
        horizon_days=5,
    )
    
    # Test MEDIUM confidence
    log_id_medium = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=2.5,
        confidence_score=0.60,
        prediction_time=prediction_time,
        horizon_days=5,
    )
    
    # Test LOW confidence
    log_id_low = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=1.5,
        confidence_score=0.40,
        prediction_time=prediction_time,
        horizon_days=5,
    )
    
    with BetaContext.read_session() as sess:
        log_high = sess.get(BetaPredictionAccuracyLog, log_id_high)
        log_medium = sess.get(BetaPredictionAccuracyLog, log_id_medium)
        log_low = sess.get(BetaPredictionAccuracyLog, log_id_low)
        
        assert log_high is not None
        assert log_high.confidence_band == "HIGH"
        assert log_high.predicted_return_pct == 3.5
        assert log_high.confidence_score == 0.85
        assert log_high.horizon_days == 5
        assert log_high.realized_return_pct is None
        
        assert log_medium is not None
        assert log_medium.confidence_band == "MEDIUM"
        
        assert log_low is not None
        assert log_low.confidence_band == "LOW"


def test_log_prediction_with_signal_observation_id(beta_context, sample_hypothesis_definition, sample_instrument):
    """Test logging prediction with signal observation ID."""
    with BetaContext.write_session() as sess:
        observation = BetaSignalObservation(
            hypothesis_definition_id=sample_hypothesis_definition,
            instrument_id=sample_instrument,
            symbol="TEST",
            observation_time=_utcnow(),
            decision_date=_utcnow().date(),
            expected_direction="BULLISH",
            expected_return_pct=2.5,
        )
        sess.add(observation)
        sess.flush()
        observation_id = observation.id
    
    log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        signal_observation_id=observation_id,
        predicted_return_pct=2.5,
        confidence_score=0.65,
        prediction_time=_utcnow(),
        horizon_days=5,
    )
    
    with BetaContext.read_session() as sess:
        log = sess.get(BetaPredictionAccuracyLog, log_id)
        assert log is not None
        assert log.signal_observation_id == observation_id


def test_log_prediction_upserts_reused_signal_observation(beta_context, sample_hypothesis_definition, sample_instrument):
    """Reused signal observations should update the prediction log instead of duplicating it."""
    with BetaContext.write_session() as sess:
        observation = BetaSignalObservation(
            hypothesis_definition_id=sample_hypothesis_definition,
            instrument_id=sample_instrument,
            symbol="TEST",
            observation_time=_utcnow(),
            decision_date=_utcnow().date(),
            expected_direction="BULLISH",
        )
        sess.add(observation)
        sess.flush()
        observation_id = observation.id

    first_log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        signal_observation_id=observation_id,
        predicted_return_pct=2.0,
        confidence_score=0.55,
        prediction_time=_utcnow(),
        horizon_days=5,
    )
    second_log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        signal_observation_id=observation_id,
        predicted_return_pct=3.0,
        confidence_score=0.85,
        prediction_time=_utcnow(),
        horizon_days=7,
    )

    assert second_log_id == first_log_id

    with BetaContext.read_session() as sess:
        logs = list(
            sess.scalars(
                select(BetaPredictionAccuracyLog).where(
                    BetaPredictionAccuracyLog.signal_observation_id == observation_id
                )
            ).all()
        )
        assert len(logs) == 1
        assert logs[0].predicted_return_pct == 3.0
        assert logs[0].confidence_band == "HIGH"
        assert logs[0].horizon_days == 7


def test_update_realized_outcome_computes_error_and_directional_match(beta_context, sample_hypothesis_definition):
    """Test that update_realized_outcome correctly computes prediction error and directional match."""
    prediction_time = _utcnow()
    
    # Create prediction expecting +3% return
    log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=3.0,
        confidence_score=0.75,
        prediction_time=prediction_time,
        horizon_days=5,
    )
    
    # Update with realized return of +2.5% (directional match, small error)
    realization_time = prediction_time + timedelta(days=5)
    BetaPredictionAccuracyService.update_realized_outcome(
        prediction_log_id=log_id,
        realized_return_pct=2.5,
        realization_time=realization_time,
    )
    
    with BetaContext.read_session() as sess:
        log = sess.get(BetaPredictionAccuracyLog, log_id)
        assert log is not None
        assert log.realized_return_pct == 2.5
        assert log.realization_time == realization_time
        assert log.prediction_error_pct == -0.5  # 2.5 - 3.0
        assert log.directional_match == 1  # Both positive


def test_update_realized_outcome_detects_directional_mismatch(beta_context, sample_hypothesis_definition):
    """Test that directional mismatch is correctly detected."""
    prediction_time = _utcnow()
    
    # Create prediction expecting +3% return
    log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=3.0,
        confidence_score=0.75,
        prediction_time=prediction_time,
        horizon_days=5,
    )
    
    # Update with realized return of -1.5% (directional mismatch)
    BetaPredictionAccuracyService.update_realized_outcome(
        prediction_log_id=log_id,
        realized_return_pct=-1.5,
        realization_time=prediction_time + timedelta(days=5),
    )
    
    with BetaContext.read_session() as sess:
        log = sess.get(BetaPredictionAccuracyLog, log_id)
        assert log is not None
        assert log.directional_match == 0  # Predicted positive, realized negative


def test_compute_calibration_metrics_with_no_predictions(beta_context):
    """Test calibration metrics computation with no predictions."""
    result = BetaPredictionAccuracyService.compute_calibration_metrics(lookback_days=30)
    
    assert "error" not in result
    assert result.get("overall", {}).get("count", 0) == 0


def test_compute_calibration_metrics_aggregates_by_confidence_band(beta_context, sample_hypothesis_definition):
    """Test that calibration metrics are correctly aggregated by confidence band."""
    base_time = _utcnow() - timedelta(days=10)
    
    # Create predictions across different confidence bands
    predictions = [
        # HIGH confidence predictions (2 correct, 1 wrong)
        {"predicted": 3.0, "realized": 2.8, "confidence": 0.85},
        {"predicted": 2.5, "realized": 2.3, "confidence": 0.80},
        {"predicted": 3.5, "realized": -1.0, "confidence": 0.75},
        
        # MEDIUM confidence predictions (1 correct, 1 wrong)
        {"predicted": 2.0, "realized": 1.8, "confidence": 0.65},
        {"predicted": 1.5, "realized": -0.5, "confidence": 0.60},
        
        # LOW confidence predictions (1 correct)
        {"predicted": 1.0, "realized": 0.8, "confidence": 0.45},
    ]
    
    for i, pred in enumerate(predictions):
        log_id = BetaPredictionAccuracyService.log_prediction(
            hypothesis_definition_id=sample_hypothesis_definition,
            predicted_return_pct=pred["predicted"],
            confidence_score=pred["confidence"],
            prediction_time=base_time + timedelta(hours=i),
            horizon_days=5,
        )
        BetaPredictionAccuracyService.update_realized_outcome(
            prediction_log_id=log_id,
            realized_return_pct=pred["realized"],
            realization_time=base_time + timedelta(days=5, hours=i),
        )
    
    # Compute calibration metrics
    result = BetaPredictionAccuracyService.compute_calibration_metrics(lookback_days=30)
    
    assert "overall" in result
    assert result["overall"]["count"] == 6
    assert result["overall"]["directional_accuracy_pct"] == pytest.approx(66.67, abs=0.1)  # 4 out of 6 correct
    
    assert "by_confidence_band" in result
    
    # HIGH band: 2 correct out of 3 = 66.67%
    high_band = result["by_confidence_band"]["HIGH"]
    assert high_band["count"] == 3
    assert high_band["directional_accuracy_pct"] == pytest.approx(66.67, abs=0.1)
    
    # MEDIUM band: 1 correct out of 2 = 50%
    medium_band = result["by_confidence_band"]["MEDIUM"]
    assert medium_band["count"] == 2
    assert medium_band["directional_accuracy_pct"] == pytest.approx(50.0, abs=0.1)
    
    # LOW band: 1 correct out of 1 = 100%
    low_band = result["by_confidence_band"]["LOW"]
    assert low_band["count"] == 1
    assert low_band["directional_accuracy_pct"] == pytest.approx(100.0, abs=0.1)


def test_compute_calibration_metrics_calculates_mae_and_rmse(beta_context, sample_hypothesis_definition):
    """Test that MAE and RMSE are correctly calculated."""
    base_time = _utcnow() - timedelta(days=5)
    
    # Create predictions with known errors
    predictions = [
        {"predicted": 3.0, "realized": 2.0},  # Error: -1.0
        {"predicted": 2.0, "realized": 4.0},  # Error: +2.0
        {"predicted": 1.0, "realized": 1.5},  # Error: +0.5
    ]
    
    for i, pred in enumerate(predictions):
        log_id = BetaPredictionAccuracyService.log_prediction(
            hypothesis_definition_id=sample_hypothesis_definition,
            predicted_return_pct=pred["predicted"],
            confidence_score=0.75,
            prediction_time=base_time + timedelta(hours=i),
            horizon_days=5,
        )
        BetaPredictionAccuracyService.update_realized_outcome(
            prediction_log_id=log_id,
            realized_return_pct=pred["realized"],
            realization_time=base_time + timedelta(days=5, hours=i),
        )
    
    result = BetaPredictionAccuracyService.compute_calibration_metrics(lookback_days=30)
    
    # MAE = (1.0 + 2.0 + 0.5) / 3 = 1.167
    assert result["overall"]["mae"] == pytest.approx(1.167, abs=0.01)
    
    # RMSE = sqrt((1.0^2 + 2.0^2 + 0.5^2) / 3) = sqrt(5.25/3) = 1.323
    assert result["overall"]["rmse"] == pytest.approx(1.323, abs=0.01)


def test_compute_calibration_metrics_filters_by_hypothesis(beta_context, sample_hypothesis_definition):
    """Test that calibration metrics can be filtered by hypothesis definition."""
    with BetaContext.write_session() as sess:
        other_definition = BetaHypothesisDefinition(
            hypothesis_code="OTHER_HYPOTHESIS_V1",
            name="Other Hypothesis",
            entry_conditions_json='{}',
            regime_filters_json='{}',
            holding_period_days=5,
            status="CANDIDATE",
        )
        sess.add(other_definition)
        sess.flush()
        other_definition_id = other_definition.id
    
    base_time = _utcnow() - timedelta(days=5)
    
    # Create predictions for both hypotheses
    for hypothesis_id in [sample_hypothesis_definition, other_definition_id]:
        log_id = BetaPredictionAccuracyService.log_prediction(
            hypothesis_definition_id=hypothesis_id,
            predicted_return_pct=2.0,
            confidence_score=0.75,
            prediction_time=base_time,
            horizon_days=5,
        )
        BetaPredictionAccuracyService.update_realized_outcome(
            prediction_log_id=log_id,
            realized_return_pct=1.8,
            realization_time=base_time + timedelta(days=5),
        )
    
    # Get metrics for specific hypothesis
    result = BetaPredictionAccuracyService.compute_calibration_metrics(
        hypothesis_definition_id=sample_hypothesis_definition,
        lookback_days=30,
    )
    
    assert result["overall"]["count"] == 1


def test_compute_calibration_metrics_respects_time_window(beta_context, sample_hypothesis_definition):
    """Test that calibration metrics respect the specified time window."""
    now = _utcnow()
    
    # Create old prediction (outside window)
    old_log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=2.0,
        confidence_score=0.75,
        prediction_time=now - timedelta(days=40),
        horizon_days=5,
    )
    BetaPredictionAccuracyService.update_realized_outcome(
        prediction_log_id=old_log_id,
        realized_return_pct=1.8,
        realization_time=now - timedelta(days=35),
    )
    
    # Create recent prediction (inside window)
    recent_log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=3.0,
        confidence_score=0.75,
        prediction_time=now - timedelta(days=10),
        horizon_days=5,
    )
    BetaPredictionAccuracyService.update_realized_outcome(
        prediction_log_id=recent_log_id,
        realized_return_pct=2.8,
        realization_time=now - timedelta(days=5),
    )
    
    # Get metrics for last 30 days
    result = BetaPredictionAccuracyService.compute_calibration_metrics(lookback_days=30)
    
    # Should only include the recent prediction
    assert result["overall"]["count"] == 1


def test_compute_calibration_metrics_upserts_existing_rows(beta_context, sample_hypothesis_definition):
    """Repeated calibration runs for the same window should update, not duplicate, stored rows."""
    prediction_time = _utcnow() - timedelta(days=2)
    log_id = BetaPredictionAccuracyService.log_prediction(
        hypothesis_definition_id=sample_hypothesis_definition,
        predicted_return_pct=2.0,
        confidence_score=0.80,
        prediction_time=prediction_time,
        horizon_days=5,
    )
    BetaPredictionAccuracyService.update_realized_outcome(
        prediction_log_id=log_id,
        realized_return_pct=2.4,
        realization_time=prediction_time + timedelta(days=1),
    )

    first = BetaPredictionAccuracyService.compute_calibration_metrics(lookback_days=30)
    second = BetaPredictionAccuracyService.compute_calibration_metrics(lookback_days=30)

    assert first["overall"]["count"] == 1
    assert second["overall"]["count"] == 1

    with BetaContext.read_session() as sess:
        rows = list(
            sess.scalars(
                select(BetaCalibrationMetrics).where(
                    BetaCalibrationMetrics.hypothesis_definition_id.is_(None),
                    BetaCalibrationMetrics.confidence_band == "HIGH",
                    BetaCalibrationMetrics.evaluation_period_start == first["period_start"].date(),
                    BetaCalibrationMetrics.evaluation_period_end == first["period_end"].date(),
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].prediction_count == 1


def test_confidence_band_thresholds(beta_context, sample_hypothesis_definition):
    """Test that confidence band thresholds are correctly applied."""
    test_cases = [
        (0.95, "HIGH"),
        (0.70, "HIGH"),
        (0.69, "MEDIUM"),
        (0.50, "MEDIUM"),
        (0.49, "LOW"),
        (0.20, "LOW"),
    ]
    
    for confidence, expected_band in test_cases:
        log_id = BetaPredictionAccuracyService.log_prediction(
            hypothesis_definition_id=sample_hypothesis_definition,
            predicted_return_pct=2.0,
            confidence_score=confidence,
            prediction_time=_utcnow(),
            horizon_days=5,
        )
        
        with BetaContext.read_session() as sess:
            log = sess.get(BetaPredictionAccuracyLog, log_id)
            assert log is not None
            assert log.confidence_band == expected_band, f"Confidence {confidence} should be {expected_band}, got {log.confidence_band}"
