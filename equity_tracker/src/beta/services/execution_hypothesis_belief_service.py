"""Belief refresh for intraday execution hypotheses."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionHypothesisBeliefState,
    BetaExecutionHypothesisDefinition,
    BetaExecutionHypothesisTestRun,
)
from ..settings import BetaSettings
from ..state import get_beta_db_path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaExecutionHypothesisBeliefService:
    """Translate execution backtest history into persistent intraday belief states."""

    @staticmethod
    def refresh_belief_states(settings: BetaSettings | None = None) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"definitions_considered": 0, "beliefs_written": 0}

        if settings is None:
            beta_db_path = get_beta_db_path()
            settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()

        with BetaContext.write_session() as sess:
            definitions = list(sess.scalars(select(BetaExecutionHypothesisDefinition)).all())
            beliefs_written = 0
            candidate_count = 0
            promising_count = 0
            validated_count = 0
            for definition in definitions:
                test_runs = list(
                    sess.scalars(
                        select(BetaExecutionHypothesisTestRun)
                        .where(BetaExecutionHypothesisTestRun.execution_hypothesis_definition_id == definition.id)
                        .order_by(desc(BetaExecutionHypothesisTestRun.created_at))
                    ).all()
                )
                assessment = BetaExecutionHypothesisBeliefService._assess_definition(test_runs, settings=settings)
                belief = sess.get(BetaExecutionHypothesisBeliefState, definition.id)
                if belief is None:
                    belief = BetaExecutionHypothesisBeliefState(execution_hypothesis_definition_id=definition.id)
                    sess.add(belief)
                belief.confidence_score = assessment["confidence_score"]
                belief.evidence_count = assessment["evidence_count"]
                belief.in_sample_strength = assessment["in_sample_strength"]
                belief.out_of_sample_strength = assessment["out_of_sample_strength"]
                belief.degradation_rate = assessment["degradation_rate"]
                belief.recency_score = assessment["recency_score"]
                belief.stability_score = assessment["stability_score"]
                belief.last_validated_at = assessment["last_validated_at"]
                belief.last_tested_at = assessment["last_tested_at"]
                belief.status = assessment["status"]
                belief.supporting_test_run_id = assessment["supporting_test_run_id"]
                belief.contradicting_test_run_id = assessment["contradicting_test_run_id"]
                belief.notes_json = json.dumps(assessment["notes"], sort_keys=True)

                definition.status = BetaExecutionHypothesisBeliefService._map_definition_status(assessment["status"])
                beliefs_written += 1
                if assessment["status"] == "CANDIDATE":
                    candidate_count += 1
                elif assessment["status"] == "PROMISING":
                    promising_count += 1
                elif assessment["status"] == "VALIDATED":
                    validated_count += 1
            return {
                "definitions_considered": len(definitions),
                "beliefs_written": beliefs_written,
                "candidate_definitions": candidate_count,
                "promising_definitions": promising_count,
                "validated_definitions": validated_count,
            }

    @staticmethod
    def _assess_definition(
        test_runs: list[BetaExecutionHypothesisTestRun],
        *,
        settings: BetaSettings,
    ) -> dict[str, object]:
        if not test_runs:
            return {
                "confidence_score": 0.05,
                "evidence_count": 0,
                "in_sample_strength": 0.0,
                "out_of_sample_strength": 0.0,
                "degradation_rate": 0.0,
                "recency_score": 0.0,
                "stability_score": 0.0,
                "last_validated_at": None,
                "last_tested_at": None,
                "status": "DISCOVERED",
                "supporting_test_run_id": None,
                "contradicting_test_run_id": None,
                "notes": {"reason": "no_test_runs"},
            }

        now = _utcnow()
        latest = test_runs[0]
        evidence_count = len(test_runs)
        sample_size = int(latest.support_count or latest.sample_size or 0)
        matched_instruments = int(latest.matched_instruments or 0)
        latest_adjusted = float(latest.transaction_cost_adjusted_return_pct or 0.0)
        latest_median = float(latest.median_return_pct or 0.0)
        latest_edge = float(latest.baseline_edge_pct or 0.0)
        latest_win_rate = float(latest.win_rate_pct or 0.0)
        avg_stability = round(
            sum(float(run.stability_score or 0.0) for run in test_runs) / evidence_count,
            4,
        )
        days_since_latest = max(0.0, (now - latest.created_at).total_seconds() / 86400.0)
        recency_score = round(max(0.0, min(1.0, 1.0 - (days_since_latest / 30.0))), 4)
        degradation_rate = BetaExecutionHypothesisBeliefService._degradation_rate(test_runs)
        sample_factor = min(1.0, sample_size / 60.0)
        evidence_factor = min(1.0, evidence_count / 3.0)
        positive_strength = (
            max(0.0, latest_adjusted / 0.2)
            + max(0.0, latest_median / 0.15)
            + max(0.0, latest_edge / 0.15)
            + (avg_stability * 0.9)
        )
        negative_strength = (
            max(0.0, -latest_adjusted / 0.15)
            + max(0.0, -latest_median / 0.1)
            + max(0.0, -latest_edge / 0.1)
            + degradation_rate
            + max(0.0, 0.25 - avg_stability)
        )
        confidence_score = round(
            min(
                0.95,
                max(
                    0.05,
                    0.08
                    + (sample_factor * 0.2)
                    + (evidence_factor * 0.18)
                    + min(0.35, positive_strength * 0.08)
                    - min(0.3, negative_strength * 0.08),
                ),
            ),
            4,
        )

        min_support = max(5, int(settings.intraday_execution_hypothesis_min_support))
        min_instruments = max(1, int(settings.intraday_execution_hypothesis_min_matched_instruments))
        status = "DISCOVERED"
        if sample_size >= min_support and latest_adjusted > -0.02:
            status = "CANDIDATE"
        if latest_adjusted < 0.0 or latest_median <= 0.0 or latest_edge < 0.0:
            status = "DEGRADED"
        if (
            evidence_count >= 2
            and sample_size >= min_support
            and latest_adjusted > 0.0
            and latest_median > 0.0
            and latest_win_rate >= 52.0
            and avg_stability >= 0.3
        ):
            status = "PROMISING"
        if (
            evidence_count >= 3
            and sample_size >= max(min_support, 30)
            and matched_instruments >= min_instruments
            and latest_adjusted > 0.05
            and latest_median > 0.02
            and latest_edge >= 0.0
            and latest_win_rate >= 54.0
            and avg_stability >= 0.45
            and degradation_rate <= 0.08
        ):
            status = "VALIDATED"
        if BetaExecutionHypothesisBeliefService._degradation_streak(test_runs) >= 3 and status not in {"REJECTED", "RETIRED"}:
            status = "DEGRADED"
        if evidence_count >= 3 and latest_adjusted <= -0.08 and latest_median <= -0.04 and confidence_score <= 0.25:
            status = "REJECTED"
        if evidence_count >= 4 and latest_adjusted <= -0.12 and latest_edge <= -0.05:
            status = "RETIRED"

        supporting = max(
            test_runs,
            key=lambda run: (
                float(run.transaction_cost_adjusted_return_pct or 0.0)
                + float(run.baseline_edge_pct or 0.0)
                + float(run.stability_score or 0.0)
            ),
        )
        contradicting = min(
            test_runs,
            key=lambda run: (
                float(run.transaction_cost_adjusted_return_pct or 0.0)
                + float(run.baseline_edge_pct or 0.0)
                + float(run.stability_score or 0.0)
            ),
        )
        return {
            "confidence_score": confidence_score,
            "evidence_count": evidence_count,
            "in_sample_strength": round(
                sum(float(run.transaction_cost_adjusted_return_pct or 0.0) for run in test_runs) / evidence_count,
                4,
            ),
            "out_of_sample_strength": round(
                sum(float(run.baseline_edge_pct or 0.0) for run in test_runs) / evidence_count,
                4,
            ),
            "degradation_rate": degradation_rate,
            "recency_score": recency_score,
            "stability_score": avg_stability,
            "last_validated_at": latest.created_at if status == "VALIDATED" else None,
            "last_tested_at": latest.created_at,
            "status": status,
            "supporting_test_run_id": supporting.id if supporting is not None else None,
            "contradicting_test_run_id": contradicting.id if contradicting is not None else None,
            "notes": {
                "latest_adjusted_return_pct": latest_adjusted,
                "latest_median_return_pct": latest_median,
                "latest_win_rate_pct": latest_win_rate,
                "latest_baseline_edge_pct": latest_edge,
                "latest_matched_instruments": matched_instruments,
            },
        }

    @staticmethod
    def _degradation_rate(test_runs: list[BetaExecutionHypothesisTestRun]) -> float:
        recent = test_runs[: min(3, len(test_runs))]
        if not recent:
            return 0.0
        recent_avg = sum(float(run.transaction_cost_adjusted_return_pct or 0.0) for run in recent) / len(recent)
        long_avg = sum(float(run.transaction_cost_adjusted_return_pct or 0.0) for run in test_runs) / len(test_runs)
        return round(max(0.0, long_avg - recent_avg), 4)

    @staticmethod
    def _degradation_streak(test_runs: list[BetaExecutionHypothesisTestRun]) -> int:
        streak = 0
        for run in test_runs:
            adjusted = float(run.transaction_cost_adjusted_return_pct or 0.0)
            median_return = float(run.median_return_pct or 0.0)
            if adjusted < 0.0 or median_return <= 0.0:
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def _map_definition_status(belief_status: str) -> str:
        if belief_status in {"REJECTED", "RETIRED", "ARCHIVED"}:
            return "ARCHIVED"
        if belief_status == "DEGRADED":
            return "PAUSED"
        return "ACTIVE"
