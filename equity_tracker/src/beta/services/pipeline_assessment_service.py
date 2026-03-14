"""Compute DB-backed pipeline health snapshots for the beta runtime."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaDailyBar,
    BetaEvaluationRun,
    BetaFeatureValue,
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaHypothesisTestRun,
    BetaHypothesis,
    BetaInstrument,
    BetaIntradaySnapshot,
    BetaJobRun,
    BetaLabelValue,
    BetaModelVersion,
    BetaPipelineSnapshot,
    BetaRecommendationDecision,
    BetaScoreRun,
    BetaScoreTape,
    BetaSignalObservation,
    BetaSignalCandidate,
    BetaStrategyVersion,
    BetaTrainingDecision,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _job_details(job: BetaJobRun | None) -> dict[str, object]:
    if job is None or not job.details_json:
        return {}
    try:
        payload = json.loads(job.details_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _score_run_details(run: BetaScoreRun | None) -> dict[str, object]:
    if run is None or not run.notes_json:
        return {}
    try:
        payload = json.loads(run.notes_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _training_decision_details(decision: BetaTrainingDecision | None) -> dict[str, object]:
    if decision is None or not decision.notes_json:
        return {}
    try:
        payload = json.loads(decision.notes_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class BetaPipelineAssessmentService:
    """Persist compact, assessable summaries of the live beta loop."""

    _STALE_JOB_MINUTES = 15
    _STALE_OBSERVATION_MINUTES = 10
    _RECENT_SCORE_HOURS = 24

    @staticmethod
    def build_metrics() -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {
                "available": False,
                "overall_status": "UNAVAILABLE",
                "summary_text": "Beta context is not initialized.",
            }

        now = _utcnow()
        recent_score_cutoff = now - timedelta(hours=BetaPipelineAssessmentService._RECENT_SCORE_HOURS)
        recent_observation_cutoff = now - timedelta(minutes=BetaPipelineAssessmentService._STALE_OBSERVATION_MINUTES)
        stale_job_cutoff = now - timedelta(minutes=BetaPipelineAssessmentService._STALE_JOB_MINUTES)
        today = date.today()

        with BetaContext.read_session() as sess:
            active_instruments = list(
                sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all()
            )
            instrument_ids = [row.id for row in active_instruments]
            tracked_core_ids = [row.id for row in active_instruments if row.core_security_id]
            active_universe_count = len(active_instruments)
            tracked_core_count = len(tracked_core_ids)

            latest_daily_date = sess.scalar(select(func.max(BetaDailyBar.bar_date)))
            latest_intraday_at = sess.scalar(select(func.max(BetaIntradaySnapshot.observed_at)))
            latest_model = sess.scalar(
                select(BetaModelVersion).order_by(desc(BetaModelVersion.created_at)).limit(1)
            )
            active_model = sess.scalar(
                select(BetaModelVersion)
                .where(BetaModelVersion.is_active.is_(True))
                .order_by(desc(BetaModelVersion.activated_at), desc(BetaModelVersion.created_at))
                .limit(1)
            )
            active_strategy = sess.scalar(
                select(BetaStrategyVersion)
                .where(BetaStrategyVersion.is_active.is_(True))
                .order_by(desc(BetaStrategyVersion.activated_at), desc(BetaStrategyVersion.created_at))
                .limit(1)
            )
            latest_evaluation = sess.scalar(
                select(BetaEvaluationRun).order_by(desc(BetaEvaluationRun.created_at)).limit(1)
            )
            latest_training_decision = sess.scalar(
                select(BetaTrainingDecision).order_by(desc(BetaTrainingDecision.created_at)).limit(1)
            )
            latest_training_decision_details = _training_decision_details(latest_training_decision)
            latest_training_model = (
                sess.get(BetaModelVersion, latest_training_decision.model_version_id)
                if latest_training_decision is not None and latest_training_decision.model_version_id
                else None
            )

            latest_jobs = {}
            for job_name in (
                "beta_learning_universe_sync",
                "beta_daily_observation_sync",
                "beta_tracked_core_feature_build",
                "beta_tracked_core_label_build",
                "beta_feature_backlog_build",
                "beta_label_backlog_build",
                "beta_daily_feature_build",
                "beta_daily_label_build",
                "beta_daily_shadow_cycle",
                "beta_tracked_core_shadow_cycle",
                "beta_live_evaluation",
                "beta_hypothesis_backtests",
                "beta_hypothesis_belief_refresh",
                "beta_hypothesis_refresh",
                "beta_daily_training",
                "beta_daily_potential_gains_review",
                ):
                latest_jobs[job_name] = sess.scalar(
                    select(BetaJobRun)
                    .where(BetaJobRun.job_name == job_name)
                    .order_by(desc(BetaJobRun.completed_at))
                    .limit(1)
                )

            recent_score_runs = list(
                sess.scalars(select(BetaScoreRun).order_by(desc(BetaScoreRun.scored_at)).limit(20)).all()
            )
            latest_core_score_run = None
            latest_full_score_run = None
            latest_core_score_details: dict[str, object] = {}
            latest_full_score_details: dict[str, object] = {}
            for score_run in recent_score_runs:
                details = _score_run_details(score_run)
                scope = str(details.get("scope") or "")
                if latest_core_score_run is None and scope == "CORE_TRACKED":
                    latest_core_score_run = score_run
                    latest_core_score_details = details
                elif latest_full_score_run is None and scope == "FULL_UNIVERSE":
                    latest_full_score_run = score_run
                    latest_full_score_details = details
                if latest_core_score_run is not None and latest_full_score_run is not None:
                    break

            instruments_with_daily_latest = 0
            instruments_with_intraday_recent = 0
            instruments_with_feature_latest = 0
            instruments_with_label_total = 0
            if instrument_ids:
                if latest_daily_date is not None:
                    instruments_with_daily_latest = int(
                        sess.scalar(
                            select(func.count(func.distinct(BetaDailyBar.instrument_id))).where(
                                BetaDailyBar.instrument_id.in_(instrument_ids),
                                BetaDailyBar.bar_date == latest_daily_date,
                            )
                        )
                        or 0
                    )
                    instruments_with_feature_latest = int(
                        sess.scalar(
                            select(func.count(func.distinct(BetaFeatureValue.instrument_id))).where(
                                BetaFeatureValue.instrument_id.in_(instrument_ids),
                                BetaFeatureValue.feature_date == latest_daily_date,
                            )
                        )
                        or 0
                    )
                if latest_intraday_at is not None:
                    instruments_with_intraday_recent = int(
                        sess.scalar(
                            select(func.count(func.distinct(BetaIntradaySnapshot.instrument_id))).where(
                                BetaIntradaySnapshot.instrument_id.in_(instrument_ids),
                                BetaIntradaySnapshot.observed_at >= recent_observation_cutoff,
                            )
                        )
                        or 0
                    )
                instruments_with_label_total = int(
                    sess.scalar(
                        select(func.count(func.distinct(BetaLabelValue.instrument_id))).where(
                            BetaLabelValue.instrument_id.in_(instrument_ids),
                        )
                    )
                    or 0
                )

            total_scores = int(sess.scalar(select(func.count()).select_from(BetaScoreTape)) or 0)
            scores_last_24h = int(
                sess.scalar(
                    select(func.count()).select_from(BetaScoreTape).where(BetaScoreTape.scored_at >= recent_score_cutoff)
                )
                or 0
            )
            tracked_core_scores_last_24h = int(
                sess.scalar(
                    select(func.count()).select_from(BetaScoreTape).where(
                        BetaScoreTape.scored_at >= recent_score_cutoff,
                        BetaScoreTape.instrument_id.in_(tracked_core_ids if tracked_core_ids else [""]),
                    )
                )
                or 0
            ) if tracked_core_ids else 0
            candidates_active = int(
                sess.scalar(
                    select(func.count()).select_from(BetaSignalCandidate).where(
                        BetaSignalCandidate.status.in_(("WATCHING", "PROMOTED"))
                    )
                )
                or 0
            )
            hypotheses_total = int(sess.scalar(select(func.count()).select_from(BetaHypothesis)) or 0)
            hypotheses_promoted = int(
                sess.scalar(
                    select(func.count()).select_from(BetaHypothesis).where(BetaHypothesis.status == "PROMOTED")
                )
                or 0
            )
            hypothesis_definition_count = int(
                sess.scalar(select(func.count()).select_from(BetaHypothesisDefinition)) or 0
            )
            validated_hypothesis_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaHypothesisBeliefState).where(
                        BetaHypothesisBeliefState.status == "VALIDATED"
                    )
                )
                or 0
            )
            promising_hypothesis_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaHypothesisBeliefState).where(
                        BetaHypothesisBeliefState.status == "PROMISING"
                    )
                )
                or 0
            )
            signal_observations_last_24h = int(
                sess.scalar(
                    select(func.count()).select_from(BetaSignalObservation).where(
                        BetaSignalObservation.created_at >= recent_score_cutoff
                    )
                )
                or 0
            )
            recommendation_decisions_last_24h = int(
                sess.scalar(
                    select(func.count()).select_from(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.created_at >= recent_score_cutoff
                    )
                )
                or 0
            )
            recommended_decisions_last_24h = int(
                sess.scalar(
                    select(func.count()).select_from(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.created_at >= recent_score_cutoff,
                        BetaRecommendationDecision.decision_status == "RECOMMENDED",
                    )
                )
                or 0
            )
            latest_hypothesis_test = sess.scalar(
                select(BetaHypothesisTestRun).order_by(desc(BetaHypothesisTestRun.created_at)).limit(1)
            )
            latest_belief_update = sess.scalar(
                select(BetaHypothesisBeliefState).order_by(desc(BetaHypothesisBeliefState.updated_at)).limit(1)
            )

            latest_training_job = latest_jobs["beta_daily_training"]
            latest_scoring_job = latest_jobs["beta_daily_shadow_cycle"]
            latest_core_scoring_job = latest_jobs["beta_tracked_core_shadow_cycle"]
            latest_observation_job = latest_jobs["beta_daily_observation_sync"]
            latest_feature_job = (
                latest_jobs.get("beta_feature_backlog_build")
                or latest_jobs.get("beta_tracked_core_feature_build")
                or latest_jobs.get("beta_daily_feature_build")
            )
            latest_label_job = (
                latest_jobs.get("beta_label_backlog_build")
                or latest_jobs.get("beta_tracked_core_label_build")
                or latest_jobs.get("beta_daily_label_build")
            )
            latest_hypothesis_backtest_job = latest_jobs["beta_hypothesis_backtests"]
            latest_belief_job = latest_jobs["beta_hypothesis_belief_refresh"]
            latest_hypothesis_job = latest_jobs["beta_hypothesis_refresh"]
            latest_training_details = _job_details(latest_training_job)
            latest_scoring_details = _job_details(latest_scoring_job)
            latest_core_scoring_details = _job_details(latest_core_scoring_job)

            def lane_metrics(
                *,
                score_run: BetaScoreRun | None,
                score_details: dict[str, object],
                job: BetaJobRun | None,
                job_details: dict[str, object],
            ) -> dict[str, object]:
                details = score_details if score_details else job_details
                completed_at = score_run.scored_at if score_run is not None else (job.completed_at if job is not None else None)
                status = score_run.status if score_run is not None else (job.status if job is not None else None)
                active_instruments = int(details.get("active_instruments") or 0)
                scored = int(details.get("scored") or 0)
                recommended = int(details.get("recommended") or 0)
                skipped_insufficient_bars = int(details.get("skipped_insufficient_bars") or 0)
                skipped_invalid_close = int(details.get("skipped_invalid_close") or 0)
                skipped_missing_features = int(details.get("skipped_missing_features") or 0)
                skipped_total = (
                    skipped_insufficient_bars
                    + skipped_invalid_close
                    + skipped_missing_features
                )
                coverage_pct = round((scored / active_instruments) * 100.0, 1) if active_instruments else 0.0
                recommendation_rate_pct = round((recommended / scored) * 100.0, 1) if scored else 0.0
                return {
                    "status": status,
                    "completed_at": _dt_to_iso(completed_at),
                    "scope": details.get("scope"),
                    "active_instruments": active_instruments,
                    "scored": scored,
                    "coverage_pct": coverage_pct,
                    "recommended": recommended,
                    "recommendation_rate_pct": recommendation_rate_pct,
                    "candidates_created": int(details.get("candidates_created") or 0),
                    "positions_opened": int(details.get("positions_opened") or 0),
                    "positions_closed": int(details.get("positions_closed") or 0),
                    "skipped_total": skipped_total,
                    "skipped_insufficient_bars": skipped_insufficient_bars,
                    "skipped_invalid_close": skipped_invalid_close,
                    "skipped_missing_features": skipped_missing_features,
                    "active_model_version": details.get("active_model_version"),
                    "active_strategy_version": details.get("active_strategy_version"),
                }

            scoring_completed_at = (
                latest_full_score_run.scored_at
                if latest_full_score_run is not None
                else (latest_scoring_job.completed_at if latest_scoring_job is not None else None)
            )
            core_scoring_completed_at = (
                latest_core_score_run.scored_at
                if latest_core_score_run is not None
                else (latest_core_scoring_job.completed_at if latest_core_scoring_job is not None else None)
            )
            observation_fresh = bool(
                latest_observation_job is not None
                and latest_observation_job.status == "SUCCESS"
                and latest_observation_job.completed_at is not None
                and latest_observation_job.completed_at >= recent_observation_cutoff
            )
            scoring_recent = bool(
                scoring_completed_at is not None
                and scoring_completed_at >= stale_job_cutoff
            )
            core_scoring_recent = bool(
                core_scoring_completed_at is not None
                and core_scoring_completed_at >= stale_job_cutoff
            )
            feature_recent = bool(
                latest_feature_job is not None
                and latest_feature_job.status == "SUCCESS"
                and latest_feature_job.completed_at is not None
                and latest_feature_job.completed_at >= stale_job_cutoff
            )
            label_recent = bool(
                latest_label_job is not None
                and latest_label_job.status == "SUCCESS"
                and latest_label_job.completed_at is not None
                and latest_label_job.completed_at >= stale_job_cutoff
            )
            training_recent = bool(
                latest_training_job is not None
                and latest_training_job.status == "SUCCESS"
                and latest_training_job.completed_at is not None
                and latest_training_job.completed_at.date() == today
            )
            evaluation_recent = bool(
                latest_evaluation is not None
                and latest_evaluation.created_at >= stale_job_cutoff
            )
            hypothesis_recent = bool(
                latest_hypothesis_job is not None
                and latest_hypothesis_job.status == "SUCCESS"
                and latest_hypothesis_job.completed_at is not None
                and latest_hypothesis_job.completed_at >= stale_job_cutoff
            )
            backtests_recent = bool(
                (
                    latest_hypothesis_backtest_job is not None
                    and latest_hypothesis_backtest_job.status == "SUCCESS"
                    and latest_hypothesis_backtest_job.completed_at is not None
                    and latest_hypothesis_backtest_job.completed_at >= (now - timedelta(hours=2))
                )
                or (
                    latest_hypothesis_test is not None
                    and latest_hypothesis_test.created_at >= (now - timedelta(hours=2))
                )
            )
            beliefs_recent = bool(
                (
                    latest_belief_job is not None
                    and latest_belief_job.status == "SUCCESS"
                    and latest_belief_job.completed_at is not None
                    and latest_belief_job.completed_at >= (now - timedelta(hours=2))
                )
                or (
                    latest_belief_update is not None
                    and latest_belief_update.updated_at >= (now - timedelta(hours=2))
                )
            )

            if active_universe_count <= 0:
                overall_status = "BOOTSTRAPPING"
            elif not observation_fresh:
                overall_status = "DEGRADED"
            elif tracked_core_count > 0 and (tracked_core_scores_last_24h <= 0 or not core_scoring_recent):
                overall_status = "DEGRADED"
            elif scores_last_24h <= 0 or not scoring_recent:
                overall_status = "DEGRADED"
            elif validated_hypothesis_count <= 0:
                overall_status = "DEGRADED"
            elif candidates_active <= 0 and tracked_core_count > 0:
                overall_status = "DEGRADED"
            else:
                overall_status = "HEALTHY"

            metrics = {
                "available": True,
                "overall_status": overall_status,
                "active_universe_count": active_universe_count,
                "tracked_core_instrument_count": tracked_core_count,
                "instruments_with_latest_daily_bar": instruments_with_daily_latest,
                "instruments_with_recent_intraday_snapshot": instruments_with_intraday_recent,
                "instruments_with_latest_feature_rows": instruments_with_feature_latest,
                "instruments_with_any_labels": instruments_with_label_total,
                "latest_daily_bar_date": str(latest_daily_date) if latest_daily_date is not None else None,
                "latest_intraday_snapshot_at": _dt_to_iso(latest_intraday_at),
                "total_scores": total_scores,
                "scores_last_24h": scores_last_24h,
                "tracked_core_scores_last_24h": tracked_core_scores_last_24h,
                "active_candidates": candidates_active,
                "hypotheses_total": hypotheses_total,
                "hypotheses_promoted": hypotheses_promoted,
                "hypothesis_definition_count": hypothesis_definition_count,
                "validated_hypothesis_count": validated_hypothesis_count,
                "promising_hypothesis_count": promising_hypothesis_count,
                "signal_observations_last_24h": signal_observations_last_24h,
                "recommendation_decisions_last_24h": recommendation_decisions_last_24h,
                "recommended_decisions_last_24h": recommended_decisions_last_24h,
                "active_model_version": active_model.version_code if active_model is not None else None,
                "active_strategy_version": active_strategy.version_code if active_strategy is not None else None,
                "latest_model_version": latest_model.version_code if latest_model is not None else None,
                "active_model_accuracy_pct": (
                    float(active_model.validation_sign_accuracy_pct)
                    if active_model is not None and active_model.validation_sign_accuracy_pct is not None
                    else None
                ),
                "latest_model_created_at": _dt_to_iso(latest_model.created_at) if latest_model is not None else None,
                "latest_evaluation_at": _dt_to_iso(latest_evaluation.created_at) if latest_evaluation is not None else None,
                "observation_fresh": observation_fresh,
                "feature_recent": feature_recent,
                "label_recent": label_recent,
                "scoring_recent": scoring_recent,
                "core_scoring_recent": core_scoring_recent,
                "training_recent": training_recent,
                "evaluation_recent": evaluation_recent,
                "hypothesis_recent": hypothesis_recent,
                "backtests_recent": backtests_recent,
                "beliefs_recent": beliefs_recent,
                "latest_hypothesis_test_at": _dt_to_iso(latest_hypothesis_test.created_at if latest_hypothesis_test is not None else None),
                "latest_belief_update_at": _dt_to_iso(latest_belief_update.updated_at if latest_belief_update is not None else None),
                "lane_metrics": {
                    "tracked_core": lane_metrics(
                        score_run=latest_core_score_run,
                        score_details=latest_core_score_details,
                        job=latest_core_scoring_job,
                        job_details=latest_core_scoring_details,
                    ),
                    "full_universe": lane_metrics(
                        score_run=latest_full_score_run,
                        score_details=latest_full_score_details,
                        job=latest_scoring_job,
                        job_details=latest_scoring_details,
                    ),
                },
                "latest_training_state": {
                    "status": latest_training_decision.status_code if latest_training_decision is not None else (latest_training_job.status if latest_training_job is not None else None),
                    "completed_at": _dt_to_iso(latest_training_decision.created_at if latest_training_decision is not None else (latest_training_job.completed_at if latest_training_job is not None else None)),
                    "performed": bool(latest_training_decision.performed) if latest_training_decision is not None else (bool(latest_training_details.get("performed")) if latest_training_details else False),
                    "trained": bool(latest_training_decision.trained) if latest_training_decision is not None else (bool(latest_training_details.get("trained")) if latest_training_details else False),
                    "reason": latest_training_decision.reason_code if latest_training_decision is not None else latest_training_details.get("reason"),
                    "model_id": latest_training_decision.model_version_id if latest_training_decision is not None else latest_training_details.get("model_id"),
                    "version_code": latest_training_model.version_code if latest_training_model is not None else latest_training_details.get("version_code"),
                    "training_rows": latest_training_decision.training_rows if latest_training_decision is not None else latest_training_details.get("training_rows"),
                    "validation_rows": latest_training_decision.validation_rows if latest_training_decision is not None else latest_training_details.get("validation_rows"),
                    "validation_sign_accuracy_pct": latest_training_decision.validation_sign_accuracy_pct if latest_training_decision is not None else latest_training_details.get("validation_sign_accuracy_pct"),
                    "walkforward_validation_sign_accuracy_pct": latest_training_decision.walkforward_validation_sign_accuracy_pct if latest_training_decision is not None else latest_training_details.get("walkforward_validation_sign_accuracy_pct"),
                    "walkforward_window_count": latest_training_decision.walkforward_window_count if latest_training_decision is not None else latest_training_details.get("walkforward_window_count"),
                    "new_observations": latest_training_decision_details.get("new_observations", latest_training_details.get("new_observations")),
                    "model_age_hours": latest_training_decision_details.get("model_age_hours"),
                    "activation_gate_reasons": (
                        _job_details(latest_training_job).get("activation_gate_reasons")
                        if latest_training_job is not None
                        else None
                    ),
                    "decision_status_code": latest_training_decision.status_code if latest_training_decision is not None else None,
                    "decision_reason_code": latest_training_decision.reason_code if latest_training_decision is not None else None,
                },
                "latest_jobs": {
                    job_name: {
                        "status": job.status if job is not None else None,
                        "completed_at": _dt_to_iso(job.completed_at if job is not None else None),
                    }
                    for job_name, job in latest_jobs.items()
                },
            }
            tracked_lane = metrics["lane_metrics"]["tracked_core"]  # type: ignore[index]
            full_lane = metrics["lane_metrics"]["full_universe"]  # type: ignore[index]
            metrics["summary_text"] = (
                f"Universe {active_universe_count}, tracked core {tracked_core_count}, "
                f"latest daily coverage {instruments_with_daily_latest}/{active_universe_count}, "
                f"tracked-core lane {tracked_lane.get('scored', 0)}/{tracked_lane.get('active_instruments', 0)} "
                f"({tracked_lane.get('coverage_pct', 0)}%), full lane {full_lane.get('scored', 0)}/"
                f"{full_lane.get('active_instruments', 0)} ({full_lane.get('coverage_pct', 0)}%), "
                f"tracked-core scores last 24h {tracked_core_scores_last_24h}, scores last 24h {scores_last_24h}, "
                f"validated hypotheses {validated_hypothesis_count}, active candidates {candidates_active}, "
                f"overall {overall_status.lower()}."
            )
            return metrics

    @staticmethod
    def record_snapshot(*, snapshot_type: str = "SUPERVISOR_CYCLE", trigger_job_name: str | None = None) -> dict[str, object]:
        metrics = BetaPipelineAssessmentService.build_metrics()
        if not BetaContext.is_initialized():
            return metrics

        with BetaContext.write_session() as sess:
            sess.add(
                BetaPipelineSnapshot(
                    snapshot_type=snapshot_type,
                    trigger_job_name=trigger_job_name,
                    overall_status=str(metrics.get("overall_status") or "UNAVAILABLE"),
                    summary_text=str(metrics.get("summary_text") or ""),
                    metrics_json=json.dumps(metrics, sort_keys=True),
                )
            )
        return metrics
