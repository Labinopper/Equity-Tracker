# Beta Runtime Check

Snapshot time: `2026-03-20 00:25:58 +00:00`
Label: `plus_120m`
DB path: `C:\EquityTrackerData\portfolio.beta_research.db`
Assessment: `HEALTHY`

## Restart Review
- attempted: `no`
- succeeded: `no`
- message: `not_needed`

## Status Before Review
- supervisor status: `running`
- supervisor pid: `48036`
- last heartbeat: `2026-03-20 00:25:10.759243`
- updated at: `2026-03-20 00:25:10.759243`

## Status After Review
- supervisor status: `running`
- supervisor pid: `48036`
- last heartbeat: `2026-03-20 00:25:10.759243`
- updated at: `2026-03-20 00:25:10.759243`

## Running Jobs
| job_name | status | started_at | completed_at |
| --- | --- | --- | --- |
| beta_intraday_focus_backfill | RUNNING | 2026-03-20 00:25:10.745126 | - |

## Recent Jobs
| job_name | status | started_at | completed_at |
| --- | --- | --- | --- |
| beta_intraday_focus_backfill | RUNNING | 2026-03-20 00:25:10.745126 | - |
| beta_intraday_bar_backfill | SUCCESS | 2026-03-20 00:25:10.506827 | 2026-03-20 00:25:10.729128 |
| beta_eod_bar_fetch | SUCCESS | 2026-03-20 00:24:37.642722 | 2026-03-20 00:25:10.491092 |
| beta_intraday_short_trade_simulation | SUCCESS | 2026-03-20 00:24:34.701714 | 2026-03-20 00:24:37.624378 |
| beta_execution_outcomes | SUCCESS | 2026-03-20 00:24:32.589302 | 2026-03-20 00:24:34.675380 |
| beta_intraday_execution_signals | SUCCESS | 2026-03-20 00:24:32.418717 | 2026-03-20 00:24:32.572368 |
| beta_intraday_execution_prepare | SUCCESS | 2026-03-20 00:23:24.792127 | 2026-03-20 00:24:32.401716 |
| beta_filing_sync | SUCCESS | 2026-03-20 00:23:22.824057 | 2026-03-20 00:23:24.787706 |
| beta_news_sync | SUCCESS | 2026-03-20 00:23:20.670667 | 2026-03-20 00:23:22.791838 |
| beta_learning_universe_sync | SUCCESS | 2026-03-20 00:21:42.923596 | 2026-03-20 00:23:20.626849 |
| beta_supervisor_bootstrap | SUCCESS | 2026-03-20 00:21:27.856803 | 2026-03-20 00:21:27.856803 |
| beta_storage_retention | INTERRUPTED | 2026-03-19 23:58:41.493246 | 2026-03-20 00:21:27.838341 |

## Intraday Activity
- execution signals total: `394`
- latest execution signal created: `2026-03-19 19:59:53.946120`
- latest intraday session date: `2026-03-19`
- intraday observations on latest session: `567`
- latest intraday observation on latest session: `2026-03-20 00:21:00.000000`

## Simulated Trades
| simulation_source | total_trades | open_trades | closed_trades | wins | avg_post_cost_return_pct |
| --- | --- | --- | --- | --- | --- |
| HISTORICAL_BACKFILL | 9 | 0 | 9 | 5 | 0.021 |
