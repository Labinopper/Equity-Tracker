# Paper Trading Beta Hypothesis Engine Evolution

Date: `2026-03-14`  
Status: proposed next architectural evolution  
Purpose: evolve the current beta from a disciplined predictive stack into a true hypothesis-learning research engine without discarding the parts that are already working.

## 1. Executive Judgment

The recent predictive refactor materially improved the platform.

The beta is now:
- far better as a quantitative research pipeline than it was before;
- structurally credible as a `model + baseline governed scoring engine`;
- still not a true `hypothesis-learning research engine`.

The critical difference is this:

- today, the system mostly learns a return-scoring function and compares it with baselines;
- target state: the system should learn which explicitly defined setups produce positive expected returns, how robust those setups are, and how belief in those setups changes over time.

That means the current predictive stack should be kept, but demoted from `top-level signal authority` to `evidence-producing sublayer` inside a larger hypothesis engine.

## 2. Current Assessment

### 2.1 What now reflects good quantitative research discipline

The following parts are now materially good and should be preserved:

- Richer feature construction in [feature_service.py](C:/Users/labin/OneDrive/Documents/Equity-Tracker/equity_tracker/src/beta/services/feature_service.py).
  It now includes:
  - `10d` and `20d` trend;
  - `20d` realized volatility;
  - drawdown / rebound structure;
  - benchmark and sector-relative context;
  - wider news and filing windows.

- More credible model training in [training_service.py](C:/Users/labin/OneDrive/Documents/Equity-Tracker/equity_tracker/src/beta/services/training_service.py).
  It now uses:
  - date-based train / validation splitting;
  - ridge regularisation;
  - winsorisation / clipping of features and labels;
  - persisted clipping bounds;
  - walk-forward validation;
  - explicit activation guardrails.

- Baseline-aware governance.
  The system no longer pretends a weak learned model is automatically useful.
  It explicitly compares against:
  - `continuation_excess`
  - `mean_reversion_excess`
  - `zero_excess`

- Model activation discipline.
  The repo now correctly refuses to activate challengers that do not beat baseline floors or minimum accuracy thresholds.

- Confidence calibration.
  Confidence is now derived from validation or baseline calibration rather than arbitrary manual scaling.

- Operational evidence capture.
  The beta now records:
  - model versions;
  - validation runs;
  - training decisions;
  - score tape;
  - pipeline snapshots;
  - job runs.

This is real research discipline. It is not enough for the final goal, but it is a solid foundation.

### 2.2 What remains heuristic or weak

The system is still weak in several important ways.

- The current live signal layer still frequently emits `heuristic` or `validated baseline` outcomes rather than model-backed outputs. That is visible in [scoring_service.py](C:/Users/labin/OneDrive/Documents/Equity-Tracker/equity_tracker/src/beta/services/scoring_service.py), where the current logic can still produce signals with:
  - no active model;
  - no active strategy;
  - baseline or heuristic direction source.

- The current hypothesis layer is not really a hypothesis engine.
  In [hypothesis_service.py](C:/Users/labin/OneDrive/Documents/Equity-Tracker/equity_tracker/src/beta/services/hypothesis_service.py), the current hypotheses are only:
  - `TREND_PULLBACK_RECOVERY`
  - `CATALYST_CONFIRMATION`

  These are broad families, not machine-testable setups.

- Hypothesis state is currently downstream of candidate and paper-trade outcomes, not upstream of them.
  The current system:
  - scores instruments first;
  - assigns them to a broad family afterwards;
  - refreshes family status from candidate activity.

  That is the wrong causal order for a research engine.

- Learning is still mostly symbol-first, not setup-first.
  The system asks:
  - `What does the model think about this stock now?`

  It does not yet ask:
  - `Which validated setup is present right now, and how strong is belief in that setup?`

- Confidence is still not hypothesis confidence.
  It is:
  - model calibration confidence; or
  - baseline calibration confidence; or
  - heuristic-magnitude confidence.

  It is not:
  - confidence in a hypothesis belief state built from repeated out-of-sample tests.

- The recommendation surface is still not idea-driven.
  It is still score-driven with governance, not belief-driven with explicit setup matching.

### 2.3 Is the predictive pipeline now structurally sound?

Yes, with an important qualifier.

It is structurally sound as:

- a return-prediction research loop;
- a baseline-governed challenger system;
- a score-tape and validation engine.

It is not yet structurally complete as:

- a self-improving research engine that learns and updates explicit ideas over time.

So the correct answer is:

- `predictive pipeline sound`: yes
- `hypothesis-learning engine complete`: no

### 2.4 Where the system still fails to accumulate real learning over time

The key learning failures are architectural rather than operational.

- There is no first-class machine-readable setup object.
- There is no backtest record per setup.
- There is no persistent belief state per setup.
- There is no degradation model per setup.
- There is no mutation / evolutionary discovery process.
- There is no formal mapping from `current market state` to `validated setup match`.
- Score tape is accumulating, but setup-level belief is not.

That means the DB is accumulating a lot of useful facts, but not yet the right research object.

## 3. Current DB-Backed Reality Check

As of the current reviewed beta DB on `D:`

- instruments: `178`
- open memberships: `169`
- daily bars: `232,180`
- feature values: `5,655,930`
- label values: `528,756`
- score tape rows: `1,189`
- signal candidates: `78`
- training decisions: `25`
- model versions: `11`
- active models: `0`
- active strategies: `0`
- promoted candidates: `0`

Latest challengers are still sitting around:

- validation sign accuracy: roughly `51.3% - 51.6%`
- best baseline in notes: often `mean_reversion_excess`
- activation outcome: challenger retained, not activated

This is the correct result under the current governance rules.

Interpretation:

- the data engine is working;
- the research evidence store is real;
- the model-governance layer is correctly conservative;
- the platform still lacks a true idea-learning layer.

## 4. What Must Stay Unchanged

The next architecture should retain and reuse these components:

- walk-forward validation
- model versioning
- dataset lineage
- candidate history
- paper trading lifecycle
- baseline-aware governance
- pipeline snapshots
- training decision records

These are good foundations. They should become evidence and governance layers inside the new hypothesis engine.

## 5. Target Operating Model

The system should stop trying to directly answer:

- `What will the stock price do next?`

and instead answer:

- `Which specific setups have historically produced positive expected returns?`
- `Which of those setups is present now?`
- `How much evidence supports that setup today?`
- `Has that setup degraded recently?`

This yields a different architecture:

1. generate setup ideas;
2. normalize them into machine-testable conditions;
3. backtest them on historical data;
4. compare them with baselines and transaction costs;
5. maintain a belief state for each setup;
6. only emit signals when live state matches validated setups.

## 6. Updated Architecture Diagram

```text
                     +----------------------+
                     |  Universe / Corpus   |
                     |  Instruments, Bars,  |
                     |  News, Filings       |
                     +----------+-----------+
                                |
                                v
                     +----------------------+
                     |  Feature + Label     |
                     |  Store               |
                     |  Existing layer      |
                     +----------+-----------+
                                |
               +----------------+----------------+
               |                                 |
               v                                 v
  +---------------------------+      +---------------------------+
  | HypothesisGenerator       |      | TemplateMutationEngine    |
  | LLM proposals + motifs    |      | Threshold / regime /      |
  | from historical patterns  |      | holding-period variants   |
  +-------------+-------------+      +-------------+-------------+
                |                                  |
                +----------------+-----------------+
                                 |
                                 v
                    +---------------------------+
                    | HypothesisNormalizer      |
                    | Condition DSL / AST       |
                    | Machine-testable rules    |
                    +-------------+-------------+
                                  |
                                  v
                    +---------------------------+
                    | HypothesisDefinition      |
                    | + HypothesisFamily        |
                    +-------------+-------------+
                                  |
                                  v
                    +---------------------------+
                    | HypothesisBacktester      |
                    | Historical match engine   |
                    | Walk-forward tests        |
                    | Baseline + cost compare   |
                    +-------------+-------------+
                                  |
                                  v
                    +---------------------------+
                    | HypothesisTestRuns        |
                    | Per-run evidence store    |
                    +-------------+-------------+
                                  |
                                  v
                    +---------------------------+
                    | BeliefUpdater             |
                    | Evidence accumulation     |
                    | Degradation tracking      |
                    +-------------+-------------+
                                  |
                                  v
                    +---------------------------+
                    | HypothesisBeliefState     |
                    | candidate/promising/      |
                    | validated/degraded/etc    |
                    +-------------+-------------+
                                  |
                +-----------------+------------------+
                |                                    |
                v                                    v
     +--------------------------+         +--------------------------+
     | SignalEngine             |         | Predictive Stack         |
     | Match live market state  | <-----> | Existing ridge /         |
     | against validated setups |         | baseline governance      |
     +-------------+------------+         +-------------+------------+
                   |                                    |
                   +----------------+-------------------+
                                    |
                                    v
                     +---------------------------+
                     | SignalObservations        |
                     | Setup match records       |
                     +-------------+-------------+
                                   |
                                   v
                     +---------------------------+
                     | RecommendationEngine      |
                     | Portfolio/risk aware      |
                     | decisioning               |
                     +-------------+-------------+
                                   |
                                   v
                     +---------------------------+
                     | RecommendationDecisions   |
                     | Candidate history +       |
                     | paper-trading lifecycle   |
                     +---------------------------+
```

## 7. Proposed Research Objects

### 7.1 HypothesisDefinition

This is the core setup object.

Fields:

- `hypothesis_id`
- `family_id`
- `name`
- `hypothesis_family`
- `universe_json`
- `entry_conditions_json`
- `exit_conditions_json`
- `holding_period_days`
- `target_metric`
- `expected_direction`
- `feature_subset_json`
- `parent_hypothesis_id`
- `generation_source`
- `provenance_json`
- `status`
- `created_at`
- `updated_at`

Interpretation:

- `hypothesis_family` is the broad idea class
- `entry_conditions_json` is the machine-testable setup
- `exit_conditions_json` defines the lifecycle of the test
- `parent_hypothesis_id` enables evolutionary mutation chains

### 7.2 HypothesisTestRun

Each test run is a specific evaluation of one hypothesis over a defined slice.

Fields:

- `test_run_id`
- `hypothesis_id`
- `dataset_version_id`
- `test_start_date`
- `test_end_date`
- `sample_size`
- `matched_instruments`
- `average_return`
- `median_return`
- `win_rate`
- `max_drawdown`
- `baseline_name`
- `baseline_return`
- `baseline_sign_accuracy`
- `transaction_cost_bps`
- `transaction_cost_adjusted_return`
- `walk_forward_score`
- `out_of_sample_score`
- `regime_slice_json`
- `notes_json`
- `created_at`

### 7.3 HypothesisBeliefState

This is the persistent summary of whether a hypothesis is still credible.

Fields:

- `hypothesis_id`
- `confidence_score`
- `evidence_count`
- `in_sample_strength`
- `out_of_sample_strength`
- `degradation_rate`
- `last_validated_date`
- `current_status`
- `supporting_test_run_id`
- `contradicting_test_run_id`
- `notes_json`
- `updated_at`

Status examples:

- `candidate`
- `promising`
- `validated`
- `degraded`
- `rejected`
- `archived`

## 8. Condition Language

Entry and exit conditions must become machine-testable.

Recommended representation: JSON condition AST.

Example:

```json
{
  "all": [
    {"feature": "ret_20d_pct", "op": ">", "value": 0.0},
    {"feature": "ret_5d_pct", "op": ">=", "value": -5.0},
    {"feature": "ret_5d_pct", "op": "<=", "value": -2.0},
    {"feature": "intraday_pct_change", "op": ">", "value": 1.4},
    {"feature": "news_sentiment_7d", "op": ">", "value": -0.3}
  ]
}
```

This should be the normalized storage format, regardless of whether the idea originated from:

- an LLM proposal;
- a human-authored template;
- a mutation of an existing hypothesis.

## 9. Proposed Database Schema

### 9.1 `hypothesis_families`

Purpose: broad research themes and mutation roots.

Columns:

- `id`
- `family_code`
- `family_name`
- `description`
- `generator_type`
- `default_target_metric`
- `default_holding_period_days`
- `mutation_policy_json`
- `status`
- `created_at`
- `updated_at`

Notes:

- The current `beta_hypotheses` table should be conceptually reinterpreted as the precursor to this level, not the final setup object.

### 9.2 `hypothesis_definitions`

Purpose: first-class machine-testable setup definitions.

Columns:

- `id`
- `family_id`
- `name`
- `hypothesis_code`
- `universe_json`
- `entry_conditions_json`
- `exit_conditions_json`
- `holding_period_days`
- `target_metric`
- `expected_direction`
- `feature_subset_json`
- `parent_hypothesis_id`
- `generation_source`
- `provenance_json`
- `status`
- `created_at`
- `updated_at`

Indexes:

- `(status, updated_at)`
- `(family_id, updated_at)`
- `(parent_hypothesis_id)`

### 9.3 `hypothesis_test_runs`

Purpose: immutable evidence records for each historical evaluation.

Columns:

- `id`
- `hypothesis_id`
- `dataset_version_id`
- `model_version_id` nullable
- `baseline_name`
- `test_start_date`
- `test_end_date`
- `sample_size`
- `matched_instruments`
- `average_return_pct`
- `median_return_pct`
- `win_rate_pct`
- `max_drawdown_pct`
- `baseline_return_pct`
- `baseline_sign_accuracy_pct`
- `transaction_cost_bps`
- `transaction_cost_adjusted_return_pct`
- `walk_forward_score`
- `out_of_sample_score`
- `regime_slice_json`
- `notes_json`
- `created_at`

Indexes:

- `(hypothesis_id, created_at)`
- `(created_at)`
- `(sample_size, walk_forward_score)`

### 9.4 `hypothesis_belief_states`

Purpose: one current credibility state per hypothesis.

Columns:

- `hypothesis_id` primary key
- `confidence_score`
- `evidence_count`
- `in_sample_strength`
- `out_of_sample_strength`
- `degradation_rate`
- `last_validated_date`
- `status`
- `supporting_test_run_id`
- `contradicting_test_run_id`
- `notes_json`
- `updated_at`

Indexes:

- `(status, confidence_score)`
- `(updated_at)`

### 9.5 `signal_observations`

Purpose: every time the live market state matches a hypothesis.

Columns:

- `id`
- `hypothesis_id`
- `test_run_id` nullable
- `instrument_id`
- `symbol`
- `observation_time`
- `decision_date`
- `matched_conditions_json`
- `feature_snapshot_json`
- `regime_context_json`
- `prediction_source`
- `expected_direction`
- `expected_return_pct`
- `baseline_name`
- `belief_confidence_score`
- `observation_status`
- `realized_return_pct` nullable
- `realized_at` nullable
- `created_at`

Indexes:

- `(hypothesis_id, observation_time)`
- `(instrument_id, observation_time)`
- `(observation_status, created_at)`

### 9.6 `recommendation_decisions`

Purpose: explicit decision records over signal observations.

Columns:

- `id`
- `signal_observation_id`
- `candidate_id` nullable
- `instrument_id`
- `symbol`
- `decision_status`
- `decision_reason_code`
- `decision_reason_text`
- `belief_confidence_score`
- `portfolio_constraint_json`
- `paper_trade_action`
- `recommendation_score`
- `created_at`

Indexes:

- `(decision_status, created_at)`
- `(instrument_id, created_at)`

## 10. How Current Tables Should Map Forward

Reuse, do not rewrite:

- `beta_hypotheses`
  - repurpose into family-level registry or migrate into `hypothesis_families`

- `beta_hypothesis_events`
  - retain as event log, but augment with definition-level events

- `beta_score_tape`
  - keep as low-level scoring evidence
  - add references from `signal_observations`

- `beta_signal_candidates`
  - keep as candidate history
  - generate them from `recommendation_decisions`, not directly from raw score logic

- `beta_model_versions`
  - keep as supporting predictive model lineage

- `beta_validation_runs`
  - keep as validation evidence for model / baseline support layers

- `beta_training_decisions`
  - keep as governance trail

- `beta_demo_positions`
  - keep as paper-trading lifecycle output

## 11. Hypothesis Generation

### 11.1 LLM-assisted proposals

Use the LLM to propose candidate ideas, but not to define executable logic directly.

The pipeline should be:

1. LLM proposes a motif:
   - volatility + sentiment compression
   - drawdown + rebound continuation
   - sector divergence reversion
   - earnings reaction follow-through

2. Proposal is normalized into strict conditions.

3. Proposal is either:
   - accepted into `hypothesis_definitions`; or
   - rejected with a reason record.

Required proposal outputs:

- family
- target universe
- candidate features
- expected direction
- suggested thresholds
- suggested holding period

### 11.2 Template mutation

Existing validated or promising hypotheses should generate variants.

Mutation types:

- threshold moves
- feature additions
- feature removals
- regime segmentation
- holding-period changes
- benchmark-relative variants
- news-required versus price-only variants

Each mutation must record:

- parent hypothesis
- mutation reason
- changed fields
- resulting test performance

## 12. Testing Discipline

Every hypothesis test must include:

- walk-forward validation
- out-of-sample evaluation
- baseline comparison
- transaction-cost adjustment
- minimum sample size
- minimum distinct dates
- minimum distinct instruments where applicable

Hypotheses should fail fast when they do not meet evidence floors.

Recommended first thresholds:

- minimum sample size: `75`
- minimum distinct dates: `40`
- minimum distinct instruments: `5` for market-wide ideas, `1` for symbol-specific ideas
- positive transaction-cost-adjusted return
- walk-forward lift over baseline > configurable floor

## 13. Belief Updating

Belief should not be binary.

Recommended belief update inputs:

- latest out-of-sample return
- walk-forward score
- baseline lift
- recent degradation
- sample-size growth
- recency weighting

Recommended state transitions:

- `candidate` -> `promising`
  when minimum sample and initial out-of-sample strength are met

- `promising` -> `validated`
  when repeated walk-forward and live-observation performance remain positive

- `validated` -> `degraded`
  when recent evidence weakens materially

- `degraded` -> `rejected` or `archived`
  when persistent failure continues

## 14. Recommendation Engine

Signals should only be generated when all of the following are true:

1. live state matches a machine-testable hypothesis definition
2. that hypothesis is currently `validated` or `promising` with sufficient confidence
3. belief state is not degraded
4. current portfolio / risk constraints allow action

Then:

- `signal_observation` is written
- `recommendation_decision` is written
- optional `beta_signal_candidate` is created or updated
- optional paper trade lifecycle begins

This reverses the current causality.

Current state:

- score first
- assign broad family later

Target state:

- match validated setup first
- score and rank only within the context of that setup

## 15. Incremental Migration Plan

### Phase 1: Introduce research schema alongside current stack

Add new tables:

- `hypothesis_families`
- `hypothesis_definitions`
- `hypothesis_test_runs`
- `hypothesis_belief_states`
- `signal_observations`
- `recommendation_decisions`

Do not remove current tables.

Success criteria:

- migrations apply cleanly
- no current beta runtime path breaks

### Phase 2: Reinterpret current hypotheses as families

Map current:

- `TREND_PULLBACK_RECOVERY`
- `CATALYST_CONFIRMATION`

into `hypothesis_families`.

Keep `beta_hypotheses` read-compatible during transition.

Success criteria:

- existing UI still works
- family registry exists separately from machine-testable definitions

### Phase 3: Add condition DSL and normalizer

Create:

- `HypothesisNormalizer`
- JSON AST format for entry and exit conditions

Build adapters so current family heuristics can produce first-generation concrete definitions.

Success criteria:

- at least `10-20` explicit hypothesis definitions exist
- each is machine-testable from existing features

### Phase 4: Add historical backtester

Create:

- `HypothesisBacktester`
- `HypothesisTestRun` persistence

Use existing:

- `beta_feature_values`
- `beta_label_values`
- dataset lineage
- baseline evaluation machinery

Success criteria:

- every hypothesis can be backtested without live scoring
- evidence is queryable directly from DB

### Phase 5: Add belief updater

Create:

- `BeliefUpdater`
- `hypothesis_belief_states`

Belief should update from:

- new test runs
- live evaluation observations
- degradation indicators

Success criteria:

- every active definition has a current belief state
- belief transitions are evented and explainable

### Phase 6: Route live scoring through setup matching

Create:

- `SignalEngine`
- live hypothesis matcher

Scoring should become:

- hypothesis-aware
- provenance-aware
- belief-aware

The current predictive model / baseline stack becomes:

- a supporting ranking layer within matched hypotheses

Success criteria:

- every candidate can answer:
  - which hypothesis matched?
  - which test runs support it?
  - what is current belief?

### Phase 7: Route recommendation and paper trading from signal observations

Create:

- `RecommendationEngine`
- `recommendation_decisions`

Generate `beta_signal_candidates` from recommendation decisions rather than directly from raw score logic.

Success criteria:

- candidate history remains intact
- paper trades have explicit originating hypothesis and belief provenance

### Phase 8: Add LLM proposal and mutation workflow

Create:

- `HypothesisGenerator`
- `TemplateMutationEngine`

Do not let LLM output go directly to live recommendations.

Success criteria:

- proposals are normalized, tested, and belief-scored before they ever produce live signals

## 16. Measurable Outputs

The new architecture is only working if the DB can answer all of these directly:

- How many active hypothesis families exist?
- How many machine-testable hypothesis definitions exist?
- How many definitions are `validated`, `promising`, `degraded`, `rejected`?
- Which validated hypotheses produced the latest live signal observations?
- Which recommendation decisions were blocked by low belief?
- Which hypotheses degraded over the last `30` days?
- Which families mutate into consistently stronger descendants?
- Which hypotheses beat baselines after transaction costs?

## 17. Immediate TODO

- [ ] Add hypothesis-family and hypothesis-definition schema.
- [ ] Convert the current broad family registry into `family-level` objects.
- [ ] Build a JSON condition DSL and normalizer.
- [ ] Implement historical hypothesis backtests on top of the existing feature / label store.
- [ ] Persist belief state per hypothesis.
- [ ] Introduce `signal_observations` and `recommendation_decisions`.
- [ ] Make `beta_signal_candidates` downstream of validated setup matches.
- [ ] Keep the predictive stack as a supporting scorer, not the top-level authority.

## 18. Final Recommendation

The recent predictive refactor was the correct move.

It gave the beta:

- much better quant discipline;
- much better model governance;
- much better honesty about when the engine does not yet know enough.

The next step is not another round of pure model tuning.

The next step is to lift the research object one level higher:

- from `predicted return per stock`
- to `validated setup belief per hypothesis`

That is the change that turns this from a careful scoring engine into a true hypothesis-learning research platform.
