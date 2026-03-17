# Beta DB Benchmark

Snapshot time: `2026-03-14 23:48:43 +00:00`

DB path: `D:\EquityTrackerData\portfolio.beta_research.db`

DB file size:
- `4,579,733,504` bytes
- `4367.57 MB`
- `4.2652 GB`

## Corpus And Universe

- instruments: `1151`
- open memberships: `444`
- active memberships: `21`
- seed memberships: `423`
- daily bars: `589420`
- intraday snapshots: `5282`
- feature values: `10133858`
- label values: `980433`
- score tape rows: `4629`
- signal candidates: `78`
- research ranking rows: `3321`
- pipeline snapshots: `161`

Latest data dates:
- latest daily bar date: `2026-03-13`
- latest intraday observation: `2026-03-14 22:53:41.845809`
- latest feature date: `2026-03-13`
- latest label date: `2026-03-06`
- latest score row: `2026-03-14 23:05:20.936618`
- latest ranking row: `2026-03-14 21:10:44.728503`

## Hypothesis Engine

- hypothesis families: `3`
- hypothesis definitions: `6`
- hypothesis test runs: `48`
- hypothesis belief states: `6`
- signal observations: `553`
- recommendation decisions: `508`

Belief status breakdown:
- `DEGRADED = 4`
- `CANDIDATE = 2`

Top current beliefs:
- `CATALYST_CONFIRMATION_NEGATIVE_V1` | `CANDIDATE` | confidence `0.1875` | evidence `1` | OOS `0.0000`
- `CATALYST_CONFIRMATION_POSITIVE_V1` | `CANDIDATE` | confidence `0.1875` | evidence `1` | OOS `0.0000`
- `MEAN_REVERSION_BOUNCE_V1` | `DEGRADED` | confidence `0.1375` | evidence `1` | OOS `-0.0587`
- `MEAN_REVERSION_BREAKDOWN_V1` | `DEGRADED` | confidence `0.1375` | evidence `1` | OOS `-70.5292`
- `TREND_PULLBACK_RECOVERY_V1` | `DEGRADED` | confidence `0.1375` | evidence `1` | OOS `0.0983`
- `TREND_PULLBACK_RECOVERY_V2` | `DEGRADED` | confidence `0.1375` | evidence `1` | OOS `1.5788`

Latest hypothesis/test timestamps:
- latest test run: `2026-03-14 23:05:10.058386`
- latest signal observation: `2026-03-14 23:05:20.732263`
- latest recommendation decision: `2026-03-14 23:05:20.732263`
- latest training decision: `2026-03-14 22:53:10.564605`

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `BLOCKED = 483`
- `MATCHED = 45`
- `DISMISSED = 25`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 553`

Recommendation status breakdown, last 24 hours:
- `BLOCKED / hypothesis_degraded = 453`
- `BLOCKED / hypothesis_not_validated = 30`
- `DISMISSED / belief_insufficient = 15`
- `DISMISSED / direction_mismatch = 10`

## News And Filings

- news articles: `20`
- news article links: `5`
- filing events: `12`
- latest news timestamp: `2026-03-14 03:49:14.000000`
- latest filing timestamp: `2026-03-13 14:02:43.000000`

## Model And Training Governance

- model versions: `21`
- validation runs: `21`
- training decisions: `35`

## Latest Successful Jobs

- `beta_live_evaluation` at `2026-03-14 23:05:34.675412`
- `beta_daily_shadow_cycle` at `2026-03-14 23:05:22.648234`
- `beta_tracked_core_shadow_cycle` at `2026-03-14 23:05:12.650110`
- `beta_hypothesis_refresh` at `2026-03-14 23:05:11.692063`
- `beta_hypothesis_belief_refresh` at `2026-03-14 23:05:11.331221`
- `beta_hypothesis_backtests` at `2026-03-14 23:05:10.938932`
- `beta_hypothesis_definition_seed` at `2026-03-14 22:58:34.692810`
- `beta_research_universe_refresh` at `2026-03-14 22:58:33.101670`
- `beta_label_backlog_build` at `2026-03-14 22:58:30.527081`
- `beta_feature_backlog_build` at `2026-03-14 22:58:02.983616`

## Stop State

The server and beta supervisor were stopped after this benchmark.

Persisted beta status was updated at `2026-03-14 23:50:19.596183` to:
- `supervisor_status = stopped`
- `supervisor_pid = null`

Verification after stop:
- no `run_api.py` process
- no `src.beta.supervisor_process` process
- `http://127.0.0.1:8000/admin/status` no longer responds

## Comparison Anchors

The most useful fields to compare tomorrow are:
- DB file size
- daily bars
- intraday snapshots
- feature values
- label values
- score tape
- hypothesis test runs
- signal observations
- recommendation decisions
- latest score row time
- latest test run time
- belief status breakdown
- recommendation status breakdown
