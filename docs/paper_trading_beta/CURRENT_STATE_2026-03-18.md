# Paper Trading Beta Current State

Last updated: `2026-03-18`

This document describes the current beta implementation and the live database state that was validated on `2026-03-18`. It replaces the earlier "proposed architecture" documents as the best high-level reference for what the beta actually does now.

## 1. Summary

The paper-trading beta is no longer just a plan. The repo contains:

- a separate beta database and settings boundary
- a detached supervisor process
- server-rendered beta UI routes and templates
- reference, observation, news, filing, feature, label, scoring, training, replay, and intraday-execution services
- a growing research corpus and score/evidence trail

The current beta is operational, but it is not yet producing demo trades. The main blockers are model promotion and recommendation activation rather than raw ingestion.

## 2. Storage And Runtime Boundary

Validated on `2026-03-18` from the live DB:

- Core DB: `data/portfolio.db`
- Live beta DB: `C:\EquityTrackerData\portfolio.beta_research.db`
- Live beta settings: `C:\EquityTrackerData\portfolio.beta_research.db.settings.json`
- Supervisor lock: `data/beta_supervisor.lock`
- Schema version: `v7`

Validated system status row:

- `runtime_mode = FULL_INTERNAL_BETA`
- `enabled = 1`
- `web_ui_enabled = 1`
- `observation_enabled = 1`
- `learning_enabled = 1`
- `shadow_scoring_enabled = 1`
- `demo_execution_enabled = 1`
- `supervisor_status = running`
- `supervisor_pid = 23928`
- `latest_snapshot_date = 2026-03-18`

## 3. Implemented Surface Area

Current implementation is spread across:

- `equity_tracker/src/beta/runtime_manager.py`
  - web-process bootstrap, schema init, detached supervisor start/stop
- `equity_tracker/src/beta/supervisor_process.py`
  - recurring background runtime loop and job scheduling
- `equity_tracker/src/beta/db/models.py`
  - actual `v7` beta schema
- `equity_tracker/src/api/routers/paper_trading_beta.py`
  - server-rendered beta UI
- `equity_tracker/src/api/templates/paper_trading_beta/`
  - overview, opportunities, hypotheses, trades, replay, health, and detail pages
- `equity_tracker/src/beta/services/`
  - reference, corpus, observation, feature, label, training, scoring, evaluation, replay, review, news, filings, intraday execution, and hypothesis services

## 4. Database-Validated State

Validated table counts from `C:\EquityTrackerData\portfolio.beta_research.db` on `2026-03-18`:

- `beta_instruments`: `2249`
- `beta_universe_membership`: `2438`
- `beta_daily_bars`: `1409676`
- `beta_benchmark_bars`: `5037`
- `beta_intraday_snapshots`: `6605`
- `beta_minute_bars`: `13991`
- `beta_feature_values`: `20914160`
- `beta_label_values`: `2023461`
- `beta_hypotheses`: `20`
- `beta_hypothesis_definitions`: `34`
- `beta_signal_candidates`: `78`
- `beta_demo_positions`: `0`
- `beta_execution_signals`: `128`
- `beta_execution_label_values`: `127`
- `beta_job_runs`: `6336`
- `beta_ui_notifications`: `351`
- `beta_ui_summary_snapshots`: `5`

Validated minute-bar source mix:

- `twelvedata_1min_historical`: `9700`
- `twelvedata_1min_eod`: `2891`
- `twelvedata_1min_live`: `800`
- legacy snapshot-derived sources remain in smaller counts from earlier ingestion phases

Validated execution-monitoring state:

- all `128` recorded execution signals currently belong to `IBM`
- signal types are `NO_ACTION` (`122`) and `WAIT_FOR_CLOSE_CONFIRMATION` (`6`)
- recent signals show the intraday execution layer is live and writing rationales

Validated research/training state:

- `61` model versions exist with `status = CHALLENGER`
- `0` model versions are active
- `62` strategy versions exist and all are inactive
- recent shadow-cycle rows show `active_model_version = null`, `active_strategy_version = null`, `recommended = 0`, and `positions_opened = 0`

## 5. What The Current Beta Is Actually Doing

As of `2026-03-18`, the live beta is successfully:

- maintaining a separate schema and runtime settings surface
- running the detached supervisor and recording heartbeats
- ingesting a large daily research corpus
- ingesting and backfilling minute bars
- building features and labels
- running hypothesis discovery and belief refresh jobs
- training challenger models
- recording shadow-cycle decisions and intraday execution signals
- generating replay/dashboard snapshots and UI notifications

As of `2026-03-18`, the live beta is not yet successfully:

- promoting a model into the active scoring path
- generating accepted recommendations in current shadow cycles
- opening any demo positions despite demo mode being enabled

## 6. What Has Drifted From The Older Docs

The older design docs in this folder have materially drifted from the implementation:

- the runtime architecture is implemented, not merely proposed
- the current schema is the real `v7` ORM schema, not the much broader proposed schema document
- the beta UI and detached supervisor are live and should now be documented as implemented surfaces
- generated benchmark files are evidence artifacts, not architecture docs

## 7. Recommended Doc Usage

For current work:

1. Read this document first
2. Read `REVIEW_2026-03-18.md` next
3. Use `CHANGES_2026-03-16.md` and the benchmark files as time-stamped evidence
4. Treat the March 14-15 design docs as historical context only
