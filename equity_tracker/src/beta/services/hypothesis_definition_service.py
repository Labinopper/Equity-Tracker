"""Seed and govern first-class hypothesis families, templates, and definitions."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select, text

from ..context import BetaContext
from ..db.models import (
    BetaHypothesis,
    BetaHypothesisDefinition,
    BetaHypothesisFamily,
    BetaHypothesisTemplate,
)
from .hypothesis_normalizer import BetaHypothesisNormalizer
from .hypothesis_service import BetaHypothesisService


class BetaHypothesisDefinitionService:
    """Seed first-class research families, templates, and definitions."""

    _ACTIVE_DEFINITION_STATUSES = (
        "DISCOVERED",
        "SCREENED_IN",
        "CANDIDATE",
        "PROMISING",
        "VALIDATED",
        "DEGRADED",
        "REJECTED",
    )
    _STATUS_FALLBACKS = {
        "DISCOVERED": "CANDIDATE",
        "SCREENED_IN": "CANDIDATE",
        "RETIRED": "REJECTED",
    }

    @staticmethod
    def ensure_default_research_objects() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {
                "families_added": 0,
                "templates_added": 0,
                "definitions_added": 0,
                "definitions_updated": 0,
                "legacy_hypotheses_added": 0,
            }

        legacy_result = BetaHypothesisService.ensure_default_hypotheses()
        families_added = 0
        templates_added = 0
        templates_updated = 0
        definitions_added = 0
        definitions_updated = 0

        with BetaContext.write_session() as sess:
            family_specs = BetaHypothesisDefinitionService._load_json_config("hypothesis_families.json")
            template_specs = BetaHypothesisDefinitionService._load_json_config("hypothesis_template_specs.json")
            definition_specs = BetaHypothesisDefinitionService._load_json_config("hypothesis_seed_definitions.json")

            families_by_code: dict[str, BetaHypothesisFamily] = {}
            for spec in family_specs:
                existing = sess.scalar(
                    select(BetaHypothesisFamily).where(BetaHypothesisFamily.family_code == spec["family_code"])
                )
                payload_template = {
                    "family_code": spec["family_code"],
                    "generator_type": spec.get("generator_type", "TEMPLATE_MUTATION"),
                    "mutation_policy": spec.get("mutation_policy", {}),
                    "generation_budget": spec.get("generation_budget", {}),
                }
                if existing is None:
                    existing = BetaHypothesisFamily(
                        family_code=str(spec["family_code"]),
                        family_name=str(spec["family_name"]),
                        description=str(spec.get("description") or ""),
                        generator_type=str(spec.get("generator_type") or "TEMPLATE_MUTATION"),
                        default_target_metric=str(spec.get("default_target_metric") or "fwd_5d_excess_return_pct"),
                        default_holding_period_days=int(spec.get("default_holding_period_days") or 5),
                        mutation_policy_json=json.dumps(spec.get("mutation_policy", {}), sort_keys=True),
                        template_spec_json=json.dumps(payload_template, sort_keys=True),
                        generation_budget_json=json.dumps(spec.get("generation_budget", {}), sort_keys=True),
                        status="ACTIVE",
                    )
                    sess.add(existing)
                    sess.flush()
                    families_added += 1
                else:
                    existing.family_name = str(spec["family_name"])
                    existing.description = str(spec.get("description") or "")
                    existing.generator_type = str(spec.get("generator_type") or "TEMPLATE_MUTATION")
                    existing.default_target_metric = str(spec.get("default_target_metric") or "fwd_5d_excess_return_pct")
                    existing.default_holding_period_days = int(spec.get("default_holding_period_days") or 5)
                    existing.mutation_policy_json = json.dumps(spec.get("mutation_policy", {}), sort_keys=True)
                    existing.template_spec_json = json.dumps(payload_template, sort_keys=True)
                    existing.generation_budget_json = json.dumps(spec.get("generation_budget", {}), sort_keys=True)
                    existing.status = "ACTIVE"
                families_by_code[str(spec["family_code"])] = existing

            existing_templates = {
                row.template_code: row
                for row in sess.scalars(select(BetaHypothesisTemplate)).all()
            }
            for spec in template_specs:
                family = families_by_code[str(spec["family_code"])]
                required_features = sorted({str(name) for name in spec.get("required_features", []) if str(name).strip()})
                existing = existing_templates.get(str(spec["template_code"]))
                if existing is None:
                    existing = BetaHypothesisTemplate(
                        family_id=family.id,
                        template_code=str(spec["template_code"]),
                        template_name=str(spec["template_name"]),
                        hypothesis_family=str(spec.get("hypothesis_family") or family.family_code.lower()),
                        expected_direction=str(spec["expected_direction"]),
                        target_metric=str(spec.get("target_metric") or family.default_target_metric),
                        holding_period_days=int(spec.get("holding_period_days") or family.default_holding_period_days),
                        required_features_json=json.dumps(required_features, sort_keys=True),
                        template_spec_json=json.dumps(spec, sort_keys=True),
                        mutation_rules_json=json.dumps(
                            {
                                "condition_slots": spec.get("condition_slots", []),
                                "max_variants": int(spec.get("max_variants") or 0),
                                "max_condition_count": int(spec.get("max_condition_count") or 0),
                                "min_support": int(spec.get("min_support") or 0),
                            },
                            sort_keys=True,
                        ),
                        regime_options_json=json.dumps(spec.get("regime_gate_codes", []), sort_keys=True),
                        universe_json=json.dumps(spec.get("universe", {"markets": ["UK", "US"]}), sort_keys=True),
                        source_type="GENERATED",
                        status="ACTIVE",
                    )
                    sess.add(existing)
                    templates_added += 1
                else:
                    existing.family_id = family.id
                    existing.template_name = str(spec["template_name"])
                    existing.hypothesis_family = str(spec.get("hypothesis_family") or family.family_code.lower())
                    existing.expected_direction = str(spec["expected_direction"])
                    existing.target_metric = str(spec.get("target_metric") or family.default_target_metric)
                    existing.holding_period_days = int(spec.get("holding_period_days") or family.default_holding_period_days)
                    existing.required_features_json = json.dumps(required_features, sort_keys=True)
                    existing.template_spec_json = json.dumps(spec, sort_keys=True)
                    existing.mutation_rules_json = json.dumps(
                        {
                            "condition_slots": spec.get("condition_slots", []),
                            "max_variants": int(spec.get("max_variants") or 0),
                            "max_condition_count": int(spec.get("max_condition_count") or 0),
                            "min_support": int(spec.get("min_support") or 0),
                        },
                        sort_keys=True,
                    )
                    existing.regime_options_json = json.dumps(spec.get("regime_gate_codes", []), sort_keys=True)
                    existing.universe_json = json.dumps(spec.get("universe", {"markets": ["UK", "US"]}), sort_keys=True)
                    existing.status = "ACTIVE"
                    templates_updated += 1
                existing_templates[str(spec["template_code"])] = existing

            existing_definitions = {
                row.hypothesis_code: row
                for row in sess.scalars(select(BetaHypothesisDefinition)).all()
            }
            for spec in definition_specs:
                result = BetaHypothesisDefinitionService.upsert_definition(
                    sess,
                    family_by_code=families_by_code,
                    existing_definitions=existing_definitions,
                    spec=spec,
                    default_status="CANDIDATE",
                )
                definitions_added += int(result["added"])
                definitions_updated += int(result["updated"])

            BetaHypothesisDefinitionService.sync_legacy_family_registry(sess)
            definition_count = int(sess.scalar(select(func.count()).select_from(BetaHypothesisDefinition)) or 0)
            family_count = int(sess.scalar(select(func.count()).select_from(BetaHypothesisFamily)) or 0)
            template_count = int(sess.scalar(select(func.count()).select_from(BetaHypothesisTemplate)) or 0)

        return {
            "families_added": families_added,
            "templates_added": templates_added,
            "templates_updated": templates_updated,
            "definitions_added": definitions_added,
            "definitions_updated": definitions_updated,
            "family_count": family_count,
            "template_count": template_count,
            "definition_count": definition_count,
            "legacy_hypotheses_added": int(legacy_result.get("added", 0)),
        }

    @staticmethod
    def supports_extended_definition_statuses(sess) -> bool:
        return BetaHypothesisDefinitionService._table_sql_contains(
            sess,
            "beta_hypothesis_definitions",
            ("DISCOVERED", "SCREENED_IN", "RETIRED"),
        )

    @staticmethod
    def supports_extended_belief_statuses(sess) -> bool:
        return BetaHypothesisDefinitionService._table_sql_contains(
            sess,
            "beta_hypothesis_belief_states",
            ("DISCOVERED", "SCREENED_IN", "RETIRED"),
        )

    @staticmethod
    def map_definition_status(sess, status: str) -> str:
        if BetaHypothesisDefinitionService.supports_extended_definition_statuses(sess):
            return status
        return BetaHypothesisDefinitionService._STATUS_FALLBACKS.get(status, status)

    @staticmethod
    def map_belief_status(sess, status: str) -> str:
        if BetaHypothesisDefinitionService.supports_extended_belief_statuses(sess):
            return status
        return BetaHypothesisDefinitionService._STATUS_FALLBACKS.get(status, status)

    @staticmethod
    def upsert_definition(
        sess,
        *,
        family_by_code: dict[str, BetaHypothesisFamily],
        existing_definitions: dict[str, BetaHypothesisDefinition],
        spec: dict[str, object],
        default_status: str,
        discovery_run_id: str | None = None,
    ) -> dict[str, int | BetaHypothesisDefinition]:
        family = family_by_code[str(spec["family_code"])]
        normalized_conditions = BetaHypothesisNormalizer.normalize_conditions(spec.get("entry_conditions") or {})
        regime_filters = BetaHypothesisNormalizer.normalize_regime_filters(spec.get("regime_filters") or {})
        feature_subset = sorted(
            BetaHypothesisNormalizer.extract_feature_names(normalized_conditions)
            | BetaHypothesisNormalizer.extract_feature_names(regime_filters)
        )
        parent_id = None
        parent_code = spec.get("parent_hypothesis_code")
        if parent_code:
            parent_row = existing_definitions.get(str(parent_code))
            parent_id = parent_row.id if parent_row is not None else None

        existing = existing_definitions.get(str(spec["hypothesis_code"]))
        payload_metadata = dict(spec.get("metadata") or {})
        if discovery_run_id is not None:
            payload_metadata["discovery_run_id"] = discovery_run_id

        if existing is None:
            existing = BetaHypothesisDefinition(
                family_id=family.id,
                hypothesis_code=str(spec["hypothesis_code"]),
                name=str(spec["name"]),
                universe_json=json.dumps(spec.get("universe", {"markets": ["UK", "US"], "core_bias": True}), sort_keys=True),
                entry_conditions_json=json.dumps(normalized_conditions, sort_keys=True),
                regime_filters_json=json.dumps(regime_filters, sort_keys=True) if regime_filters else json.dumps({}, sort_keys=True),
                exit_conditions_json=json.dumps(
                    {"holding_period_days": int(spec.get("holding_period_days") or 5)},
                    sort_keys=True,
                ),
                holding_period_days=int(spec.get("holding_period_days") or family.default_holding_period_days),
                target_metric=str(spec.get("target_metric") or family.default_target_metric),
                expected_direction=str(spec.get("expected_direction") or "BULLISH"),
                feature_subset_json=json.dumps(feature_subset, sort_keys=True),
                parent_hypothesis_id=parent_id,
                generation_source=str(spec.get("generation_source") or "MANUAL_SEED"),
                source_type=str(spec.get("source_type") or "MANUAL"),
                template_code=str(spec.get("template_code") or "") or None,
                metadata_json=json.dumps(payload_metadata, sort_keys=True) if payload_metadata else None,
                provenance_json=json.dumps(
                    {
                        "family_code": family.family_code,
                        "parent_hypothesis_code": parent_code,
                        "seed_template": spec.get("template_code") or spec["hypothesis_code"],
                        "source_type": spec.get("source_type") or "MANUAL",
                    },
                    sort_keys=True,
                ),
                status=BetaHypothesisDefinitionService.map_definition_status(
                    sess,
                    str(spec.get("status") or default_status),
                ),
            )
            sess.add(existing)
            added = 1
            updated = 0
        else:
            existing.family_id = family.id
            existing.name = str(spec["name"])
            existing.universe_json = json.dumps(spec.get("universe", {"markets": ["UK", "US"], "core_bias": True}), sort_keys=True)
            existing.entry_conditions_json = json.dumps(normalized_conditions, sort_keys=True)
            existing.regime_filters_json = json.dumps(regime_filters, sort_keys=True) if regime_filters else json.dumps({}, sort_keys=True)
            existing.exit_conditions_json = json.dumps(
                {"holding_period_days": int(spec.get("holding_period_days") or 5)},
                sort_keys=True,
            )
            existing.holding_period_days = int(spec.get("holding_period_days") or family.default_holding_period_days)
            existing.target_metric = str(spec.get("target_metric") or family.default_target_metric)
            existing.expected_direction = str(spec.get("expected_direction") or "BULLISH")
            existing.feature_subset_json = json.dumps(feature_subset, sort_keys=True)
            existing.parent_hypothesis_id = parent_id
            existing.generation_source = str(spec.get("generation_source") or existing.generation_source)
            existing.source_type = str(spec.get("source_type") or existing.source_type or "MANUAL")
            existing.template_code = str(spec.get("template_code") or existing.template_code or "") or None
            existing.metadata_json = json.dumps(payload_metadata, sort_keys=True) if payload_metadata else existing.metadata_json
            existing.provenance_json = json.dumps(
                {
                    "family_code": family.family_code,
                    "parent_hypothesis_code": parent_code,
                    "seed_template": spec.get("template_code") or spec["hypothesis_code"],
                    "source_type": spec.get("source_type") or existing.source_type or "MANUAL",
                },
                sort_keys=True,
            )
            if existing.status in {"ARCHIVED", "RETIRED"}:
                existing.status = BetaHypothesisDefinitionService.map_definition_status(
                    sess,
                    str(spec.get("status") or default_status),
                )
            added = 0
            updated = 1

        existing_definitions[str(spec["hypothesis_code"])] = existing
        return {"added": added, "updated": updated, "definition": existing}

    @staticmethod
    def sync_legacy_family_registry(sess) -> None:
        legacy_rows = {row.code: row for row in sess.scalars(select(BetaHypothesis)).all()}
        family_rows = list(sess.scalars(select(BetaHypothesisFamily)).all())
        definition_rows = list(sess.scalars(select(BetaHypothesisDefinition)).all())
        definitions_by_family: dict[str, list[BetaHypothesisDefinition]] = {}
        for definition in definition_rows:
            if definition.family_id is None:
                continue
            definitions_by_family.setdefault(definition.family_id, []).append(definition)

        for family in family_rows:
            legacy = legacy_rows.get(family.family_code)
            if legacy is None:
                legacy = BetaHypothesis(
                    code=family.family_code,
                    title=family.family_name,
                    status="RESEARCH",
                    notes=family.description,
                    auto_promoted=False,
                )
                sess.add(legacy)
                sess.flush()
            family_definitions = definitions_by_family.get(family.id, [])
            status = "RESEARCH"
            if any(row.status == "VALIDATED" for row in family_definitions):
                status = "PROMOTED"
            elif family.status != "ACTIVE" or all(
                row.status in {"DEGRADED", "REJECTED", "RETIRED", "ARCHIVED"}
                for row in family_definitions
                if family_definitions
            ):
                status = "SUSPENDED"
            evidence_score = 0.0
            if family_definitions:
                evidence_score = round(
                    sum(
                        1.0
                        for row in family_definitions
                        if row.status in {"SCREENED_IN", "CANDIDATE", "PROMISING", "VALIDATED"}
                    )
                    / len(family_definitions)
                    * 100.0,
                    2,
                )
            legacy.title = family.family_name
            legacy.status = status
            legacy.evidence_score = f"{evidence_score:.2f}"
            legacy.auto_promoted = status == "PROMOTED"
            legacy.notes = (
                f"{family.description or ''} Definitions: {len(family_definitions)}; "
                f"screened/candidate/promising/validated: "
                f"{len([row for row in family_definitions if row.status in {'SCREENED_IN','CANDIDATE','PROMISING','VALIDATED'}])}."
            ).strip()

    @staticmethod
    def _table_sql_contains(sess, table_name: str, tokens: tuple[str, ...]) -> bool:
        sql = sess.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"
            ),
            {"table_name": table_name},
        ).scalar()
        table_sql = str(sql or "")
        return bool(table_sql) and all(token in table_sql for token in tokens)

    @staticmethod
    def _load_json_config(filename: str) -> list[dict[str, object]]:
        path = Path(__file__).resolve().parent.parent / "config" / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to load hypothesis config '{filename}': {exc}") from exc
        if not isinstance(payload, list):
            raise RuntimeError(f"Hypothesis config '{filename}' must contain a JSON list.")
        return [row for row in payload if isinstance(row, dict)]
