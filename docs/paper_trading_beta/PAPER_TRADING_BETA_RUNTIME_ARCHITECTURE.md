# Paper Trading Beta Runtime Architecture

Last updated: `2026-03-13`

Status: proposed operational architecture for the exploratory paper-trading beta. This document translates the strategy and schema into a practical runtime plan.

## 1. Purpose

The paper-trading beta needs two things at the same time:

- a `large historical learning corpus` that is broad enough to support useful offline research and hypothesis testing;
- a `narrow live operating lane` that is disciplined enough to support prospective shadow scoring and immutable demo-trade evaluation.

This document defines how the runtime should support both without collapsing them into one operational surface.

The core rule is:

- broad historical observation;
- narrow prospective promotion.

## 2. Core Runtime Separation

The runtime should be split into seven lanes:

1. `reference and corpus management`
2. `historical fact ingestion`
3. `prospective observation`
4. `feature and label materialization`
5. `research and experiment execution`
6. `shadow scoring and demo-trade execution`
7. `evaluation, replay, backup, and archive`

Each lane should produce versioned outputs for the next lane rather than relying on mutable in-memory state or notebook-only artifacts.

## 3. Recommended v1 Learning Corpus

The beta should not confuse the live paper-trading universe with the full learning corpus.

### 3.1 Historical research corpus

Recommended `v1` target:

- US and UK common equities only;
- liquid, seasoned names with cleaner corporate-action history and better pricing/news coverage;
- roughly `1,000` to `2,000` combined names after filtering, rather than the entire long tail of listed names;
- ideally `10` years of daily history for corpus members;
- ideally `10` years of daily market benchmarks, sector references, and required FX conversion series.

This is the main learning asset.

It is large enough to produce:

- multiple market regimes;
- a stronger label base for ranking models;
- better calibration analysis;
- more credible cross-regime signal tests;
- more defensible promotion decisions.

### 3.2 Active live-paper universe

Recommended `v1` target:

- one primary live market at first;
- roughly `50` liquid names actively scored in shadow mode and demo trading;
- minute-bar and live news capture focused on that active universe and its required benchmark/reference instruments.

This is the main prospective testing asset.

### 3.3 What should not be a v1 requirement

The beta should not require:

- `10` years of minute bars across the whole US+UK corpus;
- `10` years of retained article text;
- full-market live minute coverage across both markets on day one;
- full-market continuous paid-news polling.

Those goals would slow implementation badly while adding less learning value than a strong multi-market daily corpus.

## 4. Required Data Assets

### 4.1 Reference data

Required:

- instrument master for all corpus and live-universe names;
- exchange and session metadata for US and UK exchanges;
- sector and industry mappings;
- benchmark mappings by instrument;
- universe membership history for both the research corpus and the live-paper universe.

### 4.2 Daily market history

Required:

- daily OHLCV bars for the US+UK research corpus;
- daily benchmark series for each market;
- daily sector reference series;
- daily FX conversion series needed for GBP-based attribution and label construction.

Recommended depth:

- target `10` years;
- practical minimum `5` years where a full decade is not yet available or quality is weaker.

### 4.3 Intraday market history

Required:

- prospective minute bars from day one for the active live-paper universe and required benchmark/reference instruments;
- recent minute backfill for entry-timing and diagnostic features.

Recommended depth:

- ideal `6` to `12` months for the active universe;
- practical minimum `60` to `90` trading days.

### 4.4 Corporate actions and event history

Required:

- splits;
- symbol changes;
- dividend events where they affect adjusted history or event labeling;
- earnings dates and other scheduled catalysts used by strategies.

Coverage rule:

- corporate-action and event coverage should span the full retained daily-bar period for any instrument in the research corpus.

### 4.5 News and text-derived context

Required:

- prospective news ingestion from day one;
- metadata-first storage with stable timestamps and source ids;
- article linkage, deduplication, novelty tagging, and directional/relevance classification.

Recommended historical depth:

- ideally `12` to `36` months of metadata/headlines if rights permit;
- prospective coverage matters more than decade-scale text backfill in `v1`.

### 4.6 FX context

Required:

- FX conversion series for non-GBP instruments;
- explicit local-return, FX-return, and GBP-return attribution;
- FX features only where formally defined and versioned.

At minimum for a US+UK corpus:

- `GBP/USD` daily history is mandatory;
- live FX freshness is required for any active US instrument scored in GBP terms.

## 5. Runtime Lanes and Jobs

### 5.1 Reference and corpus management lane

Purpose:

- maintain the instrument master, benchmark mappings, sector mappings, exchange calendars, research-corpus membership, and live-universe membership.

Jobs:

- `reference_sync_job`
  - cadence: daily or on demand
  - outputs: updated reference rows, new instrument candidates, changed mappings
- `research_corpus_review_job`
  - cadence: monthly or quarterly
  - outputs: governed adds/removes for the broader US+UK corpus
- `live_universe_review_job`
  - cadence: monthly or more conservatively
  - outputs: governed adds/removes for the narrow live-paper universe

Design rule:

- the research corpus and live-paper universe should be governed separately.

### 5.2 Historical fact ingestion lane

Purpose:

- backfill and maintain the raw historical facts needed for offline learning.

Jobs:

- `daily_history_backfill_job`
  - cadence: one-off initial load, then repair/retry runs as needed
  - scope: US+UK research corpus, benchmarks, sectors, FX
- `corporate_action_backfill_job`
  - cadence: one-off plus periodic reconciliation
- `event_calendar_backfill_job`
  - cadence: one-off plus rolling updates

Primary outputs:

- raw daily bars
- corporate actions
- event calendar records

### 5.3 Prospective observation lane

Purpose:

- capture the forward-looking data stream used for prospective learning and demo-trade replay.

Jobs:

- `minute_market_collector`
  - cadence: once per minute during active sessions
  - scope: active live-paper universe plus required benchmark/reference instruments
- `news_collector_rss`
  - cadence: every `5` to `15` minutes depending on source stability
  - scope: broad observation feeds
- `news_collector_official`
  - cadence: every `15` to `60` minutes depending on source
  - scope: regulator/company/official feeds where useful
- `news_enrichment_paid`
  - cadence: low-frequency or event-triggered only
  - scope: active live-paper names, promoted hypotheses, or active demo positions

Design rules:

- paid enrichment is not on the same hot path as minute market refresh;
- price and FX freshness outrank optional news enrichment;
- prospective capture starts early even if historical backfill is incomplete.

### 5.4 Feature and label materialization lane

Purpose:

- convert raw facts into reusable, versioned features and explicit stored labels.

Jobs:

- `feature_build_incremental`
  - cadence: after each scoring interval or end-of-bar checkpoint
  - scope: active live universe and any incremental research windows
- `feature_build_batch`
  - cadence: nightly or on-demand backfill
  - scope: historical research corpus
- `label_finalize_job`
  - cadence: nightly
  - scope: any decision timestamps whose forward horizon has completed

Design rules:

- features are stored, not left embedded in model code;
- labels are materialized, not recomputed ad hoc inside experiments;
- label timing rules must prevent future leakage.

### 5.5 Research and experiment lane

Purpose:

- construct frozen datasets, run walk-forward research, and register hypotheses, experiments, models, and strategies.

Jobs:

- `dataset_manifest_build_job`
  - cadence: on demand
  - output: frozen dataset version and row manifest
- `research_train_job`
  - cadence: on demand or scheduled overnight
  - output: trained model version with metrics
- `walk_forward_eval_job`
  - cadence: on demand or scheduled overnight
  - output: experiment results, baseline comparisons, confidence analysis
- `promotion_review_job`
  - cadence: manual review supported by prepared outputs
  - output: promote, reject, suspend, or retain-in-research decision

Design rule:

- research should run against frozen dataset versions, not mutable rolling joins.

### 5.6 Shadow scoring and demo-trade execution lane

Purpose:

- score the full active universe prospectively, decide whether a candidate qualifies, and run the immutable demo-trade lane.

Jobs:

- `shadow_score_job`
  - cadence: per strategy horizon, often once per minute for minute-aware setups or less frequently for hourly/daily strategies
  - output: score run plus full-universe score tape
- `recommendation_gate_job`
  - cadence: immediately after a score run
  - output: accepted recommendations, rejected-candidate reasons
- `allocation_job`
  - cadence: immediately after recommendation gating
  - output: trade intents and blocked-by-limit outcomes
- `paper_execution_job`
  - cadence: event-driven on next eligible bar
  - output: immutable position events, fills, and ledger impact
- `position_monitor_job`
  - cadence: every minute during active sessions plus end-of-day checks
  - output: exits, stale-data safety actions, and lifecycle updates

Design rules:

- all eligible symbols should still be scored even if no trade is taken;
- paper execution must remain append-only;
- shadow scoring should exist before demo execution is turned on.

### 5.7 Evaluation, replay, backup, and archive lane

Purpose:

- turn raw outcomes into learning outputs, preserve replayability, and keep the data operationally safe.

Jobs:

- `evaluation_summary_job`
  - cadence: nightly and weekly
  - output: strategy summaries, calibration reports, regime scorecards, rejection analytics
- `replay_pack_job`
  - cadence: on demand or scheduled for promoted strategies
  - output: artifact bundles for recommendation replay
- `beta_backup_job`
  - cadence: nightly
  - output: backup of `beta_research.db`
- `artifact_archive_job`
  - cadence: weekly or monthly
  - output: compressed archive of models, reports, and replay packs

Design rules:

- portfolio backup and beta backup should be independent;
- replay should depend on preserved raw facts, not only summaries;
- archive jobs should prune only derived caches, never canonical audit data.

## 6. Job Cadence Summary

Recommended `v1` cadence:

- reference sync: daily
- research-corpus review: monthly or quarterly
- live-universe review: monthly
- daily history backfill: initial bulk plus repair runs
- minute market collector: every minute during session
- RSS news collector: every `5` to `15` minutes
- official-feed collector: every `15` to `60` minutes
- paid enrichment: low-frequency only, budget permitting
- incremental feature build: per scoring cycle
- batch feature rebuild: nightly or on demand
- label finalization: nightly
- dataset build: on demand
- research training and walk-forward evaluation: on demand or overnight
- shadow scoring: per strategy cadence
- paper execution checks: event-driven plus minute monitoring
- evaluation summaries: nightly and weekly
- beta backup: nightly

## 7. Failure Handling and Degradation

Degrade in this order:

1. freeze paid news enrichment;
2. continue RSS and official-feed ingestion;
3. preserve minute market and FX refresh for the active live universe;
4. continue writing score tape and audit events even if recommendations are frozen;
5. freeze new entries if price freshness, ledger integrity, or model/version loading fails.

The system should prefer:

- incomplete opportunity coverage with explicit alerts

over:

- silently degraded scoring or false confidence.

## 8. Storage and Backup Strategy

Recommended layout:

- `portfolio.db`
  - holdings, transactions, tax, deterministic product state
- `beta_research.db`
  - beta tables only
- `beta_artifacts/`
  - models, replay packs, exports, archive bundles

Backup rule:

- back up `portfolio.db` and `beta_research.db` independently;
- do not require a research restore to recover core portfolio truth;
- store periodic compressed copies of `beta_artifacts/` separately from database snapshots.

## 9. Implementation Order

Recommended order:

1. build the beta reference domain and split-database boundary;
2. build the US+UK daily research corpus and benchmark/FX context first;
3. build corporate actions and event calendar coverage against that corpus;
4. turn on prospective minute capture for the narrow live universe;
5. turn on prospective news ingestion;
6. build feature store and label store;
7. build frozen dataset manifests and offline experiment registry;
8. build shadow scoring and full-universe score tape;
9. build recommendation gating and immutable demo-trade execution;
10. build evaluation summaries, replay packs, and backup/archive automation.

## 10. Bottom Line

The strongest `v1` runtime architecture is not:

- “all markets live at once”

and not:

- “one tiny single-market dataset with shallow history.”

It is:

- a broad US+UK daily learning corpus with decade-scale ambition;
- a narrow live-paper operating lane with strong audit controls;
- prospective minute/news capture focused where it matters operationally;
- explicit separation between research breadth and live promotion breadth.
