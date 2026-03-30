from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaHypothesisDefinition,
    BetaHypothesisFamily,
    BetaInstrument,
    BetaIntradayFeatureSnapshot,
    BetaIntradaySnapshot,
    BetaJobRun,
    BetaMinuteBar,
    BetaPipelineSnapshot,
    BetaRecommendationDecision,
    BetaScoreRun,
    BetaScoreTape,
    BetaSignalCandidate,
    BetaSignalCandidateEvent,
    BetaSignalObservation,
)
from src.beta.services.runtime_service import BetaRuntimeService
from src.beta.services.storage_governance_service import BetaStorageGovernanceService
from src.beta.settings import BetaSettings


@pytest.fixture()
def beta_context():
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


def test_storage_governance_prunes_old_transient_exhaust(beta_context, monkeypatch):
    now = datetime(2026, 3, 19, 12, 0, 0)
    old_time = now - timedelta(days=15)
    very_old_time = now - timedelta(days=25)
    recent_time = now - timedelta(days=1)

    settings = BetaSettings()
    settings.storage_pipeline_snapshot_retention_days = 7
    settings.storage_job_run_retention_days = 7
    settings.storage_score_tape_retention_days = 7
    settings.storage_actionable_score_tape_retention_days = 20
    settings.storage_recommendation_retention_days = 7
    settings.storage_actionable_recommendation_retention_days = 20
    settings.storage_intraday_snapshot_retention_days = 7
    settings.storage_intraday_feature_retention_days = 7
    settings.storage_minute_bar_retention_days = 10

    monkeypatch.setattr("src.beta.services.storage_governance_service._utcnow", lambda: now)

    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
            is_active=True,
        )
        family = BetaHypothesisFamily(
            family_code="REVERSAL",
            family_name="Reversal",
            generator_type="MANUAL",
            default_target_metric="fwd_3d_excess_return_pct",
            default_holding_period_days=3,
            status="ACTIVE",
        )
        sess.add_all([instrument, family])
        sess.flush()

        definition = BetaHypothesisDefinition(
            family_id=family.id,
            hypothesis_code="REVERSAL_TEST_V1",
            name="Reversal Test",
            universe_json=json.dumps({"markets": ["US"]}, sort_keys=True),
            entry_conditions_json=json.dumps(
                {"all": [{"feature": "ret_5d_pct", "op": "lt", "value": -1.0}]},
                sort_keys=True,
            ),
            holding_period_days=3,
            target_metric="fwd_3d_excess_return_pct",
            expected_direction="BULLISH",
            status="VALIDATED",
        )
        sess.add(definition)
        sess.flush()

        old_observation = BetaSignalObservation(
            hypothesis_definition_id=definition.id,
            instrument_id=instrument.id,
            symbol="IBM",
            observation_time=old_time,
            decision_date=date(2026, 3, 4),
            matched_conditions_json=json.dumps([], sort_keys=True),
            feature_snapshot_json=json.dumps({}, sort_keys=True),
            regime_context_json=json.dumps({"market": "US"}, sort_keys=True),
            prediction_source="MODEL",
            expected_direction="BULLISH",
            expected_return_pct=3.0,
            observation_status="MATCHED",
            created_at=old_time,
        )
        recent_observation = BetaSignalObservation(
            hypothesis_definition_id=definition.id,
            instrument_id=instrument.id,
            symbol="IBM",
            observation_time=recent_time,
            decision_date=date(2026, 3, 18),
            matched_conditions_json=json.dumps([], sort_keys=True),
            feature_snapshot_json=json.dumps({}, sort_keys=True),
            regime_context_json=json.dumps({"market": "US"}, sort_keys=True),
            prediction_source="MODEL",
            expected_direction="BULLISH",
            expected_return_pct=3.0,
            observation_status="MATCHED",
            created_at=recent_time,
        )
        sess.add_all([old_observation, recent_observation])
        sess.flush()

        candidate = BetaSignalCandidate(
            instrument_id=instrument.id,
            hypothesis_definition_id=definition.id,
            signal_observation_id=recent_observation.id,
            symbol="IBM",
            title="IBM thesis",
            status="WATCHING",
            direction="BULLISH",
            confidence_score=0.6,
            expected_edge_score=0.4,
            market="US",
            evidence_json=json.dumps({"predicted_return_pct": 3.0}, sort_keys=True),
            discovered_at=recent_time,
            updated_at=recent_time,
        )
        sess.add(candidate)
        sess.flush()

        old_score_run = BetaScoreRun(run_type="TEST", status="SUCCESS", scored_at=old_time)
        old_actionable_score_run = BetaScoreRun(
            run_type="TEST",
            status="SUCCESS",
            scored_at=now - timedelta(days=10),
        )
        recent_score_run = BetaScoreRun(run_type="TEST", status="SUCCESS", scored_at=recent_time)
        sess.add_all([old_score_run, old_actionable_score_run, recent_score_run])
        sess.flush()

        sess.add_all(
            [
                BetaPipelineSnapshot(
                    snapshot_type="TEST",
                    overall_status="HEALTHY",
                    summary_text="old",
                    metrics_json="{}",
                    created_at=old_time,
                ),
                BetaPipelineSnapshot(
                    snapshot_type="TEST",
                    overall_status="HEALTHY",
                    summary_text="recent",
                    metrics_json="{}",
                    created_at=recent_time,
                ),
                BetaJobRun(
                    job_name="beta_old_job",
                    job_type="test",
                    status="SUCCESS",
                    started_at=old_time,
                    completed_at=old_time,
                ),
                BetaJobRun(
                    job_name="beta_storage_retention",
                    job_type="storage",
                    status="SUCCESS",
                    started_at=old_time,
                    completed_at=old_time,
                ),
                BetaJobRun(
                    job_name="beta_running_job",
                    job_type="test",
                    status="RUNNING",
                    started_at=recent_time,
                ),
                BetaScoreTape(
                    score_run_id=old_score_run.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    direction="BULLISH",
                    predicted_return_5d=0.4,
                    confidence_score=0.45,
                    expected_edge_score=0.1,
                    recommendation_flag=False,
                    rejection_reason="below_threshold",
                    scored_at=old_time,
                ),
                BetaScoreTape(
                    score_run_id=old_actionable_score_run.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    direction="BULLISH",
                    predicted_return_5d=3.2,
                    confidence_score=0.8,
                    expected_edge_score=0.5,
                    recommendation_flag=True,
                    scored_at=now - timedelta(days=10),
                ),
                BetaScoreTape(
                    score_run_id=recent_score_run.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    direction="BULLISH",
                    predicted_return_5d=0.5,
                    confidence_score=0.46,
                    expected_edge_score=0.11,
                    recommendation_flag=False,
                    rejection_reason="below_threshold",
                    scored_at=recent_time,
                ),
                BetaRecommendationDecision(
                    signal_observation_id=old_observation.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    decision_status="REJECTED",
                    decision_reason_code="hypothesis_rejected",
                    belief_confidence_score=0.4,
                    recommendation_score=0.2,
                    created_at=old_time,
                ),
                BetaRecommendationDecision(
                    signal_observation_id=old_observation.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    decision_status="WATCHING",
                    decision_reason_code="promising_watch_only",
                    belief_confidence_score=0.55,
                    paper_trade_action="WATCH_ONLY",
                    recommendation_score=0.3,
                    created_at=old_time,
                ),
                BetaRecommendationDecision(
                    signal_observation_id=old_observation.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    decision_status="RECOMMENDED",
                    decision_reason_code="validated_hypothesis_match",
                    belief_confidence_score=0.82,
                    paper_trade_action="OPEN_IF_ALLOWED",
                    recommendation_score=0.9,
                    created_at=now - timedelta(days=10),
                ),
                BetaRecommendationDecision(
                    signal_observation_id=recent_observation.id,
                    instrument_id=instrument.id,
                    symbol="IBM",
                    decision_status="REJECTED",
                    decision_reason_code="hypothesis_rejected",
                    belief_confidence_score=0.4,
                    recommendation_score=0.2,
                    created_at=recent_time,
                ),
                BetaIntradaySnapshot(
                    instrument_id=instrument.id,
                    price_date=date(2026, 3, 4),
                    price_gbp="100",
                    price_native="100",
                    currency="USD",
                    observed_at=old_time,
                    source="test",
                ),
                BetaIntradaySnapshot(
                    instrument_id=instrument.id,
                    price_date=date(2026, 3, 18),
                    price_gbp="101",
                    price_native="101",
                    currency="USD",
                    observed_at=recent_time,
                    source="test",
                ),
                BetaIntradayFeatureSnapshot(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 4),
                    session_state="CLOSED",
                    feature_snapshot_json=json.dumps({}, sort_keys=True),
                    accumulator_state_json=json.dumps({}, sort_keys=True),
                    created_at=old_time,
                    updated_at=old_time,
                ),
                BetaIntradayFeatureSnapshot(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 18),
                    session_state="CLOSED",
                    feature_snapshot_json=json.dumps({}, sort_keys=True),
                    accumulator_state_json=json.dumps({}, sort_keys=True),
                    created_at=recent_time,
                    updated_at=recent_time,
                ),
                BetaMinuteBar(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 4),
                    minute_ts=old_time,
                    open_price_gbp="100",
                    high_price_gbp="100",
                    low_price_gbp="100",
                    close_price_gbp="100",
                    close_price_native="100",
                    currency="USD",
                    volume_native="1000",
                    snapshot_count=1,
                    first_snapshot_at=old_time,
                    last_snapshot_at=old_time,
                    source="test",
                ),
                BetaMinuteBar(
                    instrument_id=instrument.id,
                    session_date=date(2026, 3, 18),
                    minute_ts=recent_time,
                    open_price_gbp="101",
                    high_price_gbp="101",
                    low_price_gbp="101",
                    close_price_gbp="101",
                    close_price_native="101",
                    currency="USD",
                    volume_native="1200",
                    snapshot_count=1,
                    first_snapshot_at=recent_time,
                    last_snapshot_at=recent_time,
                    source="test",
                ),
                BetaSignalCandidateEvent(
                    candidate_id=candidate.id,
                    event_type="UPDATED",
                    message_text="old event",
                    payload_json="{}",
                    created_at=very_old_time,
                ),
                BetaSignalCandidateEvent(
                    candidate_id=candidate.id,
                    event_type="UPDATED",
                    message_text="recent event",
                    payload_json="{}",
                    created_at=recent_time,
                ),
            ]
        )

    result = BetaStorageGovernanceService.enforce_retention(settings)

    with BetaContext.read_session() as sess:
        pipeline_count = int(sess.scalar(select(func.count()).select_from(BetaPipelineSnapshot)) or 0)
        job_rows = list(sess.scalars(select(BetaJobRun).order_by(BetaJobRun.job_name.asc())).all())
        score_tape_count = int(sess.scalar(select(func.count()).select_from(BetaScoreTape)) or 0)
        recommendation_count = int(sess.scalar(select(func.count()).select_from(BetaRecommendationDecision)) or 0)
        intraday_snapshot_count = int(sess.scalar(select(func.count()).select_from(BetaIntradaySnapshot)) or 0)
        intraday_feature_count = int(sess.scalar(select(func.count()).select_from(BetaIntradayFeatureSnapshot)) or 0)
        minute_bar_count = int(sess.scalar(select(func.count()).select_from(BetaMinuteBar)) or 0)
        candidate_event_count = int(sess.scalar(select(func.count()).select_from(BetaSignalCandidateEvent)) or 0)

    assert result["performed"] is True
    assert result["deleted"]["pipeline_snapshots"] == 1
    assert result["deleted"]["job_runs"] == 1
    assert result["deleted"]["score_tape_non_actionable"] == 1
    assert result["deleted"]["score_tape_actionable"] == 0
    assert result["deleted"]["recommendations_non_actionable"] == 1
    assert result["deleted"]["recommendations_watch_only"] == 1
    assert result["deleted"]["recommendations_actionable"] == 0
    assert result["deleted"]["intraday_snapshots"] == 1
    assert result["deleted"]["intraday_feature_snapshots"] == 1
    assert result["deleted"]["minute_bars"] == 1
    assert result["deleted"]["candidate_events"] == 1
    assert pipeline_count == 1
    assert sorted(row.job_name for row in job_rows) == ["beta_running_job", "beta_storage_retention"]
    assert score_tape_count == 2
    assert recommendation_count == 2
    assert intraday_snapshot_count == 1
    assert intraday_feature_count == 1
    assert minute_bar_count == 1
    assert candidate_event_count == 1


def test_storage_governance_result_serializes_previous_cleanup_timestamps(beta_context, monkeypatch):
    now = datetime(2026, 3, 19, 12, 0, 0)
    previous_cleanup_at = now - timedelta(days=1)
    settings = BetaSettings()

    monkeypatch.setattr("src.beta.services.storage_governance_service._utcnow", lambda: now)

    with BetaContext.write_session() as sess:
        sess.add(
            BetaJobRun(
                job_name="beta_storage_retention",
                job_type="storage",
                status="SUCCESS",
                details_json=json.dumps({"rows_deleted": 12}, sort_keys=True),
                started_at=previous_cleanup_at,
                completed_at=previous_cleanup_at,
            )
        )

    result = BetaStorageGovernanceService.enforce_retention(settings)
    job_run_id = BetaRuntimeService.start_job_run(
        job_name="beta_storage_retention",
        job_type="storage",
    )

    BetaRuntimeService.finish_job_run(
        job_run_id,
        status="SUCCESS",
        details=result,
        completed_at=now,
    )

    with BetaContext.read_session() as sess:
        stored_job = sess.get(BetaJobRun, job_run_id)

    assert stored_job is not None
    payload = json.loads(stored_job.details_json or "{}")
    assert payload["profile"]["last_cleanup_at"] == previous_cleanup_at.isoformat()
