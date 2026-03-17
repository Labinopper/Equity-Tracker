# Beta DB Benchmark

Snapshot time: `2026-03-15 10:53:04 +00:00`

DB path: `D:\EquityTrackerData\portfolio.beta_research.db`

DB file size:
- `5,673,091,072` bytes
- `5410.28 MB`
- `5.2835 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `16132`
- last heartbeat at snapshot: `2026-03-15 10:11:59.386085`
- status row last updated at snapshot: `2026-03-15 10:11:59.386085`

## Corpus And Universe

- instruments: `1384`
- open memberships: `493`
- active memberships: `21`
- seed memberships: `472`
- daily bars: `788259`
- intraday snapshots: `5341`
- minute bars: `69`
- intraday feature snapshots: `1`
- position states: `0`
- feature values: `11540324`
- label values: `1116513`
- score tape rows: `6987`
- signal candidates: `78`
- research ranking rows: `4383`
- pipeline snapshots: `166`

Latest data dates:
- latest daily bar date: `2026-03-13`
- latest intraday observation: `2026-03-15 10:09:24.337295`
- latest minute bar: `2026-03-15 10:09:00.000000`
- latest intraday feature snapshot: `2026-03-15 10:12:09.828052`
- latest feature date: `2026-03-13`
- latest label date: `2026-03-06`
- latest score row: `2026-03-15 10:16:34.876470`
- latest ranking row: `2026-03-15 07:37:39.195199`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `29`
- hypothesis templates: `9`
- hypothesis discovery runs: `5`
- hypothesis discovery candidates: `675`
- hypothesis test runs: `187`
- hypothesis belief states: `29`
- signal observations: `3731`
- recommendation decisions: `1515`

Belief status breakdown:
- `DEGRADED = 21`
- `CANDIDATE = 8`

## Execution Layer

- execution hypothesis definitions: `0`
- execution signals: `0`
- execution label values: `0`

This means the intraday execution schema is present, but no held-position execution activity had been persisted yet.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 2216`
- `BLOCKED = 1191`
- `DISMISSED = 324`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 3731`

Recommendation status breakdown, last 24 hours:
- `BLOCKED = 1191`
- `DISMISSED = 324`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_degraded = 1161`
- `belief_insufficient = 314`
- `hypothesis_not_validated = 30`
- `direction_mismatch = 10`

## News And Filings

- news articles: `27`
- news article links: `10`
- filing events: `12`
- latest news timestamp: `2026-03-15 00:51:10.000000`
- latest filing timestamp: `2026-03-13 14:02:43.000000`

## Model And Training Governance

- model versions: `26`
- validation runs: `26`
- training decisions: `40`

## Latest Successful Jobs Before Shutdown

- `beta_live_evaluation` at `2026-03-15 10:16:53.960441`
- `beta_daily_shadow_cycle` at `2026-03-15 10:16:36.899803`
- `beta_tracked_core_shadow_cycle` at `2026-03-15 10:16:25.016828`
- `beta_research_universe_refresh` at `2026-03-15 10:16:22.660816`
- `beta_label_backlog_build` at `2026-03-15 10:16:18.357055`
- `beta_feature_backlog_build` at `2026-03-15 10:15:43.946241`
- `beta_tracked_core_label_build` at `2026-03-15 10:13:52.396193`
- `beta_tracked_core_feature_build` at `2026-03-15 10:13:27.323340`
- `beta_daily_observation_sync` at `2026-03-15 10:12:42.492267`
- `beta_execution_outcomes` at `2026-03-15 10:12:10.301465`
- `beta_intraday_execution_signals` at `2026-03-15 10:12:10.138905`
- `beta_intraday_execution_prepare` at `2026-03-15 10:12:09.969499`
- `beta_filing_sync` at `2026-03-15 10:12:07.523859`
- `beta_news_sync` at `2026-03-15 10:12:04.183898`
- `beta_daily_replay_pack` at `2026-03-15 10:11:16.845315`

## Stop State

The server and beta supervisor were stopped after this benchmark.

Persisted beta status was updated at `2026-03-15 10:53:24.917632` to:
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
