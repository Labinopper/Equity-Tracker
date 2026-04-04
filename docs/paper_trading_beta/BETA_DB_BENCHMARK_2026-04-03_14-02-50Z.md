# Beta DB Benchmark

Snapshot time: `2026-04-03 14:02:50 +00:00`

DB path: `C:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-30_09-36-30Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-30_09-36-30Z.md)

DB file size:
- `27,242,930,176` bytes
- `25,980.88 MB`
- `25.3720 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `2928`
- last heartbeat at snapshot: `2026-04-03 13:53:25.576135`
- status row last updated at snapshot: `2026-04-03 13:53:25.576135`
- actual supervisor process at snapshot: `not running`

Note: the runtime had been healthy and actively cycling through `2026-04-03 13:53:25`, but the supervisor was manually stopped later on `2026-04-03` at user request. The status row is therefore stale in a favorable direction: it reflects the last clean heartbeat rather than the current stopped state. This is materially different from the March 30 benchmark, where the status row said `running` while the runtime was functionally stalled.

## Corpus And Universe

- instruments: `6,630`
- open memberships: `0`
- active memberships: `21`
- seed memberships: `1,590`
- daily bars: `2,359,593`
- intraday snapshots: `5,263`
- minute bars: `319,107`
- intraday feature snapshots: `163`
- position states: `33`
- feature values: `40,175,219`
- label values: `6,603,831`
- score tape rows: `1,608`
- signal candidates: `115`
- research ranking rows: `54,602`
- pipeline snapshots: `27,840`

Latest data dates:
- latest daily bar date: `2026-04-02`
- latest intraday observation: `2026-04-03 13:52:00.872134`
- latest minute bar: `2026-04-03 13:52:00.000000`
- latest intraday feature snapshot: `2026-04-03 13:52:31.460479`
- latest feature date: `2026-04-02`
- latest label date: `2026-03-30`
- latest score row: `2026-04-03 13:52:00.872134`
- latest ranking row: `2026-04-03 02:17:25.143666`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `34`
- hypothesis templates: `12`
- hypothesis discovery runs: `56`
- hypothesis discovery candidates: `6,372`
- hypothesis test runs: `1,960`
- hypothesis belief states: `34`
- signal observations: `45,365`
- recommendation decisions: `507`

Belief status breakdown:
- `REJECTED = 32`
- `DEGRADED = 2`
- `CANDIDATE = 0`
- `PROMISING = 0`
- `VALIDATED = 0`

## Execution Layer

- execution hypothesis definitions: `30`
- execution signals: `1,203`
- execution label values: `1,202`

The execution layer is alive again but still not trade-ready. There were `243` execution signals in the last 24 hours, split `224` `NO_ACTION` / `HOLD` and `19` `CONFIRM` / `WAIT`, all still concentrated in a narrow held-position monitoring pattern. There are `0` demo positions, `0` open simulated positions, and `0` active signal candidates at snapshot. Simulated trade evidence remains entirely historical-backfill: `14` closed trades total, `0` `LIVE_FORWARD`, with `2` closed `LONG` trades averaging `+0.5301%` post-cost and `12` closed `SHORT` trades averaging `+0.0819%` post-cost. The short book is still split by state: `OPEN__GAP_DOWN_RECOVERY` is slightly negative (`10` trades, `-0.0387%`, `50.00%` wins) while `MIDDAY__GAP_DOWN_RECOVERY` remains positive (`2` trades, `+0.6848%`, `100%` wins).

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 55`
- `REJECTED = 20`

Prediction source breakdown, last 24 hours:
- `HEURISTIC = 75`

Recommendation status breakdown, last 24 hours:
- `REJECTED = 268`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_rejected = 268`

## News And Filings

- news articles: `194`
- news article links: `298`
- filing events: `28`
- latest news timestamp: `2026-04-03 13:35:11.000000`
- latest filing timestamp: `2026-04-02 12:57:27.000000`

## Model And Training Governance

- model versions: `111`
- validation runs: `111`
- training decisions: `125`
- training runs that produced a model: `109`
- training runs that activated a model: `0`

Since the previous benchmark there have been `33` successful `beta_daily_training` jobs. Average validation sign accuracy across those runs was `48.9021%`, average walk-forward sign accuracy was `50.5970%`, and average walk-forward validation return was `2.0093%`. None of the `33` runs passed activation gates.

Most common activation gate failures since the previous benchmark:
- `validation_sign_accuracy_below_activation_floor = 33`

## Latest Successful Jobs At Snapshot

- `beta_tracked_core_shadow_cycle` at `2026-04-03 13:53:25.560104`
- `beta_intraday_short_trade_simulation` at `2026-04-03 13:53:25.473513`
- `beta_execution_outcomes` at `2026-04-03 13:53:20.338437`
- `beta_intraday_execution_signals` at `2026-04-03 13:53:13.895363`
- `beta_intraday_execution_prepare` at `2026-04-03 13:53:13.185517`
- `beta_filing_sync` at `2026-04-03 13:41:03.548607`
- `beta_news_sync` at `2026-04-03 13:41:01.308286`
- `beta_daily_replay_pack` at `2026-04-03 07:31:47.177099`
- `beta_hypothesis_refresh` at `2026-04-03 07:31:47.084528`
- `beta_hypothesis_belief_refresh` at `2026-04-03 07:31:47.007491`
- `beta_hypothesis_backtests` at `2026-04-03 07:31:44.183490`
- `beta_hypothesis_discovery` at `2026-04-03 06:53:34.398554`
- `beta_hypothesis_definition_seed` at `2026-04-03 06:48:22.257301`
- `beta_execution_hypothesis_belief_refresh` at `2026-04-03 06:48:22.158773`
- `beta_execution_hypothesis_backtests` at `2026-04-03 06:48:22.043250`
- `beta_execution_hypothesis_discovery` at `2026-04-03 06:47:56.675475`
- `beta_daily_potential_gains_review` at `2026-04-03 06:47:24.586233`
- `beta_daily_training` at `2026-04-03 06:47:24.519235`
- `beta_live_evaluation` at `2026-04-03 06:04:47.247084`
- `beta_candidate_thesis_sync` at `2026-04-03 06:04:36.395768`

## Comparison Vs 2026-03-30 09:36:35 +00:00

Key deltas:
- DB file size: `+7,856,812,032` bytes (`+7.3172 GB`)
- instruments: `+1,506`
- seed memberships: `+170`
- daily bars: `+732,396`
- intraday snapshots: `-1,515`
- minute bars: `+30,687`
- intraday feature snapshots: `+76`
- feature values: `+12,183,587`
- label values: `+2,910,843`
- score tape rows: `-45,803`
- research ranking rows: `+11,117`
- pipeline snapshots: `+4,162`
- hypothesis discovery runs: `+26`
- hypothesis discovery candidates: `+2,106`
- hypothesis test runs: `+884`
- signal observations: `+409`
- recommendation decisions: `-14,555`
- execution hypothesis definitions: `+8`
- execution signals: `+707`
- execution label values: `+707`
- news articles: `+73`
- news article links: `+180`
- filing events: `+8`
- model versions: `+33`
- validation runs: `+33`
- training decisions: `+33`

No change:
- open memberships, active memberships
- position states, signal candidates
- hypothesis engine core definitions/templates/families
- training runs that activated a model

Belief-state deltas:
- `REJECTED: 31 -> 32`
- `DEGRADED: 3 -> 2`
- `CANDIDATE: 0 -> 0`
- `PROMISING: 0 -> 0`
- `VALIDATED: 0 -> 0`

Interpretation:
- this is a ~`4.18`-day window from the March 30 benchmark to the April 3 snapshot
- the central March 30 blockers are no longer the dominant story: there were `0` `beta_supervisor_cycle_error` rows in the last 7 days, and the latest successful jobs show the full live loop running through `2026-04-03 13:53`
- memory pressure still exists, but it has eased rather than intensified: the last 7 days show `826` `beta_memory_guard` skips versus `1,701` in the March 30 benchmark
- corpus freshness has clearly recovered: the latest daily bars are now `2026-04-02`, intraday and minute data are fresh through `2026-04-03 13:52`, score tape is fresh through `2026-04-03 13:52`, rankings through `2026-04-03 02:17`, news through `2026-04-03 13:35`, and filings through `2026-04-02 12:57`
- the negative deltas in `intraday snapshots`, `score tape rows`, and `recommendation decisions` are expected storage-governance effects rather than outright regressions. The latest successful `beta_storage_retention` on `2026-04-03 05:51:52` deleted `465` intraday snapshots, `51,310` minute bars, `4,494` non-actionable score rows, and `1,418` non-actionable recommendations
- training is active again: `33` successful `beta_daily_training` jobs ran since the March 30 benchmark, producing `33` additional challenger models and validation runs, but activation remains blocked because validation sign accuracy is still below floor on every run
- the hypothesis stack is moving again: discovery, backtests, belief refresh, and recommendation generation all advanced after March 30, but the governance filter is still overwhelmingly rejecting ideas rather than promoting them
- the execution layer has recovered operationally but not strategically. It is generating fresh signals again, yet nearly all are `NO_ACTION`, there are still no live-forward trades, and the simulated short-edge pocket remains fragile
- net: this snapshot shows a real recovery from the March 30 stalled state into an actively running research system. The beta is no longer frozen, but it still has not crossed into robust trade-generation effectiveness
