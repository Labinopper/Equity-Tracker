"""Read-side aggregation for beta UI pages."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import case, desc, func, select

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
    BetaExecutionLabelValue,
    BetaExecutionSignal,
    BetaFeatureValue,
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaFilingSource,
    BetaHypothesis,
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaHypothesisEvent,
    BetaHypothesisFamily,
    BetaHypothesisTestRun,
    BetaInstrument,
    BetaIntradayFeatureObservation,
    BetaIntradaySnapshot,
    BetaIntradayPatternCandidate,
    BetaIntradayPatternDiscoveryRun,
    BetaIntradaySimulatedTrade,
    BetaIntradaySimulatedTradeEvent,
    BetaJobRun,
    BetaLedgerState,
    BetaLabelValue,
    BetaModelVersion,
    BetaNewsArticle,
    BetaNewsArticleLink,
    BetaNewsSource,
    BetaPositionState,
    BetaRecommendationDecision,
    BetaRiskControlState,
    BetaScoreTape,
    BetaSignalCandidateEvent,
    BetaSignalCandidate,
    BetaSignalObservation,
    BetaStrategyVersion,
    BetaSystemStatus,
    BetaUiNotification,
    BetaUiSummarySnapshot,
    BetaUniverseMembership,
    BetaValidationRun,
)
from ..services.pipeline_assessment_service import BetaPipelineAssessmentService
from ..services.intraday_pattern_exploration_learning_service import BetaIntradayPatternExplorationLearningService
from ..services.intraday_pattern_parameter_learning_service import BetaIntradayPatternParameterLearningService
from ..services.intraday_pattern_review_service import BetaIntradayPatternReviewService
from ..services.intraday_pattern_threshold_learning_service import BetaIntradayPatternThresholdLearningService
from ..services.intraday_pattern_execution_learning_service import BetaIntradayPatternExecutionLearningService
from ..services.session_service import BetaMarketSessionService
from ..settings import BetaSettings
from ..state import get_beta_db_path
from ..services.training_service import BetaTrainingService


def _row_to_dict(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


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


def _latest_activity_at(*moments) -> datetime | None:
    candidates = [moment for moment in moments if moment is not None]
    if not candidates:
        return None
    return max(candidates)


class BetaOverviewService:
    """Small query service for the beta dashboard and supporting pages."""

    _DASHBOARD_CACHE_LOCK = threading.Lock()
    _DASHBOARD_CACHE_TTL_SECONDS = 1800
    _DASHBOARD_CACHE: dict[str, object] | None = None
    _DASHBOARD_CACHE_AT: datetime | None = None
    _DASHBOARD_CACHE_DB_PATH: str | None = None
    _MODERN_DASHBOARD_CACHE: dict[str, object] | None = None
    _MODERN_DASHBOARD_CACHE_AT: datetime | None = None
    _MODERN_DASHBOARD_CACHE_DB_PATH: str | None = None

    @staticmethod
    def is_available() -> bool:
        return BetaContext.is_initialized()

    @staticmethod
    def invalidate_dashboard_cache() -> None:
        with BetaOverviewService._DASHBOARD_CACHE_LOCK:
            BetaOverviewService._DASHBOARD_CACHE = None
            BetaOverviewService._DASHBOARD_CACHE_AT = None
            BetaOverviewService._DASHBOARD_CACHE_DB_PATH = None
            BetaOverviewService._MODERN_DASHBOARD_CACHE = None
            BetaOverviewService._MODERN_DASHBOARD_CACHE_AT = None
            BetaOverviewService._MODERN_DASHBOARD_CACHE_DB_PATH = None

    @staticmethod
    def _get_cached_dashboard(*, beta_db_path: str | None) -> dict[str, object] | None:
        with BetaOverviewService._DASHBOARD_CACHE_LOCK:
            if BetaOverviewService._DASHBOARD_CACHE is None or BetaOverviewService._DASHBOARD_CACHE_AT is None:
                return None
            if BetaOverviewService._DASHBOARD_CACHE_DB_PATH != beta_db_path:
                return None
            age_seconds = (_utcnow() - BetaOverviewService._DASHBOARD_CACHE_AT).total_seconds()
            if age_seconds > BetaOverviewService._DASHBOARD_CACHE_TTL_SECONDS:
                return None
            return BetaOverviewService._DASHBOARD_CACHE

    @staticmethod
    def _store_cached_dashboard(*, beta_db_path: str | None, dashboard: dict[str, object]) -> dict[str, object]:
        with BetaOverviewService._DASHBOARD_CACHE_LOCK:
            BetaOverviewService._DASHBOARD_CACHE = dashboard
            BetaOverviewService._DASHBOARD_CACHE_AT = _utcnow()
            BetaOverviewService._DASHBOARD_CACHE_DB_PATH = beta_db_path
        return dashboard

    @staticmethod
    def _get_cached_modern_dashboard(*, beta_db_path: str | None) -> dict[str, object] | None:
        with BetaOverviewService._DASHBOARD_CACHE_LOCK:
            if (
                BetaOverviewService._MODERN_DASHBOARD_CACHE is None
                or BetaOverviewService._MODERN_DASHBOARD_CACHE_AT is None
            ):
                return None
            if BetaOverviewService._MODERN_DASHBOARD_CACHE_DB_PATH != beta_db_path:
                return None
            age_seconds = (_utcnow() - BetaOverviewService._MODERN_DASHBOARD_CACHE_AT).total_seconds()
            if age_seconds > BetaOverviewService._DASHBOARD_CACHE_TTL_SECONDS:
                return None
            return BetaOverviewService._MODERN_DASHBOARD_CACHE

    @staticmethod
    def _store_cached_modern_dashboard(*, beta_db_path: str | None, dashboard: dict[str, object]) -> dict[str, object]:
        with BetaOverviewService._DASHBOARD_CACHE_LOCK:
            BetaOverviewService._MODERN_DASHBOARD_CACHE = dashboard
            BetaOverviewService._MODERN_DASHBOARD_CACHE_AT = _utcnow()
            BetaOverviewService._MODERN_DASHBOARD_CACHE_DB_PATH = beta_db_path
        return dashboard

    @staticmethod
    def _latest_score_rows(sess, *, tracked_core_only: bool, limit: int = 8) -> list[dict]:
        candidate_rows = list(
            sess.scalars(select(BetaSignalCandidate).order_by(desc(BetaSignalCandidate.updated_at)).limit(200)).all()
        )
        candidate_by_symbol: dict[str, BetaSignalCandidate] = {}
        for row in candidate_rows:
            candidate_by_symbol.setdefault(str(row.symbol), row)
        candidate_payloads = {row.id: _json_object(row.evidence_json) for row in candidate_rows}

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
            candidate_payload = candidate_payloads.get(candidate.id, {}) if candidate is not None else {}
            latest_rows.append(
                {
                    **_row_to_dict(score_row),
                    "candidate_id": candidate.id if candidate is not None else None,
                    "candidate_status": candidate.status if candidate is not None else None,
                    "candidate_updated_at": candidate.updated_at if candidate is not None else None,
                    "matched_hypothesis_code": candidate_payload.get("matched_hypothesis_code"),
                    "matched_hypothesis_name": candidate_payload.get("matched_hypothesis_name"),
                    "matched_hypothesis_status": candidate_payload.get("matched_hypothesis_status"),
                    "matched_target_metric": candidate_payload.get("matched_target_metric"),
                    "matched_holding_period_days": candidate_payload.get("matched_holding_period_days"),
                    "recommendation_reason_code": candidate_payload.get("recommendation_reason_code"),
                    "recommendation_reason_text": candidate_payload.get("recommendation_reason_text"),
                    "core_security_id": instrument.core_security_id,
                    "exchange": instrument.exchange,
                    "market": instrument.market,
                }
            )
            if len(latest_rows) >= limit:
                break
        return latest_rows

    @staticmethod
    def _latest_definition_tests(sess) -> dict[str, BetaHypothesisTestRun]:
        subquery = (
            select(
                BetaHypothesisTestRun.hypothesis_definition_id.label("definition_id"),
                func.max(BetaHypothesisTestRun.created_at).label("latest_created_at"),
            )
            .group_by(BetaHypothesisTestRun.hypothesis_definition_id)
            .subquery()
        )
        rows = list(
            sess.execute(
                select(BetaHypothesisTestRun).join(
                    subquery,
                    (
                        (BetaHypothesisTestRun.hypothesis_definition_id == subquery.c.definition_id)
                        & (BetaHypothesisTestRun.created_at == subquery.c.latest_created_at)
                    ),
                )
            ).scalars().all()
        )
        return {row.hypothesis_definition_id: row for row in rows}

    @staticmethod
    def _observation_feedback_by_definition(sess) -> dict[str, dict[str, object]]:
        rows = list(
            sess.execute(
                select(
                    BetaSignalObservation.hypothesis_definition_id,
                    func.count().label("matched_count"),
                    func.sum(
                        case((BetaSignalObservation.realized_return_pct.is_not(None), 1), else_=0)
                    ).label("realized_count"),
                    func.sum(
                        case((BetaSignalObservation.realized_return_pct > 0, 1), else_=0)
                    ).label("positive_realized_count"),
                    func.avg(BetaSignalObservation.realized_return_pct).label("avg_realized_return_pct"),
                    func.avg(
                        BetaSignalObservation.realized_return_pct - BetaSignalObservation.expected_return_pct
                    ).label("avg_prediction_error_pct"),
                    func.max(BetaSignalObservation.realized_at).label("latest_realized_at"),
                    func.max(BetaSignalObservation.observation_time).label("latest_observation_at"),
                )
                .group_by(BetaSignalObservation.hypothesis_definition_id)
            ).all()
        )
        feedback: dict[str, dict[str, object]] = {}
        for row in rows:
            realized_count = int(row.realized_count or 0)
            positive_realized_count = int(row.positive_realized_count or 0)
            feedback[str(row.hypothesis_definition_id)] = {
                "matched_count": int(row.matched_count or 0),
                "realized_count": realized_count,
                "realized_win_rate_pct": round((positive_realized_count / realized_count) * 100.0, 2)
                if realized_count
                else None,
                "avg_realized_return_pct": float(row.avg_realized_return_pct)
                if row.avg_realized_return_pct is not None
                else None,
                "avg_prediction_error_pct": float(row.avg_prediction_error_pct)
                if row.avg_prediction_error_pct is not None
                else None,
                "latest_realized_at": row.latest_realized_at,
                "latest_observation_at": row.latest_observation_at,
            }
        return feedback

    @staticmethod
    def _definition_rows(sess, *, limit: int | None = None) -> list[dict[str, object]]:
        definitions = list(
            sess.scalars(select(BetaHypothesisDefinition).order_by(desc(BetaHypothesisDefinition.updated_at))).all()
        )
        families = {
            row.id: row
            for row in sess.scalars(select(BetaHypothesisFamily)).all()
        }
        beliefs = {
            row.hypothesis_definition_id: row
            for row in sess.scalars(select(BetaHypothesisBeliefState)).all()
        }
        latest_tests = BetaOverviewService._latest_definition_tests(sess)
        observation_feedback = BetaOverviewService._observation_feedback_by_definition(sess)

        rows: list[dict[str, object]] = []
        for definition in definitions:
            family = families.get(definition.family_id) if definition.family_id is not None else None
            belief = beliefs.get(definition.id)
            latest_test = latest_tests.get(definition.id)
            latest_test_notes = _json_object(latest_test.notes_json) if latest_test is not None else {}
            governance = latest_test_notes.get("governance") if isinstance(latest_test_notes.get("governance"), dict) else {}
            governance = governance if isinstance(governance, dict) else {}
            gate_metrics = governance.get("gate_metrics") if isinstance(governance.get("gate_metrics"), dict) else {}
            gate_metrics = gate_metrics if isinstance(gate_metrics, dict) else {}
            hard_fail_reasons = governance.get("hard_fail_reasons") if isinstance(governance.get("hard_fail_reasons"), list) else []
            promotion_fail_reasons = governance.get("promotion_fail_reasons") if isinstance(governance.get("promotion_fail_reasons"), list) else []
            regime_slice = _json_object(latest_test.regime_slice_json) if latest_test is not None else {}
            failure_modes = regime_slice.get("failure_modes") if isinstance(regime_slice.get("failure_modes"), list) else []
            feedback = observation_feedback.get(definition.id, {})

            rows.append(
                {
                    "id": definition.id,
                    "hypothesis_code": definition.hypothesis_code,
                    "name": definition.name,
                    "expected_direction": definition.expected_direction,
                    "target_metric": definition.target_metric,
                    "holding_period_days": definition.holding_period_days,
                    "family_id": family.id if family is not None else None,
                    "family_code": family.family_code if family is not None else None,
                    "family_name": family.family_name if family is not None else None,
                    "status": str(belief.status if belief is not None else definition.status),
                    "definition_status": definition.status,
                    "belief_confidence_score": float(belief.confidence_score) if belief is not None else None,
                    "belief_evidence_count": int(belief.evidence_count) if belief is not None else 0,
                    "sample_size": int(latest_test.sample_size or 0) if latest_test is not None else 0,
                    "support_count": int(latest_test.support_count or 0) if latest_test is not None else 0,
                    "median_excess_return_pct": latest_test.median_excess_return_pct if latest_test is not None else None,
                    "transaction_cost_adjusted_return_pct": latest_test.transaction_cost_adjusted_return_pct if latest_test is not None else None,
                    "baseline_edge_pct": latest_test.baseline_edge_pct if latest_test is not None else None,
                    "win_rate_pct": latest_test.win_rate_pct if latest_test is not None else None,
                    "stability_score": latest_test.stability_score if latest_test is not None else None,
                    "walk_forward_score": latest_test.walk_forward_score if latest_test is not None else None,
                    "out_of_sample_score": latest_test.out_of_sample_score if latest_test is not None else None,
                    "regime_consistency_score": regime_slice.get("regime_consistency_score"),
                    "matched_observation_count": int(feedback.get("matched_count") or 0),
                    "realized_observation_count": int(feedback.get("realized_count") or 0),
                    "avg_realized_return_pct": feedback.get("avg_realized_return_pct"),
                    "avg_prediction_error_pct": feedback.get("avg_prediction_error_pct"),
                    "realized_win_rate_pct": feedback.get("realized_win_rate_pct"),
                    "watch_eligible": bool(governance.get("watch_eligible")) if governance else None,
                    "promotion_eligible": bool(governance.get("promotion_eligible")) if governance else None,
                    "governance_severity": governance.get("severity"),
                    "gate_fail_reason": (
                        str(hard_fail_reasons[0])
                        if hard_fail_reasons
                        else str(promotion_fail_reasons[0])
                        if promotion_fail_reasons
                        else None
                    ),
                    "hard_fail_reasons": [str(reason) for reason in hard_fail_reasons],
                    "promotion_fail_reasons": [str(reason) for reason in promotion_fail_reasons],
                    "failure_modes": failure_modes,
                    "winsorized_adjusted_return_pct": gate_metrics.get("winsorized_adjusted_return_pct"),
                    "trimmed_adjusted_return_pct": gate_metrics.get("trimmed_adjusted_return_pct"),
                    "latest_test_at": latest_test.created_at if latest_test is not None else None,
                    "latest_realized_at": feedback.get("latest_realized_at"),
                    "updated_at": definition.updated_at,
                }
            )

        rows.sort(
            key=lambda item: (
                _status_rank(item.get("status")),
                -_safe_float(item.get("belief_confidence_score")),
                -_safe_float(item.get("baseline_edge_pct")),
                -_safe_float(item.get("stability_score")),
                item.get("updated_at") or datetime.min,
            )
        )
        return rows[:limit] if limit is not None else rows

    @staticmethod
    def _definition_family_rows(definition_rows: list[dict[str, object]]) -> list[dict[str, object]]:
        grouped: dict[str, dict[str, object]] = {}
        for row in definition_rows:
            family_code = str(row.get("family_code") or "UNASSIGNED")
            family = grouped.setdefault(
                family_code,
                {
                    "family_code": family_code,
                    "family_name": row.get("family_name") or family_code,
                    "definition_count": 0,
                    "validated_count": 0,
                    "promising_count": 0,
                    "degraded_count": 0,
                    "rejected_count": 0,
                    "watch_eligible_count": 0,
                    "promotion_eligible_count": 0,
                    "avg_confidence_score": 0.0,
                    "updated_at": row.get("updated_at"),
                },
            )
            family["definition_count"] = int(family["definition_count"]) + 1
            status = str(row.get("status") or "")
            if status == "VALIDATED":
                family["validated_count"] = int(family["validated_count"]) + 1
            if status == "PROMISING":
                family["promising_count"] = int(family["promising_count"]) + 1
            if status == "DEGRADED":
                family["degraded_count"] = int(family["degraded_count"]) + 1
            if status in {"REJECTED", "RETIRED"}:
                family["rejected_count"] = int(family["rejected_count"]) + 1
            if row.get("watch_eligible") is True:
                family["watch_eligible_count"] = int(family["watch_eligible_count"]) + 1
            if row.get("promotion_eligible") is True:
                family["promotion_eligible_count"] = int(family["promotion_eligible_count"]) + 1
            family["avg_confidence_score"] = float(family["avg_confidence_score"]) + _safe_float(
                row.get("belief_confidence_score")
            )
            updated_at = row.get("updated_at")
            if updated_at is not None and (
                family.get("updated_at") is None or updated_at > family.get("updated_at")
            ):
                family["updated_at"] = updated_at

        rows: list[dict[str, object]] = []
        for family in grouped.values():
            definition_count = max(1, int(family["definition_count"]))
            avg_confidence_score = round(float(family["avg_confidence_score"]) / definition_count, 4)
            status = "RESEARCH"
            if int(family["validated_count"]) > 0:
                status = "VALIDATED"
            elif int(family["promising_count"]) > 0:
                status = "PROMISING"
            elif int(family["watch_eligible_count"]) <= 0 and int(family["definition_count"]) > 0:
                status = "DEGRADED"
            rows.append(
                {
                    **family,
                    "avg_confidence_score": avg_confidence_score,
                    "status": status,
                }
            )
        rows.sort(
            key=lambda item: (
                _status_rank(item.get("status")),
                -_safe_float(item.get("avg_confidence_score")),
                item.get("family_name") or "",
            )
        )
        return rows

    @staticmethod
    def _definition_validity_summary(definition_rows: list[dict[str, object]]) -> dict[str, object]:
        summary = {
            "definition_count": len(definition_rows),
            "validated_count": 0,
            "promising_count": 0,
            "candidate_count": 0,
            "degraded_count": 0,
            "rejected_count": 0,
            "watch_eligible_count": 0,
            "promotion_eligible_count": 0,
            "realized_feedback_count": 0,
            "avg_confidence_score": 0.0,
            "avg_realized_return_pct": None,
            "avg_prediction_error_pct": None,
            "top_failure_reasons": [],
        }
        if not definition_rows:
            return summary

        realized_returns = [
            float(row["avg_realized_return_pct"])
            for row in definition_rows
            if row.get("avg_realized_return_pct") is not None
        ]
        prediction_errors = [
            float(row["avg_prediction_error_pct"])
            for row in definition_rows
            if row.get("avg_prediction_error_pct") is not None
        ]
        failure_counts: dict[str, int] = {}
        total_confidence = 0.0
        for row in definition_rows:
            status = str(row.get("status") or "")
            if status == "VALIDATED":
                summary["validated_count"] = int(summary["validated_count"]) + 1
            elif status == "PROMISING":
                summary["promising_count"] = int(summary["promising_count"]) + 1
            elif status in {"CANDIDATE", "SCREENED_IN", "DISCOVERED"}:
                summary["candidate_count"] = int(summary["candidate_count"]) + 1
            elif status == "DEGRADED":
                summary["degraded_count"] = int(summary["degraded_count"]) + 1
            elif status in {"REJECTED", "RETIRED"}:
                summary["rejected_count"] = int(summary["rejected_count"]) + 1
            if row.get("watch_eligible") is True:
                summary["watch_eligible_count"] = int(summary["watch_eligible_count"]) + 1
            if row.get("promotion_eligible") is True:
                summary["promotion_eligible_count"] = int(summary["promotion_eligible_count"]) + 1
            if int(row.get("realized_observation_count") or 0) > 0:
                summary["realized_feedback_count"] = int(summary["realized_feedback_count"]) + 1
            total_confidence += _safe_float(row.get("belief_confidence_score"))
            for reason in list(row.get("hard_fail_reasons") or []) + list(row.get("promotion_fail_reasons") or []):
                failure_counts[str(reason)] = failure_counts.get(str(reason), 0) + 1

        summary["avg_confidence_score"] = round(total_confidence / max(1, len(definition_rows)), 4)
        summary["avg_realized_return_pct"] = round(sum(realized_returns) / len(realized_returns), 4) if realized_returns else None
        summary["avg_prediction_error_pct"] = round(sum(prediction_errors) / len(prediction_errors), 4) if prediction_errors else None
        summary["top_failure_reasons"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]
        return summary

    @staticmethod
    def _execution_feedback_summary(sess) -> dict[str, object]:
        execution_labels_total = int(sess.scalar(select(func.count()).select_from(BetaExecutionLabelValue)) or 0)
        execution_labels_complete = int(
            sess.scalar(
                select(func.count()).select_from(BetaExecutionLabelValue).where(BetaExecutionLabelValue.evaluation_complete.is_(True))
            )
            or 0
        )
        actionable_signals = int(
            sess.scalar(
                select(func.count())
                .select_from(BetaExecutionSignal)
                .where(BetaExecutionSignal.signal_type != "NO_ACTION")
            )
            or 0
        )
        latest_actionable_signal = sess.scalar(
            select(BetaExecutionSignal)
            .where(BetaExecutionSignal.signal_type != "NO_ACTION")
            .order_by(desc(BetaExecutionSignal.signal_time), desc(BetaExecutionSignal.created_at))
            .limit(1)
        )
        open_simulated = int(
            sess.scalar(
                select(func.count())
                .select_from(BetaPositionState)
                .where(
                    BetaPositionState.position_source == "SIMULATED",
                    BetaPositionState.position_status == "OPEN",
                )
            )
            or 0
        )
        return {
            "execution_labels_total": execution_labels_total,
            "execution_labels_complete": execution_labels_complete,
            "execution_label_completion_pct": round((execution_labels_complete / execution_labels_total) * 100.0, 1)
            if execution_labels_total
            else None,
            "actionable_signal_count": actionable_signals,
            "no_action_signal_count": int(
                sess.scalar(
                    select(func.count())
                    .select_from(BetaExecutionSignal)
                    .where(BetaExecutionSignal.signal_type == "NO_ACTION")
                )
                or 0
            ),
            "open_simulated_theses": open_simulated,
            "latest_actionable_signal": _row_to_dict(latest_actionable_signal) if latest_actionable_signal is not None else None,
        }

    @staticmethod
    def _enrich_candidate_rows(
        sess,
        candidate_rows: Sequence[BetaSignalCandidate],
        *,
        definition_rows: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        if not candidate_rows:
            return []

        definition_lookup = {
            str(row["id"]): row
            for row in (definition_rows or BetaOverviewService._definition_rows(sess))
        }
        observation_ids = [row.signal_observation_id for row in candidate_rows if row.signal_observation_id]
        recommendation_ids = [row.recommendation_decision_id for row in candidate_rows if row.recommendation_decision_id]
        candidate_ids = [row.id for row in candidate_rows]

        observations = {
            row.id: row
            for row in (
                sess.scalars(select(BetaSignalObservation).where(BetaSignalObservation.id.in_(observation_ids))).all()
                if observation_ids
                else []
            )
        }
        recommendations = {
            row.id: row
            for row in (
                sess.scalars(
                    select(BetaRecommendationDecision).where(BetaRecommendationDecision.id.in_(recommendation_ids))
                ).all()
                if recommendation_ids
                else []
            )
        }
        latest_position_states: dict[str, BetaPositionState] = {}
        if candidate_ids:
            state_subquery = (
                select(
                    BetaPositionState.thesis_candidate_id.label("candidate_id"),
                    func.max(BetaPositionState.updated_at).label("latest_updated_at"),
                )
                .where(BetaPositionState.thesis_candidate_id.in_(candidate_ids))
                .group_by(BetaPositionState.thesis_candidate_id)
                .subquery()
            )
            latest_states = list(
                sess.execute(
                    select(BetaPositionState).join(
                        state_subquery,
                        (
                            (BetaPositionState.thesis_candidate_id == state_subquery.c.candidate_id)
                            & (BetaPositionState.updated_at == state_subquery.c.latest_updated_at)
                        ),
                    )
                ).scalars().all()
            )
            latest_position_states = {
                str(row.thesis_candidate_id): row for row in latest_states if row.thesis_candidate_id is not None
            }

        enriched_rows: list[dict[str, object]] = []
        for candidate in candidate_rows:
            evidence_payload = _json_object(candidate.evidence_json)
            definition_info = (
                definition_lookup.get(str(candidate.hypothesis_definition_id))
                if candidate.hypothesis_definition_id is not None
                else None
            ) or {}
            observation = observations.get(candidate.signal_observation_id) if candidate.signal_observation_id else None
            recommendation = (
                recommendations.get(candidate.recommendation_decision_id)
                if candidate.recommendation_decision_id
                else None
            )
            position_state = latest_position_states.get(str(candidate.id))
            enriched_rows.append(
                {
                    **_row_to_dict(candidate),
                    "evidence_payload": evidence_payload,
                    "hypothesis_code": evidence_payload.get("matched_hypothesis_code") or definition_info.get("hypothesis_code"),
                    "hypothesis_name": evidence_payload.get("matched_hypothesis_name") or definition_info.get("name"),
                    "hypothesis_status": evidence_payload.get("matched_hypothesis_status") or definition_info.get("status"),
                    "hypothesis_family_code": definition_info.get("family_code"),
                    "hypothesis_family_name": definition_info.get("family_name"),
                    "target_metric": evidence_payload.get("matched_target_metric") or definition_info.get("target_metric"),
                    "holding_period_days": evidence_payload.get("matched_holding_period_days")
                    or definition_info.get("holding_period_days"),
                    "expected_direction": definition_info.get("expected_direction"),
                    "watch_eligible": definition_info.get("watch_eligible"),
                    "promotion_eligible": definition_info.get("promotion_eligible"),
                    "governance_severity": definition_info.get("governance_severity"),
                    "gate_fail_reason": definition_info.get("gate_fail_reason"),
                    "median_excess_return_pct": definition_info.get("median_excess_return_pct"),
                    "winsorized_adjusted_return_pct": definition_info.get("winsorized_adjusted_return_pct"),
                    "stability_score": definition_info.get("stability_score"),
                    "avg_realized_return_pct": definition_info.get("avg_realized_return_pct"),
                    "observation_status": observation.observation_status if observation is not None else None,
                    "observation_time": observation.observation_time if observation is not None else None,
                    "expected_return_pct": observation.expected_return_pct if observation is not None else None,
                    "realized_return_pct": observation.realized_return_pct if observation is not None else None,
                    "recommendation_status": recommendation.decision_status if recommendation is not None else None,
                    "recommendation_reason_code": (
                        recommendation.decision_reason_code
                        if recommendation is not None
                        else evidence_payload.get("recommendation_reason_code")
                    ),
                    "recommendation_reason_text": (
                        recommendation.decision_reason_text
                        if recommendation is not None
                        else evidence_payload.get("recommendation_reason_text")
                    ),
                    "recommendation_score": recommendation.recommendation_score if recommendation is not None else None,
                    "paper_trade_action": recommendation.paper_trade_action if recommendation is not None else None,
                    "position_state_id": position_state.id if position_state is not None else None,
                    "position_state_status": position_state.position_status if position_state is not None else None,
                    "position_source": position_state.position_source if position_state is not None else None,
                    "thesis_expected_return_pct": (
                        position_state.thesis_expected_return_pct if position_state is not None else None
                    ),
                    "thesis_horizon_days": position_state.thesis_horizon_days if position_state is not None else None,
                    "execution_quality_score": (
                        position_state.execution_quality_score if position_state is not None else None
                    ),
                    "last_execution_signal_type": (
                        position_state.last_execution_signal_type if position_state is not None else None
                    ),
                    "last_execution_signal_at": (
                        position_state.last_execution_signal_at if position_state is not None else None
                    ),
                }
            )
        return enriched_rows

    @staticmethod
    def _enrich_position_rows(
        sess,
        position_rows: Sequence[BetaDemoPosition],
        *,
        definition_rows: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        if not position_rows:
            return []

        candidate_ids = [row.candidate_id for row in position_rows if row.candidate_id]
        candidates = list(
            sess.scalars(select(BetaSignalCandidate).where(BetaSignalCandidate.id.in_(candidate_ids))).all()
        ) if candidate_ids else []
        candidate_lookup = {
            str(row["id"]): row
            for row in BetaOverviewService._enrich_candidate_rows(
                sess,
                candidates,
                definition_rows=definition_rows,
            )
        }

        position_ids = [row.id for row in position_rows]
        latest_states_by_position: dict[str, BetaPositionState] = {}
        if position_ids:
            state_subquery = (
                select(
                    BetaPositionState.demo_position_id.label("position_id"),
                    func.max(BetaPositionState.updated_at).label("latest_updated_at"),
                )
                .where(BetaPositionState.demo_position_id.in_(position_ids))
                .group_by(BetaPositionState.demo_position_id)
                .subquery()
            )
            latest_states = list(
                sess.execute(
                    select(BetaPositionState).join(
                        state_subquery,
                        (
                            (BetaPositionState.demo_position_id == state_subquery.c.position_id)
                            & (BetaPositionState.updated_at == state_subquery.c.latest_updated_at)
                        ),
                    )
                ).scalars().all()
            )
            latest_states_by_position = {
                str(row.demo_position_id): row for row in latest_states if row.demo_position_id is not None
            }

        enriched_rows: list[dict[str, object]] = []
        for position in position_rows:
            candidate_info = candidate_lookup.get(str(position.candidate_id)) if position.candidate_id else None
            position_state = latest_states_by_position.get(str(position.id))
            enriched_rows.append(
                {
                    **_row_to_dict(position),
                    "candidate_title": candidate_info.get("title") if candidate_info else None,
                    "candidate_status": candidate_info.get("status") if candidate_info else None,
                    "candidate_direction": candidate_info.get("direction") if candidate_info else None,
                    "candidate_hypothesis_definition_id": (
                        candidate_info.get("hypothesis_definition_id") if candidate_info else None
                    ),
                    "candidate_recommendation_status": (
                        candidate_info.get("recommendation_status") if candidate_info else None
                    ),
                    "candidate_recommendation_reason_text": (
                        candidate_info.get("recommendation_reason_text") if candidate_info else None
                    ),
                    "hypothesis_code": candidate_info.get("hypothesis_code") if candidate_info else None,
                    "hypothesis_name": candidate_info.get("hypothesis_name") if candidate_info else None,
                    "hypothesis_status": candidate_info.get("hypothesis_status") if candidate_info else None,
                    "hypothesis_family_name": candidate_info.get("hypothesis_family_name") if candidate_info else None,
                    "watch_eligible": candidate_info.get("watch_eligible") if candidate_info else None,
                    "promotion_eligible": candidate_info.get("promotion_eligible") if candidate_info else None,
                    "gate_fail_reason": candidate_info.get("gate_fail_reason") if candidate_info else None,
                    "position_state_status": position_state.position_status if position_state is not None else None,
                    "position_source": position_state.position_source if position_state is not None else None,
                    "thesis_expected_return_pct": (
                        position_state.thesis_expected_return_pct if position_state is not None else None
                    ),
                    "thesis_horizon_days": position_state.thesis_horizon_days if position_state is not None else None,
                    "thesis_remaining_days": position_state.thesis_remaining_days if position_state is not None else None,
                    "unrealized_return_pct": position_state.unrealized_return_pct if position_state is not None else None,
                    "realized_return_pct": position_state.realized_return_pct if position_state is not None else None,
                    "execution_quality_score": (
                        position_state.execution_quality_score if position_state is not None else None
                    ),
                    "last_execution_signal_type": (
                        position_state.last_execution_signal_type if position_state is not None else None
                    ),
                    "last_execution_signal_at": (
                        position_state.last_execution_signal_at if position_state is not None else None
                    ),
                }
            )
        return enriched_rows

    @staticmethod
    def _enrich_position_state_rows(
        sess,
        position_state_rows: Sequence[BetaPositionState],
        *,
        definition_rows: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        if not position_state_rows:
            return []

        definition_lookup = {
            str(row["id"]): row
            for row in (definition_rows or BetaOverviewService._definition_rows(sess))
        }
        enriched_rows: list[dict[str, object]] = []
        for position_state in position_state_rows:
            definition_info = (
                definition_lookup.get(str(position_state.thesis_hypothesis_definition_id))
                if position_state.thesis_hypothesis_definition_id is not None
                else None
            ) or {}
            enriched_rows.append(
                {
                    **_row_to_dict(position_state),
                    "hypothesis_code": definition_info.get("hypothesis_code"),
                    "hypothesis_name": definition_info.get("name"),
                    "hypothesis_status": definition_info.get("status"),
                    "hypothesis_family_name": definition_info.get("family_name"),
                    "watch_eligible": definition_info.get("watch_eligible"),
                    "promotion_eligible": definition_info.get("promotion_eligible"),
                    "gate_fail_reason": definition_info.get("gate_fail_reason"),
                }
            )
        return enriched_rows

    @staticmethod
    def _enrich_intraday_trade_rows(
        trade_rows: Sequence[BetaIntradaySimulatedTrade],
    ) -> list[dict[str, object]]:
        enriched_rows: list[dict[str, object]] = []
        for trade in trade_rows:
            notes = _json_object(trade.notes_json)
            enriched_rows.append(
                {
                    **_row_to_dict(trade),
                    "entry_source": str(notes.get("entry_source") or "").strip().upper() or None,
                    "pattern_hash": notes.get("pattern_hash"),
                    "pattern_code": notes.get("pattern_code"),
                    "pattern_family_code": notes.get("pattern_family_code"),
                    "pattern_quality_score": notes.get("pattern_quality_score"),
                    "pattern_stability_score": notes.get("pattern_stability_score"),
                    "pattern_horizon_stability_score": notes.get("pattern_horizon_stability_score"),
                    "pattern_post_cost_edge_15m_pct": notes.get("pattern_post_cost_edge_15m_pct"),
                    "pattern_best_horizon_minutes": notes.get("pattern_best_horizon_minutes"),
                    "pattern_best_horizon_expected_return_pct": notes.get("pattern_best_horizon_expected_return_pct"),
                    "pattern_best_horizon_post_cost_edge_pct": notes.get("pattern_best_horizon_post_cost_edge_pct"),
                    "pattern_sample_size": notes.get("pattern_sample_size"),
                    "pattern_matched_instruments": notes.get("pattern_matched_instruments"),
                }
            )
        return enriched_rows

    @staticmethod
    def _intraday_trade_summary(trade_rows: Sequence[dict[str, object]]) -> dict[str, object]:
        live_forward_rows = [
            row for row in trade_rows if str(row.get("simulation_source") or "").strip().upper() == "LIVE_FORWARD"
        ]
        historical_backfill_rows = [
            row for row in trade_rows if str(row.get("simulation_source") or "").strip().upper() == "HISTORICAL_BACKFILL"
        ]
        pattern_rows = [
            row for row in live_forward_rows if str(row.get("entry_source") or "").strip().upper() == "PATTERN"
        ]
        closed_pattern_rows = [
            row for row in pattern_rows if str(row.get("status") or "").strip().upper() != "OPEN"
        ]
        wins = [
            row
            for row in closed_pattern_rows
            if _safe_float(row.get("realized_return_pct")) is not None
            and float(row["realized_return_pct"]) > 0.0
        ]
        avg_post_cost = _mean(
            [
                float(row["realized_post_cost_return_pct"])
                for row in closed_pattern_rows
                if _safe_float(row.get("realized_post_cost_return_pct")) is not None
            ]
        )
        return {
            "live_forward_total": len(live_forward_rows),
            "historical_backfill_total": len(historical_backfill_rows),
            "pattern_live_forward_total": len(pattern_rows),
            "pattern_live_forward_open": len(
                [row for row in pattern_rows if str(row.get("status") or "").strip().upper() == "OPEN"]
            ),
            "pattern_live_forward_closed": len(closed_pattern_rows),
            "pattern_live_forward_win_rate_pct": round((len(wins) / len(closed_pattern_rows)) * 100.0, 1)
            if closed_pattern_rows
            else None,
            "pattern_live_forward_avg_post_cost_return_pct": avg_post_cost,
        }

    @staticmethod
    def _current_pattern_matches(
        sess,
        *,
        settings: BetaSettings,
        approved_patterns: list[dict[str, object]],
        threshold_profile: dict[str, object] | None = None,
        limit: int = 8,
    ) -> list[dict[str, object]]:
        if not approved_patterns:
            return []
        observation_rows = list(
            sess.scalars(
                select(BetaIntradayFeatureObservation)
                .where(BetaIntradayFeatureObservation.session_state == "REGULAR_OPEN")
                .order_by(desc(BetaIntradayFeatureObservation.observed_at), desc(BetaIntradayFeatureObservation.id))
                .limit(200)
            ).all()
        )
        latest_by_instrument: dict[str, BetaIntradayFeatureObservation] = {}
        for row in observation_rows:
            instrument_id = str(row.instrument_id or "").strip()
            if not instrument_id or instrument_id in latest_by_instrument:
                continue
            latest_by_instrument[instrument_id] = row

        matches: list[dict[str, object]] = []
        for row in latest_by_instrument.values():
            matched_pattern = BetaIntradayPatternReviewService.best_live_forward_match(
                row,
                approved_patterns,
                settings=settings,
                threshold_profile=threshold_profile,
            )
            if matched_pattern is None:
                continue
            matches.append(
                {
                    "instrument_id": row.instrument_id,
                    "symbol": row.symbol,
                    "observed_at": row.observed_at,
                    "state_code": row.state_code,
                    "state_label": row.state_label,
                    "recommended_action_side": row.recommended_action_side,
                    "recommended_action_code": row.recommended_action_code,
                    "recommended_action_label": row.recommended_action_label,
                    "confidence_score": row.confidence_score,
                    "expected_return_15m_pct": row.expected_return_15m_pct,
                    "post_cost_expected_return_15m_pct": row.post_cost_expected_return_15m_pct,
                    "matched_pattern_hash": matched_pattern.get("pattern_hash"),
                    "matched_pattern_code": matched_pattern.get("pattern_code"),
                    "matched_pattern_family_code": matched_pattern.get("family_code"),
                    "matched_pattern_bias": matched_pattern.get("action_bias"),
                    "matched_pattern_quality_score": matched_pattern.get("quality_score"),
                    "matched_pattern_stability_score": matched_pattern.get("stability_score"),
                    "matched_pattern_horizon_stability_score": matched_pattern.get("horizon_stability_score"),
                    "matched_pattern_edge_15m_pct": matched_pattern.get("post_cost_edge_15m_pct"),
                    "matched_pattern_best_horizon_minutes": matched_pattern.get("best_horizon_minutes"),
                    "matched_pattern_best_horizon_label": matched_pattern.get("best_horizon_label"),
                    "matched_pattern_best_horizon_edge_pct": matched_pattern.get("best_horizon_post_cost_edge_pct"),
                    "matched_pattern_sample_size": matched_pattern.get("sample_size"),
                    "matched_context_depth": matched_pattern.get("matched_context_depth"),
                    "matched_context_tags": matched_pattern.get("matched_context_tags") or [],
                }
            )
        matches.sort(
            key=lambda row: (
                float(row.get("matched_pattern_quality_score") or 0.0),
                float(row.get("matched_pattern_best_horizon_edge_pct") or row.get("matched_pattern_edge_15m_pct") or 0.0),
                float(row.get("confidence_score") or 0.0),
                row.get("observed_at"),
            ),
            reverse=True,
        )
        return matches[: max(1, limit)]

    @staticmethod
    def get_modern_dashboard() -> dict[str, object]:
        """Build the lightweight dashboard used by the modern beta UI surfaces."""
        if not BetaContext.is_initialized():
            return {
                "available": False,
                "status": None,
                "runtime_flags": {},
                "runtime_activity": {},
                "pipeline_health": {"available": False},
                "intraday_pattern_summary": {"available": False},
                "intraday_pattern_policy": {},
                "intraday_pattern_thresholds": {},
                "intraday_pattern_exploration_profile": {},
                "intraday_pattern_execution_profile": {},
                "intraday_trade_summary": {},
                "current_pattern_matches": [],
                "ledger": None,
                "recent_intraday_trades": [],
                "jobs": [],
            }

        beta_db_path = get_beta_db_path()
        beta_db_key = str(beta_db_path) if beta_db_path is not None else None
        cached = BetaOverviewService._get_cached_modern_dashboard(beta_db_path=beta_db_key)
        if cached is not None:
            return cached

        with BetaContext.read_session() as sess:
            settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()
            status = sess.scalar(select(BetaSystemStatus).where(BetaSystemStatus.id == 1))
            recent_jobs = list(
                sess.scalars(select(BetaJobRun).order_by(desc(BetaJobRun.started_at)).limit(40)).all()
            )
            latest_successful_job = next((row for row in recent_jobs if row.status == "SUCCESS"), None)
            latest_failed_job = next((row for row in recent_jobs if row.status == "FAILED"), None)
            latest_jobs_by_type: dict[str, dict] = {}
            for row in recent_jobs:
                if row.job_type not in latest_jobs_by_type:
                    latest_jobs_by_type[row.job_type] = _row_to_dict(row)
            latest_job_activity_at = _latest_activity_at(
                *[_latest_activity_at(row.started_at, row.completed_at) for row in recent_jobs]
            )
            latest_runtime_activity_at = _latest_activity_at(
                status.last_heartbeat_at if status is not None else None,
                status.updated_at if status is not None else None,
                latest_job_activity_at,
            )
            current_running_job = next((row for row in recent_jobs if row.status == "RUNNING"), None)
            supervisor_alive = _process_is_alive(status.supervisor_pid if status is not None else None)
            supervisor_status = (
                "running"
                if supervisor_alive
                else str(status.supervisor_status or "stopped") if status is not None else "stopped"
            )
            runtime_activity = {
                "supervisor_alive": supervisor_alive,
                "supervisor_status_display": supervisor_status,
                "heartbeat_age_seconds": _seconds_since(latest_runtime_activity_at),
                "heartbeat_at": latest_runtime_activity_at,
                "last_successful_job": _row_to_dict(latest_successful_job) if latest_successful_job is not None else None,
                "last_failed_job": _row_to_dict(latest_failed_job) if latest_failed_job is not None else None,
                "current_running_job": _row_to_dict(current_running_job) if current_running_job is not None else None,
                "current_running_job_age_seconds": (
                    _seconds_since(current_running_job.started_at) if current_running_job is not None else None
                ),
                "latest_jobs_by_type": latest_jobs_by_type,
                "latest_success_count": len([row for row in recent_jobs[:12] if row.status == "SUCCESS"]),
                "latest_failure_count": len([row for row in recent_jobs[:12] if row.status == "FAILED"]),
            }
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
            intraday_pattern_summary = BetaIntradayPatternReviewService.latest_summary_in_session(
                sess,
                settings,
                leaderboard_limit=10,
                family_limit=8,
            )
            intraday_pattern_policy = (
                intraday_pattern_summary.get("adaptive_policy")
                or BetaIntradayPatternParameterLearningService.static_policy_snapshot(settings)
            )
            intraday_pattern_thresholds = (
                intraday_pattern_summary.get("threshold_profile")
                or BetaIntradayPatternThresholdLearningService.static_threshold_snapshot(settings)
            )
            intraday_pattern_exploration_profile = (
                BetaIntradayPatternExplorationLearningService.resolve_profile_in_session(sess, settings)
            )
            intraday_pattern_execution_profile = (
                BetaIntradayPatternExecutionLearningService.resolve_profile_in_session(sess, settings)
            )
            current_pattern_matches = BetaOverviewService._current_pattern_matches(
                sess,
                settings=settings,
                approved_patterns=list(intraday_pattern_summary.get("approved_patterns") or []),
                threshold_profile=intraday_pattern_thresholds,
                limit=8,
            )
            pipeline_health = (
                BetaPipelineAssessmentService.latest_snapshot_metrics(snapshot_type="SUPERVISOR_CYCLE")
                or {
                    "available": False,
                    "overall_status": "BOOTSTRAPPING",
                    "summary_text": "Pipeline snapshot not yet available.",
                }
            )
            ledger = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
            recent_intraday_trades = list(
                sess.scalars(
                    select(BetaIntradaySimulatedTrade)
                    .order_by(desc(BetaIntradaySimulatedTrade.updated_at), desc(BetaIntradaySimulatedTrade.created_at))
                    .limit(12)
                ).all()
            )
            enriched_intraday_trades = BetaOverviewService._enrich_intraday_trade_rows(recent_intraday_trades)
            intraday_trade_summary = BetaOverviewService._intraday_trade_summary(enriched_intraday_trades)

            dashboard = {
                "available": True,
                "status": _row_to_dict(status) if status is not None else None,
                "runtime_flags": runtime_flags,
                "runtime_activity": runtime_activity,
                "pipeline_health": pipeline_health,
                "intraday_pattern_summary": intraday_pattern_summary,
                "intraday_pattern_policy": intraday_pattern_policy,
                "intraday_pattern_thresholds": intraday_pattern_thresholds,
                "intraday_pattern_exploration_profile": intraday_pattern_exploration_profile,
                "intraday_pattern_execution_profile": intraday_pattern_execution_profile,
                "intraday_trade_summary": intraday_trade_summary,
                "current_pattern_matches": current_pattern_matches,
                "ledger": _row_to_dict(ledger) if ledger is not None else None,
                "recent_intraday_trades": enriched_intraday_trades,
                "jobs": BetaOverviewService._query_rows(recent_jobs[:10]),
            }

        return BetaOverviewService._store_cached_modern_dashboard(
            beta_db_path=beta_db_key,
            dashboard=dashboard,
        )

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

        beta_db_path = get_beta_db_path()
        beta_db_key = str(beta_db_path) if beta_db_path is not None else None
        cached = BetaOverviewService._get_cached_dashboard(beta_db_path=beta_db_key)
        if cached is not None:
            return cached

        with BetaContext.read_session() as sess:
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
                "hypothesis_definitions_total": sess.scalar(select(func.count()).select_from(BetaHypothesisDefinition)) or 0,
                "hypothesis_definitions_validated": sess.scalar(
                    select(func.count()).select_from(BetaHypothesisBeliefState).where(BetaHypothesisBeliefState.status == "VALIDATED")
                )
                or 0,
                "hypothesis_definitions_promising": sess.scalar(
                    select(func.count()).select_from(BetaHypothesisBeliefState).where(BetaHypothesisBeliefState.status == "PROMISING")
                )
                or 0,
                "hypothesis_definitions_degraded": sess.scalar(
                    select(func.count()).select_from(BetaHypothesisBeliefState).where(BetaHypothesisBeliefState.status == "DEGRADED")
                )
                or 0,
                "hypothesis_definitions_rejected": sess.scalar(
                    select(func.count()).select_from(BetaHypothesisBeliefState).where(
                        BetaHypothesisBeliefState.status.in_(("REJECTED", "RETIRED"))
                    )
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
                "held_positions_open": sess.scalar(
                    select(func.count()).select_from(BetaPositionState).where(BetaPositionState.position_status == "OPEN")
                )
                or 0,
                "positions_closed": sess.scalar(
                    select(func.count()).select_from(BetaDemoPosition).where(BetaDemoPosition.status != "OPEN")
                )
                or 0,
                "simulated_positions_open": sess.scalar(
                    select(func.count()).select_from(BetaPositionState).where(
                        BetaPositionState.position_status == "OPEN",
                        BetaPositionState.position_source == "SIMULATED",
                    )
                )
                or 0,
                "execution_signals_total": sess.scalar(select(func.count()).select_from(BetaExecutionSignal)) or 0,
                "execution_labels_total": sess.scalar(select(func.count()).select_from(BetaExecutionLabelValue)) or 0,
                "signal_observations_total": sess.scalar(select(func.count()).select_from(BetaSignalObservation)) or 0,
                "signal_observations_realized": sess.scalar(
                    select(func.count()).select_from(BetaSignalObservation).where(BetaSignalObservation.realized_return_pct.is_not(None))
                )
                or 0,
                "recommendation_decisions_total": sess.scalar(
                    select(func.count()).select_from(BetaRecommendationDecision)
                )
                or 0,
                "recommendation_decisions_recommended": sess.scalar(
                    select(func.count()).select_from(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.decision_status == "RECOMMENDED"
                    )
                )
                or 0,
                "execution_notifications_total": sess.scalar(
                    select(func.count())
                    .select_from(BetaUiNotification)
                    .where(BetaUiNotification.notification_type == "execution_signal_state_change")
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
                "intraday_pattern_runs_total": sess.scalar(
                    select(func.count()).select_from(BetaIntradayPatternDiscoveryRun)
                )
                or 0,
                "intraday_pattern_candidates_total": sess.scalar(
                    select(func.count()).select_from(BetaIntradayPatternCandidate)
                )
                or 0,
                "intraday_pattern_screened_in_total": sess.scalar(
                    select(func.count())
                    .select_from(BetaIntradayPatternCandidate)
                    .where(BetaIntradayPatternCandidate.status == "SCREENED_IN")
                )
                or 0,
                "intraday_simulated_trades_total": sess.scalar(
                    select(func.count()).select_from(BetaIntradaySimulatedTrade)
                )
                or 0,
                "intraday_simulated_trades_open": sess.scalar(
                    select(func.count())
                    .select_from(BetaIntradaySimulatedTrade)
                    .where(BetaIntradaySimulatedTrade.status == "OPEN")
                )
                or 0,
                "intraday_simulated_trades_live_forward": sess.scalar(
                    select(func.count())
                    .select_from(BetaIntradaySimulatedTrade)
                    .where(BetaIntradaySimulatedTrade.simulation_source == "LIVE_FORWARD")
                )
                or 0,
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
            latest_job_activity_at = _latest_activity_at(
                *[
                    _latest_activity_at(row.started_at, row.completed_at)
                    for row in recent_jobs
                ]
            )
            latest_runtime_activity_at = _latest_activity_at(
                status.last_heartbeat_at if status is not None else None,
                status.updated_at if status is not None else None,
                latest_job_activity_at,
            )
            current_running_job = next((row for row in recent_jobs if row.status == "RUNNING"), None)
            supervisor_alive = _process_is_alive(status.supervisor_pid if status is not None else None)
            supervisor_status = "running" if supervisor_alive else str(status.supervisor_status or "stopped") if status is not None else "stopped"
            runtime_activity = {
                "supervisor_alive": supervisor_alive,
                "supervisor_status_display": supervisor_status,
                "heartbeat_age_seconds": _seconds_since(latest_runtime_activity_at),
                "heartbeat_at": latest_runtime_activity_at,
                "last_successful_job": _row_to_dict(latest_successful_job) if latest_successful_job is not None else None,
                "last_failed_job": _row_to_dict(latest_failed_job) if latest_failed_job is not None else None,
                "current_running_job": _row_to_dict(current_running_job) if current_running_job is not None else None,
                "current_running_job_age_seconds": (
                    _seconds_since(current_running_job.started_at)
                    if current_running_job is not None
                    else None
                ),
                "latest_jobs_by_type": latest_jobs_by_type,
                "latest_success_count": len([row for row in recent_jobs[:12] if row.status == "SUCCESS"]),
                "latest_failure_count": len([row for row in recent_jobs[:12] if row.status == "FAILED"]),
            }
            hypothesis_rows = list(sess.scalars(select(BetaHypothesis)).all())
            hypothesis_rows.sort(
                key=lambda row: (_safe_float(row.evidence_score), row.updated_at),
                reverse=True,
            )
            definition_rows = BetaOverviewService._definition_rows(sess)
            definition_family_rows = BetaOverviewService._definition_family_rows(definition_rows)
            validity_summary = BetaOverviewService._definition_validity_summary(definition_rows)
            execution_feedback_summary = BetaOverviewService._execution_feedback_summary(sess)
            intraday_pattern_summary = BetaIntradayPatternReviewService.latest_summary_in_session(
                sess,
                settings,
                leaderboard_limit=10,
                family_limit=8,
            )
            intraday_pattern_policy = (
                intraday_pattern_summary.get("adaptive_policy")
                or BetaIntradayPatternParameterLearningService.static_policy_snapshot(settings)
            )
            intraday_pattern_thresholds = (
                intraday_pattern_summary.get("threshold_profile")
                or BetaIntradayPatternThresholdLearningService.static_threshold_snapshot(settings)
            )
            intraday_pattern_exploration_profile = (
                BetaIntradayPatternExplorationLearningService.resolve_profile_in_session(sess, settings)
            )
            intraday_pattern_execution_profile = (
                BetaIntradayPatternExecutionLearningService.resolve_profile_in_session(sess, settings)
            )
            current_pattern_matches = BetaOverviewService._current_pattern_matches(
                sess,
                settings=settings,
                approved_patterns=list(intraday_pattern_summary.get("approved_patterns") or []),
                threshold_profile=intraday_pattern_thresholds,
                limit=8,
            )
            pipeline_health = (
                BetaPipelineAssessmentService.latest_snapshot_metrics(snapshot_type="SUPERVISOR_CYCLE")
                or {
                    "available": False,
                    "overall_status": "BOOTSTRAPPING",
                    "summary_text": "Pipeline snapshot not yet available.",
                }
            )
            active_positions = list(
                sess.scalars(
                    select(BetaDemoPosition)
                    .where(BetaDemoPosition.status == "OPEN")
                    .order_by(desc(BetaDemoPosition.opened_at))
                    .limit(8)
                ).all()
            )
            held_position_states = list(
                sess.scalars(
                    select(BetaPositionState)
                    .where(BetaPositionState.position_status == "OPEN")
                    .order_by(desc(BetaPositionState.updated_at))
                    .limit(8)
                ).all()
            )
            watched_candidates = list(
                sess.scalars(
                    select(BetaSignalCandidate)
                    .where(BetaSignalCandidate.status.in_(("WATCHING", "PROMOTED")))
                    .order_by(
                        desc(BetaSignalCandidate.confidence_score),
                        desc(BetaSignalCandidate.expected_edge_score),
                        desc(BetaSignalCandidate.updated_at),
                    )
                    .limit(8)
                ).all()
            )
            rejected_candidates = list(
                sess.scalars(
                    select(BetaSignalCandidate)
                    .where(BetaSignalCandidate.status.in_(("DISMISSED", "REJECTED")))
                    .order_by(desc(BetaSignalCandidate.updated_at))
                    .limit(8)
                ).all()
            )
            recent_intraday_trades = list(
                sess.scalars(
                    select(BetaIntradaySimulatedTrade)
                    .order_by(desc(BetaIntradaySimulatedTrade.updated_at), desc(BetaIntradaySimulatedTrade.created_at))
                    .limit(12)
                ).all()
            )
            recent_intraday_trade_events = list(
                sess.scalars(
                    select(BetaIntradaySimulatedTradeEvent)
                    .order_by(desc(BetaIntradaySimulatedTradeEvent.event_time), desc(BetaIntradaySimulatedTradeEvent.created_at))
                    .limit(12)
                ).all()
            )
            enriched_intraday_trades = BetaOverviewService._enrich_intraday_trade_rows(recent_intraday_trades)
            intraday_trade_summary = BetaOverviewService._intraday_trade_summary(enriched_intraday_trades)

            dashboard = {
                "available": True,
                "status": _row_to_dict(status) if status is not None else None,
                "runtime_flags": runtime_flags,
                "runtime_activity": runtime_activity,
                "pipeline_health": pipeline_health,
                "counts": counts,
                "performance": performance,
                "validity_summary": validity_summary,
                "execution_feedback_summary": execution_feedback_summary,
                "intraday_pattern_summary": intraday_pattern_summary,
                "intraday_pattern_policy": intraday_pattern_policy,
                "intraday_pattern_thresholds": intraday_pattern_thresholds,
                "intraday_pattern_exploration_profile": intraday_pattern_exploration_profile,
                "intraday_pattern_execution_profile": intraday_pattern_execution_profile,
                "intraday_trade_summary": intraday_trade_summary,
                "current_pattern_matches": current_pattern_matches,
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
                "hypothesis_definitions": definition_rows[:12],
                "hypothesis_definition_families": definition_family_rows[:12],
                "hypotheses": BetaOverviewService._query_rows(hypothesis_rows[:8]),
                "active_positions": BetaOverviewService._enrich_position_rows(
                    sess,
                    active_positions,
                    definition_rows=definition_rows,
                ),
                "held_position_states": BetaOverviewService._enrich_position_state_rows(
                    sess,
                    held_position_states,
                    definition_rows=definition_rows,
                ),
                "closed_positions": BetaOverviewService._enrich_position_rows(
                    sess,
                    closed_positions[:8],
                    definition_rows=definition_rows,
                ),
                "watched_candidates": BetaOverviewService._enrich_candidate_rows(
                    sess,
                    watched_candidates,
                    definition_rows=definition_rows,
                ),
                "tracked_core_signals": BetaOverviewService._latest_score_rows(
                    sess,
                    tracked_core_only=True,
                    limit=8,
                ),
                "rejected_candidates": BetaOverviewService._enrich_candidate_rows(
                    sess,
                    rejected_candidates,
                    definition_rows=definition_rows,
                ),
                "notifications": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaUiNotification)
                        .order_by(desc(BetaUiNotification.created_at))
                        .limit(10)
                    )
                ),
                "execution_notifications": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaUiNotification)
                        .where(BetaUiNotification.notification_type == "execution_signal_state_change")
                        .order_by(desc(BetaUiNotification.created_at))
                        .limit(10)
                    )
                ),
                "jobs": BetaOverviewService._query_rows(recent_jobs[:10]),
                "recent_execution_signals": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaExecutionSignal)
                        .order_by(desc(BetaExecutionSignal.signal_time), desc(BetaExecutionSignal.created_at))
                        .limit(12)
                    )
                ),
                "recent_execution_labels": BetaOverviewService._query_rows(
                    sess.scalars(
                        select(BetaExecutionLabelValue)
                        .order_by(desc(BetaExecutionLabelValue.updated_at))
                        .limit(12)
                    )
                ),
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
                "recent_intraday_trades": enriched_intraday_trades,
                "recent_intraday_trade_events": BetaOverviewService._query_rows(recent_intraday_trade_events),
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
            return BetaOverviewService._store_cached_dashboard(
                beta_db_path=beta_db_key,
                dashboard=dashboard,
            )

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
            definition = None
            family = None
            belief = None
            observation = None
            recommendation = None
            latest_test = None
            position_state = None
            if candidate.hypothesis_definition_id is not None:
                definition = sess.scalar(
                    select(BetaHypothesisDefinition).where(BetaHypothesisDefinition.id == candidate.hypothesis_definition_id)
                )
                if definition is not None and definition.family_id is not None:
                    family = sess.scalar(select(BetaHypothesisFamily).where(BetaHypothesisFamily.id == definition.family_id))
                belief = sess.scalar(
                    select(BetaHypothesisBeliefState).where(
                        BetaHypothesisBeliefState.hypothesis_definition_id == candidate.hypothesis_definition_id
                    )
                )
                latest_test = sess.scalar(
                    select(BetaHypothesisTestRun)
                    .where(BetaHypothesisTestRun.hypothesis_definition_id == candidate.hypothesis_definition_id)
                    .order_by(desc(BetaHypothesisTestRun.created_at))
                    .limit(1)
                )
            if candidate.signal_observation_id is not None:
                observation = sess.scalar(
                    select(BetaSignalObservation).where(BetaSignalObservation.id == candidate.signal_observation_id)
                )
            if candidate.recommendation_decision_id is not None:
                recommendation = sess.scalar(
                    select(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.id == candidate.recommendation_decision_id
                    )
                )
            position_state = sess.scalar(
                select(BetaPositionState)
                .where(BetaPositionState.thesis_candidate_id == candidate.id)
                .order_by(desc(BetaPositionState.updated_at))
                .limit(1)
            )
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
            execution_signals = []
            execution_labels = []
            if position_state is not None:
                execution_signals = list(
                    sess.scalars(
                        select(BetaExecutionSignal)
                        .where(BetaExecutionSignal.position_state_id == position_state.id)
                        .order_by(desc(BetaExecutionSignal.signal_time))
                        .limit(12)
                    ).all()
                )
                execution_labels = list(
                    sess.scalars(
                        select(BetaExecutionLabelValue)
                        .where(BetaExecutionLabelValue.position_state_id == position_state.id)
                        .order_by(desc(BetaExecutionLabelValue.updated_at))
                        .limit(12)
                    ).all()
                )
            observation_context = _json_object(observation.regime_context_json) if observation is not None else {}
            latest_test_notes = _json_object(latest_test.notes_json) if latest_test is not None else {}
            return {
                "candidate": _row_to_dict(candidate),
                "hypothesis": _row_to_dict(hypothesis) if hypothesis is not None else None,
                "hypothesis_definition": _row_to_dict(definition) if definition is not None else None,
                "hypothesis_family": _row_to_dict(family) if family is not None else None,
                "belief_state": _row_to_dict(belief) if belief is not None else None,
                "signal_observation": _row_to_dict(observation) if observation is not None else None,
                "recommendation_decision": _row_to_dict(recommendation) if recommendation is not None else None,
                "latest_test_run": _row_to_dict(latest_test) if latest_test is not None else None,
                "latest_test_notes": latest_test_notes,
                "governance": (
                    latest_test_notes.get("governance")
                    if isinstance(latest_test_notes.get("governance"), dict)
                    else None
                ),
                "observation_context": observation_context,
                "position_state": _row_to_dict(position_state) if position_state is not None else None,
                "execution_signals": BetaOverviewService._query_rows(execution_signals),
                "execution_labels": BetaOverviewService._query_rows(execution_labels),
                "evidence_payload": _json_object(candidate.evidence_json),
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
            definition = None
            family = None
            belief = None
            observation = None
            recommendation = None
            latest_test = None
            if position.candidate_id is not None:
                candidate = sess.scalar(select(BetaSignalCandidate).where(BetaSignalCandidate.id == position.candidate_id))
            if candidate is not None and candidate.hypothesis_id is not None:
                hypothesis = sess.scalar(select(BetaHypothesis).where(BetaHypothesis.id == candidate.hypothesis_id))
            if candidate is not None and candidate.hypothesis_definition_id is not None:
                definition = sess.scalar(
                    select(BetaHypothesisDefinition).where(BetaHypothesisDefinition.id == candidate.hypothesis_definition_id)
                )
                if definition is not None and definition.family_id is not None:
                    family = sess.scalar(select(BetaHypothesisFamily).where(BetaHypothesisFamily.id == definition.family_id))
                belief = sess.scalar(
                    select(BetaHypothesisBeliefState).where(
                        BetaHypothesisBeliefState.hypothesis_definition_id == candidate.hypothesis_definition_id
                    )
                )
                latest_test = sess.scalar(
                    select(BetaHypothesisTestRun)
                    .where(BetaHypothesisTestRun.hypothesis_definition_id == candidate.hypothesis_definition_id)
                    .order_by(desc(BetaHypothesisTestRun.created_at))
                    .limit(1)
                )
            if candidate is not None and candidate.signal_observation_id is not None:
                observation = sess.scalar(
                    select(BetaSignalObservation).where(BetaSignalObservation.id == candidate.signal_observation_id)
                )
            if candidate is not None and candidate.recommendation_decision_id is not None:
                recommendation = sess.scalar(
                    select(BetaRecommendationDecision).where(
                        BetaRecommendationDecision.id == candidate.recommendation_decision_id
                    )
                )
            position_state = sess.scalar(
                select(BetaPositionState)
                .where(BetaPositionState.demo_position_id == position.id)
                .order_by(desc(BetaPositionState.updated_at))
                .limit(1)
            )
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
            execution_signals = []
            execution_labels = []
            if position_state is not None:
                execution_signals = list(
                    sess.scalars(
                        select(BetaExecutionSignal)
                        .where(BetaExecutionSignal.position_state_id == position_state.id)
                        .order_by(desc(BetaExecutionSignal.signal_time))
                        .limit(20)
                    ).all()
                )
                execution_labels = list(
                    sess.scalars(
                        select(BetaExecutionLabelValue)
                        .where(BetaExecutionLabelValue.position_state_id == position_state.id)
                        .order_by(desc(BetaExecutionLabelValue.updated_at))
                        .limit(20)
                    ).all()
                )
            position_state_metadata = _json_object(position_state.metadata_json) if position_state is not None else {}
            latest_test_notes = _json_object(latest_test.notes_json) if latest_test is not None else {}
            return {
                "position": _row_to_dict(position),
                "candidate": _row_to_dict(candidate) if candidate is not None else None,
                "hypothesis": _row_to_dict(hypothesis) if hypothesis is not None else None,
                "hypothesis_definition": _row_to_dict(definition) if definition is not None else None,
                "hypothesis_family": _row_to_dict(family) if family is not None else None,
                "belief_state": _row_to_dict(belief) if belief is not None else None,
                "signal_observation": _row_to_dict(observation) if observation is not None else None,
                "recommendation_decision": _row_to_dict(recommendation) if recommendation is not None else None,
                "latest_test_run": _row_to_dict(latest_test) if latest_test is not None else None,
                "latest_test_notes": latest_test_notes,
                "governance": (
                    latest_test_notes.get("governance")
                    if isinstance(latest_test_notes.get("governance"), dict)
                    else None
                ),
                "position_state": _row_to_dict(position_state) if position_state is not None else None,
                "position_state_metadata": position_state_metadata,
                "execution_signals": BetaOverviewService._query_rows(execution_signals),
                "execution_labels": BetaOverviewService._query_rows(execution_labels),
                "candidate_evidence": _json_object(candidate.evidence_json) if candidate is not None else {},
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
            definition = sess.scalar(
                select(BetaHypothesisDefinition).where(BetaHypothesisDefinition.id == hypothesis_id)
            )
            if definition is not None:
                family = None
                if definition.family_id is not None:
                    family = sess.scalar(select(BetaHypothesisFamily).where(BetaHypothesisFamily.id == definition.family_id))
                belief = sess.scalar(
                    select(BetaHypothesisBeliefState).where(
                        BetaHypothesisBeliefState.hypothesis_definition_id == definition.id
                    )
                )
                latest_test = sess.scalar(
                    select(BetaHypothesisTestRun)
                    .where(BetaHypothesisTestRun.hypothesis_definition_id == definition.id)
                    .order_by(desc(BetaHypothesisTestRun.created_at))
                    .limit(1)
                )
                recent_tests = list(
                    sess.scalars(
                        select(BetaHypothesisTestRun)
                        .where(BetaHypothesisTestRun.hypothesis_definition_id == definition.id)
                        .order_by(desc(BetaHypothesisTestRun.created_at))
                        .limit(12)
                    ).all()
                )
                observations = list(
                    sess.scalars(
                        select(BetaSignalObservation)
                        .where(BetaSignalObservation.hypothesis_definition_id == definition.id)
                        .order_by(desc(BetaSignalObservation.realized_at), desc(BetaSignalObservation.observation_time))
                        .limit(20)
                    ).all()
                )
                candidates = list(
                    sess.scalars(
                        select(BetaSignalCandidate)
                        .where(BetaSignalCandidate.hypothesis_definition_id == definition.id)
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
                thesis_states = list(
                    sess.scalars(
                        select(BetaPositionState)
                        .where(BetaPositionState.thesis_hypothesis_definition_id == definition.id)
                        .order_by(desc(BetaPositionState.updated_at))
                        .limit(20)
                    ).all()
                )
                latest_test_notes = _json_object(latest_test.notes_json) if latest_test is not None else {}
                governance = latest_test_notes.get("governance") if isinstance(latest_test_notes.get("governance"), dict) else None
                observation_feedback = BetaOverviewService._observation_feedback_by_definition(sess).get(definition.id, {})
                return {
                    "detail_type": "definition",
                    "hypothesis_definition": _row_to_dict(definition),
                    "hypothesis_family": _row_to_dict(family) if family is not None else None,
                    "belief_state": _row_to_dict(belief) if belief is not None else None,
                    "latest_test_run": _row_to_dict(latest_test) if latest_test is not None else None,
                    "latest_test_notes": latest_test_notes,
                    "governance": governance,
                    "observation_feedback": observation_feedback,
                    "recent_tests": BetaOverviewService._query_rows(recent_tests),
                    "observations": BetaOverviewService._query_rows(observations),
                    "candidates": BetaOverviewService._query_rows(candidates),
                    "positions": BetaOverviewService._query_rows(positions),
                    "position_states": BetaOverviewService._query_rows(thesis_states),
                }

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
                "detail_type": "family",
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


def _status_rank(value: str | None) -> int:
    order = {
        "VALIDATED": 0,
        "PROMISING": 1,
        "CANDIDATE": 2,
        "SCREENED_IN": 3,
        "DISCOVERED": 4,
        "WATCHING": 5,
        "RECOMMENDED": 6,
        "DEGRADED": 7,
        "BLOCKED": 8,
        "REJECTED": 9,
        "RETIRED": 10,
        "ARCHIVED": 11,
    }
    return order.get(str(value or "").upper(), 20)
