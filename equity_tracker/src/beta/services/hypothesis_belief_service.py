"""Belief-state updates for hypothesis definitions based on accumulated test evidence."""

from __future__ import annotations

import json
from statistics import median
from datetime import datetime, timezone

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaSignalObservation,
    BetaHypothesisTestRun,
)
from .hypothesis_definition_service import BetaHypothesisDefinitionService
from .hypothesis_governance_service import BetaHypothesisGovernanceService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaHypothesisBeliefService:
    """Compute persistent belief states from historical hypothesis test runs."""

    _MIN_VALIDATED_EVIDENCE_POINTS = 3
    _MIN_PROMISING_EVIDENCE_POINTS = 2
    _MIN_PROMISING_CONFIDENCE_SCORE = 0.48
    _MIN_VALIDATED_CONFIDENCE_SCORE = 0.50

    @staticmethod
    def refresh_belief_states() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"definitions_considered": 0, "beliefs_written": 0}

        with BetaContext.write_session() as sess:
            definitions = list(sess.scalars(select(BetaHypothesisDefinition)).all())
            realized_feedback_by_definition = BetaHypothesisBeliefService._realized_observation_feedback(sess)
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
                assessment = BetaHypothesisBeliefService._assess_definition(
                    test_runs,
                    realized_feedback=realized_feedback_by_definition.get(definition.id),
                )
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
    def _assess_definition(
        test_runs: list[BetaHypothesisTestRun],
        *,
        realized_feedback: dict[str, object] | None = None,
    ) -> dict[str, object]:
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

        # Multi-factor robustness evaluation from latest test run metrics
        latest_notes: dict[str, object] = {}
        try:
            latest_notes = json.loads(latest.notes_json or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        robustness_collapse = float(latest_notes.get("robustness_collapse_pct", 0.0))
        max_window_edge_share = float(latest_notes.get("max_window_edge_share", 0.0))
        mean_median_ratio = float(latest_notes.get("mean_median_ratio", 1.0))
        latest_win_rate = float(latest.win_rate_pct or 50.0)
        latest_volatility = float(latest.outcome_volatility_pct or 0.0)

        robustness_penalty = 0.0
        # Window concentration: penalize when one window dominates the edge
        if max_window_edge_share > 0.6:
            robustness_penalty += min(0.15, (max_window_edge_share - 0.6) * 0.375)
        # Robustness collapse: penalize when edge collapses under winsorization
        if robustness_collapse > 30.0:
            robustness_penalty += min(0.12, (robustness_collapse - 30.0) / 100.0 * 0.17)
        # Distribution shape: penalize extreme mean/median divergence
        if mean_median_ratio > 10.0:
            robustness_penalty += min(0.08, (mean_median_ratio - 10.0) / 100.0 * 0.1)
        # Combined fragility: near-coinflip win rate with extreme volatility
        if latest_win_rate < 52.0 and latest_volatility > 200.0:
            vol_excess = min(1.0, (latest_volatility - 200.0) / 300.0)
            rate_weakness = min(1.0, (52.0 - latest_win_rate) / 4.0)
            robustness_penalty += min(0.06, vol_excess * rate_weakness * 0.06)

        governance = BetaHypothesisBeliefService._governance_for_test_run(latest, latest_notes)
        hard_fail_reasons = (
            governance.get("hard_fail_reasons") if isinstance(governance.get("hard_fail_reasons"), list) else []
        )
        governance_severity = str(governance.get("severity") or "PASS")
        promotion_eligible = bool(governance.get("promotion_eligible"))
        watch_eligible = bool(governance.get("watch_eligible", not hard_fail_reasons))
        failure_modes = (
            governance.get("failure_modes") if isinstance(governance.get("failure_modes"), list) else []
        )

        observation_feedback = realized_feedback or {}
        observation_count = int(observation_feedback.get("observation_count") or 0)
        observation_avg = float(observation_feedback.get("average_realized_return_pct") or 0.0)
        observation_recent_avg = float(observation_feedback.get("recent_average_realized_return_pct") or 0.0)
        observation_win_rate = float(observation_feedback.get("win_rate_pct") or 0.0)
        observation_prediction_error = float(observation_feedback.get("average_prediction_error_pct") or 0.0)
        observation_degradation = float(observation_feedback.get("degradation_rate") or 0.0)
        observation_factor = min(1.0, observation_count / 6.0)
        positive_strength += observation_factor * (
            max(0.0, observation_avg / 2.5) + max(0.0, (observation_win_rate - 50.0) / 50.0)
        )
        negative_strength += observation_factor * (
            max(0.0, -observation_avg / 2.0)
            + max(0.0, -observation_prediction_error / 2.5)
            + max(0.0, observation_degradation)
        )
        degradation_rate = round(max(degradation_rate, observation_degradation), 4)

        latest_observation_at = observation_feedback.get("latest_realized_at")
        if isinstance(latest_observation_at, datetime):
            days_since_observation = max(0.0, (now - latest_observation_at).total_seconds() / 86400.0)
            observation_recency = max(0.0, min(1.0, 1.0 - (days_since_observation / 30.0)))
            recency_score = round(max(recency_score, observation_recency), 4)
        observation_feedback_notes = dict(observation_feedback)
        if isinstance(observation_feedback_notes.get("latest_realized_at"), datetime):
            observation_feedback_notes["latest_realized_at"] = observation_feedback_notes["latest_realized_at"].isoformat()

        confidence_score = round(
            min(
                0.95,
                max(
                    0.05,
                    0.1
                    + (sample_factor * 0.18)
                    + (evidence_factor * 0.18)
                    + min(0.3, positive_strength * 0.12)
                    - min(0.28, negative_strength * 0.12)
                    - robustness_penalty,
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
        if hard_fail_reasons:
            status = "REJECTED" if governance_severity == "REJECTED" else "DEGRADED"
        if observation_count >= 3 and (
            observation_avg < 0.0 or observation_win_rate < 50.0 or observation_prediction_error < -1.0
        ):
            status = "DEGRADED"
        if (
            evidence_count >= BetaHypothesisBeliefService._MIN_PROMISING_EVIDENCE_POINTS
            and sample_size >= 80
            and latest_adjusted > 0.0
            and latest_walk > 0.0
            and latest_edge > 0.0
            and avg_stability >= 0.25
            and confidence_score >= BetaHypothesisBeliefService._MIN_PROMISING_CONFIDENCE_SCORE
            and watch_eligible
        ):
            status = "PROMISING"
        if (
            evidence_count >= BetaHypothesisBeliefService._MIN_VALIDATED_EVIDENCE_POINTS
            and sample_size >= 110
            and latest_adjusted > 0.12
            and latest_walk > 0.05
            and latest_edge > 0.02
            and avg_stability >= 0.4
            and confidence_score >= BetaHypothesisBeliefService._MIN_VALIDATED_CONFIDENCE_SCORE
            and degradation_rate <= 0.1
            and promotion_eligible
            and not (
                observation_count >= 3
                and (observation_avg < 0.0 or observation_prediction_error < -1.0)
            )
        ):
            status = "VALIDATED"
        if (
            BetaHypothesisBeliefService._degradation_streak(unique_runs) >= 3
            and latest_adjusted <= 0.05
            and status not in {"REJECTED", "RETIRED"}
        ):
            status = "DEGRADED"
        if (
            evidence_count >= 3
            and (latest_adjusted < -0.15 or latest_walk < -0.08 or latest_edge < -0.05)
        ):
            status = "RETIRED"
        if (
            observation_count >= 5
            and observation_recent_avg < -0.5
            and observation_prediction_error < -1.5
            and confidence_score <= 0.3
        ):
            status = "REJECTED"

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
                "robustness_penalty": round(robustness_penalty, 4),
                "robustness_collapse_pct": robustness_collapse,
                "max_window_edge_share": max_window_edge_share,
                "mean_median_ratio": mean_median_ratio,
                "governance": governance,
                "failure_modes": failure_modes,
                "degradation_streak": BetaHypothesisBeliefService._degradation_streak(unique_runs),
                "observation_feedback": observation_feedback_notes,
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

    @staticmethod
    def _governance_for_test_run(
        test_run: BetaHypothesisTestRun,
        notes: dict[str, object],
    ) -> dict[str, object]:
        governance = notes.get("governance")
        if isinstance(governance, dict):
            return governance
        regime_slice: dict[str, object] = {}
        try:
            payload = json.loads(test_run.regime_slice_json or "{}")
            if isinstance(payload, dict):
                regime_slice = payload
        except (json.JSONDecodeError, TypeError):
            regime_slice = {}
        summary = {
            "sample_size": int(test_run.sample_size or test_run.support_count or 0),
            "support_count": int(test_run.support_count or test_run.sample_size or 0),
            "median_excess_return_pct": test_run.median_excess_return_pct,
            "transaction_cost_adjusted_return_pct": test_run.transaction_cost_adjusted_return_pct,
            "baseline_edge_pct": test_run.baseline_edge_pct,
            "win_rate_pct": test_run.win_rate_pct,
            "walk_forward_score": test_run.walk_forward_score,
            "out_of_sample_score": test_run.out_of_sample_score,
            "stability_score": test_run.stability_score,
            "regime_slice": regime_slice,
            "notes": notes,
        }
        return BetaHypothesisGovernanceService.evaluate_summary(summary)

    @staticmethod
    def _degradation_streak(test_runs: list[BetaHypothesisTestRun]) -> int:
        if len(test_runs) < 3:
            return 0
        streak = 1
        prior_value = float(test_runs[0].transaction_cost_adjusted_return_pct or 0.0)
        for row in test_runs[1:]:
            current_value = float(row.transaction_cost_adjusted_return_pct or 0.0)
            if prior_value < current_value:
                streak += 1
                prior_value = current_value
                continue
            break
        return streak

    @staticmethod
    def _realized_observation_feedback(sess) -> dict[str, dict[str, object]]:
        rows = list(
            sess.scalars(
                select(BetaSignalObservation)
                .where(BetaSignalObservation.realized_return_pct.is_not(None))
                .order_by(desc(BetaSignalObservation.realized_at), desc(BetaSignalObservation.observation_time))
            ).all()
        )
        grouped: dict[str, list[BetaSignalObservation]] = {}
        for row in rows:
            grouped.setdefault(row.hypothesis_definition_id, []).append(row)

        feedback: dict[str, dict[str, object]] = {}
        for definition_id, observations in grouped.items():
            realized_values = [
                float(row.realized_return_pct)
                for row in observations
                if row.realized_return_pct is not None
            ]
            if not realized_values:
                continue
            prediction_errors = [
                float(row.realized_return_pct) - float(row.expected_return_pct)
                for row in observations
                if row.realized_return_pct is not None and row.expected_return_pct is not None
            ]
            recent_values = realized_values[: min(3, len(realized_values))]
            long_average = sum(realized_values) / len(realized_values)
            recent_average = sum(recent_values) / len(recent_values)
            feedback[definition_id] = {
                "observation_count": len(realized_values),
                "average_realized_return_pct": round(long_average, 4),
                "median_realized_return_pct": round(float(median(realized_values)), 4),
                "win_rate_pct": round(
                    (len([value for value in realized_values if value > 0]) / len(realized_values)) * 100.0,
                    2,
                ),
                "average_prediction_error_pct": round(
                    sum(prediction_errors) / len(prediction_errors),
                    4,
                ) if prediction_errors else 0.0,
                "recent_average_realized_return_pct": round(recent_average, 4),
                "degradation_rate": round(max(0.0, long_average - recent_average), 4),
                "latest_realized_at": observations[0].realized_at or observations[0].observation_time,
            }
        return feedback
