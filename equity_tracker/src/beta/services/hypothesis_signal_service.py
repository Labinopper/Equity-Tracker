"""Live hypothesis matching, signal observations, and recommendation decisions."""

from __future__ import annotations

import json

from sqlalchemy import select

from ..db.models import (
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaHypothesis,
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaHypothesisFamily,
    BetaInstrument,
    BetaRecommendationDecision,
    BetaSignalObservation,
)
from .hypothesis_normalizer import BetaHypothesisNormalizer


class BetaHypothesisSignalService:
    """Match live market state against validated hypothesis definitions."""

    @staticmethod
    def load_runtime_context(sess) -> dict[str, object]:
        definitions = list(
            sess.scalars(
                select(BetaHypothesisDefinition).where(
                    BetaHypothesisDefinition.status.in_(("CANDIDATE", "PROMISING", "VALIDATED", "DEGRADED", "REJECTED"))
                )
            ).all()
        )
        beliefs = {
            row.hypothesis_definition_id: row
            for row in sess.scalars(select(BetaHypothesisBeliefState)).all()
        }
        families = {
            row.id: row
            for row in sess.scalars(select(BetaHypothesisFamily)).all()
        }
        legacy_hypotheses = {
            row.code: row
            for row in sess.scalars(select(BetaHypothesis)).all()
        }
        feature_names = sorted(
            {
                feature_name
                for definition in definitions
                for feature_name in BetaHypothesisNormalizer.extract_feature_names(
                    BetaHypothesisNormalizer.normalize_conditions(
                        json.loads(definition.entry_conditions_json or "{}")
                    )
                )
            }
        )
        feature_defs = {
            row.feature_name: row
            for row in sess.scalars(
                select(BetaFeatureDefinition).where(BetaFeatureDefinition.feature_name.in_(feature_names or [""]))
            ).all()
        }
        return {
            "definitions": definitions,
            "beliefs": beliefs,
            "families": families,
            "legacy_hypotheses": legacy_hypotheses,
            "feature_defs": feature_defs,
        }

    @staticmethod
    def evaluate_live_matches(
        sess,
        *,
        context: dict[str, object],
        instrument: BetaInstrument,
        decision_date,
        observation_time,
        evidence: dict[str, object],
        direction: str,
        confidence: float,
        edge: float,
        predicted_return_pct: float,
        prediction_source: str,
        signal_qualified: bool,
        candidate_promotion_allowed: bool,
    ) -> dict[str, object]:
        feature_snapshot = BetaHypothesisSignalService._feature_snapshot(
            sess,
            context=context,
            instrument_id=instrument.id,
            decision_date=decision_date,
        )
        matched_rows: list[dict[str, object]] = []
        for definition in context["definitions"]:
            belief = context["beliefs"].get(definition.id)
            family = context["families"].get(definition.family_id)
            if not BetaHypothesisSignalService._universe_match(definition, instrument):
                continue
            conditions = BetaHypothesisNormalizer.normalize_conditions(
                json.loads(definition.entry_conditions_json or "{}")
            )
            match_result = BetaHypothesisNormalizer.evaluate(conditions, feature_snapshot)
            if not match_result.matched:
                continue
            belief_status = str(belief.status if belief is not None else "CANDIDATE")
            belief_confidence = float(belief.confidence_score if belief is not None else 0.1)
            recommendation_score = BetaHypothesisSignalService._recommendation_score(
                belief_confidence=belief_confidence,
                score_confidence=confidence,
                edge=edge,
            )
            observation = BetaSignalObservation(
                hypothesis_definition_id=definition.id,
                hypothesis_test_run_id=belief.supporting_test_run_id if belief is not None else None,
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                observation_time=observation_time,
                decision_date=decision_date,
                matched_conditions_json=json.dumps(match_result.matched_terms, sort_keys=True),
                feature_snapshot_json=json.dumps(feature_snapshot, sort_keys=True),
                regime_context_json=json.dumps(
                    {
                        "market": instrument.market,
                        "sector_key": instrument.sector_key,
                        "prediction_source": prediction_source,
                        "score_direction": direction,
                        "belief_status": belief_status,
                        "family_code": family.family_code if family is not None else None,
                    },
                    sort_keys=True,
                ),
                prediction_source=prediction_source,
                expected_direction=definition.expected_direction,
                expected_return_pct=predicted_return_pct,
                baseline_name=(
                    str(((evidence.get("validated_baseline_policy") or {}) if isinstance(evidence.get("validated_baseline_policy"), dict) else {}).get("policy_name") or "").strip()
                    or None
                ),
                belief_confidence_score=belief_confidence,
                observation_status="MATCHED",
            )
            sess.add(observation)
            sess.flush()
            matched_rows.append(
                {
                    "definition": definition,
                    "belief": belief,
                    "family": family,
                    "observation": observation,
                    "belief_status": belief_status,
                    "belief_confidence": belief_confidence,
                    "recommendation_score": recommendation_score,
                }
            )

        if not matched_rows:
            return {"matched": False, "matches": []}

        matched_rows.sort(key=lambda row: row["recommendation_score"], reverse=True)
        best = matched_rows[0]
        decision_status, reason_code, reason_text, paper_trade_action = BetaHypothesisSignalService._decision_for_match(
            expected_direction=str(best["definition"].expected_direction),
            score_direction=direction,
            belief_status=str(best["belief_status"]),
            belief_confidence=float(best["belief_confidence"]),
            prediction_source=prediction_source,
            signal_qualified=signal_qualified,
            candidate_promotion_allowed=candidate_promotion_allowed,
        )
        portfolio_constraint_payload = {
            "candidate_promotion_allowed": candidate_promotion_allowed,
            "signal_qualified": signal_qualified,
            "prediction_source": prediction_source,
        }
        decision = BetaRecommendationDecision(
            signal_observation_id=best["observation"].id,
            instrument_id=instrument.id,
            symbol=instrument.symbol,
            decision_status=decision_status,
            decision_reason_code=reason_code,
            decision_reason_text=reason_text,
            belief_confidence_score=float(best["belief_confidence"]),
            portfolio_constraint_json=json.dumps(portfolio_constraint_payload, sort_keys=True),
            paper_trade_action=paper_trade_action,
            recommendation_score=float(best["recommendation_score"]),
        )
        sess.add(decision)
        sess.flush()
        best["observation"].observation_status = decision_status
        return {
            "matched": True,
            "matches": matched_rows,
            "best_match": best,
            "decision": decision,
            "legacy_hypothesis": context["legacy_hypotheses"].get(
                best["family"].family_code if best["family"] is not None else ""
            ),
        }

    @staticmethod
    def _feature_snapshot(sess, *, context: dict[str, object], instrument_id: str, decision_date) -> dict[str, float]:
        feature_defs = context["feature_defs"]
        feature_ids = [row.id for row in feature_defs.values()]
        rows = list(
            sess.scalars(
                select(BetaFeatureValue).where(
                    BetaFeatureValue.instrument_id == instrument_id,
                    BetaFeatureValue.feature_date == decision_date,
                    BetaFeatureValue.feature_definition_id.in_(feature_ids or [""]),
                )
            ).all()
        )
        by_id = {row.id: row for row in feature_defs.values()}
        snapshot: dict[str, float] = {}
        for row in rows:
            feature_def = by_id.get(row.feature_definition_id)
            if feature_def is None or row.value_numeric is None:
                continue
            snapshot[feature_def.feature_name] = float(row.value_numeric)
        return snapshot

    @staticmethod
    def _universe_match(definition: BetaHypothesisDefinition, instrument: BetaInstrument) -> bool:
        try:
            universe = json.loads(definition.universe_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            universe = {}
        if not isinstance(universe, dict):
            universe = {}
        markets = universe.get("markets")
        if isinstance(markets, list) and markets:
            if str(instrument.market or "OTHER") not in {str(item) for item in markets}:
                return False
        if bool(universe.get("core_only")) and instrument.core_security_id is None:
            return False
        return True

    @staticmethod
    def _recommendation_score(*, belief_confidence: float, score_confidence: float, edge: float) -> float:
        normalized_edge = max(-1.0, min(1.0, edge / 3.0))
        return round(
            (belief_confidence * 0.65) + (score_confidence * 0.25) + ((normalized_edge + 1.0) * 0.05),
            4,
        )

    @staticmethod
    def _direction_compatible(expected_direction: str, score_direction: str) -> bool:
        if expected_direction == score_direction:
            return True
        if expected_direction == "RISK_OFF" and score_direction in {"RISK_OFF", "BEARISH"}:
            return True
        if expected_direction == "BEARISH" and score_direction in {"BEARISH", "RISK_OFF"}:
            return True
        return False

    @staticmethod
    def _decision_for_match(
        *,
        expected_direction: str,
        score_direction: str,
        belief_status: str,
        belief_confidence: float,
        prediction_source: str,
        signal_qualified: bool,
        candidate_promotion_allowed: bool,
    ) -> tuple[str, str, str, str | None]:
        if belief_status == "DEGRADED":
            return "BLOCKED", "hypothesis_degraded", "Matched hypothesis is degraded and cannot drive a recommendation.", None
        if belief_status == "REJECTED":
            return "REJECTED", "hypothesis_rejected", "Matched hypothesis has been rejected by accumulated evidence.", None
        if belief_status == "PROMISING":
            return "BLOCKED", "hypothesis_not_validated", "Matched hypothesis is promising but not yet validated.", None
        if belief_status != "VALIDATED":
            return "DISMISSED", "belief_insufficient", "Matched setup does not yet have enough belief strength to generate a signal.", None
        if not BetaHypothesisSignalService._direction_compatible(expected_direction, score_direction):
            return "DISMISSED", "direction_mismatch", "Matched setup direction does not align with current score direction.", None
        if prediction_source == "HEURISTIC":
            return "BLOCKED", "heuristic_support_only", "Validated setup matched but live support is only heuristic.", None
        if belief_confidence >= 0.68 and signal_qualified and candidate_promotion_allowed:
            action = "OPEN_IF_ALLOWED" if score_direction == "BULLISH" else "WATCH_ONLY"
            return "RECOMMENDED", "validated_hypothesis_match", "Validated hypothesis matched and current score is actionable.", action
        if not signal_qualified:
            return "WATCHING", "score_not_actionable", "Validated setup matched but live score does not clear action thresholds.", None
        if not candidate_promotion_allowed:
            return "BLOCKED", "governance_not_actionable", "Validated setup matched but governance does not allow recommendation.", None
        return "WATCHING", "validated_hypothesis_watch", "Validated setup matched; continue watching for stronger conviction.", None
