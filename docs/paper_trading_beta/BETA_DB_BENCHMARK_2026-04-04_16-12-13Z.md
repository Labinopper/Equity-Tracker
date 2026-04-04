# Beta DB Benchmark

Snapshot time: `2026-04-04 16:12:13 +00:00`

DB path: `C:\Users\labin\OneDrive\Documents\Equity-Tracker\data\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-04-03_14-02-50Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-04-03_14-02-50Z.md)

Important note: this is a fresh post-restart baseline, not a continuation of the populated April 3 benchmark. The beta DB file was empty before the supervisor restart and is now freshly bootstrapped. Future comparisons over the next few hours should be made against this file, not against the April 3 populated-state benchmark.

DB file size:
- `2,002,944` bytes
- `1.91 MB`
- `0.0019 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `15512`
- last heartbeat at snapshot: `2026-04-04 16:11:58.484122`
- status row last updated at snapshot: `2026-04-04 16:11:58.484122`
- actual supervisor process at snapshot: `running`
- core DB path at snapshot: `C:\Users\labin\OneDrive\Documents\Equity-Tracker\data\portfolio.db`
- runtime mode: `FULL_INTERNAL_BETA`
- latest pipeline snapshot status: `BOOTSTRAPPING`
- latest pipeline snapshot at: `2026-04-04 16:11:58.539668`

Since the restart at roughly `2026-04-04 16:02:41 UTC`, the supervisor logged:
- `84` `SUCCESS`
- `10` `FAILED`
- `4` `SKIPPED`

The runtime is alive and cycling, but it is still in an early bootstrapping state with an upstream core-access blocker.

## Corpus And Universe

- instruments: `0`
- universe memberships: `0`
- research rankings: `0`
- news articles: `20`
- job runs: `98`
- pipeline snapshots: `36`
- minute bars: `0`
- intraday observations: `0`
- intraday labels: `0`
- intraday feature snapshots: `0`
- execution signals: `0`
- simulated trades: `0`
- pattern discovery runs: `1`
- pattern candidates: `0`
- execution discovery runs: `0`
- execution discovery candidates: `0`
- exploration profiles: `0`
- threshold profiles: `0`
- policy profiles: `0`
- execution profiles: `0`

Latest data dates:
- latest intraday observation: `NULL`
- latest intraday feature snapshot: `NULL`
- latest execution signal time: `NULL`
- latest simulated trade timestamp: `NULL`
- latest news timestamp: `2026-04-03 23:27:52.000000`

## Intraday Focus

The intraday process is currently blocked upstream, and that is the main thing to benchmark over the next few hours.

Current state:
- `beta_intraday_execution_prepare` has failed `9` times since restart
- latest failure was at `2026-04-04 16:11:12.745383`
- error: `Salt file not found: C:\Users\labin\OneDrive\Documents\Equity-Tracker\data\portfolio.db.salt. Was this database created with DatabaseEngine.create()?`
- `data\portfolio.db.salt` is currently missing

Downstream behavior is therefore a no-op rather than real intraday progress:
- `beta_intraday_execution_signals`: `9` `SUCCESS`, but latest run evaluated `0` positions and created `0` signals
- `beta_execution_outcomes`: `9` `SUCCESS`, but latest run evaluated `0` signals and wrote `0` labels
- `beta_intraday_short_trade_simulation`: `9` `SUCCESS`, but latest run had `0` focus items, `0` open trades, `0` opened, `0` closed

Historical intraday bootstrap jobs did run once successfully:
- `beta_intraday_bar_backfill`
- `beta_intraday_focus_backfill`
- `beta_intraday_outlook_history`
- `beta_intraday_short_trade_history`

But they have not yet produced retained intraday rows in the fresh beta DB:
- minute bars: `0`
- intraday observations: `0`
- intraday labels: `0`
- intraday feature snapshots: `0`

## Intraday Research Loop

The new no-op behavior is working correctly.

Latest intraday pattern research jobs after restart:
- `beta_intraday_pattern_exploration_learning`: `SKIPPED` at `2026-04-04 16:03:06.208303` with reason `no_evidence`
- `beta_intraday_pattern_threshold_learning`: `SKIPPED` at `2026-04-04 16:03:06.265815` with reason `no_observations`
- `beta_intraday_pattern_policy_learning`: `SKIPPED` at `2026-04-04 16:03:06.396015` with reason `no_evidence`
- `beta_intraday_pattern_execution_learning`: `SKIPPED` at `2026-04-04 16:03:06.457531` with reason `no_evidence`

Latest pattern exploration run:
- run code: `20260404160306356014`
- status: `SUCCESS`
- created at: `2026-04-04 16:03:06.356014`
- observations considered: `0`
- labeled observations: `0`
- patterns generated: `0`
- patterns screened in: `0`
- input fingerprint: `480e25c1d6e4deebe45278e1b03ee81574ae8cdf`

Interpretation:
- the skip instrumentation is doing its job
- the current lack of intraday research output is because there is no intraday evidence in the fresh DB, not because the loop is writing duplicate fake progress

## Other Active Failures

The intraday blocker is not isolated. The same core-access problem has already hit upstream ingestion:
- `beta_learning_universe_sync` failed at `2026-04-04 16:03:01.082958`
- same root cause: missing `portfolio.db.salt`

That helps explain why the whole beta is still bootstrapping:
- active universe count: `0`
- tracked core count: `0`
- research rankings: `0`
- no intraday corpus has been populated yet

## What To Compare In A Few Hours

If we want a clean next benchmark, these are the most important deltas to track from this snapshot:

1. Supervisor health
- heartbeat still advancing
- failure count stable or improving

2. Intraday unblock
- whether `beta_intraday_execution_prepare` is still failing
- whether `data\portfolio.db.salt` has been restored

3. First real intraday corpus growth
- minute bars rising above `0`
- intraday observations rising above `0`
- intraday labels rising above `0`
- intraday feature snapshots rising above `0`
- execution signals rising above `0`
- simulated trades rising above `0`

4. Intraday research quality
- whether pattern exploration starts seeing non-zero observations
- whether any learned exploration / threshold / policy / execution profiles are created instead of skipped

5. Overall beta bootstrapping
- universe count rising above `0`
- research rankings rising above `0`
- pipeline snapshot moving out of `BOOTSTRAPPING`
