# Beta DB Benchmark

Snapshot time: `2026-03-16 13:04:56 +00:00`

DB path: `D:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-16_11-31-33Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-16_11-31-33Z.md)

DB file size:
- `7,055,413,248` bytes
- `6728.57 MB`
- `6.5709 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `10996`
- last heartbeat at snapshot: `2026-03-16 13:04:52.118529`
- status row last updated at snapshot: `2026-03-16 13:04:52.118529`

Note: the supervisor was restarted during this window. A shutdown was recorded at `2026-03-16 12:54:39` and the runtime bootstrapped again at `2026-03-16 13:01:27`.

## Corpus And Universe

- instruments: `1890`
- open memberships: `618`
- active memberships: `21`
- seed memberships: `597`
- daily bars: `1033063`
- intraday snapshots: `5496`
- minute bars: `234`
- intraday feature snapshots: `1`
- position states: `1`
- feature values: `13415612`
- label values: `1297953`
- score tape rows: `10981`
- signal candidates: `78`
- research ranking rows: `7473`
- pipeline snapshots: `719`

Latest data dates:
- latest daily bar date: `2026-03-13`
- latest intraday observation: `2026-03-16 12:57:41.904386`
- latest minute bar: `2026-03-16 12:57:00.000000`
- latest intraday feature snapshot: `2026-03-16 13:01:51.149405`
- latest feature date: `2026-03-13`
- latest label date: `2026-03-06`
- latest score row: `2026-03-16 13:01:52.003529`
- latest ranking row: `2026-03-16 01:00:56.241571`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `32`
- hypothesis templates: `9`
- hypothesis discovery runs: `10`
- hypothesis discovery candidates: `1350`
- hypothesis test runs: `339`
- hypothesis belief states: `32`
- signal observations: `8494`
- recommendation decisions: `2996`

Belief status breakdown:
- `DEGRADED = 21`
- `CANDIDATE = 11`
- `PROMISING = 0`
- `VALIDATED = 0`

## Execution Layer

- execution hypothesis definitions: `5`
- execution signals: `0`
- execution label values: `0`

The execution layer remains seeded with the held-position bridge active. No live execution signals have been persisted.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 2898`
- `BLOCKED = 1039`
- `DISMISSED = 269`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 4206`

Recommendation status breakdown, last 24 hours:
- `BLOCKED = 1039`
- `DISMISSED = 269`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_degraded = 1039`
- `belief_insufficient = 269`

## News And Filings

- news articles: `46`
- news article links: `26`
- filing events: `13`
- latest news timestamp: `2026-03-16 12:43:32.000000`
- latest filing timestamp: `2026-03-16 10:22:48.000000`

## Model And Training Governance

- model versions: `31`
- validation runs: `31`
- training decisions: `45`

## Latest Successful Jobs At Snapshot

- `beta_execution_outcomes` at `2026-03-16 13:05:10.537295`
- `beta_intraday_execution_signals` at `2026-03-16 13:05:10.395908`
- `beta_intraday_execution_prepare` at `2026-03-16 13:05:10.221985`
- `beta_tracked_core_shadow_cycle` at `2026-03-16 13:01:52.234551`
- `beta_filing_sync` at `2026-03-16 13:01:49.422857`
- `beta_news_sync` at `2026-03-16 13:01:47.354473`
- `beta_supervisor_bootstrap` at `2026-03-16 13:01:29.254239`
- `beta_filing_source_seed` at `2026-03-16 13:01:27.987292`
- `beta_news_source_seed` at `2026-03-16 13:01:27.841233`
- `beta_hypothesis_definition_seed` at `2026-03-16 13:01:27.668716`
- `beta_hypothesis_seed` at `2026-03-16 13:01:27.466440`
- `beta_runtime_bootstrap` at `2026-03-16 13:01:27.273955`
- `beta_supervisor_shutdown` at `2026-03-16 12:54:39.141783`
- `beta_daily_replay_pack` at `2026-03-16 12:47:36.149104`

## Comparison Vs 2026-03-16 11:31:33 +00:00

Key deltas:
- DB file size: `+1,794,048` bytes (`+0.0017 GB`)
- intraday snapshots: `+8`
- minute bars: `+18`
- score tape rows: `+18`
- pipeline snapshots: `+225`
- signal observations: `+36`
- recommendation decisions: `+18`
- news articles: `+4`
- news article links: `+3`
- filing events: `+1`

No change:
- instruments, memberships, daily bars, feature values, label values, signal candidates, research ranking rows
- hypothesis engine (families, definitions, templates, discovery runs/candidates, test runs, belief states)
- model versions, validation runs, training decisions
- execution layer (hypothesis definitions, signals, label values)

Belief-state deltas:
- `CANDIDATE: 11 -> 11`
- `DEGRADED: 21 -> 21`
- `PROMISING: 0 -> 0`
- `VALIDATED: 0 -> 0`

Interpretation:
- this is a short ~1.5-hour window snapshot; corpus accumulation is minimal as expected mid-session
- the supervisor was restarted during this window (shutdown at 12:54, back up at 13:01), which accounts for the large pipeline snapshot delta (+225 rows) and the full seed/bootstrap job sequence rerunning
- intraday execution jobs are cycling normally post-restart
- the filing layer gained one new event and news gained four articles, reflecting active market-hours ingestion
- the hypothesis engine and ML governance layers are unchanged — no new discovery runs or model training occurred in this window, which is consistent with an intraday-only profile
- no validated hypotheses or live execution signals yet
