# Beta Runtime Check

Snapshot time: `2026-03-19 22:24:59 +00:00`
Label: `immediate_verify`
DB path: `C:\EquityTrackerData\portfolio.beta_research.db`
Assessment: `HEALTHY`

## Restart Review
- attempted: `no`
- succeeded: `no`
- message: `not_needed`

## Status Before Review
- supervisor status: `running`
- supervisor pid: `40368`
- last heartbeat: `2026-03-19 22:22:11.730169`
- updated at: `2026-03-19 22:22:11.730169`

## Status After Review
- supervisor status: `running`
- supervisor pid: `40368`
- last heartbeat: `2026-03-19 22:22:11.730169`
- updated at: `2026-03-19 22:22:11.730169`

## Running Jobs
| job_name | status | started_at | completed_at |
| --- | --- | --- | --- |
| beta_intraday_focus_backfill | RUNNING | 2026-03-19 22:22:11.715417 | - |

## Recent Jobs
| job_name | status | started_at | completed_at |
| --- | --- | --- | --- |
| beta_intraday_focus_backfill | RUNNING | 2026-03-19 22:22:11.715417 | - |
| beta_intraday_bar_backfill | SUCCESS | 2026-03-19 22:21:48.156003 | 2026-03-19 22:22:11.699658 |
| beta_eod_bar_fetch | SUCCESS | 2026-03-19 22:21:15.765171 | 2026-03-19 22:21:48.137999 |
| beta_intraday_short_trade_simulation | SUCCESS | 2026-03-19 22:21:13.632518 | 2026-03-19 22:21:15.746172 |
| beta_execution_outcomes | SUCCESS | 2026-03-19 22:21:11.621622 | 2026-03-19 22:21:13.618519 |
| beta_intraday_execution_signals | SUCCESS | 2026-03-19 22:21:11.485091 | 2026-03-19 22:21:11.612623 |
| beta_intraday_execution_prepare | SUCCESS | 2026-03-19 22:20:18.713916 | 2026-03-19 22:21:11.475579 |
| beta_filing_sync | SUCCESS | 2026-03-19 22:20:17.042163 | 2026-03-19 22:20:18.701910 |
| beta_news_sync | SUCCESS | 2026-03-19 22:20:15.054624 | 2026-03-19 22:20:17.017112 |
| beta_learning_universe_sync | SUCCESS | 2026-03-19 22:19:55.361237 | 2026-03-19 22:20:15.031280 |
| beta_supervisor_bootstrap | SUCCESS | 2026-03-19 22:19:40.304427 | 2026-03-19 22:19:40.304427 |
| beta_filing_source_seed | SUCCESS | 2026-03-19 22:19:38.814550 | 2026-03-19 22:19:38.814550 |

## Intraday Activity
- execution signals total: `394`
- latest execution signal created: `2026-03-19 19:59:53.946120`
- latest intraday session date: `2026-03-19`
- intraday observations on latest session: `566`
- latest intraday observation on latest session: `2026-03-19 22:10:00.000000`

## Simulated Trades
| simulation_source | total_trades | open_trades | closed_trades | wins | avg_post_cost_return_pct |
| --- | --- | --- | --- | --- | --- |
| HISTORICAL_BACKFILL | 9 | 0 | 9 | 5 | 0.0136 |
