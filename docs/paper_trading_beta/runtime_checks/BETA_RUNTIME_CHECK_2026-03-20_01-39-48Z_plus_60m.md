# Beta Runtime Check

Snapshot time: `2026-03-20 01:39:48 +00:00`
Label: `plus_60m`
DB path: `C:\EquityTrackerData\portfolio.beta_research.db`
Assessment: `HEALTHY`

## Restart Review
- attempted: `no`
- succeeded: `no`
- message: `not_needed`

## Status Before Review
- supervisor status: `running`
- supervisor pid: `43288`
- last heartbeat: `2026-03-20 01:30:54.138719`
- updated at: `2026-03-20 01:30:54.138719`

## Status After Review
- supervisor status: `running`
- supervisor pid: `43288`
- last heartbeat: `2026-03-20 01:30:54.138719`
- updated at: `2026-03-20 01:30:54.138719`

## Running Jobs
| job_name | status | started_at | completed_at |
| --- | --- | --- | --- |
| beta_intraday_outlook_history | RUNNING | 2026-03-20 01:30:54.123098 | - |

## Recent Jobs
| job_name | status | started_at | completed_at |
| --- | --- | --- | --- |
| beta_intraday_outlook_history | RUNNING | 2026-03-20 01:30:54.123098 | - |
| beta_intraday_focus_backfill | SUCCESS | 2026-03-20 00:39:46.425616 | 2026-03-20 01:30:54.107469 |
| beta_intraday_bar_backfill | SUCCESS | 2026-03-20 00:39:24.175896 | 2026-03-20 00:39:46.389740 |
| beta_eod_bar_fetch | SUCCESS | 2026-03-20 00:38:52.180573 | 2026-03-20 00:39:24.164894 |
| beta_intraday_short_trade_simulation | SUCCESS | 2026-03-20 00:38:49.332458 | 2026-03-20 00:38:52.175745 |
| beta_execution_outcomes | SUCCESS | 2026-03-20 00:38:47.145886 | 2026-03-20 00:38:49.311428 |
| beta_intraday_execution_signals | SUCCESS | 2026-03-20 00:38:47.034401 | 2026-03-20 00:38:47.145886 |
| beta_intraday_execution_prepare | SUCCESS | 2026-03-20 00:37:47.628496 | 2026-03-20 00:38:47.014286 |
| beta_filing_sync | SUCCESS | 2026-03-20 00:37:45.846986 | 2026-03-20 00:37:47.618494 |
| beta_news_sync | SUCCESS | 2026-03-20 00:37:43.929543 | 2026-03-20 00:37:45.838178 |
| beta_learning_universe_sync | SUCCESS | 2026-03-20 00:37:24.913313 | 2026-03-20 00:37:43.897118 |
| beta_supervisor_bootstrap | SUCCESS | 2026-03-20 00:37:09.882326 | 2026-03-20 00:37:09.882326 |

## Intraday Activity
- execution signals total: `394`
- latest execution signal created: `2026-03-19 19:59:53.946120`
- latest intraday session date: `2026-03-19`
- intraday observations on latest session: `569`
- latest intraday observation on latest session: `2026-03-20 00:31:00.000000`

## Simulated Trades
| simulation_source | total_trades | open_trades | closed_trades | wins | avg_post_cost_return_pct |
| --- | --- | --- | --- | --- | --- |
| HISTORICAL_BACKFILL | 9 | 0 | 9 | 5 | 0.021 |
