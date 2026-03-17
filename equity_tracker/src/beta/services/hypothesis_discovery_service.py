"""Bounded template-based hypothesis discovery for the beta research engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import product

from sqlalchemy import case, select

from ..context import BetaContext
from ..db.models import (
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaHypothesisDefinition,
    BetaHypothesisDiscoveryCandidate,
    BetaHypothesisDiscoveryRun,
    BetaHypothesisFamily,
    BetaHypothesisTemplate,
    BetaInstrument,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaUniverseMembership,
)
from ..settings import BetaSettings
from .hypothesis_backtest_service import BetaHypothesisBacktestService
from .hypothesis_definition_service import BetaHypothesisDefinitionService
from .hypothesis_normalizer import BetaHypothesisNormalizer


@dataclass(frozen=True)
class _DiscoveryRow:
    instrument_id: str
    symbol: str
    market: str
    sector_key: str
    decision_date: date
    label_value: float
    features: dict[str, float]


class BetaHypothesisDiscoveryService:
    """Generate, prune, and promote bounded template-based daily hypotheses."""

    _MIN_MATCHED_INSTRUMENTS = 5
    _MIN_WALK_WINDOWS = 3

    @staticmethod
    def run_discovery(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.hypothesis_discovery_enabled:
            return {
                "templates_considered": 0,
                "candidates_generated": 0,
                "candidates_screened_in": 0,
                "candidates_promoted": 0,
            }

        with BetaContext.write_session() as sess:
            templates = list(
                sess.scalars(
                    select(BetaHypothesisTemplate)
                    .where(BetaHypothesisTemplate.status == "ACTIVE")
                    .order_by(BetaHypothesisTemplate.created_at.asc())
                    .limit(settings.hypothesis_discovery_template_limit)
                ).all()
            )
            if not templates:
                return {
                    "templates_considered": 0,
                    "candidates_generated": 0,
                    "candidates_screened_in": 0,
                    "candidates_promoted": 0,
                }

            discovery_run = BetaHypothesisDiscoveryRun(
                run_code=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
                status="SUCCESS",
            )
            sess.add(discovery_run)
            sess.flush()

            dataset_rows, dataset_notes = BetaHypothesisDiscoveryService._load_dataset(
                sess,
                settings=settings,
                templates=templates,
            )
            discovery_run.discovery_window_start = dataset_notes.get("window_start")
            discovery_run.discovery_window_end = dataset_notes.get("window_end")

            existing_definition_hashes = BetaHypothesisDiscoveryService._existing_definition_hashes(sess)
            family_by_code = {
                row.family_code: row
                for row in sess.scalars(select(BetaHypothesisFamily)).all()
            }
            existing_definitions = {
                row.hypothesis_code: row
                for row in sess.scalars(select(BetaHypothesisDefinition)).all()
            }

            generated_rows: list[dict[str, object]] = []
            promoted_rows: list[dict[str, object]] = []
            for template in templates:
                template_spec = BetaHypothesisDiscoveryService._json_object(template.template_spec_json)
                candidate_specs = BetaHypothesisDiscoveryService._generate_candidate_specs(
                    template=template,
                    template_spec=template_spec,
                    settings=settings,
                )
                discovery_run.templates_considered += 1
                discovery_run.candidates_generated += len(candidate_specs)
                evaluated_rows = [
                    BetaHypothesisDiscoveryService._evaluate_candidate(
                        candidate_spec=spec,
                        dataset_rows=dataset_rows,
                        settings=settings,
                    )
                    for spec in candidate_specs
                ]
                pruned_rows = BetaHypothesisDiscoveryService._prune_redundant_candidates(evaluated_rows)
                for row in pruned_rows:
                    if row["candidate_hash"] in existing_definition_hashes:
                        row["status"] = "PRUNED"
                        row["stage_reached"] = min(int(row["stage_reached"]), 4)
                        row.setdefault("notes", {})
                        row["notes"]["pruned_reason"] = "already_promoted_or_seeded"
                    persisted = BetaHypothesisDiscoveryService._persist_candidate(
                        sess,
                        discovery_run_id=discovery_run.id,
                        template=template,
                        candidate=row,
                    )
                    generated_rows.append({"candidate": row, "row": persisted, "template": template})

            survivors = [
                row
                for row in generated_rows
                if row["candidate"]["status"] in {"SCREENED_IN", "PROMOTED"}
                and int(row["candidate"]["stage_reached"]) >= 5
            ]
            survivors.sort(
                key=lambda item: (
                    float(item["candidate"].get("stability_score") or 0.0),
                    float(item["candidate"].get("baseline_edge_pct") or 0.0),
                    float(item["candidate"].get("friction_adjusted_return_pct") or 0.0),
                    int(item["candidate"].get("support_count") or 0),
                    -int(item["candidate"].get("condition_count") or 0),
                ),
                reverse=True,
            )
            for row in survivors[: settings.hypothesis_discovery_max_promotions_per_run]:
                template = row["template"]
                candidate = row["candidate"]
                candidate_row = row["row"]
                template_spec = BetaHypothesisDiscoveryService._json_object(template.template_spec_json)
                family_code = str(template_spec.get("family_code") or "")
                family = family_by_code.get(family_code)
                if family is None:
                    continue
                spec = {
                    "family_code": family.family_code,
                    "hypothesis_code": candidate["hypothesis_code"],
                    "name": candidate["hypothesis_name"],
                    "expected_direction": candidate["expected_direction"],
                    "target_metric": candidate["target_metric"],
                    "holding_period_days": candidate["holding_period_days"],
                    "generation_source": "TEMPLATE_MUTATION",
                    "source_type": "GENERATED",
                    "template_code": template.template_code,
                    "entry_conditions": candidate["entry_conditions"],
                    "regime_filters": candidate["regime_filters"],
                    "metadata": {
                        "discovery_run_id": discovery_run.id,
                        "candidate_hash": candidate["candidate_hash"],
                        "redundancy_group": candidate["redundancy_group"],
                        "stage_reached": candidate["stage_reached"],
                        "support_count": candidate["support_count"],
                        "baseline_edge_pct": candidate["baseline_edge_pct"],
                        "stability_score": candidate["stability_score"],
                    },
                    "status": "SCREENED_IN",
                }
                result = BetaHypothesisDefinitionService.upsert_definition(
                    sess,
                    family_by_code=family_by_code,
                    existing_definitions=existing_definitions,
                    spec=spec,
                    default_status="SCREENED_IN",
                    discovery_run_id=discovery_run.id,
                )
                promoted_definition = result["definition"]
                candidate_row.promoted_hypothesis_definition_id = promoted_definition.id
                candidate_row.status = "PROMOTED"
                candidate_row.stage_reached = max(int(candidate_row.stage_reached or 0), 6)
                promoted_rows.append(row)

            discovery_run.candidates_screened_in = len(
                [row for row in generated_rows if row["candidate"]["status"] in {"SCREENED_IN", "PROMOTED"}]
            )
            discovery_run.candidates_promoted = len(promoted_rows)
            discovery_run.notes_json = json.dumps(
                {
                    "dataset_rows": len(dataset_rows),
                    "window_start": str(dataset_notes.get("window_start")) if dataset_notes.get("window_start") else None,
                    "window_end": str(dataset_notes.get("window_end")) if dataset_notes.get("window_end") else None,
                    "scope_instruments": dataset_notes.get("scope_instruments", 0),
                    "history_years": settings.hypothesis_discovery_history_years,
                    "universe_cap": settings.hypothesis_discovery_universe_cap,
                },
                sort_keys=True,
            )
            return {
                "templates_considered": discovery_run.templates_considered,
                "candidates_generated": discovery_run.candidates_generated,
                "candidates_screened_in": discovery_run.candidates_screened_in,
                "candidates_promoted": discovery_run.candidates_promoted,
                "discovery_run_code": discovery_run.run_code,
                "dataset_rows": len(dataset_rows),
            }

    @staticmethod
    def _load_dataset(
        sess,
        *,
        settings: BetaSettings,
        templates: list[BetaHypothesisTemplate],
    ) -> tuple[list[_DiscoveryRow], dict[str, object]]:
        cutoff_date = date.today() - timedelta(days=max(365, settings.hypothesis_discovery_history_years * 365))
        membership_rows = list(
            sess.execute(
                select(BetaUniverseMembership.instrument_id)
                .where(BetaUniverseMembership.effective_to.is_(None))
                .order_by(
                    case((BetaUniverseMembership.status == "ACTIVE", 0), else_=1),
                    BetaUniverseMembership.priority_rank.asc(),
                    BetaUniverseMembership.created_at.asc(),
                )
                .limit(settings.hypothesis_discovery_universe_cap)
            )
        )
        instrument_ids = [row.instrument_id for row in membership_rows]
        if not instrument_ids:
            return [], {"window_start": None, "window_end": None, "scope_instruments": 0}

        instruments = {
            row.id: row
            for row in sess.scalars(select(BetaInstrument).where(BetaInstrument.id.in_(instrument_ids))).all()
        }
        feature_names = set(BetaHypothesisDiscoveryService._regime_feature_names())
        for template in templates:
            spec = BetaHypothesisDiscoveryService._json_object(template.template_spec_json)
            for name in spec.get("required_features", []):
                feature_names.add(str(name))
            for slot in spec.get("condition_slots", []):
                if isinstance(slot, dict) and slot.get("feature"):
                    feature_names.add(str(slot["feature"]))

        feature_defs = {
            row.id: row.feature_name
            for row in sess.scalars(
                select(BetaFeatureDefinition).where(BetaFeatureDefinition.feature_name.in_(sorted(feature_names)))
            ).all()
        }
        label_def = sess.scalar(
            select(BetaLabelDefinition).where(BetaLabelDefinition.label_name == "fwd_5d_excess_return_pct")
        )
        if label_def is None or not feature_defs:
            return [], {"window_start": None, "window_end": None, "scope_instruments": len(instrument_ids)}

        label_map: dict[tuple[str, date], float] = {}
        for row in sess.execute(
            select(BetaLabelValue.instrument_id, BetaLabelValue.decision_date, BetaLabelValue.value_numeric)
            .where(
                BetaLabelValue.label_definition_id == label_def.id,
                BetaLabelValue.instrument_id.in_(instrument_ids),
                BetaLabelValue.decision_date >= cutoff_date,
                BetaLabelValue.value_numeric.is_not(None),
            )
        ):
            label_map[(row.instrument_id, row.decision_date)] = float(row.value_numeric)

        feature_map: dict[tuple[str, date], dict[str, float]] = {}
        for row in sess.execute(
            select(
                BetaFeatureValue.instrument_id,
                BetaFeatureValue.feature_date,
                BetaFeatureValue.feature_definition_id,
                BetaFeatureValue.value_numeric,
            ).where(
                BetaFeatureValue.instrument_id.in_(instrument_ids),
                BetaFeatureValue.feature_date >= cutoff_date,
                BetaFeatureValue.feature_definition_id.in_(list(feature_defs.keys())),
                BetaFeatureValue.value_numeric.is_not(None),
            )
        ):
            key = (row.instrument_id, row.feature_date)
            if key not in label_map:
                continue
            feature_name = feature_defs.get(row.feature_definition_id)
            if feature_name is None:
                continue
            feature_map.setdefault(key, {})[feature_name] = float(row.value_numeric)

        rows: list[_DiscoveryRow] = []
        for (instrument_id, decision_date), label_value in label_map.items():
            instrument = instruments.get(instrument_id)
            if instrument is None:
                continue
            rows.append(
                _DiscoveryRow(
                    instrument_id=instrument_id,
                    symbol=str(instrument.symbol),
                    market=str(instrument.market or "OTHER"),
                    sector_key=str(instrument.sector_key or "GENERAL"),
                    decision_date=decision_date,
                    label_value=float(label_value),
                    features=feature_map.get((instrument_id, decision_date), {}),
                )
            )
        rows.sort(key=lambda item: (item.decision_date, item.instrument_id))
        return rows, {
            "window_start": cutoff_date if rows else None,
            "window_end": rows[-1].decision_date if rows else None,
            "scope_instruments": len(instrument_ids),
        }

    @staticmethod
    def _generate_candidate_specs(
        *,
        template: BetaHypothesisTemplate,
        template_spec: dict[str, object],
        settings: BetaSettings,
    ) -> list[dict[str, object]]:
        condition_slots = template_spec.get("condition_slots", [])
        if not isinstance(condition_slots, list) or not condition_slots:
            return []
        slot_variants: list[list[dict[str, object]]] = []
        for slot in condition_slots:
            if not isinstance(slot, dict):
                continue
            feature_name = str(slot.get("feature") or "").strip()
            op = str(slot.get("op") or "").strip()
            thresholds = slot.get("thresholds", [])
            if not feature_name or not op or not isinstance(thresholds, list) or not thresholds:
                continue
            variants: list[dict[str, object]] = []
            for threshold in thresholds:
                if op == "between" and isinstance(threshold, (list, tuple)) and len(threshold) == 2:
                    variants.append(
                        {
                            "feature": feature_name,
                            "op": op,
                            "min": float(threshold[0]),
                            "max": float(threshold[1]),
                        }
                    )
                else:
                    variants.append(
                        {
                            "feature": feature_name,
                            "op": op,
                            "value": float(threshold),
                        }
                    )
            if variants:
                slot_variants.append(variants)
        if not slot_variants:
            return []

        regime_codes = template_spec.get("regime_gate_codes", ["none"])
        if not isinstance(regime_codes, list) or not regime_codes:
            regime_codes = ["none"]
        max_variants = min(
            int(template_spec.get("max_variants") or settings.hypothesis_discovery_variant_cap),
            settings.hypothesis_discovery_variant_cap,
        )
        max_condition_count = min(
            int(template_spec.get("max_condition_count") or settings.hypothesis_discovery_max_condition_count),
            settings.hypothesis_discovery_max_condition_count,
        )
        min_support = max(
            int(template_spec.get("min_support") or 0),
            settings.hypothesis_discovery_min_support,
        )

        candidates: list[dict[str, object]] = []
        seen_hashes: set[str] = set()
        for threshold_combo in product(*slot_variants):
            conditions = {"all": list(threshold_combo)}
            for regime_code in regime_codes:
                regime_filters = BetaHypothesisDiscoveryService._regime_filters_for_code(str(regime_code))
                condition_count = (
                    BetaHypothesisNormalizer.leaf_count(conditions)
                    + BetaHypothesisNormalizer.leaf_count(regime_filters)
                )
                if condition_count > max_condition_count:
                    continue
                feature_subset = sorted(
                    BetaHypothesisNormalizer.extract_feature_names(conditions)
                    | BetaHypothesisNormalizer.extract_feature_names(regime_filters)
                )
                candidate_hash = BetaHypothesisDiscoveryService._candidate_hash(
                    expected_direction=str(template.expected_direction),
                    target_metric=str(template.target_metric),
                    holding_period_days=int(template.holding_period_days or 5),
                    entry_conditions=conditions,
                    regime_filters=regime_filters,
                )
                if candidate_hash in seen_hashes:
                    continue
                seen_hashes.add(candidate_hash)
                candidates.append(
                    {
                        "template_code": template.template_code,
                        "template_name": template.template_name,
                        "family_code": str(template_spec.get("family_code") or ""),
                        "hypothesis_family": str(template.hypothesis_family or ""),
                        "expected_direction": str(template.expected_direction),
                        "target_metric": str(template.target_metric or "fwd_5d_excess_return_pct"),
                        "holding_period_days": int(template.holding_period_days or 5),
                        "entry_conditions": conditions,
                        "regime_filters": regime_filters,
                        "regime_code": str(regime_code),
                        "feature_subset": feature_subset,
                        "condition_count": condition_count,
                        "candidate_hash": candidate_hash,
                        "hypothesis_code": BetaHypothesisDiscoveryService._generated_hypothesis_code(
                            template.template_code,
                            candidate_hash,
                        ),
                        "hypothesis_name": BetaHypothesisDiscoveryService._generated_hypothesis_name(
                            template.template_name,
                            candidate_hash,
                        ),
                        "min_support": min_support,
                    }
                )
                if len(candidates) >= max_variants:
                    return candidates
        return candidates

    @staticmethod
    def _evaluate_candidate(
        *,
        candidate_spec: dict[str, object],
        dataset_rows: list[_DiscoveryRow],
        settings: BetaSettings,
    ) -> dict[str, object]:
        entry_conditions = BetaHypothesisNormalizer.normalize_conditions(candidate_spec["entry_conditions"])
        regime_filters = BetaHypothesisNormalizer.normalize_regime_filters(candidate_spec.get("regime_filters") or {})
        required_features = set(candidate_spec.get("feature_subset") or [])

        eligible_rows: list[_DiscoveryRow] = []
        matched_rows: list[_DiscoveryRow] = []
        for row in dataset_rows:
            if not required_features.issubset(row.features.keys()):
                continue
            if regime_filters and not BetaHypothesisNormalizer.evaluate(regime_filters, row.features).matched:
                continue
            eligible_rows.append(row)
            if BetaHypothesisNormalizer.evaluate(entry_conditions, row.features).matched:
                matched_rows.append(row)

        support_count = len(matched_rows)
        matched_instruments = len({row.instrument_id for row in matched_rows})
        result = {
            **candidate_spec,
            "status": "DISCOVERED",
            "stage_reached": 1,
            "support_count": support_count,
            "matched_instruments": matched_instruments,
            "hit_rate_pct": None,
            "average_target_return_pct": None,
            "average_excess_return_pct": None,
            "median_excess_return_pct": None,
            "outcome_volatility_pct": None,
            "friction_adjusted_return_pct": None,
            "walk_forward_score": None,
            "baseline_edge_pct": None,
            "stability_score": None,
            "redundancy_group": BetaHypothesisDiscoveryService._redundancy_group(
                family_code=str(candidate_spec.get("family_code") or ""),
                expected_direction=str(candidate_spec.get("expected_direction") or ""),
                entry_conditions=entry_conditions,
                regime_filters=regime_filters,
            ),
            "notes": {},
        }
        min_support = max(
            int(candidate_spec.get("min_support") or 0),
            settings.hypothesis_discovery_min_support,
        )
        if support_count < min_support or matched_instruments < BetaHypothesisDiscoveryService._MIN_MATCHED_INSTRUMENTS:
            result["status"] = "PRUNED"
            result["notes"]["pruned_reason"] = "insufficient_support"
            return result

        matched_samples = [(row.instrument_id, row.decision_date, row.label_value) for row in matched_rows]
        eligible_samples = [(row.instrument_id, row.decision_date, row.label_value) for row in eligible_rows]
        summary = BetaHypothesisBacktestService.summarize_candidate_samples(
            matched_samples=matched_samples,
            eligible_samples=eligible_samples,
            expected_direction=str(candidate_spec.get("expected_direction") or "BULLISH"),
            settings=settings,
        )
        result.update(
            {
                "hit_rate_pct": summary["win_rate_pct"],
                "average_target_return_pct": summary["average_target_return_pct"],
                "average_excess_return_pct": summary["average_excess_return_pct"],
                "median_excess_return_pct": summary["median_excess_return_pct"],
                "outcome_volatility_pct": summary["outcome_volatility_pct"],
                "friction_adjusted_return_pct": summary["transaction_cost_adjusted_return_pct"],
                "walk_forward_score": summary["walk_forward_score"],
                "baseline_edge_pct": summary["baseline_edge_pct"],
                "stability_score": summary["stability_score"],
            }
        )

        hit_rate = float(summary["win_rate_pct"] or 0.0)
        friction_adjusted = float(summary["transaction_cost_adjusted_return_pct"] or 0.0)
        baseline_edge = float(summary["baseline_edge_pct"] or 0.0)
        walk_forward = float(summary["walk_forward_score"] or 0.0)
        out_of_sample = float(summary["out_of_sample_score"] or 0.0)
        stability_score = float(summary["stability_score"] or 0.0)
        walk_windows = ((summary.get("notes") or {}).get("walk_forward_windows") or [])
        recency_windows = walk_windows[-max(1, len(walk_windows) // 3) :] if walk_windows else []
        recency_edge = (
            sum(float(row.get("edge_pct") or 0.0) for row in recency_windows) / len(recency_windows)
            if recency_windows
            else 0.0
        )

        if friction_adjusted <= 0.0 or hit_rate < 48.0:
            result["status"] = "PRUNED"
            result["stage_reached"] = 2
            result["notes"]["pruned_reason"] = "weak_in_sample_edge"
            return result
        result["stage_reached"] = 2

        if len(walk_windows) < BetaHypothesisDiscoveryService._MIN_WALK_WINDOWS or walk_forward <= 0.0:
            result["status"] = "PRUNED"
            result["stage_reached"] = 3
            result["notes"]["pruned_reason"] = "walk_forward_weak"
            return result
        result["stage_reached"] = 3

        if baseline_edge <= 0.0 or out_of_sample <= 0.0:
            result["status"] = "PRUNED"
            result["stage_reached"] = 4
            result["notes"]["pruned_reason"] = "baseline_edge_missing"
            return result
        result["stage_reached"] = 4

        if stability_score < 0.18 or recency_edge <= -0.05:
            result["status"] = "PRUNED"
            result["stage_reached"] = 5
            result["notes"]["pruned_reason"] = "unstable_or_stale"
            return result

        result["status"] = "SCREENED_IN"
        result["stage_reached"] = 5
        result["notes"].update(
            {
                "walk_window_count": len(walk_windows),
                "recency_edge_pct": round(recency_edge, 4),
            }
        )
        return result

    @staticmethod
    def _prune_redundant_candidates(evaluated_rows: list[dict[str, object]]) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in evaluated_rows:
            grouped.setdefault(str(row.get("redundancy_group") or row["candidate_hash"]), []).append(row)

        pruned_rows: list[dict[str, object]] = []
        for group_rows in grouped.values():
            ordered = sorted(
                group_rows,
                key=lambda item: (
                    int(item.get("stage_reached") or 0),
                    float(item.get("stability_score") or 0.0),
                    float(item.get("baseline_edge_pct") or 0.0),
                    float(item.get("friction_adjusted_return_pct") or 0.0),
                    int(item.get("support_count") or 0),
                    -int(item.get("condition_count") or 0),
                ),
                reverse=True,
            )
            winner = ordered[0]
            pruned_rows.append(winner)
            for row in ordered[1:]:
                if row["status"] == "PROMOTED":
                    pruned_rows.append(row)
                    continue
                row["status"] = "PRUNED"
                row["stage_reached"] = min(int(row.get("stage_reached") or 0), 5)
                row.setdefault("notes", {})
                row["notes"]["pruned_reason"] = "redundant_variant"
                row["notes"]["redundancy_winner"] = winner["candidate_hash"]
                pruned_rows.append(row)
        return pruned_rows

    @staticmethod
    def _persist_candidate(
        sess,
        *,
        discovery_run_id: str,
        template: BetaHypothesisTemplate,
        candidate: dict[str, object],
    ) -> BetaHypothesisDiscoveryCandidate:
        row = BetaHypothesisDiscoveryCandidate(
            discovery_run_id=discovery_run_id,
            template_id=template.id,
            family_id=template.family_id,
            candidate_hash=str(candidate["candidate_hash"]),
            hypothesis_code=str(candidate["hypothesis_code"]),
            hypothesis_name=str(candidate["hypothesis_name"]),
            expected_direction=str(candidate["expected_direction"]),
            target_metric=str(candidate["target_metric"]),
            holding_period_days=int(candidate["holding_period_days"]),
            entry_conditions_json=json.dumps(candidate["entry_conditions"], sort_keys=True),
            regime_filters_json=json.dumps(candidate.get("regime_filters") or {}, sort_keys=True),
            feature_subset_json=json.dumps(candidate.get("feature_subset") or [], sort_keys=True),
            stage_reached=int(candidate.get("stage_reached") or 0),
            status=str(candidate.get("status") or "DISCOVERED"),
            support_count=int(candidate.get("support_count") or 0),
            matched_instruments=int(candidate.get("matched_instruments") or 0),
            hit_rate_pct=candidate.get("hit_rate_pct"),
            average_target_return_pct=candidate.get("average_target_return_pct"),
            average_excess_return_pct=candidate.get("average_excess_return_pct"),
            median_excess_return_pct=candidate.get("median_excess_return_pct"),
            outcome_volatility_pct=candidate.get("outcome_volatility_pct"),
            friction_adjusted_return_pct=candidate.get("friction_adjusted_return_pct"),
            walk_forward_score=candidate.get("walk_forward_score"),
            baseline_edge_pct=candidate.get("baseline_edge_pct"),
            stability_score=candidate.get("stability_score"),
            redundancy_group=str(candidate.get("redundancy_group") or ""),
            notes_json=json.dumps(candidate.get("notes") or {}, sort_keys=True),
        )
        sess.add(row)
        sess.flush()
        return row

    @staticmethod
    def _existing_definition_hashes(sess) -> set[str]:
        hashes: set[str] = set()
        for row in sess.scalars(select(BetaHypothesisDefinition)).all():
            try:
                entry_conditions = json.loads(row.entry_conditions_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                entry_conditions = {}
            try:
                regime_filters = json.loads(row.regime_filters_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                regime_filters = {}
            hashes.add(
                BetaHypothesisDiscoveryService._candidate_hash(
                    expected_direction=str(row.expected_direction or "BULLISH"),
                    target_metric=str(row.target_metric or "fwd_5d_excess_return_pct"),
                    holding_period_days=int(row.holding_period_days or 5),
                    entry_conditions=entry_conditions,
                    regime_filters=regime_filters,
                )
            )
        return hashes

    @staticmethod
    def _regime_filters_for_code(code: str) -> dict[str, object]:
        regime_code = str(code or "none").strip().lower()
        if regime_code in {"", "none"}:
            return {}
        if regime_code == "weak_market_regime":
            return {"all": [{"feature": "market_ret_5d_pct", "op": "lt", "value": -1.0}]}
        if regime_code == "strong_market_regime":
            return {"all": [{"feature": "market_ret_5d_pct", "op": "gt", "value": 1.0}]}
        if regime_code == "weak_sector_regime":
            return {"all": [{"feature": "sector_ret_5d_pct", "op": "lt", "value": -1.0}]}
        if regime_code == "strong_sector_regime":
            return {"all": [{"feature": "sector_ret_5d_pct", "op": "gt", "value": 1.0}]}
        if regime_code == "high_vol_regime":
            return {"all": [{"feature": "realized_vol_20d_pct", "op": "gte", "value": 8.0}]}
        if regime_code == "low_vol_regime":
            return {"all": [{"feature": "realized_vol_20d_pct", "op": "lte", "value": 4.0}]}
        return {}

    @staticmethod
    def _regime_feature_names() -> set[str]:
        return {
            "market_ret_5d_pct",
            "sector_ret_5d_pct",
            "realized_vol_20d_pct",
        }

    @staticmethod
    def _candidate_hash(
        *,
        expected_direction: str,
        target_metric: str,
        holding_period_days: int,
        entry_conditions: dict[str, object],
        regime_filters: dict[str, object],
    ) -> str:
        payload = {
            "expected_direction": expected_direction,
            "target_metric": target_metric,
            "holding_period_days": holding_period_days,
            "entry_conditions": json.loads(BetaHypothesisNormalizer.canonical_json(entry_conditions)),
            "regime_filters": json.loads(BetaHypothesisNormalizer.canonical_json(regime_filters)),
        }
        digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return digest[:16]

    @staticmethod
    def _redundancy_group(
        *,
        family_code: str,
        expected_direction: str,
        entry_conditions: dict[str, object],
        regime_filters: dict[str, object],
    ) -> str:
        condition_signature = "|".join(sorted(BetaHypothesisDiscoveryService._leaf_signatures(entry_conditions)))
        regime_signature = "|".join(sorted(BetaHypothesisDiscoveryService._leaf_signatures(regime_filters)))
        return f"{family_code}:{expected_direction}:{condition_signature}:{regime_signature}"

    @staticmethod
    def _leaf_signatures(conditions: dict[str, object]) -> list[str]:
        if not conditions:
            return []
        if "all" in conditions:
            result: list[str] = []
            for child in conditions.get("all", []):
                result.extend(BetaHypothesisDiscoveryService._leaf_signatures(child))
            return result
        if "any" in conditions:
            result: list[str] = []
            for child in conditions.get("any", []):
                result.extend(BetaHypothesisDiscoveryService._leaf_signatures(child))
            return result
        if "not" in conditions:
            return BetaHypothesisDiscoveryService._leaf_signatures(conditions.get("not") or {})
        feature_name = str(conditions.get("feature") or conditions.get("field") or "")
        op = str(conditions.get("op") or "")
        return [f"{feature_name}:{op}"]

    @staticmethod
    def _generated_hypothesis_code(template_code: str, candidate_hash: str) -> str:
        return f"{template_code}_{candidate_hash[:8]}"

    @staticmethod
    def _generated_hypothesis_name(template_name: str, candidate_hash: str) -> str:
        return f"{template_name} [{candidate_hash[:6]}]"

    @staticmethod
    def _json_object(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
