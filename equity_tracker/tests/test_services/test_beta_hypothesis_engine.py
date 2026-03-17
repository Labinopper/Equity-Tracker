from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaHypothesisDefinition,
    BetaHypothesisFamily,
    BetaHypothesisTemplate,
    BetaHypothesisTestRun,
    BetaInstrument,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaUniverseMembership,
)
from src.beta.services.hypothesis_backtest_service import BetaHypothesisBacktestService
from src.beta.services.hypothesis_belief_service import BetaHypothesisBeliefService
from src.beta.services.hypothesis_definition_service import BetaHypothesisDefinitionService
from src.beta.services.hypothesis_discovery_service import BetaHypothesisDiscoveryService
from src.beta.services.hypothesis_normalizer import BetaHypothesisNormalizer
from src.beta.settings import BetaSettings


@pytest.fixture()
def beta_context():
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


def test_hypothesis_normalizer_supports_between_all_any_and_regime_filters():
    conditions = BetaHypothesisNormalizer.normalize_conditions(
        {
            "all": [
                {"feature": "ret_5d_pct", "op": "between", "min": -4.0, "max": -1.0},
                {
                    "any": [
                        {"feature": "realized_vol_20d_pct", "op": "lt", "value": 6.0},
                        {"feature": "market_ret_5d_pct", "op": "lt", "value": -1.0},
                    ]
                },
            ]
        }
    )
    regime_filters = BetaHypothesisNormalizer.normalize_regime_filters(
        {"all": [{"feature": "sector_ret_5d_pct", "op": "gt", "value": 1.0}]}
    )

    matched = BetaHypothesisNormalizer.evaluate(
        conditions,
        {
            "ret_5d_pct": -2.5,
            "realized_vol_20d_pct": 5.5,
            "market_ret_5d_pct": 0.1,
        },
    )
    regime_match = BetaHypothesisNormalizer.evaluate(
        regime_filters,
        {"sector_ret_5d_pct": 1.5},
    )
    regime_miss = BetaHypothesisNormalizer.evaluate(
        regime_filters,
        {"sector_ret_5d_pct": 0.5},
    )

    assert matched.matched is True
    assert any(term["op"] == "between" for term in matched.matched_terms)
    assert regime_match.matched is True
    assert regime_miss.matched is False


def test_definition_seed_loads_manual_hypotheses_and_templates(beta_context):
    result = BetaHypothesisDefinitionService.ensure_default_research_objects()

    with BetaContext.read_session() as sess:
        seeded_template = sess.scalar(
            select(BetaHypothesisTemplate).where(
                BetaHypothesisTemplate.template_code == "PANIC_TREND_TEMPLATE"
            )
        )
        seeded_definition = sess.scalar(
            select(BetaHypothesisDefinition).where(
                BetaHypothesisDefinition.hypothesis_code == "SHORT_TERM_EXHAUSTION_REVERSAL_V1"
            )
        )

    assert result["family_count"] >= 10
    assert result["template_count"] >= 8
    assert seeded_template is not None
    assert seeded_definition is not None


def test_discovery_candidate_generation_is_bounded_and_deduplicated():
    template = BetaHypothesisTemplate(
        template_code="TEST_TEMPLATE",
        template_name="Test template",
        hypothesis_family="divergence",
        expected_direction="BULLISH",
        target_metric="fwd_5d_excess_return_pct",
        holding_period_days=5,
    )
    settings = BetaSettings()
    settings.hypothesis_discovery_variant_cap = 5
    specs = BetaHypothesisDiscoveryService._generate_candidate_specs(
        template=template,
        template_spec={
            "family_code": "DIVERGENCE",
            "condition_slots": [
                {"feature": "ret_5d_pct", "op": "gt", "thresholds": [1.0, 1.5, 2.0]},
                {"feature": "market_ret_5d_pct", "op": "lt", "thresholds": [-0.5, -1.0]},
            ],
            "regime_gate_codes": ["none", "weak_market_regime"],
            "max_variants": 20,
            "max_condition_count": 3,
            "min_support": 10,
        },
        settings=settings,
    )

    assert len(specs) == 5
    assert len({spec["candidate_hash"] for spec in specs}) == len(specs)
    assert all(spec["condition_count"] <= settings.hypothesis_discovery_max_condition_count for spec in specs)


def test_discovery_prunes_redundant_candidates_and_keeps_best():
    winner = {
        "candidate_hash": "winner",
        "stage_reached": 5,
        "stability_score": 0.55,
        "baseline_edge_pct": 1.4,
        "friction_adjusted_return_pct": 1.8,
        "support_count": 120,
        "condition_count": 3,
        "status": "SCREENED_IN",
        "redundancy_group": "group-a",
        "notes": {},
    }
    weaker = {
        "candidate_hash": "weaker",
        "stage_reached": 5,
        "stability_score": 0.25,
        "baseline_edge_pct": 0.4,
        "friction_adjusted_return_pct": 0.6,
        "support_count": 90,
        "condition_count": 3,
        "status": "SCREENED_IN",
        "redundancy_group": "group-a",
        "notes": {},
    }

    rows = BetaHypothesisDiscoveryService._prune_redundant_candidates([winner, weaker])

    assert any(row["candidate_hash"] == "winner" and row["status"] == "SCREENED_IN" for row in rows)
    assert any(
        row["candidate_hash"] == "weaker"
        and row["status"] == "PRUNED"
        and row["notes"]["pruned_reason"] == "redundant_variant"
        for row in rows
    )


def test_backtest_summary_is_friction_aware():
    settings = BetaSettings()
    summary = BetaHypothesisBacktestService.summarize_candidate_samples(
        matched_samples=[
            ("i1", date(2026, 3, 1), 3.0),
            ("i2", date(2026, 3, 2), 2.5),
            ("i3", date(2026, 3, 3), 1.5),
            ("i4", date(2026, 3, 4), 2.0),
        ],
        eligible_samples=[
            ("i1", date(2026, 3, 1), 3.0),
            ("i2", date(2026, 3, 2), 2.5),
            ("i3", date(2026, 3, 3), 1.5),
            ("i4", date(2026, 3, 4), 2.0),
            ("i5", date(2026, 3, 5), 0.5),
            ("i6", date(2026, 3, 6), 0.0),
        ],
        expected_direction="BULLISH",
        settings=settings,
    )

    assert summary["average_excess_return_pct"] > 0
    assert summary["transaction_cost_adjusted_return_pct"] < summary["average_excess_return_pct"]
    assert summary["baseline_edge_pct"] is not None


def test_belief_assessment_transitions_to_validated_and_retired():
    base_created = datetime(2026, 3, 10, 12, 0, 0)
    validated_runs = [
        BetaHypothesisTestRun(
            sample_size=140,
            support_count=140,
            transaction_cost_adjusted_return_pct=0.45,
            walk_forward_score=0.18,
            out_of_sample_score=0.22,
            baseline_edge_pct=0.12,
            stability_score=0.55,
            test_end_date=date(2026, 3, 1),
            created_at=base_created,
        ),
        BetaHypothesisTestRun(
            sample_size=150,
            support_count=150,
            transaction_cost_adjusted_return_pct=0.4,
            walk_forward_score=0.16,
            out_of_sample_score=0.2,
            baseline_edge_pct=0.1,
            stability_score=0.5,
            test_end_date=date(2026, 2, 20),
            created_at=base_created - timedelta(days=5),
        ),
        BetaHypothesisTestRun(
            sample_size=145,
            support_count=145,
            transaction_cost_adjusted_return_pct=0.38,
            walk_forward_score=0.15,
            out_of_sample_score=0.18,
            baseline_edge_pct=0.09,
            stability_score=0.48,
            test_end_date=date(2026, 2, 10),
            created_at=base_created - timedelta(days=10),
        ),
    ]
    retired_runs = [
        BetaHypothesisTestRun(
            sample_size=130,
            support_count=130,
            transaction_cost_adjusted_return_pct=-0.4,
            walk_forward_score=-0.18,
            out_of_sample_score=-0.2,
            baseline_edge_pct=-0.12,
            stability_score=0.1,
            test_end_date=date(2026, 3, 1),
            created_at=base_created,
        ),
        BetaHypothesisTestRun(
            sample_size=120,
            support_count=120,
            transaction_cost_adjusted_return_pct=-0.35,
            walk_forward_score=-0.15,
            out_of_sample_score=-0.16,
            baseline_edge_pct=-0.1,
            stability_score=0.12,
            test_end_date=date(2026, 2, 20),
            created_at=base_created - timedelta(days=5),
        ),
        BetaHypothesisTestRun(
            sample_size=110,
            support_count=110,
            transaction_cost_adjusted_return_pct=-0.3,
            walk_forward_score=-0.12,
            out_of_sample_score=-0.14,
            baseline_edge_pct=-0.08,
            stability_score=0.15,
            test_end_date=date(2026, 2, 10),
            created_at=base_created - timedelta(days=10),
        ),
    ]

    validated = BetaHypothesisBeliefService._assess_definition(validated_runs)
    retired = BetaHypothesisBeliefService._assess_definition(retired_runs)

    assert validated["status"] == "VALIDATED"
    assert retired["status"] == "RETIRED"


def test_discovery_run_promotes_survivor_into_definition(beta_context):
    settings = BetaSettings()
    settings.hypothesis_discovery_enabled = True
    settings.hypothesis_discovery_template_limit = 1
    settings.hypothesis_discovery_variant_cap = 8
    settings.hypothesis_discovery_max_promotions_per_run = 2
    settings.hypothesis_discovery_min_support = 3
    settings.hypothesis_discovery_max_condition_count = 4

    with BetaContext.write_session() as sess:
        family = BetaHypothesisFamily(
            family_code="DIVERGENCE",
            family_name="Divergence",
            generator_type="TEMPLATE_MUTATION",
            default_target_metric="fwd_5d_excess_return_pct",
            default_holding_period_days=5,
            status="ACTIVE",
        )
        sess.add(family)
        sess.flush()

        template_spec = {
            "template_code": "DIVERGENCE_BULL_TEMPLATE",
            "family_code": "DIVERGENCE",
            "condition_slots": [
                {"feature": "market_ret_5d_pct", "op": "lt", "thresholds": [-0.5, -1.0, -1.5]},
                {"feature": "ret_5d_pct", "op": "gt", "thresholds": [1.0, 1.5, 2.0]},
                {"feature": "market_excess_5d_pct", "op": "gt", "thresholds": [1.0, 1.5, 2.0]},
            ],
            "regime_gate_codes": ["none"],
            "max_variants": 8,
            "max_condition_count": 4,
            "min_support": 3,
        }
        sess.add(
            BetaHypothesisTemplate(
                family_id=family.id,
                template_code="DIVERGENCE_BULL_TEMPLATE",
                template_name="Bullish market divergence",
                hypothesis_family="divergence",
                expected_direction="BULLISH",
                target_metric="fwd_5d_excess_return_pct",
                holding_period_days=5,
                required_features_json=json.dumps(
                    ["market_ret_5d_pct", "ret_5d_pct", "market_excess_5d_pct"],
                    sort_keys=True,
                ),
                template_spec_json=json.dumps(template_spec, sort_keys=True),
                mutation_rules_json=json.dumps({}, sort_keys=True),
                regime_options_json=json.dumps(["none"], sort_keys=True),
                universe_json=json.dumps({"markets": ["US"]}, sort_keys=True),
                source_type="GENERATED",
                status="ACTIVE",
            )
        )

        feature_defs = {}
        for feature_name in ("market_ret_5d_pct", "ret_5d_pct", "market_excess_5d_pct"):
            feature_def = BetaFeatureDefinition(
                feature_name=feature_name,
                version_code="test_v1",
                feature_family="test",
                timeframe="1D",
                is_active=True,
            )
            sess.add(feature_def)
            sess.flush()
            feature_defs[feature_name] = feature_def

        label_def = BetaLabelDefinition(
            label_name="fwd_5d_excess_return_pct",
            version_code="test_v1",
            horizon_days=5,
            definition_text="test",
            is_active=True,
        )
        sess.add(label_def)
        sess.flush()

        decision_dates = [date(2026, 2, 1) + timedelta(days=offset) for offset in range(9)]
        for index in range(5):
            instrument = BetaInstrument(
                symbol=f"S{index}",
                name=f"Stock {index}",
                market="US",
                exchange="NYSE",
                currency="USD",
                is_active=True,
            )
            sess.add(instrument)
            sess.flush()
            sess.add(
                BetaUniverseMembership(
                    instrument_id=instrument.id,
                    status="ACTIVE",
                    priority_rank=index + 1,
                )
            )
            for row_index, decision_date in enumerate(decision_dates):
                if row_index % 3 != 2:
                    feature_values = {
                        "market_ret_5d_pct": -1.2,
                        "ret_5d_pct": 2.3,
                        "market_excess_5d_pct": 2.1,
                    }
                    label_value = 3.2
                else:
                    feature_values = {
                        "market_ret_5d_pct": -1.2,
                        "ret_5d_pct": 0.2,
                        "market_excess_5d_pct": 0.3,
                    }
                    label_value = -0.2
                for feature_name, feature_value in feature_values.items():
                    sess.add(
                        BetaFeatureValue(
                            feature_definition_id=feature_defs[feature_name].id,
                            instrument_id=instrument.id,
                            feature_date=decision_date,
                            value_numeric=feature_value,
                        )
                    )
                sess.add(
                    BetaLabelValue(
                        label_definition_id=label_def.id,
                        instrument_id=instrument.id,
                        decision_date=decision_date,
                        horizon_end_date=decision_date + timedelta(days=5),
                        value_numeric=label_value,
                    )
                )

    result = BetaHypothesisDiscoveryService.run_discovery(settings)

    with BetaContext.read_session() as sess:
        generated_definition = sess.scalar(
            select(BetaHypothesisDefinition).where(
                BetaHypothesisDefinition.source_type == "GENERATED"
            )
        )

    assert result["candidates_promoted"] >= 1
    assert generated_definition is not None
