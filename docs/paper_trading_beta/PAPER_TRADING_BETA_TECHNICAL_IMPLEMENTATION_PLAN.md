# Paper Trading Beta Technical Implementation Plan

Last updated: `2026-03-14`

Status: implementation-planning document for the exploratory paper-trading beta. This document converts the strategy, runtime architecture, and schema into an engineering delivery plan.

Companion docs:

- `docs/paper_trading_beta/PAPER_TRADING_BETA_STRATEGY.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_RUNTIME_ARCHITECTURE.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_DATABASE_SCHEMA.md`

## 1. Purpose

This document defines how to implement the Paper Trading Beta inside Equity Tracker without destabilizing the existing site.

The plan must satisfy all of the following:

- preserve the deterministic core product and its current workflows;
- keep the beta isolated from any live-trading interpretation;
- support a large historical learning corpus;
- support prospective shadow scoring and immutable demo-trade testing;
- persist potential signals and their provenance so later AI or human reviews can independently verify them;
- allow learning to be disabled cleanly so the site can continue operating normally;
- prevent the beta from consuming unbounded CPU, memory, I/O, or provider budget.

This is a local, self-hosted system design. It does not assume a distributed queue, Kubernetes, or enterprise orchestration.

## 2. Non-Negotiable Constraints

The implementation should follow these rules:

- the main site must continue to work if the beta is disabled, paused, unhealthy, or absent;
- the beta must use separate storage from the core portfolio database;
- beta startup or migration failure must not block the deterministic site from starting;
- the beta must not mutate existing core holdings, transactions, or deterministic reports;
- the beta must be able to operate in `observation only`, `learning off`, and `fully off` modes;
- the first implementation should prefer simple interpretable models and CPU-first batch jobs;
- GPU usage should be disabled by default in `v1`.

## 3. Readiness Baseline

The current codebase already provides useful building blocks:

- `DatabaseEngine` and the SQLCipher/plain-SQLite patterns in `equity_tracker/src/db/engine.py`
- startup background-task patterns in `equity_tracker/src/api/app.py`
- existing price/fx ingestion and budgeting logic in:
  - `equity_tracker/src/services/price_service.py`
  - `equity_tracker/src/services/twelve_data_price_service.py`
  - `equity_tracker/src/services/twelve_data_stream_service.py`
  - `equity_tracker/src/services/fx_service.py`
- existing fee-estimation logic in `equity_tracker/src/services/broker_fee_service.py`
- existing settings persistence pattern in `equity_tracker/src/settings.py`
- existing migration-safety patterns in `equity_tracker/src/db/migration_manager.py`

What does not yet exist:

- beta database models and repositories;
- beta scheduler/supervisor;
- news ingestion pipeline;
- feature store;
- label store;
- dataset manifests;
- hypothesis/model/strategy registry;
- score tape;
- recommendation and demo-trade lane;
- evaluation/reporting pipeline;
- beta-specific kill switches and runtime controls.

So the project is ready for a technical plan, but not yet ready for live research execution.

## 4. Recommended Implementation Boundary

### 4.1 Separate storage

Use three persistence boundaries:

- `portfolio.db`
  - existing deterministic source of truth
- `beta_research.db`
  - all beta tables
- `beta_artifacts/`
  - model files, replay packs, archived evaluation outputs, exported manifests

The beta should never write its high-volume research data into the core portfolio database.

### 4.2 Separate runtime

Do not run heavy beta learning jobs inside the main FastAPI app process.

Recommended runtime split:

- `web app process`
  - existing site
  - beta UI surfaces and lightweight read models when the beta is enabled
  - no heavy training or research loops
- `beta supervisor process`
  - separate Python process
  - owns beta scheduling, observation, feature/label builds, scoring, and evaluation
- optional `beta training subprocess`
  - spawned only when needed for bounded offline training

This is the safest way to ensure the site keeps operating normally even if beta learning is paused, broken, or resource constrained.

### 4.3 Separate initialization

The deterministic site should not depend on the beta DB being healthy.

Implementation rule:

- the main app should start even if `beta_research.db` does not exist;
- beta migrations should be run by the beta supervisor or beta CLI tools, not by the core startup path;
- if beta initialization fails, the core app should log that beta is unavailable and continue normally.

## 5. Operating Modes and Kill Switches

This is one of the most important parts of the implementation.

### 5.1 Required runtime modes

The beta should support these explicit modes:

- `OFF`
  - no beta DB opens, no beta jobs run, no beta pages exposed
- `OBSERVE_ONLY`
  - collect facts only: market, FX, news, events
  - no dataset builds, no training, no shadow scoring, no demo trades
- `SHADOW_ONLY`
  - collect facts, build features/labels, run shadow scoring, persist score tape
  - no demo-trade execution
- `DEMO_NO_LEARN`
  - collect facts, score, and run demo trades using an already-approved strategy
  - no retraining, no pattern mining, no model refresh
- `FULL_INTERNAL_BETA`
  - full observation, scoring, evaluation, controlled retraining, and demo-trade lane

Recommended `v1` default:

- run in `FULL_INTERNAL_BETA` once the beta is enabled;
- start observation immediately;
- auto-enable shadow scoring once observation health, freshness, and minimum evidence plumbing are sound;
- keep demo execution capability present from the start, but block new demo entries until the relevant candidate, confidence, and validation gates are satisfied;
- keep `OBSERVE_ONLY`, `SHADOW_ONLY`, and `DEMO_NO_LEARN` available as fallback or degradation modes when the site or data environment requires them.

### 5.2 Required kill switches

Add beta-specific runtime controls, separate from existing `AppSettings`.

Recommended settings:

- `beta_enabled`
- `beta_mode`
- `beta_observation_enabled`
- `beta_learning_enabled`
- `beta_shadow_scoring_enabled`
- `beta_demo_execution_enabled`
- `beta_news_enabled`
- `beta_paid_news_enrichment_enabled`
- `beta_gpu_enabled`
- `beta_training_enabled`
- `beta_validation_enabled`
- `beta_incremental_learning_enabled`
- `beta_background_jobs_enabled`
- `beta_max_cpu_workers`
- `beta_max_concurrent_heavy_jobs`
- `beta_max_training_minutes_per_run`
- `beta_retrain_min_new_observations`
- `beta_max_memory_mb`
- `beta_training_window_start_local`
- `beta_training_window_end_local`
- `beta_research_quiet_hours_only`
- `beta_auto_shadow_enable`
- `beta_auto_demo_enable_when_ready`
- `beta_shadow_default_cadence_minutes`
- `beta_auto_pause_entries_on_degradation`
- `beta_auto_resume_entries_on_recovery`
- `beta_market_hours_credit_buffer`
- `beta_market_hours_live_data_priority_enabled`
- `beta_pause_on_startup`

### 5.3 Disable-learning behavior

If `beta_learning_enabled = false`:

- stop hypothesis mining;
- stop retraining;
- stop dataset rebuilds except manually requested ones;
- stop heavy evaluation refreshes;
- preserve existing facts already collected;
- preserve the last approved shadow-scoring and demo-trade configurations if those lanes remain separately enabled;
- optionally keep shadow scoring or demo execution running only if explicitly allowed by separate toggles.

If `beta_enabled = false`:

- do not start the beta supervisor;
- do not open beta DB connections from the web app;
- do not show beta routes or controls;
- leave the rest of the site untouched.

This gives the user a safe fallback if the beta needs to be paused or the machine is under pressure.

## 6. 24/7 Learning: What Should and Should Not Be Continuous

The beta should not do unbounded, full-speed learning 24/7.

That would be the wrong design technically and statistically.

### 6.1 What should be continuous or near-continuous

These are the things that should run continuously where relevant:

- prospective minute market capture during supported market sessions;
- prospective FX refresh;
- prospective news/feed polling;
- prospective official-release and filing polling for supported sources;
- append-only event and audit writes;
- shadow scoring on declared strategy cadence for the active live universe;
- position monitoring for open demo trades.

This is `observation and controlled scoring`, not continuous unconstrained learning.

### 6.2 What should not be continuous

These should not run all day at full intensity:

- full retraining;
- large-scale feature recomputation over the entire corpus;
- broad hypothesis search across all combinations continuously;
- repeated walk-forward backtests;
- repeated leaderboard rebuilds from scratch;
- repeated AI audit sweeps over the full corpus;
- GPU-heavy experimentation loops.

Those should run:

- on schedule;
- on bounded windows;
- with strict concurrency limits;
- preferably outside active site-usage hours.

### 6.3 Recommended answer for v1

Recommended `v1` policy:

- `24/7 observation`: yes, where appropriate
- `24/7 training`: no
- `continuous pattern discovery`: no
- `scheduled bounded retraining/evaluation`: yes

The system should always be learning in the broad sense by collecting new evidence, but it should not always be retraining or mining patterns.

### 6.4 Recommended ongoing-learning policy

The practical answer for a local machine that may run all day is:

- continuous evidence capture;
- continuous candidate detection and score-tape accumulation where enabled;
- incremental feature and label finalization on bounded windows;
- challenger-model retraining only when enough new evidence has accumulated or drift rules fire;
- promotion-grade validation on schedule or on demand, not after every small data update;
- last-approved model remains the active scorer until a challenger clears validation and activation thresholds, at which point the replacement may be automatic if the configured gates allow it.

This allows learning to be ongoing in the operational sense without turning the machine into a permanent brute-force research loop.

## 7. Resource Management and System Safety

### 7.1 Default compute policy

The beta must be conservative by default.

Recommended `v1` defaults:

- CPU-only training
- GPU disabled
- single training worker
- low process priority where possible
- bounded memory usage
- chunked historical processing
- no more than one heavy batch job at a time

### 7.2 CPU guardrails

Recommended guardrails:

- `beta_max_cpu_workers = 1` by default
- `beta_max_concurrent_heavy_jobs = 1` by default
- limit BLAS/OpenMP thread pools in the beta worker process:
  - `OMP_NUM_THREADS=1`
  - `OPENBLAS_NUM_THREADS=1`
  - `MKL_NUM_THREADS=1`
  - `NUMEXPR_NUM_THREADS=1`
- process corpus builds in chunks by symbol/date range instead of loading the entire corpus into memory
- schedule heavy research jobs overnight or user-configured quiet windows
- do not run backtests, training, and full evaluation summaries simultaneously

### 7.3 Provider and market-hours budget guardrails

Recommended `v1` policy:

- reserve a hard safety buffer of `5` provider credits per minute during market hours;
- give first priority to price and FX refresh for the existing core portfolio and any active beta positions;
- allow research polling, backfills, filings, and enrichment to consume only the remaining budget;
- let backfills and heavier syncs run during market hours only when they can yield immediately to live-data needs;
- prefer historical backfill, validation, and heavier enrichment in off-session or quiet hours.

### 7.4 GPU policy

Recommended `v1` policy:

- `beta_gpu_enabled = false`

Reasoning:

- the first strategy families are intended to be interpretable;
- GPU adds operational complexity and resource risk before it adds real value;
- CPU-first makes it easier to control thermals, background load, and predictability on a local machine.

GPU should only be introduced later if:

- the dataset and evaluation regime are already stable;
- the user explicitly enables it;
- the worker has clear GPU budgeting and isolation;
- GPU failure does not affect observation or demo trading.

### 7.5 Memory and I/O guardrails

Recommended rules:

- use incremental builds rather than giant in-memory joins;
- write features and labels in batches;
- avoid loading `10` years of the full US+UK corpus into a single dataframe at once;
- keep beta artifact generation asynchronous or on-demand;
- cap the number of concurrent provider requests;
- pause enrichment before market/FX freshness is compromised.

### 7.5 Backpressure and degradation

If the system is under stress:

1. freeze paid news enrichment;
2. freeze heavy research/training;
3. continue observation and score tape where possible;
4. freeze new demo entries if scoring quality degrades;
5. keep the core site unaffected.

The beta should always fail in a way that protects:

- data integrity first;
- site availability second;
- research freshness third.

## 8. Recommended Code Structure

Create a separate beta package rather than mixing large new logic into the current core services.

Recommended package structure:

- `equity_tracker/src/beta/__init__.py`
- `equity_tracker/src/beta/context.py`
- `equity_tracker/src/beta/runtime_settings.py`
- `equity_tracker/src/beta/engine.py`
- `equity_tracker/src/beta/migration_manager.py`
- `equity_tracker/src/beta/models.py`
- `equity_tracker/src/beta/repository/`
- `equity_tracker/src/beta/jobs/`
- `equity_tracker/src/beta/services/`
- `equity_tracker/src/beta/artifacts/`
- `equity_tracker/src/beta/cli.py`

### 8.1 Recommended service modules

Suggested modules:

- `reference_service.py`
- `market_data_service.py`
- `news_ingestion_service.py`
- `filings_ingestion_service.py`
- `fundamental_snapshot_service.py`
- `signal_candidate_service.py`
- `feature_store_service.py`
- `label_store_service.py`
- `dataset_service.py`
- `validation_service.py`
- `learning_scheduler_service.py`
- `hypothesis_service.py`
- `model_registry_service.py`
- `strategy_registry_service.py`
- `shadow_scoring_service.py`
- `recommendation_service.py`
- `paper_execution_service.py`
- `ledger_service.py`
- `evaluation_service.py`
- `ai_audit_service.py`
- `replay_service.py`
- `backup_service.py`

### 8.2 Existing core services to reuse carefully

Reuse or adapt logic from:

- `DatabaseEngine`
- `TwelveDataPriceService`
- `TwelveDataStreamService`
- `PriceService`
- `FxService`
- `BrokerFeeService`

But do not rewrite those core services in a way that changes current site behavior.

Preferred pattern:

- extract pure helper logic where genuinely shared;
- create beta adapters around existing services;
- keep beta-specific orchestration inside the beta package.

## 9. Database and Migration Plan

### 9.1 Separate beta DB engine

Implement a beta-specific context, not reuse `AppContext` directly.

Reason:

- `AppContext` is currently the singleton for the main site DB;
- the beta needs its own independent DB lifecycle.

Recommended new object:

- `BetaContext`

Responsibilities:

- open/close `beta_research.db`
- expose read/write sessions for beta tables
- remain optional and independent from the main site

### 9.2 Separate migration path

Use a separate beta migration manager and migration directory.

Recommended:

- `equity_tracker/beta_alembic.ini`
- `equity_tracker/beta_alembic/`
- `equity_tracker/src/beta/migration_manager.py`

Reason:

- beta schema churn will be high early on;
- it should not be mixed into the deterministic core migration flow;
- beta migration failure must not block the site.

### 9.3 Initialization rules

Rules:

- the beta supervisor ensures the beta DB is migrated before beta jobs start;
- the web app may read beta status only if the beta DB is available;
- if migrations fail, beta is disabled and the core site keeps running.

## 10. Technical Workstreams

### 10.1 Workstream A: Beta foundation

Build:

- beta package skeleton
- beta context
- runtime settings
- beta CLI
- beta migration path
- beta health/status objects

Deliverables:

- `beta_research.db` can be initialized independently
- beta can be turned fully off
- no beta logic interferes with the main site

### 10.2 Workstream B: Reference domain

Build:

- beta instrument master
- exchange/session metadata
- sector/industry mappings
- benchmark mappings
- research-corpus membership
- live-universe membership

Deliverables:

- research corpus and live universe are defined separately
- soft linkage to `portfolio.db` securities is supported where useful

### 10.3 Workstream C: Historical daily corpus

Build:

- US+UK daily-bar backfill pipeline
- benchmark/sector/FX history backfill
- corporate-action and event history support
- official release and filing capture for approved sources
- narrow point-in-time fundamental snapshots for approved fields only

Leverage:

- existing price-service patterns
- existing Twelve Data and yfinance-style history approaches where suitable

Deliverables:

- broad multi-market daily learning corpus
- reproducible retained-history baseline
- structured catalyst context that improves foresight without requiring a giant alternative-data stack

### 10.4 Workstream D: Prospective observation

Build:

- minute-bar collector for the active live universe
- news collectors
- official release and filing collectors
- source throttling
- ingestion run tracking
- observation health checks

Deliverables:

- live facts arrive continuously
- provider budget stays bounded
- observation can run without training being enabled

### 10.5 Workstream E: Feature store and label store

Build:

- feature definitions
- feature values
- feature lineage
- label definitions
- label values
- label lineage

Deliverables:

- reusable versioned research inputs
- explicit reproducible outcomes

### 10.6 Workstream F: Dataset and experiment registry

Build:

- dataset manifests
- dataset rows and split assignment
- hypothesis registry
- experiment registry
- statistical validation runs and metrics
- model registry
- strategy registry
- promotion decisions

Deliverables:

- frozen datasets
- no notebook-only research
- explicit trial-aware validation records for promotion reviews
- clear promotion chain from hypothesis to model to strategy

### 10.7 Workstream G: Shadow scoring

Build:

- score-run creation
- full-universe score tape persistence
- potential-signal candidate capture for notable opportunities even when they are not promoted or traded
- score explanations
- rejection reasons
- confidence capture

Deliverables:

- live shadow scoring without demo execution
- enough evidence to assess models prospectively

### 10.8 Workstream H: Potential-signal tracking and AI audit

Build:

- signal candidate recorder
- signal candidate evidence tracker
- signal candidate lifecycle events
- review-pack builder for candidate signals, score tape rows, and demo trades
- AI audit run registry
- AI audit findings registry
- verification-oriented audit prompts or rules that read stored evidence instead of free-form summaries

Required design rule:

- AI review is a consumer of persisted evidence, not a substitute for persistence.

Deliverables:

- later queries such as `review our potential gains` can be answered from stored evidence;
- AI can independently verify where a signal came from, what supported it, what was expected, and what actually happened;
- unpromoted or rejected opportunities remain auditable instead of disappearing from the record.

### 10.9 Workstream I: Recommendation and demo-trade lane

Build:

- recommendation gating
- trade intents
- demo positions
- position events
- fills
- ledger entries
- cost attribution

Deliverables:

- immutable prospective paper-trade lane
- full audit trail

### 10.10 Workstream J: Evaluation and replay

Build:

- nightly/weekly evaluation summaries
- calibration reports
- regime scorecards
- rejection analytics
- hypothesis leaderboard snapshots
- promotion-grade validation summaries including purged-fold results and trial-aware diagnostics
- replay bundles
- AI-audit input packs for strategy, signal, and potential-gain reviews

Deliverables:

- clear learning outputs
- explainable promotion/suspension decisions

### 10.11 Workstream K: Controls and UI

Build:

- beta overview page modeled after the current Portfolio page structure
- beta health/status surface
- beta mode controls
- beta watched-opportunities page
- beta paper-trades page
- beta replay/evidence page
- interactive job status and runtime controls
- paper-trade summaries for active and recently closed demo positions
- watched-opportunity summaries for current signal candidates and promoted ideas under observation
- in-app milestone notifications
- daily dashboard summary snapshots
- drill-down links to replay and evidence views
- ability to pause learning
- ability to pause shadow scoring
- ability to pause demo trading
- ability to trigger safe refresh or repair runs
- ability to pause all beta work

Important rule:

- beta controls should be isolated from the main portfolio workflows;
- if absent, the rest of the site still works normally.

### 10.11.1 Recommended beta overview surface

The beta should have one primary read surface that is easy to scan and intentionally familiar.

Recommended route shape:

- `/paper-trading-beta`
  - internal-only overview page

Recommended design direction:

- mirror the current Portfolio page at a layout level, not at a behavioral or data-coupling level;
- reuse the same broad page grammar where useful: page title, state pill, refresh status, top-level actions, summary sections, and live-refresh shell;
- do not reuse portfolio-specific logic, holdings templates, or deterministic guardrail wiring in a way that couples the beta to the core page.

The beta overview page should answer, at a glance:

- what paper trades are currently open;
- what paper trades recently closed and how they performed;
- what opportunities the system is currently watching or has identified as potential signals;
- what the beta mode and health state are;
- whether learning, shadow scoring, and demo execution are currently enabled;
- whether learning quality or live prospective performance is improving or deteriorating;
- what important automatic actions recently occurred.

The page should be intentionally lighter than the main Portfolio page.

Design rule:

- use Portfolio as a familiarity guide, not a complexity target;
- show fewer sections;
- show fewer controls;
- show only the highest-signal columns by default;
- cap each list to a small number of rows with a `view more` path instead of making the landing page endlessly long.

Recommended sections:

- summary strip
  - beta mode
  - observation freshness
  - shadow-scoring freshness
  - count of identified signal candidates
  - count of promoted or auto-promoted candidates
  - count of rejected or dismissed candidates
  - count of active paper positions
  - count of watched opportunities
- active paper positions
  - symbol
  - strategy family
  - entry date/time
  - entry price
  - current mark
  - unrealized P/L
  - target / stop summary
  - confidence band at entry
- recent closed paper trades
  - symbol
  - open date
  - close date
  - net result after estimated costs
  - exit reason
  - replay link
- watched opportunities
  - symbol
  - signal family or candidate name
  - detected time
  - expected direction
  - expected horizon
  - confidence
  - status such as `detected`, `under review`, `promoted`, or `dismissed`
  - optional quick rationale
- recent audit notes and milestone notifications
  - timestamp
  - action type
  - affected symbol, candidate, or strategy
  - short rationale
- system state
  - current strategy versions in shadow/demo use
  - last successful collectors/build jobs
  - warnings when data freshness or enrichment has degraded

Recommended default limits:

- active paper positions: show all if the count is small, otherwise cap at roughly `5` to `10`
- recent closed paper trades: latest `5`
- watched opportunities: top `5` to `10` by priority, confidence, or recency
- recent audit notes: latest `5`
- system warnings: only active issues, not full operational history

### 10.11.2 UI detail philosophy

The default beta UI should not force the user to inspect granular internals just to understand what the system is doing.

Recommended behavior:

- show concise summaries by default;
- expose evidence, replay, score breakdowns, and raw artifacts behind secondary links or drawers;
- keep article-level diagnostics, feature details, and validation metrics available, but not the first thing the user sees.
- avoid multi-screen scrolling on the default landing page;
- prefer one compact overview plus drill-down pages over one giant dashboard.

This matches the goal you stated:

- see paper trades;
- see what the system is looking for;
- keep the overview readable.

### 10.11.3 Implementation rule for reusing the Portfolio page

Use the current Portfolio surface as a reference implementation, not as a shared dependency.

Recommended approach:

- create separate beta templates such as:
  - `equity_tracker/src/api/templates/paper_trading_beta/overview.html`
  - `equity_tracker/src/api/templates/partials/paper_trading_beta_overview_root.html`
- copy only presentation patterns that help with usability:
  - title/action row
  - live refresh shell
  - section cards
  - summary pills
  - filter/sort controls where useful
- keep all beta-specific JS, routes, and partial-refresh endpoints separate from `/portfolio` endpoints;
- do not import portfolio-only assumptions such as holdings-specific filters, tax state, or deterministic guardrail dismissal flows.

This preserves familiarity without creating coupling that could interfere with the normal site.

### 10.11.4 Recommended read-model inputs for the beta overview

The overview page should read from stable beta summary queries or materialized views, not from heavy raw-table aggregation on every page load.

Recommended backing queries or read models:

- active positions summary
  - `beta_demo_positions`
  - latest `beta_demo_position_events`
  - `beta_trade_cost_breakdowns`
- recent closed paper trades summary
  - `beta_demo_positions`
  - `beta_return_attributions`
  - `beta_recommendations`
- watched opportunities summary
  - `beta_signal_candidates`
  - optional latest `beta_score_tape` references
- notifications and audit summary
  - `beta_ui_notifications`
  - latest `beta_event_log` projections where needed
- daily dashboard snapshot summary
  - `beta_ui_summary_snapshots`
- mode and health summary
  - runtime settings
  - job-status snapshots
  - freshness monitors

If needed, add beta-specific summary tables or cached read models later rather than making the page scan the entire score tape or candidate history on demand.

## 11. Scheduling Plan

### 11.1 Always-on or session-bound tasks

Run continuously where relevant:

- minute market collector during supported sessions
- FX refresh
- feed polling
- official release and filing polling
- open-position monitoring
- shadow scoring for active strategies on declared cadence, with `5` minutes as the default unless a strategy version overrides it
- opportunistic historical sync or backfill work only when live data, the site, and the market-hours credit buffer remain protected

### 11.2 Nightly tasks

Run at low-load times:

- feature backfills
- label finalization
- evaluation summaries
- challenger validation runs
- backups
- recent-repair jobs

### 11.3 Weekly or manual tasks

Run less frequently:

- large retraining jobs
- large walk-forward jobs
- broad multiple-testing and promotion-grade validation sweeps
- broad hypothesis refreshes
- leaderboard rebuilds
- archive compaction

### 11.4 Recommended default schedule

Suggested starting schedule:

- minute collection: every minute in-session
- RSS/official news polling: every `5` to `15` minutes
- official release and filing polling: every `15` to `60` minutes
- paid enrichment: event-driven or hourly at most
- feature incremental builds: per scoring cycle
- default shadow scoring cadence: every `5` minutes
- label finalization: nightly
- evaluation summaries: nightly and weekly
- challenger retraining: weekly, on drift trigger, or manual
- promotion-grade validation: weekly, before activation review, or manual
- broad pattern mining: weekly or manual

## 12. Technical Answer to “Should Learning Be 24/7?”

The correct answer is:

- `observation`: yes, as appropriate
- `shadow scoring`: yes, on declared cadence
- `continuous brute-force pattern search`: no
- `continuous full retraining`: no

The system should be constantly collecting evidence, not constantly consuming all available compute.

The practical live-learning model should be:

- the active scorer is stable and approved;
- challengers train slowly in the background on bounded schedules;
- validation is stricter than training;
- validated replacements may activate automatically once configured thresholds are met, with the activation logged and surfaced in the beta UI.

`v1` should treat the machine as a shared environment:

- the site first;
- observation second;
- scoring third;
- heavy learning jobs last.

## 13. Failure Isolation

Failure modes should isolate cleanly:

- if news ingestion fails, market observation and the site continue;
- if model training fails, observation and shadow scoring can continue with the last approved model;
- if shadow scoring fails, observation can still continue;
- if the beta DB fails, the core site still runs;
- if the core site DB is healthy but beta is disabled, the user still has full normal site behavior.

This is a core success criterion.

## 14. Delivery Phases

### Phase 0: Safe foundation

Deliver:

- beta package skeleton
- beta settings and kill switches
- beta DB and migration path
- separate beta supervisor process

Exit criteria:

- beta can be fully disabled with zero effect on core site startup

### Phase 1: Historical learning corpus

Deliver:

- US+UK reference data
- daily corpus ingestion
- benchmark/sector/FX context
- corporate actions and event history
- automatic first-run historical backfill kickoff

Exit criteria:

- broad daily research corpus exists

### Phase 2: Prospective observation

Deliver:

- minute capture for active live universe
- news ingestion
- ingestion-run and freshness monitoring

Exit criteria:

- clean forward research tape starts accumulating

### Phase 3: Feature/label/data foundation

Deliver:

- feature store
- label store
- dataset manifests

Exit criteria:

- reproducible offline research runs become possible

### Phase 4: Research registry and shadow scoring

Deliver:

- hypothesis/model/strategy registry
- full-universe score tape
- rejection logs

Exit criteria:

- prospective shadow evaluation works with no demo trades

### Phase 5: Demo-trade lane

Deliver:

- recommendation gating
- paper execution
- ledger
- attribution

Exit criteria:

- immutable demo-trade prospective testing works

### Phase 6: Evaluation and hardening

Deliver:

- calibration reports
- regime scorecards
- replay packs
- backup and archive jobs
- pause/disable controls

Exit criteria:

- beta can run safely for extended periods without disturbing the site

## 15. Definition of Done for Technical Planning

The technical plan is complete enough when:

- every major runtime lane has a module owner and implementation target;
- every critical dataset has a storage location and lifecycle;
- every heavy task has a schedule and resource budget;
- every beta function can be disabled without affecting normal site operation;
- the distinction between observation, learning, shadow scoring, and demo execution is preserved technically;
- no part of the design requires the main site to absorb research workloads in-process.

## 16. Bottom Line

The right implementation is not:

- “turn on a permanent self-training engine inside the main web app.”

The right implementation is:

- separate beta storage;
- separate beta runtime;
- bounded observation and scoring;
- scheduled, resource-limited learning;
- explicit kill switches;
- a design where the site continues to operate normally even if beta learning is turned off completely.
