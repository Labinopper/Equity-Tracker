# Beta DB Benchmark

Snapshot time: `2026-03-17 18:13:52 +00:00`

DB path: `C:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-16_13-04-56Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-16_13-04-56Z.md)

DB file size:
- `7,407,095,808` bytes
- `7063.96 MB`
- `6.8984 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `28548`
- last heartbeat at snapshot: `2026-03-17 18:09:46.705331`
- status row last updated at snapshot: `2026-03-17 18:09:46.706334`

Note: the supervisor was restarted multiple times during this window. The most recent bootstrap sequence completed at `2026-03-17 16:23:57`, with earlier restarts at `16:18` and `16:19`. A series of restarts also occurred around `12:52–12:56`.

## Corpus And Universe

- instruments: `2102`
- open memberships: `668`
- active memberships: `21`
- seed memberships: `647`
- daily bars: `1102316`
- intraday snapshots: `6114`
- minute bars: `12532`
- intraday feature snapshots: `8`
- position states: `1`
- feature values: `13884465`
- label values: `1343316`
- score tape rows: `12228`
- signal candidates: `78`
- research ranking rows: `8884`
- pipeline snapshots: `3351`

Latest data dates:
- latest daily bar date: `2026-03-16`
- latest intraday observation: `2026-03-17 16:25:01.766534`
- latest minute bar: `2026-03-17 16:25:00.000000`
- latest intraday feature snapshot: `2026-03-17 13:32:41.031762`
- latest feature date: `2026-03-16`
- latest label date: `2026-03-09`
- latest score row: `2026-03-17 16:25:21.848002`
- latest ranking row: `2026-03-17 02:59:40.090928`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `32`
- hypothesis templates: `9`
- hypothesis discovery runs: `11`
- hypothesis discovery candidates: `1485`
- hypothesis test runs: `371`
- hypothesis belief states: `32`
- signal observations: `9938`
- recommendation decisions: `3456`

Belief status breakdown:
- `DEGRADED = 21`
- `CANDIDATE = 11`
- `PROMISING = 0`
- `VALIDATED = 0`

## Execution Layer

- execution hypothesis definitions: `5`
- execution signals: `53`
- execution label values: `52`

The execution layer is now actively producing signals and label values, a significant change from the previous benchmark where both were zero.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 924`
- `BLOCKED = 322`
- `DISMISSED = 78`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 1326`

Recommendation status breakdown, last 24 hours:
- `BLOCKED = 322`
- `DISMISSED = 78`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_degraded = 322`
- `belief_insufficient = 78`

## News And Filings

- news articles: `67`
- news article links: `50`
- filing events: `15`
- latest news timestamp: `2026-03-17 12:21:55.000000`
- latest filing timestamp: `2026-03-16 17:15:29.000000`

## Model And Training Governance

- model versions: `32`
- validation runs: `32`
- training decisions: `46`

## Latest Successful Jobs At Snapshot

- `beta_tracked_core_shadow_cycle` at `2026-03-17 16:25:21.859514`
- `beta_execution_outcomes` at `2026-03-17 16:25:21.765997`
- `beta_intraday_execution_signals` at `2026-03-17 16:25:21.585429`
- `beta_intraday_execution_prepare` at `2026-03-17 16:25:21.468285`
- `beta_filing_sync` at `2026-03-17 16:25:19.885738`
- `beta_news_sync` at `2026-03-17 16:25:18.131928`
- `beta_supervisor_bootstrap` at `2026-03-17 16:23:58.623470`
- `beta_filing_source_seed` at `2026-03-17 16:23:57.231508`
- `beta_news_source_seed` at `2026-03-17 16:23:57.215509`
- `beta_hypothesis_definition_seed` at `2026-03-17 16:23:57.200510`
- `beta_hypothesis_seed` at `2026-03-17 16:23:57.158422`
- `beta_runtime_bootstrap` at `2026-03-17 16:23:57.141424`
- `beta_daily_replay_pack` at `2026-03-17 12:55:28.942210`
- `beta_live_evaluation` at `2026-03-17 03:04:52.672987`
- `beta_daily_shadow_cycle` at `2026-03-17 03:04:29.271678`
- `beta_research_universe_refresh` at `2026-03-17 03:04:05.010735`
- `beta_label_backlog_build` at `2026-03-17 03:04:00.688460`
- `beta_feature_backlog_build` at `2026-03-17 03:03:16.450392`
- `beta_tracked_core_label_build` at `2026-03-17 03:02:08.649668`
- `beta_tracked_core_feature_build` at `2026-03-17 03:01:32.782410`
- `beta_daily_observation_sync` at `2026-03-17 03:00:37.915607`
- `beta_learning_universe_sync` at `2026-03-17 02:59:45.844397`
- `beta_hypothesis_refresh` at `2026-03-17 02:57:35.772614`
- `beta_hypothesis_belief_refresh` at `2026-03-17 02:57:35.440002`
- `beta_hypothesis_backtests` at `2026-03-17 02:57:34.027692`
- `beta_hypothesis_discovery` at `2026-03-17 02:45:59.322012`
- `beta_daily_potential_gains_review` at `2026-03-17 02:40:44.433537`
- `beta_daily_training` at `2026-03-17 02:40:43.377548`
- `beta_instrument_statistics_refresh` at `2026-03-16 20:26:06.392677`
- `beta_intraday_bar_backfill` at `2026-03-16 20:00:35.562910`
- `beta_eod_bar_fetch` at `2026-03-16 20:00:22.694012`

## Comparison Vs 2026-03-16 13:04:56 +00:00

Key deltas:
- DB file size: `+351,682,560` bytes (`+0.3275 GB`)
- instruments: `+212`
- open memberships: `+50`
- seed memberships: `+50`
- daily bars: `+69,253`
- intraday snapshots: `+618`
- minute bars: `+12,298`
- intraday feature snapshots: `+7`
- feature values: `+468,853`
- label values: `+45,363`
- score tape rows: `+1,247`
- research ranking rows: `+1,411`
- pipeline snapshots: `+2,632`
- signal observations: `+1,444`
- recommendation decisions: `+460`
- news articles: `+21`
- news article links: `+24`
- filing events: `+2`
- model versions: `+1`
- validation runs: `+1`
- training decisions: `+1`
- hypothesis discovery runs: `+1`
- hypothesis discovery candidates: `+135`
- hypothesis test runs: `+32`
- execution signals: `+53`
- execution label values: `+52`

No change:
- active memberships, signal candidates
- hypothesis engine (families, definitions, templates, belief states)
- position states

Belief-state deltas:
- `CANDIDATE: 11 -> 11`
- `DEGRADED: 21 -> 21`
- `PROMISING: 0 -> 0`
- `VALIDATED: 0 -> 0`

Interpretation:
- this is a ~29-hour window covering a full overnight daily cycle plus an intraday session
- the DB has grown by ~328 MB, driven primarily by the overnight batch: +69K daily bars, +469K feature values, +45K label values from the daily replay pack and feature/label build jobs
- the instrument universe expanded by 212 instruments (1890 -> 2102) and 50 new seed memberships were added via the `beta_learning_universe_sync` and `beta_research_universe_refresh` jobs
- the hypothesis engine ran a new discovery cycle (+1 run, +135 candidates) and completed 32 additional test runs, though belief states remain unchanged — all 32 hypotheses are still DEGRADED (21) or CANDIDATE (11)
- the execution layer is now active: 53 execution signals and 52 execution label values have been persisted, up from zero in the previous benchmark — this indicates the intraday execution pipeline is producing outputs
- a new model version was trained (model versions 31 -> 32, validation runs 31 -> 32, training decisions 45 -> 46), showing the ML governance loop is cycling
- the supervisor experienced multiple restarts today (around 12:52–12:56 and 16:18–16:24), but recovered cleanly each time with the full seed/bootstrap sequence completing successfully
- intraday ingestion is healthy: +618 snapshots, +12K minute bars, +7 intraday feature snapshots
- news and filing ingestion continued normally with +21 articles and +2 filing events
- no validated hypotheses yet — all belief states remain at DEGRADED or CANDIDATE
