# Beta DB Benchmark

Snapshot time: `2026-03-15 16:22:22 +00:00`

DB path: `D:\EquityTrackerData\portfolio.beta_research.db`

DB file size:
- `5,973,245,952` bytes
- `5696.53 MB`
- `5.5630 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `17492`
- last heartbeat at snapshot: `2026-03-15 14:30:53.793600`
- status row last updated at snapshot: `2026-03-15 14:30:53.793600`

## Corpus And Universe

- instruments: `1477`
- open memberships: `518`
- active memberships: `21`
- seed memberships: `497`
- daily bars: `842004`
- intraday snapshots: `5364`
- minute bars: `92`
- intraday feature snapshots: `1`
- position states: `0`
- feature values: `12009146`
- label values: `1161873`
- score tape rows: `7888`
- signal candidates: `78`
- research ranking rows: `4951`
- pipeline snapshots: `167`

Latest data dates:
- latest daily bar date: `2026-03-13`
- latest intraday observation: `2026-03-15 14:23:06.261669`
- latest minute bar: `2026-03-15 14:23:00.000000`
- latest intraday feature snapshot: `2026-03-15 14:31:00.922674`
- latest feature date: `2026-03-13`
- latest label date: `2026-03-06`
- latest score row: `2026-03-15 14:34:57.957783`
- latest ranking row: `2026-03-15 11:08:37.391022`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `29`
- hypothesis templates: `9`
- hypothesis discovery runs: `6`
- hypothesis discovery candidates: `810`
- hypothesis test runs: `216`
- hypothesis belief states: `29`
- signal observations: `4851`
- recommendation decisions: `1863`

Belief status breakdown:
- `DEGRADED = 21`
- `CANDIDATE = 8`

## Execution Layer

- execution hypothesis definitions: `0`
- execution signals: `0`
- execution label values: `0`

The execution-layer schema is present, but no held-position execution activity had been persisted yet.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 2988`
- `BLOCKED = 1466`
- `DISMISSED = 397`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 4851`

Recommendation status breakdown, last 24 hours:
- `BLOCKED = 1466`
- `DISMISSED = 397`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_degraded = 1436`
- `belief_insufficient = 387`
- `hypothesis_not_validated = 30`
- `direction_mismatch = 10`

## News And Filings

- news articles: `31`
- news article links: `13`
- filing events: `12`
- latest news timestamp: `2026-03-15 11:42:38.000000`
- latest filing timestamp: `2026-03-13 14:02:43.000000`

## Model And Training Governance

- model versions: `27`
- validation runs: `27`
- training decisions: `41`

## Latest Successful Jobs Before Shutdown

- `beta_live_evaluation` at `2026-03-15 14:35:16.747385`
- `beta_daily_shadow_cycle` at `2026-03-15 14:34:58.942470`
- `beta_tracked_core_shadow_cycle` at `2026-03-15 14:34:47.555406`
- `beta_research_universe_refresh` at `2026-03-15 14:34:46.555346`
- `beta_label_backlog_build` at `2026-03-15 14:34:43.430383`
- `beta_feature_backlog_build` at `2026-03-15 14:34:11.783179`
- `beta_tracked_core_label_build` at `2026-03-15 14:32:21.620580`
- `beta_tracked_core_feature_build` at `2026-03-15 14:31:55.074428`
- `beta_daily_observation_sync` at `2026-03-15 14:31:21.248081`
- `beta_execution_outcomes` at `2026-03-15 14:31:01.438297`
- `beta_intraday_execution_signals` at `2026-03-15 14:31:01.250789`
- `beta_intraday_execution_prepare` at `2026-03-15 14:31:01.079241`
- `beta_filing_sync` at `2026-03-15 14:30:58.806919`
- `beta_news_sync` at `2026-03-15 14:30:56.418288`
- `beta_daily_replay_pack` at `2026-03-15 14:30:07.671605`

## Stop State

The server and beta supervisor were stopped after this benchmark.

Persisted beta status was updated at `2026-03-15 16:24:20.692857` to:
- `supervisor_status = stopped`
- `supervisor_pid = null`

Verification after stop:
- no `run_api.py` process
- no `src.beta.supervisor_process` process
- `http://127.0.0.1:8000/admin/status` no longer responds

## Comparison Anchors

The most useful fields to compare next time are:
- DB file size
- instruments
- open memberships
- daily bars
- minute bars
- intraday feature snapshots
- feature values
- label values
- score tape
- hypothesis discovery candidates
- hypothesis test runs
- signal observations
- recommendation decisions
- belief status breakdown
- latest score row time
- latest successful job times
