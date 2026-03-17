"""Belief-state updates for hypothesis definitions based on accumulated test evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaHypothesisTestRun,
)
from .hypothesis_definition_service import BetaHypothesisDefinitionService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaHypothesisBeliefService:
    """Compute persistent belief states from historical hypothesis test runs."""

    _MIN_VALIDATED_EVIDENCE_POINTS = 3
    _MIN_PROMISING_EVIDENCE_POINTS = 2

    @staticmethod
    def refresh_belief_states() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"definitions_considered": 0, "beliefs_written": 0}

        with BetaContext.write_session() as sess:
            definitions = list(sess.scalars(select(BetaHypothesisDefinition)).all())
            beliefs_written = 0
            validated_count = 0
            promising_count = 0
            screened_count = 0
            for definition in definitions:
                test_runs = list(
                    sess.scalars(
                        select(BetaHypothesisTestRun)
                        .where(BetaHypothesisTestRun.hypothesis_definition_id == definition.id)
                        .order_by(desc(BetaHypothesisTestRun.created_at))
                    ).all()
                )
                assessment = BetaHypothesisBeliefService._assess_definition(test_runs)
                stored_status = BetaHypothesisDefinitionService.map_belief_status(sess, assessment["status"])
                belief = sess.get(BetaHypothesisBeliefState, definition.id)
                if belief is None:
                    belief = BetaHypothesisBeliefState(hypothesis_definition_id=definition.id)
                    sess.add(belief)
                belief.confidence_score = assessment["confidence_score"]
                belief.evidence_count = assessment["evidence_count"]
                belief.in_sample_strength = assessment["in_sample_strength"]
                belief.out_of_sample_strength = assessment["out_of_sample_strength"]
                belief.degradation_rate = assessment["degradation_rate"]
                belief.recency_score = assessment["recency_score"]
                belief.stability_score = assessment["stability_score"]
                belief.last_validated_date = assessment["last_validated_date"]
                belief.last_tested_at = assessment["last_tested_at"]
                belief.status = stored_status
                belief.supporting_test_run_id = assessment["supporting_test_run_id"]
                belief.contradicting_test_run_id = assessment["contradicting_test_run_id"]
                notes = dict(assessment["notes"])
                if stored_status != assessment["status"]:
                    notes["logical_status"] = assessment["status"]
                    notes["stored_status"] = stored_status
                belief.notes_json = json.dumps(notes, sort_keys=True)

                definition.status = BetaHypothesisDefinitionService.map_definition_status(
                    sess,
                    assessment["status"],
                )
                beliefs_written += 1
                if definition.status == "VALIDATED":
                    validated_count += 1
                if definition.status == "PROMISING":
                    promising_count += 1
                if definition.status in {"DISCOVERED", "SCREENED_IN", "CANDIDATE"}:
                    screened_count += 1

            BetaHypothesisDefinitionService.sync_legacy_family_registry(sess)
            return {
                "definitions_considered": len(definitions),
                "beliefs_written": beliefs_written,
                "validated_definitions": validated_count,
                "promising_definitions": promising_count,
                "screened_definitions": screened_count,
            }

    @staticmethod
    def _assess_definition(test_runs: list[BetaHypothesisTestRun]) -> dict[str, object]:
        unique_runs = BetaHypothesisBeliefService._unique_evidence_runs(test_runs)
        if not unique_runs:
            return {
                "confidence_score": 0.05,
                "evidence_count": 0,
                "in_sample_strength": 0.0,
                "out_of_sample_strength": 0.0,
                "degradation_rate": 0.0,
                "recency_score": 0.0,
                "stability_score": 0.0,
                "last_validated_date": None,
                "last_tested_at": None,
                "status": "DISCOVERED",
                "supporting_test_run_id": None,
                "contradicting_test_run_id": None,
                "notes": {"reason": "no_test_runs"},
            }

        now = _utcnow()
        latest = unique_runs[0]
        evidence_count = len(unique_runs)
        in_sample_strength = round(
            sum(float(row.transaction_cost_adjusted_return_pct or 0.0) for row in unique_runs) / evidence_count,
            4,
        )
        out_of_sample_strength = round(
            sum(float(row.out_of_sample_score or 0.0) for row in unique_runs) / evidence_count,
            4,
        )
        recent_slice = unique_runs[: min(3, evidence_count)]
        recent_avg = sum(float(row.transaction_cost_adjusted_return_pct or 0.0) for row in recent_slice) / len(recent_slice)
        long_avg = sum(float(row.transaction_cost_adjusted_return_pct or 0.0) for row in unique_runs) / evidence_count
        degradation_rate = round(max(0.0, long_avg - recent_avg), 4)
        latest_adjusted = float(latest.transaction_cost_adjusted_return_pct or 0.0)
        latest_walk = float(latest.walk_forward_score or 0.0)
        latest_edge = float(latest.baseline_edge_pct or 0.0)
        sample_size = int(latest.support_count or latest.sample_size or 0)
        latest_stability = float(latest.stability_score or 0.0)
        avg_stability = round(
            sum(float(row.stability_score or 0.0) for row in unique_runs) / evidence_count,
            4,
        )
        days_since_latest = max(0.0, (now - latest.created_at).total_seconds() / 86400.0)
        recency_score = round(max(0.0, min(1.0, 1.0 - (days_since_latest / 45.0))), 4)

        sample_factor = min(1.0, sample_size / 150.0)
        evidence_factor = min(1.0, evidence_count / 4.0)
        positive_strength = (
            max(0.0, latest_adjusted / 3.0)
            + max(0.0, latest_walk / 2.0)
            + max(0.0, latest_edge / 2.5)
            + (avg_stability * 0.7)
            + (recency_score * 0.3)
        )
        negative_strength = (
            max(0.0, -latest_adjusted / 3.0)
            + max(0.0, -latest_walk / 2.0)
            + max(0.0, -latest_edge / 2.5)
            + degradation_rate
            + max(0.0, 0.15 - avg_stability)
        )
        confidence_score = round(
            min(
                0.95,
                max(
                    0.05,
                    0.1
                    + (sample_factor * 0.18)
                    + (evidence_factor * 0.18)
                    + min(0.3, positive_strength * 0.12)
                    - min(0.28, negative_strength * 0.12),
                ),
            ),
            4,
        )

        status = "SCREENED_IN"
        if sample_size < BetaHypothesisBeliefService._MIN_VALIDATED_EVIDENCE_POINTS * 10:
            status = "SCREENED_IN"
        if sample_size >= BetaHypothesisBeliefService._MIN_VALIDATED_EVIDENCE_POINTS * 10 and latest_adjusted > -0.05:
            status = "CANDIDATE"
        if latest_adjusted < 0.0 or latest_walk < 0.0 or latest_edge < 0.0:
            status = "DEGRADED"
        if (
            evidence_count >= BetaHypothesisBeliefService._MIN_PROMISING_EVIDENCE_POINTS
            and sample_size >= 80
            and latest_adjusted > 0.0
            and latest_walk > 0.0
            and latest_edge > 0.0
            and avg_stability >= 0.25
            and confidence_score >= 0.56
        ):
            status = "PROMISING"
        if (
            evidence_count >= BetaHypothesisBeliefService._MIN_VALIDATED_EVIDENCE_POINTS
            and sample_size >= 110
            and latest_adjusted > 0.12
            and latest_walk > 0.05
            and latest_edge > 0.02
            and avg_stability >= 0.4
            and confidence_score >= 0.7
            and degradation_rate <= 0.1
        ):
            status = "VALIDATED"
        if (
            evidence_count >= 3
            and (latest_adjusted < -0.15 or latest_walk < -0.08 or latest_edge < -0.05)
            and confidence_score <= 0.25
        ):
            status = "RETIRED"

        supporting = max(
            unique_runs,
            key=lambda row: (
                float(row.transaction_cost_adjusted_return_pct or 0.0)
                + float(row.walk_forward_score or 0.0)
                + float(row.baseline_edge_pct or 0.0)
            ),
        )
        contradicting = min(
            unique_runs,
            key=lambda row: (
                float(row.transaction_cost_adjusted_return_pct or 0.0)
                + float(row.walk_forward_score or 0.0)
                + float(row.baseline_edge_pct or 0.0)
            ),
        )
        last_validated_date = latest.test_end_date if status == "VALIDATED" else None
        return {
            "confidence_score": confidence_score,
            "evidence_count": evidence_count,
            "in_sample_strength": in_sample_strength,
            "out_of_sample_strength": out_of_sample_strength,
            "degradation_rate": degradation_rate,
            "recency_score": recency_score,
            "stability_score": avg_stability,
            "last_validated_date": last_validated_date,
            "last_tested_at": latest.created_at,
            "status": status,
            "supporting_test_run_id": supporting.id if supporting is not None else None,
            "contradicting_test_run_id": contradicting.id if contradicting is not None else None,
            "notes": {
                "latest_sample_size": sample_size,
                "latest_adjusted_return_pct": latest_adjusted,
                "latest_walk_forward_score": latest_walk,
                "latest_baseline_edge_pct": latest_edge,
                "recent_average_adjusted_return_pct": round(recent_avg, 4),
                "long_average_adjusted_return_pct": round(long_avg, 4),
                "distinct_evidence_points": evidence_count,
                "latest_stability_score": latest_stability,
                "average_stability_score": avg_stability,
            },
        }

    @staticmethod
    def _unique_evidence_runs(test_runs: list[BetaHypothesisTestRun]) -> list[BetaHypothesisTestRun]:
        deduped: dict[object, BetaHypothesisTestRun] = {}
        for row in sorted(test_runs, key=lambda item: item.created_at, reverse=True):
            key = row.test_end_date or row.created_at.date()
            if key in deduped:
                continue
            deduped[key] = row
        return sorted(deduped.values(), key=lambda item: item.created_at, reverse=True)
