# Beta DB Benchmark

Snapshot time: `2026-03-30 09:36:35 +00:00`

DB path: `C:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-19_11-39-39Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-19_11-39-39Z.md)

DB file size:
- `19,386,118,144` bytes
- `18,488.04 MB`
- `18.0547 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `21172`
- last heartbeat at snapshot: `2026-03-30 09:31:22.021702`
- status row last updated at snapshot: `2026-03-30 09:31:22.021702`

Note: the supervisor is alive but the runtime is not healthy. The last successful heavy jobs completed on `2026-03-22 22:32:44`, after which repeated `beta_supervisor_cycle_error` failures were logged because `beta_storage_retention` tried to persist a `datetime` into JSON job details. In the last 7 days the runtime also recorded `1,701` `beta_memory_guard` skips, all accompanied by `WARNING` UI notifications, so heartbeat alone is overstating actual progress.

## Corpus And Universe

- instruments: `5,124`
- open memberships: `0`
- active memberships: `21`
- seed memberships: `1,420`
- daily bars: `1,627,197`
- intraday snapshots: `6,778`
- minute bars: `288,420`
- intraday feature snapshots: `87`
- position states: `33`
- feature values: `27,991,632`
- label values: `3,692,988`
- score tape rows: `47,411`
- signal candidates: `115`
- research ranking rows: `43,485`
- pipeline snapshots: `23,678`

Latest data dates:
- latest daily bar date: `2026-03-18`
- latest intraday observation: `2026-03-22 20:05:00.000000`
- latest minute bar: `2026-03-22 20:05:00.000000`
- latest intraday feature snapshot: `2026-03-20 16:31:53.196227`
- latest feature date: `2026-03-18`
- latest label date: `2026-03-13`
- latest score row: `2026-03-20 19:54:28.317391`
- latest ranking row: `2026-03-22 20:12:06.777628`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `34`
- hypothesis templates: `12`
- hypothesis discovery runs: `30`
- hypothesis discovery candidates: `4,266`
- hypothesis test runs: `1,076`
- hypothesis belief states: `34`
- signal observations: `44,956`
- recommendation decisions: `15,062`

Belief status breakdown:
- `REJECTED = 31`
- `DEGRADED = 3`
- `CANDIDATE = 0`
- `PROMISING = 0`
- `VALIDATED = 0`

## Execution Layer

- execution hypothesis definitions: `22`
- execution signals: `496`
- execution label values: `495`

The execution layer is materially larger than in the March 19 benchmark, but it is operationally frozen at snapshot. There were `0` execution signals in the last 24 hours, `0` demo positions, `0` open simulated positions, and `0` active signal candidates. Simulated trade evidence remains entirely historical-backfill: `13` closed trades total, `0` `LIVE_FORWARD`, with `2` closed `LONG` trades averaging `+0.5301%` post-cost and `11` closed `SHORT` trades averaging only `+0.0272%` post-cost. The short book is split sharply by state: `OPEN__GAP_DOWN_RECOVERY` is negative (`9` trades, `-0.1190%`, `44.44%` wins) while `MIDDAY__GAP_DOWN_RECOVERY` remains positive (`2` trades, `+0.6848%`, `100%` wins).

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `none`

Prediction source breakdown, last 24 hours:
- `none`

Recommendation status breakdown, last 24 hours:
- `none`

Recommendation reason breakdown, last 24 hours:
- `none`

## News And Filings

- news articles: `121`
- news article links: `118`
- filing events: `20`
- latest news timestamp: `2026-03-22 12:52:56.000000`
- latest filing timestamp: `2026-03-20 17:13:17.000000`

## Model And Training Governance

- model versions: `78`
- validation runs: `78`
- training decisions: `92`
- training runs that produced a model: `76`
- training runs that activated a model: `0`

Since the previous benchmark there have been `0` successful `beta_daily_training` jobs. The training and validation inventories are unchanged, and no new challenger models have been produced or activated since the March 19 snapshot.

## Latest Successful Jobs At Snapshot

- `beta_instrument_statistics_refresh` at `2026-03-22 22:32:44.443895`
- `beta_intraday_short_trade_history` at `2026-03-22 22:32:44.398370`
- `beta_intraday_outlook_history` at `2026-03-22 22:32:39.524253`
- `beta_intraday_focus_backfill` at `2026-03-22 21:09:15.181059`
- `beta_intraday_bar_backfill` at `2026-03-22 20:13:25.006952`
- `beta_eod_bar_fetch` at `2026-03-22 20:13:24.666364`
- `beta_intraday_short_trade_simulation` at `2026-03-22 20:13:24.215295`
- `beta_execution_outcomes` at `2026-03-22 20:13:19.868302`
- `beta_intraday_execution_signals` at `2026-03-22 20:13:16.857464`
- `beta_intraday_execution_prepare` at `2026-03-22 20:13:16.306837`
- `beta_filing_sync` at `2026-03-22 20:12:21.869218`
- `beta_news_sync` at `2026-03-22 20:12:19.863825`
- `beta_learning_universe_sync` at `2026-03-22 20:12:17.653021`

## Comparison Vs 2026-03-19 11:39:39 +00:00

Key deltas:
- DB file size: `+673,554,432` bytes (`+0.6273 GB`)
- instruments: `+2,751`
- open memberships: `-816`
- seed memberships: `+625`
- intraday snapshots: `-89`
- minute bars: `+246,224`
- intraday feature snapshots: `+47`
- score tape rows: `+132`
- research ranking rows: `+29,775`
- pipeline snapshots: `+16,472`
- signal observations: `+12`
- recommendation decisions: `+6`
- execution hypothesis definitions: `+12`
- execution signals: `+322`
- execution label values: `+322`
- news articles: `+29`
- news article links: `+40`
- filing events: `+2`

No change:
- active memberships
- daily bars, position states, feature values, label values, signal candidates
- hypothesis engine core tables (families, definitions, templates, discovery runs, discovery candidates, test runs, belief state count)
- model versions, validation runs, training decisions

Belief-state deltas:
- `REJECTED: 31 -> 31`
- `DEGRADED: 3 -> 3`
- `CANDIDATE: 0 -> 0`
- `PROMISING: 0 -> 0`
- `VALIDATED: 0 -> 0`

Interpretation:
- this is a ~`10.9`-day window from the March 19 benchmark to the March 30 snapshot
- the DB did grow, but the growth is front-loaded into March 20-22 rather than showing ongoing March 23-30 progress
- corpus freshness is stalled: no new intraday observations or minute bars after `2026-03-22 20:05`, no new score tape after `2026-03-20 19:54`, no new news after `2026-03-22 12:52`, and no new filings after `2026-03-20 17:13`
- the supervisor is not actually cycling cleanly: repeated `beta_supervisor_cycle_error` failures on `2026-03-22` were caused by `datetime` values being passed into JSON job detail serialization, and the last 7 days then devolved into `1,701` memory-guard skips
- the hypothesis and training stacks are effectively frozen relative to March 19: no new discovery, no new tests, no new models, and no new daily training jobs
- the execution layer has accumulated many more persisted rows, but that is historical buildup rather than live decision flow; the last 24 hours are empty, and simulated evidence is still historical-only with no `LIVE_FORWARD` trades
- the short-side simulation evidence is weaker than the headline pocket summary implies: overall short backfills are only slightly positive, and `OPEN__GAP_DOWN_RECOVERY` is net negative while `MIDDAY__GAP_DOWN_RECOVERY` remains positive on only two trades
- net: the benchmark shows more offline evidence than the March 19 snapshot, but the current system state is stalled rather than progressing
