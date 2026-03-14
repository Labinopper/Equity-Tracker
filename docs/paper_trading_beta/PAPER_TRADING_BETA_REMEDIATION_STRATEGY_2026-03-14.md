# Paper Trading Beta Remediation Strategy

Date: `2026-03-14`  
Status: active remediation and instrumentation plan  
Purpose: turn the beta from an ingest-heavy prototype into an assessable research loop that can be judged from DB evidence rather than UI impressions.

## 1. Target Operating Model

The beta should operate as a strict secondary system behind the deterministic portfolio app.

It should:
- protect the main app first;
- maintain full historical context for relevant instruments;
- generate hypotheses from historical structure, not only from recent heuristics;
- challenge those hypotheses against other regimes, sectors, and benchmarks;
- score live or recent market states continuously enough to build a meaningful score tape;
- record candidate quality, paper outcomes, and non-selections;
- train only when there is enough new evidence to justify retraining;
- leave a DB trail that proves what happened, when, and whether it helped.

## 2. Current Assessment

Current strengths:
- reference, price, feature, and label stores are materially populated;
- the beta is now isolated from the deterministic app startup path;
- supervisor failures are less fatal than before;
- one active model and one validation run now exist;
- core operational failures are recorded in DB tables.

Current weaknesses:
- the learning loop is thin relative to the volume of stored market data;
- score generation is too sparse to support serious evaluation;
- hypothesis generation is still a small governed registry, not a true discovery system;
- evaluation quality is weak because labeled score counts are low;
- training quality cannot be trusted while validation remains this small and this perfect;
- the DB does not yet give a single compact answer to “is the loop healthy end to end?”

## 3. Guiding Principles

1. Deterministic app safety comes first.
2. Every stage must write assessable evidence.
3. No stage should report success without useful stage metrics.
4. Training should be evidence-driven, not merely frequent.
5. Tracked core equities deserve higher cadence and better observability than seeded research names.
6. Beta output must be falsifiable from stored history.

## 4. Required DB Evidence By Stage

Each stage must be assessable without re-reading code.

### 4.1 Reference and history
- universe size
- tracked-core instrument count
- daily-bar coverage by instrument
- intraday freshness by instrument
- latest source timestamps

### 4.2 Features and labels
- latest feature date written
- feature coverage count on latest decision date
- label coverage count
- missing-feature and missing-label counts

### 4.3 Hypothesis layer
- hypotheses total
- hypotheses promoted / suspended / research
- evidence score trend by hypothesis
- change events with reason payloads

### 4.4 Scoring layer
- active instruments considered
- scored instruments
- instruments skipped and why
- recommendation count
- candidate count by direction and status
- score tape growth over time

### 4.5 Evaluation layer
- labeled score count
- alignment rate by direction and confidence bucket
- conversion rate from recommendation to paper position
- paper outcome summaries

### 4.6 Training layer
- dataset row counts
- train/validation splits
- walk-forward windows
- validation metrics
- challenger versus current active model comparison
- reason when training does not occur

## 5. Measurable Success Outputs

The beta is not “working” until these are visible in the DB.

### 5.1 Minimum operational health
- fresh successful observation run within `10` minutes
- fresh successful scoring run within `15` minutes
- latest feature and label builds within `15` minutes of observation
- no unresolved latest-stage failure for the core loop

### 5.2 Minimum data sufficiency
- `score_tape` growth is continuous over time, not stalled
- at least one active candidate when tracked core equity exists
- labeled score count grows over time
- multiple training/validation records across days, not one isolated model

### 5.3 Minimum research credibility
- validation windows > `2`
- nonzero labeled evaluation population
- realistic validation metrics rather than suspiciously perfect outcomes
- hypothesis promotion/suspension linked to evidence, not static defaults

## 6. Delivery Waves

### Wave 1: Assessability foundation
- Add a DB-backed pipeline snapshot table.
- Record end-to-end health summaries after supervisor cycles.
- Expand scoring job outputs to include skip reasons and coverage counts.
- Ensure every major stage returns structured details.

Acceptance:
- A single DB query can show whether ingest, features, labels, scoring, training, evaluation, and hypotheses are fresh or stale.

### Wave 2: Tracked-equity priority loop
- Give tracked core equities an explicit first-priority lane.
- Separate “core tracked learning” from “seeded research universe exploration.”
- Record per-lane counts and freshness.

Acceptance:
- The DB can prove that tracked holdings are being observed and scored even when the wider universe lags.

### Wave 3: Scoring breadth and candidate quality
- Increase score-tape production and explain why instruments were not scored.
- Add stronger candidate evidence and rejection reasons.
- Persist score-coverage diagnostics.

Acceptance:
- Score tape growth becomes meaningful and interpretable.

### Wave 4: Honest evaluation and training discipline
- Increase walk-forward rigor.
- Store richer validation diagnostics.
- Gate activation on meaningful evidence thresholds.

Acceptance:
- A model cannot become active on trivial or suspicious evidence.

### Wave 5: Genuine hypothesis discovery
- Move beyond the current two-family registry.
- Add historical motif generation and regime challenge logic.
- Persist discovery provenance and failure reasons.

Acceptance:
- Hypotheses become a traceable research output, not only a label bucket.

## 7. Execution Backlog

### Immediate TODO
- [x] Add DB-backed pipeline snapshots with health summaries.
- [x] Record latest-stage freshness and stage counts after supervisor cycles.
- [x] Expand scoring job details with skip reasons and coverage counts.
- [x] Verify new records against the live beta DB.

### Near-term TODO
- [x] Add tracked-core versus research-universe split metrics.
- [x] Persist per-lane score coverage.
- [ ] Persist training skip reasons in a more queryable form.
- [x] Add activation guardrails based on minimum labeled evidence and minimum walk-forward depth.

### Medium-term TODO
- [ ] Add richer evaluation diagnostics and failure clustering.
- [ ] Add historical regime challenge records for hypotheses.
- [ ] Add strategy promotion decision records tied to validation evidence.

## 8. Verification Method

For each implementation step:
1. make the code change;
2. run the stage or snapshot generator;
3. query the beta DB directly;
4. confirm the expected new evidence exists;
5. only then continue to the next piece.

## 9. First Execution Wave In This Pass

This pass should complete:
- pipeline snapshot persistence;
- richer stage details for scoring;
- direct DB verification after write;
- tracked-core priority scoring lane;
- post-change DB verification of score growth and pipeline health.
