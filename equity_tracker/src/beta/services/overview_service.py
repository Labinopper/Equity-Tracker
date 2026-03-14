"""Read-side aggregation for beta UI pages."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaAiReviewFinding,
    BetaAiReviewRun,
    BetaBenchmarkBar,
    BetaCashLedgerEntry,
    BetaConfidenceBucketSummary,
    BetaDemoPosition,
    BetaDemoPositionEvent,
    BetaDatasetVersion,
    BetaDirectionSummary,
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
    BetaJobRun,
    BetaLedgerState,
    BetaLabelValue,
    BetaModelVersion,
    BetaNewsArticle,
    BetaNewsArticleLink,
    BetaNewsSource,
    BetaRiskControlState,
    BetaScoreTape,
    BetaSignalCandidateEvent,
    BetaSignalCandidate,
    BetaStrategyVersion,
    BetaSystemStatus,
    BetaUiNotification,
    BetaUiSummarySnapshot,
    BetaUniverseMembership,
    BetaValidationRun,
)
from ..services.session_service import BetaMarketSessionService
from ..settings import BetaSettings
from ..state import get_beta_db_path
from ..services.training_service import BetaTrainingService


def _row_to_dict(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _process_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _seconds_since(moment) -> int | None:
    if moment is None:
        return None
    delta = _utcnow() - moment
    return max(0, int(delta.total_seconds()))


class BetaOverviewService:
    """Small query service for the beta dashboard and supporting pages."""

    @staticmethod
    def is_available() -> bool:
        return BetaContext.is_initialized()

    @staticmethod
    def _latest_score_rows(sess, *, tracked_core_only: bool, limit: int = 8) -> list[dict]:
        candidate_rows = list(
            sess.scalars(select(BetaSignalCandidate).order_by(desc(BetaSignalCandidate.updated_at)).limit(200)).all()
        )
        candidate_by_symbol: dict[str, BetaSignalCandidate] = {}
        for row in candidate_rows:
            candidate_by_symbol.setdefault(str(row.symbol), row)

        stmt = (
            select(BetaScoreTape, BetaInstrument)
            .join(BetaInstrument, BetaInstrument.id == BetaScoreTape.instrument_id)
            .order_by(desc(BetaScoreTape.scored_at), desc(BetaScoreTape.id))
        )
        if tracked_core_only:
            stmt = stmt.where(BetaInstrument.core_security_id.is_not(None))

        latest_rows: list[dict] = []
        seen_instruments: set[str] = set()
        for score_row, instrument in sess.execute(stmt).all():
            if score_row.instrument_id in seen_instruments:
                continue
            seen_instruments.add(str(score_row.instrument_id))
            candidate = candidate_by_symbol.get(str(score_row.symbol))
            latest_rows.append(
                {
                    **_row_to_dict(score_row),
                    "candidate_id": candidate.id if candidate is not None else None,
                    "candidate_status": candidate.status if candidate is not None else None,
                    "candidate_updated_at": candidate.updated_at if candidate is not None else None,
                    "core_security_id": instrument.core_security_id,
                    "exchange": instrument.exchange,
                    "market": instrument.market,
                }
            )
            if len(latest_rows) >= limit:
                break
        return latest_rows

    @staticmethod
    def get_dashboard() -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {
                "available": False,
                "status": None,
                "counts": {},
                "active_positions": [],
                "closed_positions": [],
                "watched_candidates": [],
                "rejected_candidates": [],
                "notifications": [],
                "jobs": [],
                "snapshots": [],
            }

        with BetaContext.read_session() as sess:
            beta_db_path = get_beta_db_path()
            settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()
            status = sess.scalar(select(BetaSystemStatus).where(BetaSystemStatus.id == 1))
            counts = {
                "universe_active": sess.scalar(
                    select(func.count()).select_from(BetaUniverseMembership).where(BetaUniverseMembership.status.in_(("SEED", "ACTIVE")))
                )
                or 0,
                "hypotheses_total": sess.scalar(select(func.count()).select_from(BetaHypothesis)) or 0,
                "hypotheses_promoted": sess.scalar(
                    select(func.count()).select_from(BetaHypothesis).where(BetaHypothesis.status == "PROMOTED")
                )
                or 0,
                "hypotheses_suspended": sess.scalar(
                    select(func.count()).select_from(BetaHypothesis).where(BetaHypothesis.status == "SUSPENDED")
                )
                or 0,
                "candidates_watching": sess.scalar(
                    select(func.count()).select_from(BetaSignalCandidate).where(BetaSignalCandidate.status == "WATCHING")
                )
                or 0,
                "candidates_promoted": sess.scalar(
                    select(func.count()).select_from(BetaSignalCandidate).where(BetaSignalCandidate.status == "PROMOTED")
                )
                or 0,
                "candidates_rejected": sess.scalar(
                    select(func.count()).select_from(BetaSignalCandidate).where(BetaSignalCandidate.status.in_(("REJECTED", "DISMISSED")))
                )
                or 0,
                "positions_open": sess.scalar(
                    select(func.count()).select_from(BetaDemoPosition).where(BetaDemoPosition.status == "OPEN")
                )
                or 0,
                "positions_closed": sess.scalar(
                    select(func.count()).select_from(BetaDemoPosition).where(BetaDemoPosition.status != "OPEN")
                )
                or 0,
                "notifications_recent": sess.scalar(select(func.count()).select_from(BetaUiNotification)) or 0,
                "scores_total": sess.scalar(select(func.count()).select_from(BetaScoreTape)) or 0,
                "scores_recommended": sess.scalar(
                    select(func.count()).select_from(BetaScoreTape).where(BetaScoreTape.recommendation_flag.is_(True))
                )
                or 0,
                "models_total": sess.scalar(select(func.count()).select_from(BetaModelVersion)) or 0,
                "models_active": sess.scalar(
                    select(func.count()).select_from(BetaModelVersion).where(BetaModelVersion.is_active.is_(True))
                )
                or 0,
                "datasets_total": sess.scalar(select(func.count()).select_from(BetaDatasetVersion)) or 0,
                "experiments_total": sess.scalar(select(func.count()).select_from(BetaExperimentRun)) or 0,
                "strategies_total": sess.scalar(select(func.count()).select_from(BetaStrategyVersion)) or 0,
                "strategies_active": sess.scalar(
                    select(func.count()).select_from(BetaStrategyVersion).where(BetaStrategyVersion.is_active.is_(True))
                )
                or 0,
                "validation_runs_total": sess.scalar(select(func.count()).select_from(BetaValidationRun)) or 0,
                "news_sources_total": sess.scalar(select(func.count()).select_from(BetaNewsSource)) or 0,
                "news_articles_total": sess.scalar(select(func.count()).select_from(BetaNewsArticle)) or 0,
                "news_links_total": sess.scalar(select(func.count()).select_from(BetaNewsArticleLink)) or 0,
                "filing_sources_total": sess.scalar(select(func.count()).select_from(BetaFilingSource)) or 0,
                "filing_events_total": sess.scalar(select(func.count()).select_from(BetaFilingEvent)) or 0,
                "filing_links_total": sess.scalar(select(func.count()).select_from(BetaFilingEventLink)) or 0,
                "benchmark_rows_total": sess.scalar(select(func.count()).select_from(BetaBenchmarkBar)) or 0,
                "evaluation_runs_total": sess.scalar(select(func.count()).select_from(BetaEvaluationRun)) or 0,
                "review_runs_total": sess.scalar(select(func.count()).select_from(BetaAiReviewRun)) or 0,
                "feature_rows_total": sess.scalar(select(func.count()).select_from(BetaFeatureValue)) or 0,
                "intraday_snapshots_total": sess.scalar(select(func.count()).select_from(BetaIntradaySnapshot)) or 0,
                "label_rows_total": sess.scalar(select(func.count()).select_from(BetaLabelValue)) or 0,
            }
            closed_positions = list(
                sess.scalars(
                    select(BetaDemoPosition)
                    .where(BetaDemoPosition.status != "OPEN")
                    .order_by(desc(BetaDemoPosition.updated_at))
                    .limit(12)
                ).all()
            )
            pnl_pct_values = []
            pnl_gbp_values = []
            for row in closed_positions:
                try:
                    if row.pnl_pct is not None:
                        pnl_pct_values.append(float(row.pnl_pct))
                    if row.pnl_gbp is not None:
                        pnl_gbp_values.append(float(row.pnl_gbp))
                except (TypeError, ValueError):
                    continue
            wins = len([value for value in pnl_gbp_values if value > 0])
            recent_avg = sum(pnl_pct_values[:3]) / min(3, len(pnl_pct_values)) if pnl_pct_values else 0.0
            prior_avg = (
                sum(pnl_pct_values[3:6]) / len(pnl_pct_values[3:6])
                if len(pnl_pct_values) >= 6
                else recent_avg
            )
            if recent_avg > prior_avg + 0.25:
                trend = "improving"
            elif recent_avg < prior_avg - 0.25:
                trend = "declining"
            else:
                trend = "stable"
            performance = {
                "closed_positions": len(closed_positions),
                "win_rate_pct": round((wins / len(closed_positions)) * 100, 1) if closed_positions else 0.0,
                "realized_pnl_gbp_total": round(sum(pnl_gbp_values), 2),
                "avg_pnl_pct": round(sum(pnl_pct_values) / len(pnl_pct_values), 2) if pnl_pct_values else 0.0,
                "trend": trend,
            }
            ledger = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
            risk_control = sess.scalar(select(BetaRiskControlState).where(BetaRiskControlState.id == 1))
            latest_evaluation_run = sess.scalar(
                select(BetaEvaluationRun).order_by(desc(BetaEvaluationRun.created_at)).limit(1)
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
            latest_dataset = sess.scalar(
                select(BetaDatasetVersion).order_by(desc(BetaDatasetVersion.created_at)).limit(1)
            )
            latest_experiment = sess.scalar(
                select(BetaExperimentRun).order_by(desc(BetaExperimentRun.created_at)).limit(1)
            )
            latest_validation = sess.scalar(
                select(BetaValidationRun).order_by(desc(BetaValidationRun.created_at)).limit(1)
            )
            latest_evaluation_summary = None
            confidence_buckets: list[dict] = []
            direction_summaries: list[dict] = []
            if latest_evaluation_run is not None:
                latest_evaluation_summary = sess.scalar(
                    select(BetaEvaluationSummary).where(BetaEvaluationSummary.evaluation_run_id == latest_evaluation_run.id)
                )
                confidence_rows = list(
                    sess.scalars(
                        select(BetaConfidenceBucketSummary)
                        .where(BetaConfidenceBucketSummary.evaluation_run_id == latest_evaluation_run.id)
                    ).all()
                )
                direction_rows = list(
                    sess.scalars(
                        select(BetaDirectionSummary)
                        .where(BetaDirectionSummary.evaluation_run_id == latest_evaluation_run.id)
                    ).all()
                )
                bucket_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
                confidence_buckets = [
                    _row_to_dict(row) for row in sorted(confidence_rows, key=lambda row: bucket_order.get(row.bucket_label, 9))
                ]
                direction_order = {"BULLISH": 0, "BEARISH": 1, "RISK_OFF": 2, "NEUTRAL": 3}
                direction_summaries = [
                    _row_to_dict(row) for row in sorted(direction_rows, key=lambda row: direction_order.get(row.direction, 9))
                ]
            runtime_flags = {
                "training_window_open": BetaMarketSessionService.training_window_is_open(settings),
                "tracked_equity_training_enabled": BetaTrainingService.has_tracked_core_equity(),
                "training_allowed": (
                    BetaMarketSessionService.training_window_is_open(settings)
                    or BetaTrainingService.has_tracked_core_equity()
                ),
                "uk_market_open": BetaMarketSessionService.market_is_tradeable("LSE"),
                "us_market_open": BetaMarketSessionService.market_is_tradeable("NASDAQ"),
            }
            recent_jobs = list(
                sess.scalars(select(BetaJobRun).order_by(desc(BetaJobRun.started_at)).limit(40)).all()
            )
            latest_successful_job = next((row for row in recent_jobs if row.status == "SUCCESS"), None)
            latest_failed_job = next((row for row in recent_jobs if row.status == "FAILED"), None)
            latest_jobs_by_type: dict[str, dict] = {}
            for row in recent_jobs:
                if row.job_type not in latest_jobs_by_type:
                    latest_jobs_by_type[row.job_type] = _row_to_dict(row)
            supervisor_alive = _process_is_alive(status.supervisor_pid if status is not None else None)
            supervisor_status = "running" if supervisor_alive else str(status.supervisor_status or "stopped") if status is not None else "stopped"
            runtime_activity = {
                "supervisor_alive": supervisor_alive,
                "supervisor_status_display": supervisor_status,
                "heartbeat_age_seconds": _seconds_since(status.last_heartbeat_at) if status is not None else None,
                "last_successful_job": _row_to_dict(latest_successful_job) if latest_successful_job is not None else None,
                "last_failed_job": _row_to_dict(latest_failed_job) if latest_failed_job is not None else None,
                "latest_jobs_by_type": latest_jobs_by_type,
                "latest_success_count": len([row for row in recent_jobs[:12] if row.status == "SUCCESS"]),
                "latest_failure_count": len([row for row in recent_jobs[:12] if row.status == "FAILED"]),
            }
            hypothesis_rows = list(sess.scalars(select(BetaHypothesis)).all())
            hypothesis_rows.sort(
                key=lambda row: (_safe_float(row.evidence_score), row.updated_at),
                reverse=True,
            )

            return {
                "available": True,
                "status": _row_to_dict(status) if status is not None else None,
                "runtime_flags": runtime_flags,
                "runtime_activity": runtime_activity,
                "counts": counts,
                "performance": performance,
                "ledger": _row_to_dict(ledger) if ledger is not None else None,
                "risk_control": _row_to_dict(risk_control) if risk_control is not None else None,
                "active_model": _row_to_dict(active_model) if active_model is not None else None,
                "active_strategy": _row_to_dict(active_strategy) if active_strategy is not None else None,
                "latest_dataset": _row_to_dict(latest_dataset) if latest_dataset is not None else None,
                "latest_experiment": _row_to_dict(latest_experiment) if latest_experiment is not None else None,
                "latest_validation": _row_to_dict(latest_validation) if latest_validation is not None else None,
                "latest_evaluation_run": _row_to_dict(latest_evaluation_run) if latest_evaluation_run is not None else None,
                "latest_evaluation_summary": _row_to_dict(latest_evaluation_summary) if latest_evaluation_summary is not None else None,
                "confidence_buckets": confidence_buckets,
                "direction_summaries": direction_summaries,
                "hypotheses": BetaOverviewService._query_rows(hypothesis_rows[:8]),
                "active_positions": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaDemoPosition)
                        .where(BetaDemoPosition.status == "OPEN")
                        .order_by(desc(BetaDemoPosition.opened_at))
                        .limit(8)
                    )
                ),
                "closed_positions": BetaOverviewService._query_rows(
                    closed_positions[:8]
                ),
                "watched_candidates": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaSignalCandidate)
                        .where(BetaSignalCandidate.status.in_(("WATCHING", "PROMOTED")))
                        .order_by(
                            desc(BetaSignalCandidate.confidence_score),
                            desc(BetaSignalCandidate.expected_edge_score),
                            desc(BetaSignalCandidate.updated_at),
                        )
                        .limit(8)
                    )
                ),
                "tracked_core_signals": BetaOverviewService._latest_score_rows(
                    sess,
                    tracked_core_only=True,
                    limit=8,
                ),
                "rejected_candidates": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaSignalCandidate)
                        .where(BetaSignalCandidate.status.in_(("DISMISSED", "REJECTED")))
                        .order_by(desc(BetaSignalCandidate.updated_at))
                        .limit(8)
                    )
                ),
                "notifications": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaUiNotification)
                        .order_by(desc(BetaUiNotification.created_at))
                        .limit(10)
                    )
                ),
                "jobs": BetaOverviewService._query_rows(recent_jobs[:10]),
                "recent_scores": BetaOverviewService._query_rows(
                    sess.scalars(select(BetaScoreTape).order_by(desc(BetaScoreTape.scored_at)).limit(12))
                ),
                "candidate_events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaSignalCandidateEvent)
                        .order_by(desc(BetaSignalCandidateEvent.created_at))
                        .limit(12)
                    )
                ),
                "hypothesis_events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaHypothesisEvent)
                        .order_by(desc(BetaHypothesisEvent.created_at))
                        .limit(12)
                    )
                ),
                "position_events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaDemoPositionEvent)
                        .order_by(desc(BetaDemoPositionEvent.created_at))
                        .limit(12)
                    )
                ),
                "recent_feature_rows": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaFeatureValue)
                        .order_by(desc(BetaFeatureValue.feature_date), desc(BetaFeatureValue.id))
                        .limit(12)
                    )
                ),
                "recent_intraday_snapshots": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaIntradaySnapshot)
                        .order_by(desc(BetaIntradaySnapshot.observed_at))
                        .limit(12)
                    )
                ),
                "recent_news_articles": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaNewsArticle)
                        .order_by(desc(BetaNewsArticle.published_at), desc(BetaNewsArticle.created_at))
                        .limit(12)
                    )
                ),
                "recent_news_links": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaNewsArticleLink)
                        .order_by(desc(BetaNewsArticleLink.created_at))
                        .limit(12)
                    )
                ),
                "recent_filing_events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaFilingEvent)
                        .order_by(desc(BetaFilingEvent.published_at), desc(BetaFilingEvent.created_at))
                        .limit(12)
                    )
                ),
                "recent_label_rows": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaLabelValue)
                        .order_by(desc(BetaLabelValue.decision_date), desc(BetaLabelValue.id))
                        .limit(12)
                    )
                ),
                "recent_benchmark_rows": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaBenchmarkBar)
                        .order_by(desc(BetaBenchmarkBar.bar_date), desc(BetaBenchmarkBar.id))
                        .limit(12)
                    )
                ),
                "review_runs": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaAiReviewRun)
                        .order_by(desc(BetaAiReviewRun.created_at))
                        .limit(8)
                    )
                ),
                "review_findings": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaAiReviewFinding)
                        .order_by(desc(BetaAiReviewFinding.created_at))
                        .limit(12)
                    )
                ),
                "snapshots": [
                    {
                        **_row_to_dict(row),
                        "summary": json.loads(row.summary_json),
                    }
                    for row in sess.scalars(
                        select(BetaUiSummarySnapshot)
                        .order_by(desc(BetaUiSummarySnapshot.snapshot_date))
                        .limit(7)
                    ).all()
                ],
            }

    @staticmethod
    def _query_rows(rows: Sequence) -> list[dict]:
        return [_row_to_dict(row) for row in rows]

    @staticmethod
    def get_candidate_detail(candidate_id: str) -> dict[str, object] | None:
        if not BetaContext.is_initialized():
            return None

        with BetaContext.read_session() as sess:
            candidate = sess.scalar(select(BetaSignalCandidate).where(BetaSignalCandidate.id == candidate_id))
            if candidate is None:
                return None
            hypothesis = None
            if candidate.hypothesis_id is not None:
                hypothesis = sess.scalar(select(BetaHypothesis).where(BetaHypothesis.id == candidate.hypothesis_id))
            positions = list(
                sess.scalars(
                    select(BetaDemoPosition)
                    .where(BetaDemoPosition.candidate_id == candidate.id)
                    .order_by(desc(BetaDemoPosition.opened_at))
                ).all()
            )
            news_rows = list(
                sess.execute(
                    select(BetaNewsArticle, BetaNewsArticleLink)
                    .join(BetaNewsArticleLink, BetaNewsArticleLink.article_id == BetaNewsArticle.id)
                    .where(BetaNewsArticleLink.symbol == candidate.symbol)
                    .order_by(desc(BetaNewsArticle.published_at), desc(BetaNewsArticle.created_at))
                    .limit(10)
                ).all()
            )
            filing_rows = list(
                sess.execute(
                    select(BetaFilingEvent, BetaFilingEventLink)
                    .join(BetaFilingEventLink, BetaFilingEventLink.event_id == BetaFilingEvent.id)
                    .where(BetaFilingEventLink.symbol == candidate.symbol)
                    .order_by(desc(BetaFilingEvent.published_at), desc(BetaFilingEvent.created_at))
                    .limit(10)
                ).all()
            )
            return {
                "candidate": _row_to_dict(candidate),
                "hypothesis": _row_to_dict(hypothesis) if hypothesis is not None else None,
                "candidate_events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaSignalCandidateEvent)
                        .where(BetaSignalCandidateEvent.candidate_id == candidate.id)
                        .order_by(desc(BetaSignalCandidateEvent.created_at))
                        .limit(20)
                    )
                ),
                "positions": BetaOverviewService._query_rows(positions),
                "scores": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaScoreTape)
                        .where(BetaScoreTape.symbol == candidate.symbol)
                        .order_by(desc(BetaScoreTape.scored_at))
                        .limit(20)
                    )
                ),
                "news_articles": [
                    {
                        "article": _row_to_dict(article),
                        "link": _row_to_dict(link),
                    }
                    for article, link in news_rows
                ],
                "filing_events": [
                    {
                        "event": _row_to_dict(event),
                        "link": _row_to_dict(link),
                    }
                    for event, link in filing_rows
                ],
                "review_findings": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaAiReviewFinding)
                        .where(BetaAiReviewFinding.subject_symbol == candidate.symbol)
                        .order_by(desc(BetaAiReviewFinding.created_at))
                        .limit(20)
                    )
                ),
            }

    @staticmethod
    def get_trade_detail(position_id: str) -> dict[str, object] | None:
        if not BetaContext.is_initialized():
            return None

        with BetaContext.read_session() as sess:
            position = sess.scalar(select(BetaDemoPosition).where(BetaDemoPosition.id == position_id))
            if position is None:
                return None
            candidate = None
            hypothesis = None
            if position.candidate_id is not None:
                candidate = sess.scalar(select(BetaSignalCandidate).where(BetaSignalCandidate.id == position.candidate_id))
            if candidate is not None and candidate.hypothesis_id is not None:
                hypothesis = sess.scalar(select(BetaHypothesis).where(BetaHypothesis.id == candidate.hypothesis_id))
            news_rows = list(
                sess.execute(
                    select(BetaNewsArticle, BetaNewsArticleLink)
                    .join(BetaNewsArticleLink, BetaNewsArticleLink.article_id == BetaNewsArticle.id)
                    .where(BetaNewsArticleLink.symbol == position.symbol)
                    .order_by(desc(BetaNewsArticle.published_at), desc(BetaNewsArticle.created_at))
                    .limit(10)
                ).all()
            )
            filing_rows = list(
                sess.execute(
                    select(BetaFilingEvent, BetaFilingEventLink)
                    .join(BetaFilingEventLink, BetaFilingEventLink.event_id == BetaFilingEvent.id)
                    .where(BetaFilingEventLink.symbol == position.symbol)
                    .order_by(desc(BetaFilingEvent.published_at), desc(BetaFilingEvent.created_at))
                    .limit(10)
                ).all()
            )
            return {
                "position": _row_to_dict(position),
                "candidate": _row_to_dict(candidate) if candidate is not None else None,
                "hypothesis": _row_to_dict(hypothesis) if hypothesis is not None else None,
                "position_events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaDemoPositionEvent)
                        .where(BetaDemoPositionEvent.position_id == position.id)
                        .order_by(desc(BetaDemoPositionEvent.created_at))
                        .limit(20)
                    )
                ),
                "ledger_entries": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaCashLedgerEntry)
                        .where(BetaCashLedgerEntry.position_id == position.id)
                        .order_by(desc(BetaCashLedgerEntry.created_at))
                        .limit(20)
                    )
                ),
                "scores": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaScoreTape)
                        .where(BetaScoreTape.symbol == position.symbol)
                        .order_by(desc(BetaScoreTape.scored_at))
                        .limit(20)
                    )
                ),
                "news_articles": [
                    {
                        "article": _row_to_dict(article),
                        "link": _row_to_dict(link),
                    }
                    for article, link in news_rows
                ],
                "filing_events": [
                    {
                        "event": _row_to_dict(event),
                        "link": _row_to_dict(link),
                    }
                    for event, link in filing_rows
                ],
            }

    @staticmethod
    def get_hypothesis_detail(hypothesis_id: str) -> dict[str, object] | None:
        if not BetaContext.is_initialized():
            return None

        with BetaContext.read_session() as sess:
            hypothesis = sess.scalar(select(BetaHypothesis).where(BetaHypothesis.id == hypothesis_id))
            if hypothesis is None:
                return None
            candidates = list(
                sess.scalars(
                    select(BetaSignalCandidate)
                    .where(BetaSignalCandidate.hypothesis_id == hypothesis.id)
                    .order_by(desc(BetaSignalCandidate.updated_at))
                    .limit(30)
                ).all()
            )
            candidate_ids = [row.id for row in candidates]
            positions = []
            if candidate_ids:
                positions = list(
                    sess.scalars(
                        select(BetaDemoPosition)
                        .where(BetaDemoPosition.candidate_id.in_(candidate_ids))
                        .order_by(desc(BetaDemoPosition.updated_at))
                        .limit(30)
                    ).all()
                )
            return {
                "hypothesis": _row_to_dict(hypothesis),
                "events": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaHypothesisEvent)
                        .where(BetaHypothesisEvent.hypothesis_id == hypothesis.id)
                        .order_by(desc(BetaHypothesisEvent.created_at))
                        .limit(25)
                    )
                ),
                "candidates": BetaOverviewService._query_rows(candidates),
                "positions": BetaOverviewService._query_rows(positions),
            }


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
