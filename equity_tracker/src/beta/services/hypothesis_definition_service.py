"""Seed and govern first-class hypothesis families and definitions."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from ..context import BetaContext
from ..db.models import (
    BetaHypothesis,
    BetaHypothesisDefinition,
    BetaHypothesisFamily,
)
from .hypothesis_normalizer import BetaHypothesisNormalizer
from .hypothesis_service import BetaHypothesisService

_DEFAULT_FAMILIES = (
    {
        "family_code": "TREND_PULLBACK_RECOVERY",
        "family_name": "Trend pullback recovery",
        "description": "Continuation-style setups after controlled pullbacks and recoveries.",
        "generator_type": "TEMPLATE_MUTATION",
        "default_target_metric": "fwd_5d_excess_return_pct",
        "default_holding_period_days": 5,
        "mutation_policy": {
            "threshold_variants": True,
            "regime_segmentation": True,
            "holding_period_variants": [3, 5, 10],
        },
    },
    {
        "family_code": "CATALYST_CONFIRMATION",
        "family_name": "Catalyst confirmation",
        "description": "Event-led setups requiring price confirmation after news or filings.",
        "generator_type": "TEMPLATE_MUTATION",
        "default_target_metric": "fwd_5d_excess_return_pct",
        "default_holding_period_days": 5,
        "mutation_policy": {
            "threshold_variants": True,
            "sentiment_variants": True,
            "holding_period_variants": [3, 5, 10],
        },
    },
    {
        "family_code": "MEAN_REVERSION",
        "family_name": "Mean reversion",
        "description": "Oversold or overextended structures with reversal potential.",
        "generator_type": "TEMPLATE_MUTATION",
        "default_target_metric": "fwd_5d_excess_return_pct",
        "default_holding_period_days": 5,
        "mutation_policy": {
            "threshold_variants": True,
            "volatility_filters": True,
            "holding_period_variants": [3, 5, 7],
        },
    },
)

_DEFAULT_DEFINITIONS = (
    {
        "family_code": "TREND_PULLBACK_RECOVERY",
        "hypothesis_code": "TREND_PULLBACK_RECOVERY_V1",
        "name": "20d trend positive with controlled 5d pullback",
        "entry_conditions": {
            "all": [
                {"feature": "ret_20d_pct", "op": ">", "value": 8.0},
                {"feature": "ret_5d_pct", "op": "between", "min": -6.0, "max": -1.0},
                {"feature": "drawdown_from_20d_high_pct", "op": "between", "min": -12.0, "max": -1.0},
                {"feature": "rebound_from_20d_low_pct", "op": ">", "value": 3.0},
                {"feature": "news_sentiment_7d", "op": ">", "value": -0.35},
            ]
        },
        "expected_direction": "BULLISH",
        "holding_period_days": 5,
        "generation_source": "TEMPLATE_SEED",
    },
    {
        "family_code": "TREND_PULLBACK_RECOVERY",
        "hypothesis_code": "TREND_PULLBACK_RECOVERY_V2",
        "name": "Trend recovery with low-volatility pullback",
        "entry_conditions": {
            "all": [
                {"feature": "ret_10d_pct", "op": ">", "value": 4.0},
                {"feature": "ret_5d_pct", "op": "between", "min": -4.5, "max": -0.5},
                {"feature": "realized_vol_20d_pct", "op": "<", "value": 6.0},
                {"feature": "rebound_from_20d_low_pct", "op": ">", "value": 4.0},
            ]
        },
        "expected_direction": "BULLISH",
        "holding_period_days": 5,
        "generation_source": "TEMPLATE_MUTATION",
        "parent_hypothesis_code": "TREND_PULLBACK_RECOVERY_V1",
    },
    {
        "family_code": "CATALYST_CONFIRMATION",
        "hypothesis_code": "CATALYST_CONFIRMATION_POSITIVE_V1",
        "name": "Positive catalyst with price confirmation",
        "entry_conditions": {
            "all": [
                {"feature": "news_count_7d", "op": ">", "value": 0.0},
                {"feature": "news_sentiment_7d", "op": ">", "value": 0.1},
                {"feature": "official_sentiment_14d", "op": ">", "value": 0.05},
                {"feature": "ret_5d_pct", "op": ">", "value": 0.0},
                {"feature": "intraday_pct_change", "op": ">", "value": 0.0},
            ]
        },
        "expected_direction": "BULLISH",
        "holding_period_days": 5,
        "generation_source": "TEMPLATE_SEED",
    },
    {
        "family_code": "CATALYST_CONFIRMATION",
        "hypothesis_code": "CATALYST_CONFIRMATION_NEGATIVE_V1",
        "name": "Negative catalyst with downside confirmation",
        "entry_conditions": {
            "all": [
                {
                    "any": [
                        {"feature": "news_sentiment_7d", "op": "<", "value": -0.1},
                        {"feature": "official_sentiment_14d", "op": "<", "value": -0.05},
                    ]
                },
                {"feature": "ret_5d_pct", "op": "<", "value": -1.0},
                {"feature": "drawdown_from_20d_high_pct", "op": "<", "value": -3.0},
            ]
        },
        "expected_direction": "RISK_OFF",
        "holding_period_days": 5,
        "generation_source": "TEMPLATE_SEED",
    },
    {
        "family_code": "MEAN_REVERSION",
        "hypothesis_code": "MEAN_REVERSION_BOUNCE_V1",
        "name": "Oversold bounce with stabilising volatility",
        "entry_conditions": {
            "all": [
                {"feature": "drawdown_from_20d_high_pct", "op": "<", "value": -8.0},
                {"feature": "rebound_from_20d_low_pct", "op": ">", "value": 1.0},
                {"feature": "realized_vol_20d_pct", "op": "<", "value": 12.0},
                {"feature": "market_excess_5d_pct", "op": "<", "value": -1.0},
            ]
        },
        "expected_direction": "BULLISH",
        "holding_period_days": 5,
        "generation_source": "TEMPLATE_SEED",
    },
    {
        "family_code": "MEAN_REVERSION",
        "hypothesis_code": "MEAN_REVERSION_BREAKDOWN_V1",
        "name": "Overextended downside continuation after failed rebound",
        "entry_conditions": {
            "all": [
                {"feature": "drawdown_from_20d_high_pct", "op": "<", "value": -10.0},
                {"feature": "rebound_from_20d_low_pct", "op": "<", "value": 2.0},
                {"feature": "ret_5d_pct", "op": "<", "value": -3.0},
                {"feature": "realized_vol_20d_pct", "op": ">", "value": 4.0},
            ]
        },
        "expected_direction": "RISK_OFF",
        "holding_period_days": 5,
        "generation_source": "TEMPLATE_MUTATION",
        "parent_hypothesis_code": "MEAN_REVERSION_BOUNCE_V1",
    },
)


class BetaHypothesisDefinitionService:
    """Seed first-class research families and definitions."""

    @staticmethod
    def ensure_default_research_objects() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {
                "families_added": 0,
                "definitions_added": 0,
                "definitions_updated": 0,
                "legacy_hypotheses_added": 0,
            }

        legacy_result = BetaHypothesisService.ensure_default_hypotheses()
        families_added = 0
        definitions_added = 0
        definitions_updated = 0

        with BetaContext.write_session() as sess:
            families_by_code: dict[str, BetaHypothesisFamily] = {}
            for spec in _DEFAULT_FAMILIES:
                existing = sess.scalar(
                    select(BetaHypothesisFamily).where(BetaHypothesisFamily.family_code == spec["family_code"])
                )
                if existing is None:
                    existing = BetaHypothesisFamily(
                        family_code=spec["family_code"],
                        family_name=spec["family_name"],
                        description=spec["description"],
                        generator_type=spec["generator_type"],
                        default_target_metric=spec["default_target_metric"],
                        default_holding_period_days=int(spec["default_holding_period_days"]),
                        mutation_policy_json=json.dumps(spec["mutation_policy"], sort_keys=True),
                        status="ACTIVE",
                    )
                    sess.add(existing)
                    sess.flush()
                    families_added += 1
                else:
                    existing.family_name = spec["family_name"]
                    existing.description = spec["description"]
                    existing.generator_type = spec["generator_type"]
                    existing.default_target_metric = spec["default_target_metric"]
                    existing.default_holding_period_days = int(spec["default_holding_period_days"])
                    existing.mutation_policy_json = json.dumps(spec["mutation_policy"], sort_keys=True)
                    existing.status = "ACTIVE"
                families_by_code[spec["family_code"]] = existing

            existing_definitions = {
                row.hypothesis_code: row
                for row in sess.scalars(select(BetaHypothesisDefinition)).all()
            }
            for spec in _DEFAULT_DEFINITIONS:
                family = families_by_code[spec["family_code"]]
                normalized_conditions = BetaHypothesisNormalizer.normalize_conditions(spec["entry_conditions"])
                feature_subset = sorted(BetaHypothesisNormalizer.extract_feature_names(normalized_conditions))
                parent_id = None
                parent_code = spec.get("parent_hypothesis_code")
                if parent_code:
                    parent_row = existing_definitions.get(str(parent_code))
                    parent_id = parent_row.id if parent_row is not None else None

                existing = existing_definitions.get(spec["hypothesis_code"])
                if existing is None:
                    existing = BetaHypothesisDefinition(
                        family_id=family.id,
                        hypothesis_code=spec["hypothesis_code"],
                        name=spec["name"],
                        universe_json=json.dumps({"markets": ["UK", "US"], "core_bias": True}, sort_keys=True),
                        entry_conditions_json=json.dumps(normalized_conditions, sort_keys=True),
                        exit_conditions_json=json.dumps(
                            {"holding_period_days": int(spec["holding_period_days"])},
                            sort_keys=True,
                        ),
                        holding_period_days=int(spec["holding_period_days"]),
                        target_metric=family.default_target_metric,
                        expected_direction=str(spec["expected_direction"]),
                        feature_subset_json=json.dumps(feature_subset, sort_keys=True),
                        parent_hypothesis_id=parent_id,
                        generation_source=str(spec["generation_source"]),
                        provenance_json=json.dumps(
                            {
                                "seed_template": spec["hypothesis_code"],
                                "family_code": family.family_code,
                                "parent_hypothesis_code": parent_code,
                            },
                            sort_keys=True,
                        ),
                        status="CANDIDATE",
                    )
                    sess.add(existing)
                    definitions_added += 1
                else:
                    existing.family_id = family.id
                    existing.name = spec["name"]
                    existing.universe_json = json.dumps({"markets": ["UK", "US"], "core_bias": True}, sort_keys=True)
                    existing.entry_conditions_json = json.dumps(normalized_conditions, sort_keys=True)
                    existing.exit_conditions_json = json.dumps(
                        {"holding_period_days": int(spec["holding_period_days"])},
                        sort_keys=True,
                    )
                    existing.holding_period_days = int(spec["holding_period_days"])
                    existing.target_metric = family.default_target_metric
                    existing.expected_direction = str(spec["expected_direction"])
                    existing.feature_subset_json = json.dumps(feature_subset, sort_keys=True)
                    existing.parent_hypothesis_id = parent_id
                    existing.generation_source = str(spec["generation_source"])
                    existing.provenance_json = json.dumps(
                        {
                            "seed_template": spec["hypothesis_code"],
                            "family_code": family.family_code,
                            "parent_hypothesis_code": parent_code,
                        },
                        sort_keys=True,
                    )
                    if existing.status == "ARCHIVED":
                        existing.status = "CANDIDATE"
                    definitions_updated += 1
                existing_definitions[spec["hypothesis_code"]] = existing

            BetaHypothesisDefinitionService.sync_legacy_family_registry(sess)
            definition_count = int(
                sess.scalar(select(func.count()).select_from(BetaHypothesisDefinition)) or 0
            )
            family_count = int(
                sess.scalar(select(func.count()).select_from(BetaHypothesisFamily)) or 0
            )

        return {
            "families_added": families_added,
            "definitions_added": definitions_added,
            "definitions_updated": definitions_updated,
            "family_count": family_count,
            "definition_count": definition_count,
            "legacy_hypotheses_added": int(legacy_result.get("added", 0)),
        }

    @staticmethod
    def sync_legacy_family_registry(sess) -> None:
        legacy_rows = {
            row.code: row
            for row in sess.scalars(select(BetaHypothesis)).all()
        }
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
            elif family.status != "ACTIVE" or all(row.status in {"DEGRADED", "REJECTED", "ARCHIVED"} for row in family_definitions if family_definitions):
                status = "SUSPENDED"
            evidence_score = 0.0
            if family_definitions:
                evidence_score = round(
                    sum(
                        1.0
                        for row in family_definitions
                        if row.status in {"PROMISING", "VALIDATED"}
                    ) / len(family_definitions) * 100.0,
                    2,
                )
            legacy.title = family.family_name
            legacy.status = status
            legacy.evidence_score = f"{evidence_score:.2f}"
            legacy.auto_promoted = status == "PROMOTED"
            legacy.notes = (
                f"{family.description or ''} Definitions: {len(family_definitions)}; "
                f"promising/validated: {len([row for row in family_definitions if row.status in {'PROMISING','VALIDATED'}])}."
            ).strip()
