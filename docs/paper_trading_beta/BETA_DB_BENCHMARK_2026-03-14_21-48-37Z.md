# Beta DB Benchmark

Snapshot time: `2026-03-14 21:48:37 +00:00`

DB path: `D:\EquityTrackerData\portfolio.beta_research.db`

DB file size:
- `4,201,799,680` bytes
- `4007.15 MB`
- `3.9132 GB`

Runtime status:
- `supervisor_status = running`
- `supervisor_pid = 13476`
- `last_heartbeat_at = 2026-03-14 21:35:48.965464`
- `latest_snapshot_date = 2026-03-14`

## Corpus And Universe

- instruments: `1151`
- open memberships: `444`
- active memberships: `21`
- seed memberships: `423`
- daily bars: `527669`
- intraday snapshots: `5274`
- feature values: `9665036`
- label values: `935073`
- score tape rows: `3958`
- signal candidates: `78`
- research ranking rows: `3321`
- pipeline snapshots: `159`

Latest data dates:
- latest daily bar date: `2026-03-13`
- latest intraday observation: `2026-03-14 21:31:28.029597`
- latest feature date: `2026-03-13`
- latest label date: `2026-03-06`
- latest score row: `2026-03-14 21:40:04.231072`
- latest ranking row: `2026-03-14 21:10:44.728503`

## Hypothesis Engine

- hypothesis families: `3`
- hypothesis definitions: `6`
- hypothesis test runs: `42`
- hypothesis belief states: `6`
- signal observations: `461`
- recommendation decisions: `422`

Belief status breakdown:
- `DEGRADED = 4`
- `CANDIDATE = 2`

Top current beliefs:
- `TREND_PULLBACK_RECOVERY_V2` | `DEGRADED` | confidence `0.2110` | evidence `1` | OOS `1.5880`
- `TREND_PULLBACK_RECOVERY_V1` | `DEGRADED` | confidence `0.2004` | evidence `1` | OOS `0.1978`
- `MEAN_REVERSION_BOUNCE_V1` | `DEGRADED` | confidence `0.1974` | evidence `1` | OOS `-0.6056`
- `CATALYST_CONFIRMATION_NEGATIVE_V1` | `CANDIDATE` | confidence `0.1875` | evidence `1` | OOS `0.0000`
- `CATALYST_CONFIRMATION_POSITIVE_V1` | `CANDIDATE` | confidence `0.1875` | evidence `1` | OOS `0.0000`
- `MEAN_REVERSION_BREAKDOWN_V1` | `DEGRADED` | confidence `0.1375` | evidence `1` | OOS `-58.0139`

Latest hypothesis/test timestamps:
- latest test run: `2026-03-14 21:15:04.328874`
- latest signal observation: `2026-03-14 21:40:04.231072`
- latest recommendation decision: `2026-03-14 21:40:04.231072`
- latest training decision: `2026-03-14 21:34:59.970946`

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `BLOCKED = 399`
- `MATCHED = 39`
- `DISMISSED = 23`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 461`

Recommendation status breakdown, last 24 hours:
- `BLOCKED / hypothesis_degraded = 369`
- `BLOCKED / hypothesis_not_validated = 30`
- `DISMISSED / belief_insufficient = 13`
- `DISMISSED / direction_mismatch = 10`

## News And Filings

- news articles: `20`
- news article links: `5`
- filing events: `12`
- latest news timestamp: `2026-03-14 03:49:14.000000`
- latest filing timestamp: `2026-03-13 14:02:43.000000`

## Model And Training Governance

- model versions: `19`
- validation runs: `19`
- training decisions: `33`

## Latest Successful Jobs

- `beta_live_evaluation` at `2026-03-14 21:40:34.304545`
- `beta_daily_shadow_cycle` at `2026-03-14 21:40:04.602273`
- `beta_tracked_core_shadow_cycle` at `2026-03-14 21:39:31.026835`
- `beta_research_universe_refresh` at `2026-03-14 21:39:29.687765`
- `beta_label_backlog_build` at `2026-03-14 21:39:26.129833`
- `beta_feature_backlog_build` at `2026-03-14 21:39:04.341300`
- `beta_tracked_core_label_build` at `2026-03-14 21:36:51.571001`
- `beta_tracked_core_feature_build` at `2026-03-14 21:36:36.494855`
- `beta_daily_observation_sync` at `2026-03-14 21:36:12.628497`
- `beta_daily_replay_pack` at `2026-03-14 21:35:06.570471`

## Comparison Anchors For Tomorrow

The most useful fields to compare first are:
- DB file size
- instruments
- open memberships
- daily bars
- feature values
- label values
- score tape
- hypothesis test runs
- signal observations
- recommendation decisions
- latest daily bar date
- latest label date
- belief status breakdown
- recommendation status breakdown
