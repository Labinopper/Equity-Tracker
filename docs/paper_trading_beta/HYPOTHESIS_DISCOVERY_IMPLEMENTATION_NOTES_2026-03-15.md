**Hypothesis Discovery Plan**

The beta now uses a two-layer research stack:

1. Manual hypotheses
   Hand-authored machine-testable definitions seeded from JSON.
2. Generated hypotheses
   Template-bounded mutations produced by a staged discovery funnel.

The discovery funnel is intentionally narrow:

1. Load a bounded daily-bar feature/label slice.
2. Generate family-specific variants from template threshold grids.
3. Screen for support and instrument breadth.
4. Screen for friction-adjusted edge.
5. Screen for walk-forward robustness.
6. Screen for baseline-relative edge and stability.
7. Cluster near-duplicates and keep the strongest simplest representative.
8. Promote only a capped number of survivors into first-class tracked hypotheses.

This preserves explicit belief governance rather than letting free-form search drive live decisions.

**Modules Changed**

- `equity_tracker/src/beta/config/hypothesis_families.json`
- `equity_tracker/src/beta/config/hypothesis_seed_definitions.json`
- `equity_tracker/src/beta/config/hypothesis_template_specs.json`
- `equity_tracker/src/beta/db/models.py`
- `equity_tracker/src/beta/db/bootstrap.py`
- `equity_tracker/src/beta/settings.py`
- `equity_tracker/src/beta/services/hypothesis_normalizer.py`
- `equity_tracker/src/beta/services/hypothesis_definition_service.py`
- `equity_tracker/src/beta/services/hypothesis_discovery_service.py`
- `equity_tracker/src/beta/services/hypothesis_backtest_service.py`
- `equity_tracker/src/beta/services/hypothesis_belief_service.py`
- `equity_tracker/src/beta/services/hypothesis_signal_service.py`
- `equity_tracker/src/beta/services/hypothesis_service.py`
- `equity_tracker/src/beta/services/pipeline_assessment_service.py`
- `equity_tracker/src/beta/supervisor_process.py`
- `equity_tracker/tests/test_services/test_beta_hypothesis_engine.py`

**Runtime Tradeoffs**

- Search remains family-bounded, not open-ended.
- Template count, variant count, promotion count, support floor, and condition count are all hard-capped in settings.
- Discovery reuses the existing daily feature/label store instead of recomputing raw windows per hypothesis.
- Redundancy pruning removes threshold-near-duplicates before promotion.
- The design fits the current single-worker CPU and memory guard model better than a brute-force grid search.

**Later Intraday Extension**

Do not merge intraday learning into the daily lane.

The correct next step later is a separate intraday hypothesis lane with:

- intraday feature definitions
- intraday forward labels
- intraday templates and discovery caps
- separate belief states or lane tags
- separate governance thresholds from daily setups

That keeps the current daily engine auditable and avoids mixing incompatible horizons.
