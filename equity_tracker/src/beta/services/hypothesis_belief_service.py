"""Belief-state updates for hypothesis definitions based on accumulated test evidence."""

from __future__ import annotations

import json

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaHypothesisTestRun,
)
from .hypothesis_definition_service import BetaHypothesisDefinitionService


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
            for definition in definitions:
                test_runs = list(
                    sess.scalars(
                        select(BetaHypothesisTestRun)
                        .where(BetaHypothesisTestRun.hypothesis_definition_id == definition.id)
                        .order_by(desc(BetaHypothesisTestRun.created_at))
                    ).all()
                )
                assessment = BetaHypothesisBeliefService._assess_definition(test_runs)
                belief = sess.get(BetaHypothesisBeliefState, definition.id)
                if belief is None:
                    belief = BetaHypothesisBeliefState(hypothesis_definition_id=definition.id)
                    sess.add(belief)
                belief.confidence_score = assessment["confidence_score"]
                belief.evidence_count = assessment["evidence_count"]
                belief.in_sample_strength = assessment["in_sample_strength"]
                belief.out_of_sample_strength = assessment["out_of_sample_strength"]
                belief.degradation_rate = assessment["degradation_rate"]
                belief.last_validated_date = assessment["last_validated_date"]
                belief.status = assessment["status"]
                belief.supporting_test_run_id = assessment["supporting_test_run_id"]
                belief.contradicting_test_run_id = assessment["contradicting_test_run_id"]
                belief.notes_json = json.dumps(assessment["notes"], sort_keys=True)
                definition.status = assessment["status"]
                beliefs_written += 1

            BetaHypothesisDefinitionService.sync_legacy_family_registry(sess)
            return {
                "definitions_considered": len(definitions),
                "beliefs_written": beliefs_written,
                "validated_definitions": len([row for row in definitions if row.status == "VALIDATED"]),
                "promising_definitions": len([row for row in definitions if row.status == "PROMISING"]),
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
                "last_validated_date": None,
                "status": "CANDIDATE",
                "supporting_test_run_id": None,
                "contradicting_test_run_id": None,
                "notes": {"reason": "no_test_runs"},
            }

        latest = unique_runs[0]
        evidence_count = len(unique_runs)
        in_sample_strength = round(
            sum(float(row.average_return_pct or 0.0) for row in unique_runs) / evidence_count,
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
        sample_size = int(latest.sample_size or 0)
        sample_factor = min(1.0, sample_size / 150.0)
        evidence_factor = min(1.0, evidence_count / 4.0)
        positive_strength = max(0.0, latest_adjusted / 4.0) + max(0.0, latest_walk / 2.0)
        negative_strength = max(0.0, -latest_adjusted / 4.0) + max(0.0, -latest_walk / 2.0) + degradation_rate
        confidence_score = round(
            min(
                0.95,
                max(
                    0.05,
                    0.15
                    + (sample_factor * 0.2)
                    + (evidence_factor * 0.15)
                    + min(0.3, positive_strength * 0.2)
                    - min(0.25, negative_strength * 0.15),
                ),
            ),
            4,
        )

        status = "CANDIDATE"
        if latest_adjusted < 0.0 or latest_walk < 0.0:
            status = "DEGRADED"
        if evidence_count >= 2 and (latest_adjusted < -0.15 or latest_walk < -0.1) and confidence_score <= 0.25:
            status = "REJECTED"
        elif (
            evidence_count >= BetaHypothesisBeliefService._MIN_PROMISING_EVIDENCE_POINTS
            and sample_size >= 75
            and latest_adjusted > 0.0
            and latest_walk > 0.0
            and confidence_score >= 0.55
        ):
            status = "PROMISING"
        if (
            evidence_count >= BetaHypothesisBeliefService._MIN_VALIDATED_EVIDENCE_POINTS
            and sample_size >= 100
            and latest_adjusted > 0.15
            and latest_walk > 0.05
            and confidence_score >= 0.68
            and degradation_rate <= 0.15
        ):
            status = "VALIDATED"

        supporting = max(
            unique_runs,
            key=lambda row: float(row.transaction_cost_adjusted_return_pct or 0.0) + float(row.walk_forward_score or 0.0),
        )
        contradicting = min(
            unique_runs,
            key=lambda row: float(row.transaction_cost_adjusted_return_pct or 0.0) + float(row.walk_forward_score or 0.0),
        )
        return {
            "confidence_score": confidence_score,
            "evidence_count": evidence_count,
            "in_sample_strength": in_sample_strength,
            "out_of_sample_strength": out_of_sample_strength,
            "degradation_rate": degradation_rate,
            "last_validated_date": latest.test_end_date,
            "status": status,
            "supporting_test_run_id": supporting.id if supporting is not None else None,
            "contradicting_test_run_id": contradicting.id if contradicting is not None else None,
            "notes": {
                "latest_sample_size": sample_size,
                "latest_adjusted_return_pct": latest_adjusted,
                "latest_walk_forward_score": latest_walk,
                "recent_average_adjusted_return_pct": round(recent_avg, 4),
                "long_average_adjusted_return_pct": round(long_avg, 4),
                "distinct_evidence_points": evidence_count,
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
