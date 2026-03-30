"""Storage classification and retention controls for the beta runtime."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionLabelValue,
    BetaFeatureValue,
    BetaHypothesisBeliefState,
    BetaHypothesisTestRun,
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradayFeatureSnapshot,
    BetaIntradaySnapshot,
    BetaJobRun,
    BetaLabelValue,
    BetaMinuteBar,
    BetaPipelineSnapshot,
    BetaRecommendationDecision,
    BetaScoreTape,
    BetaSignalCandidateEvent,
)
from ..settings import BetaSettings
from ..state import get_beta_db_path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _job_details(job: BetaJobRun | None) -> dict[str, object]:
    if job is None or not job.details_json:
        return {}
    try:
        payload = json.loads(job.details_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class BetaStorageGovernanceService:
    """Separate long-lived research assets from short-lived operational exhaust."""

    _NON_ACTIONABLE_DECISION_STATUSES = ("BLOCKED", "DISMISSED", "REJECTED")

    @staticmethod
    def build_profile(*, settings: BetaSettings | None = None) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {
                "available": False,
                "summary_text": "Beta storage profile unavailable.",
            }

        beta_db_path = get_beta_db_path()
        profile_settings = settings or (BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings())
        db_size_bytes = int(beta_db_path.stat().st_size) if beta_db_path is not None and beta_db_path.exists() else 0

        with BetaContext.read_session() as sess:
            transient_counts = {
                "pipeline_snapshots": int(sess.scalar(select(func.count()).select_from(BetaPipelineSnapshot)) or 0),
                "job_runs": int(sess.scalar(select(func.count()).select_from(BetaJobRun)) or 0),
                "score_tape": int(sess.scalar(select(func.count()).select_from(BetaScoreTape)) or 0),
                "recommendation_decisions": int(
                    sess.scalar(select(func.count()).select_from(BetaRecommendationDecision)) or 0
                ),
                "intraday_snapshots": int(sess.scalar(select(func.count()).select_from(BetaIntradaySnapshot)) or 0),
                "intraday_feature_snapshots": int(
                    sess.scalar(select(func.count()).select_from(BetaIntradayFeatureSnapshot)) or 0
                ),
                "minute_bars": int(sess.scalar(select(func.count()).select_from(BetaMinuteBar)) or 0),
                "candidate_events": int(sess.scalar(select(func.count()).select_from(BetaSignalCandidateEvent)) or 0),
            }
            permanent_counts = {
                "feature_values": int(sess.scalar(select(func.count()).select_from(BetaFeatureValue)) or 0),
                "label_values": int(sess.scalar(select(func.count()).select_from(BetaLabelValue)) or 0),
                "hypothesis_test_runs": int(
                    sess.scalar(select(func.count()).select_from(BetaHypothesisTestRun)) or 0
                ),
                "hypothesis_belief_states": int(
                    sess.scalar(select(func.count()).select_from(BetaHypothesisBeliefState)) or 0
                ),
                "execution_label_values": int(
                    sess.scalar(select(func.count()).select_from(BetaExecutionLabelValue)) or 0
                ),
            }
            retained_counts = {
                "intraday_feature_observations": int(
                    sess.scalar(select(func.count()).select_from(BetaIntradayFeatureObservation)) or 0
                ),
                "intraday_feature_label_values": int(
                    sess.scalar(select(func.count()).select_from(BetaIntradayFeatureLabelValue)) or 0
                ),
            }
            latest_cleanup_job = sess.scalar(
                select(BetaJobRun)
                .where(BetaJobRun.job_name == "beta_storage_retention")
                .order_by(desc(BetaJobRun.completed_at), desc(BetaJobRun.started_at))
                .limit(1)
            )

        transient_rows_total = int(sum(transient_counts.values()))
        permanent_rows_total = int(sum(permanent_counts.values()))
        retained_rows_total = int(sum(retained_counts.values()))
        retention_days = {
            "pipeline_snapshots": int(profile_settings.storage_pipeline_snapshot_retention_days),
            "job_runs": int(profile_settings.storage_job_run_retention_days),
            "score_tape_non_actionable": int(profile_settings.storage_score_tape_retention_days),
            "score_tape_actionable": int(profile_settings.storage_actionable_score_tape_retention_days),
            "recommendations_non_actionable": int(profile_settings.storage_recommendation_retention_days),
            "recommendations_actionable": int(profile_settings.storage_actionable_recommendation_retention_days),
            "intraday_snapshots": int(profile_settings.storage_intraday_snapshot_retention_days),
            "intraday_feature_snapshots": int(profile_settings.storage_intraday_feature_retention_days),
            "intraday_outlook_history": int(profile_settings.storage_intraday_outlook_retention_days),
            "minute_bars": int(profile_settings.storage_minute_bar_retention_days),
        }
        transient_share_pct = round(
            (transient_rows_total / max(1, transient_rows_total + permanent_rows_total + retained_rows_total)) * 100.0,
            2,
        )
        return {
            "available": True,
            "db_size_bytes": db_size_bytes,
            "db_size_mb": round(db_size_bytes / (1024.0 * 1024.0), 2),
            "db_size_gb": round(db_size_bytes / (1024.0 * 1024.0 * 1024.0), 4),
            "cleanup_enabled": bool(profile_settings.storage_cleanup_enabled),
            "retention_days": retention_days,
            "transient_counts": transient_counts,
            "permanent_counts": permanent_counts,
            "retained_counts": retained_counts,
            "transient_rows_total": transient_rows_total,
            "permanent_rows_total": permanent_rows_total,
            "retained_rows_total": retained_rows_total,
            "transient_share_pct": transient_share_pct,
            "asset_classes": {
                "permanent_research_assets": [
                    "beta_feature_values",
                    "beta_label_values",
                    "beta_hypothesis_test_runs",
                    "beta_hypothesis_belief_states",
                    "beta_execution_label_values",
                ],
                "retained_research_assets": [
                    "beta_intraday_feature_observations",
                    "beta_intraday_feature_label_values",
                ],
                "transient_operational_exhaust": [
                    "beta_pipeline_snapshots",
                    "beta_job_runs",
                    "beta_score_tape",
                    "beta_recommendation_decisions",
                    "beta_intraday_snapshots",
                    "beta_intraday_feature_snapshots",
                    "beta_minute_bars",
                    "beta_signal_candidate_events",
                ],
            },
            "last_cleanup_at": latest_cleanup_job.completed_at if latest_cleanup_job is not None else None,
            "last_cleanup_status": latest_cleanup_job.status if latest_cleanup_job is not None else None,
            "last_cleanup_details": _job_details(latest_cleanup_job),
            "summary_text": (
                f"DB {round(db_size_bytes / (1024.0 * 1024.0 * 1024.0), 2)} GB, transient rows "
                f"{transient_rows_total}, retained research rows {retained_rows_total}, "
                f"permanent rows {permanent_rows_total}, transient share {transient_share_pct}%."
            ),
        }

    @staticmethod
    def enforce_retention(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {
                "performed": False,
                "reason": "beta_unavailable",
            }
        if not settings.storage_cleanup_enabled:
            return {
                "performed": False,
                "reason": "storage_cleanup_disabled",
                "profile": BetaStorageGovernanceService.build_profile(settings=settings),
            }

        now = _utcnow()
        non_actionable_score_cutoff = now - timedelta(days=max(1, settings.storage_score_tape_retention_days))
        actionable_score_cutoff = now - timedelta(days=max(1, settings.storage_actionable_score_tape_retention_days))
        non_actionable_recommendation_cutoff = now - timedelta(
            days=max(1, settings.storage_recommendation_retention_days)
        )
        actionable_recommendation_cutoff = now - timedelta(
            days=max(1, settings.storage_actionable_recommendation_retention_days)
        )
        intraday_snapshot_cutoff = now - timedelta(days=max(1, settings.storage_intraday_snapshot_retention_days))
        intraday_feature_cutoff = now - timedelta(days=max(1, settings.storage_intraday_feature_retention_days))
        intraday_outlook_cutoff = now - timedelta(days=max(30, settings.storage_intraday_outlook_retention_days))
        minute_bar_cutoff = now - timedelta(days=max(1, settings.storage_minute_bar_retention_days))
        pipeline_cutoff = now - timedelta(days=max(1, settings.storage_pipeline_snapshot_retention_days))
        job_cutoff = now - timedelta(days=max(1, settings.storage_job_run_retention_days))

        deleted: dict[str, int] = {}
        with BetaContext.write_session() as sess:
            deleted["pipeline_snapshots"] = int(
                sess.execute(
                    delete(BetaPipelineSnapshot).where(BetaPipelineSnapshot.created_at < pipeline_cutoff)
                ).rowcount
                or 0
            )
            deleted["job_runs"] = int(
                sess.execute(
                    delete(BetaJobRun).where(
                        BetaJobRun.completed_at.is_not(None),
                        BetaJobRun.completed_at < job_cutoff,
                        BetaJobRun.status != "RUNNING",
                        BetaJobRun.job_name != "beta_storage_retention",
                    )
                ).rowcount
                or 0
            )
            deleted["score_tape_non_actionable"] = int(
                sess.execute(
                    delete(BetaScoreTape).where(
                        BetaScoreTape.recommendation_flag.is_(False),
                        BetaScoreTape.scored_at < non_actionable_score_cutoff,
                    )
                ).rowcount
                or 0
            )
            deleted["score_tape_actionable"] = int(
                sess.execute(
                    delete(BetaScoreTape).where(
                        BetaScoreTape.recommendation_flag.is_(True),
                        BetaScoreTape.scored_at < actionable_score_cutoff,
                    )
                ).rowcount
                or 0
            )
            deleted["recommendations_non_actionable"] = int(
                sess.execute(
                    delete(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.created_at < non_actionable_recommendation_cutoff,
                        BetaRecommendationDecision.decision_status.in_(
                            BetaStorageGovernanceService._NON_ACTIONABLE_DECISION_STATUSES
                        ),
                    )
                ).rowcount
                or 0
            )
            deleted["recommendations_watch_only"] = int(
                sess.execute(
                    delete(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.created_at < non_actionable_recommendation_cutoff,
                        BetaRecommendationDecision.decision_status == "WATCHING",
                        (
                            BetaRecommendationDecision.paper_trade_action.is_(None)
                            | (BetaRecommendationDecision.paper_trade_action == "WATCH_ONLY")
                        ),
                    )
                ).rowcount
                or 0
            )
            deleted["recommendations_actionable"] = int(
                sess.execute(
                    delete(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.created_at < actionable_recommendation_cutoff,
                        BetaRecommendationDecision.decision_status == "RECOMMENDED",
                    )
                ).rowcount
                or 0
            )
            deleted["intraday_snapshots"] = int(
                sess.execute(
                    delete(BetaIntradaySnapshot).where(BetaIntradaySnapshot.observed_at < intraday_snapshot_cutoff)
                ).rowcount
                or 0
            )
            deleted["intraday_feature_snapshots"] = int(
                sess.execute(
                    delete(BetaIntradayFeatureSnapshot).where(
                        BetaIntradayFeatureSnapshot.updated_at < intraday_feature_cutoff
                    )
                ).rowcount
                or 0
            )
            deleted["intraday_outlook_label_values"] = int(
                sess.execute(
                    delete(BetaIntradayFeatureLabelValue).where(
                        BetaIntradayFeatureLabelValue.observed_at < intraday_outlook_cutoff
                    )
                ).rowcount
                or 0
            )
            deleted["intraday_outlook_observations"] = int(
                sess.execute(
                    delete(BetaIntradayFeatureObservation).where(
                        BetaIntradayFeatureObservation.observed_at < intraday_outlook_cutoff
                    )
                ).rowcount
                or 0
            )
            deleted["minute_bars"] = int(
                sess.execute(delete(BetaMinuteBar).where(BetaMinuteBar.minute_ts < minute_bar_cutoff)).rowcount or 0
            )
            deleted["candidate_events"] = int(
                sess.execute(
                    delete(BetaSignalCandidateEvent).where(
                        BetaSignalCandidateEvent.created_at < actionable_recommendation_cutoff
                    )
                ).rowcount
                or 0
            )

        total_deleted = int(sum(deleted.values()))
        return {
            "performed": True,
            "rows_deleted": total_deleted,
            "deleted": deleted,
            "retention_days": {
                "pipeline_snapshots": int(settings.storage_pipeline_snapshot_retention_days),
                "job_runs": int(settings.storage_job_run_retention_days),
                "score_tape_non_actionable": int(settings.storage_score_tape_retention_days),
                "score_tape_actionable": int(settings.storage_actionable_score_tape_retention_days),
                "recommendations_non_actionable": int(settings.storage_recommendation_retention_days),
                "recommendations_actionable": int(settings.storage_actionable_recommendation_retention_days),
                "intraday_snapshots": int(settings.storage_intraday_snapshot_retention_days),
                "intraday_feature_snapshots": int(settings.storage_intraday_feature_retention_days),
                "intraday_outlook_history": int(settings.storage_intraday_outlook_retention_days),
                "minute_bars": int(settings.storage_minute_bar_retention_days),
            },
            "profile": BetaStorageGovernanceService.build_profile(settings=settings),
        }
