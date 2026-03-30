# Beta DB Benchmark

Snapshot time: `2026-03-19 11:39:39 +00:00`

DB path: `C:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-18_10-05-00Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-18_10-05-00Z.md)

DB file size:
- `18,712,563,712` bytes
- `17845.69 MB`
- `17.4274 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `32004`
- last heartbeat at snapshot: `2026-03-19 11:39:39.092533`
- status row last updated at snapshot: `2026-03-19 11:39:39.092533`

Note: the supervisor was restarted during this window. The current runtime bootstrapped at `2026-03-19 01:48:22`, and the post-fix `beta_instrument_statistics_refresh` completed at `2026-03-19 01:49:21` with bounded failed attempts instead of stalling the overnight cycle.

## Corpus And Universe

- instruments: `2373`
- open memberships: `816`
- active memberships: `21`
- seed memberships: `795`
- daily bars: `1,627,197`
- intraday snapshots: `6,867`
- minute bars: `42,196`
- intraday feature snapshots: `40`
- position states: `33`
- feature values: `27,991,632`
- label values: `3,692,988`
- score tape rows: `47,279`
- signal candidates: `115`
- research ranking rows: `13,710`
- pipeline snapshots: `7,206`

Latest data dates:
- latest daily bar date: `2026-03-18`
- latest intraday observation: `2026-03-19 11:38:45.001060`
- latest minute bar: `2026-03-19 11:38:00.000000`
- latest intraday feature snapshot: `2026-03-19 04:09:12.799926`
- latest feature date: `2026-03-18`
- latest label date: `2026-03-13`
- latest score row: `2026-03-19 11:39:22.679930`
- latest ranking row: `2026-03-19 01:48:46.722795`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `34`
- hypothesis templates: `12`
- hypothesis discovery runs: `30`
- hypothesis discovery candidates: `4,266`
- hypothesis test runs: `1,076`
- hypothesis belief states: `34`
- signal observations: `44,944`
- recommendation decisions: `15,056`

Belief status breakdown:
- `REJECTED = 31`
- `DEGRADED = 3`
- `PROMISING = 0`
- `VALIDATED = 0`

## Execution Layer

- execution hypothesis definitions: `10`
- execution signals: `174`
- execution label values: `173`

The execution layer is active but still not trade-ready. Since the previous benchmark it added `88` execution signals and `88` execution labels, but the last 24 hours were almost entirely `NO_ACTION` (`85`) with only `3` `WAIT_FOR_CLOSE_CONFIRMATION` events, all on `IBM`. There are `0` demo positions, `0` open simulated positions, and `0` active signal candidates at snapshot.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 1080`
- `REJECTED = 450`
- `WATCHING = 42`
- `BLOCKED = 17`

Prediction source breakdown, last 24 hours:
- `HEURISTIC = 1060`
- `VALIDATED_BASELINE = 529`

Recommendation status breakdown, last 24 hours:
- `REJECTED = 3867`
- `BLOCKED = 616`
- `DISMISSED = 234`
- `WATCHING = 66`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_rejected = 3867`
- `hypothesis_degraded = 616`
- `direction_mismatch = 229`
- `promising_watch_only = 66`
- `belief_insufficient = 5`

## News And Filings

- news articles: `92`
- news article links: `78`
- filing events: `18`
- latest news timestamp: `2026-03-19 07:37:38.000000`
- latest filing timestamp: `2026-03-18 09:31:06.000000`

## Model And Training Governance

- model versions: `78`
- validation runs: `78`
- training decisions: `92`
- training runs that produced a model: `76`
- training runs that activated a model: `0`

Since the previous benchmark there have been `16` successful `beta_daily_training` jobs. Average validation sign accuracy across those runs was `50.2931%`, average walk-forward sign accuracy was `50.6006%`, and average walk-forward validation return was `1.0141%`. None of the `16` runs passed activation gates.

Most common activation gate failures since the previous benchmark:
- `validation_sign_accuracy_below_activation_floor = 16`
- `walkforward_accuracy_below_activation_floor = 16`
- `walkforward_not_beating_baseline = 14`
- `walkforward_return_not_beating_baseline = 13`
- `holdout_not_beating_baseline = 12`

## Latest Successful Jobs At Snapshot

- `beta_tracked_core_shadow_cycle` at `2026-03-19 11:39:22.698935`
- `beta_execution_outcomes` at `2026-03-19 11:39:22.559426`
- `beta_intraday_execution_signals` at `2026-03-19 11:39:21.889704`
- `beta_intraday_execution_prepare` at `2026-03-19 11:39:21.752632`
- `beta_news_sync` at `2026-03-19 11:03:31.769203`
- `beta_filing_sync` at `2026-03-19 10:40:12.619535`
- `beta_daily_replay_pack` at `2026-03-19 08:02:20.001197`
- `beta_daily_potential_gains_review` at `2026-03-19 08:02:19.927190`
- `beta_daily_training` at `2026-03-19 08:02:19.829800`
- `beta_live_evaluation` at `2026-03-19 07:37:07.563081`
- `beta_candidate_thesis_sync` at `2026-03-19 07:36:50.997442`
- `beta_daily_shadow_cycle` at `2026-03-19 07:36:50.925440`
- `beta_research_universe_refresh` at `2026-03-19 07:36:42.044863`
- `beta_label_backlog_build` at `2026-03-19 07:36:33.115528`
- `beta_feature_backlog_build` at `2026-03-19 07:34:09.082531`
- `beta_tracked_core_label_build` at `2026-03-19 07:31:23.967497`
- `beta_tracked_core_feature_build` at `2026-03-19 07:29:38.954442`
- `beta_daily_observation_sync` at `2026-03-19 07:28:45.134865`
- `beta_hypothesis_refresh` at `2026-03-19 07:28:14.190005`
- `beta_hypothesis_belief_refresh` at `2026-03-19 07:28:14.111515`
- `beta_hypothesis_backtests` at `2026-03-19 07:28:12.150226`
- `beta_hypothesis_discovery` at `2026-03-19 07:15:51.015945`
- `beta_hypothesis_definition_seed` at `2026-03-19 07:12:50.302295`
- `beta_instrument_statistics_refresh` at `2026-03-19 01:49:21.562580`
- `beta_intraday_bar_backfill` at `2026-03-19 01:49:13.318978`
- `beta_eod_bar_fetch` at `2026-03-19 01:49:13.167450`
- `beta_learning_universe_sync` at `2026-03-19 01:48:53.961910`
- `beta_supervisor_bootstrap` at `2026-03-19 01:48:22.564168`
- `beta_filing_source_seed` at `2026-03-19 01:48:20.908678`
- `beta_news_source_seed` at `2026-03-19 01:48:20.893158`
- `beta_hypothesis_seed` at `2026-03-19 01:48:20.839347`
- `beta_runtime_bootstrap` at `2026-03-19 01:48:20.821634`

## Comparison Vs 2026-03-18 10:05:00 +00:00

Key deltas:
- DB file size: `+3,354,812,416` bytes (`+3.1244 GB`)
- instruments: `+124`
- open memberships: `+99`
- seed memberships: `+99`
- daily bars: `+217,521`
- intraday snapshots: `+476`
- minute bars: `+28,434`
- intraday feature snapshots: `+27`
- position states: `+32`
- feature values: `+7,077,472`
- label values: `+1,669,527`
- score tape rows: `+14,327`
- signal candidates: `+37`
- research ranking rows: `+3,315`
- pipeline snapshots: `+2,964`
- hypothesis templates: `+3`
- hypothesis discovery runs: `+9`
- hypothesis discovery candidates: `+1,431`
- hypothesis test runs: `+374`
- signal observations: `+11,597`
- recommendation decisions: `+4,802`
- execution hypothesis definitions: `+5`
- execution signals: `+88`
- execution label values: `+88`
- news articles: `+23`
- news article links: `+25`
- filing events: `+1`
- model versions: `+16`
- validation runs: `+16`
- training decisions: `+16`

No change:
- active memberships
- hypothesis families, hypothesis definitions, hypothesis belief state count

Belief-state deltas:
- `DEGRADED: 19 -> 3`
- `PROMISING: 7 -> 0`
- `CANDIDATE: 4 -> 0`
- `REJECTED: 4 -> 31`
- `VALIDATED: 0 -> 0`

Interpretation:
- this is a ~25.6-hour window covering the post-change overnight cycle plus the following morning session
- the platform clearly ran end-to-end overnight: corpus, feature, label, ranking, scoring, execution, discovery, backtests, belief refresh, review, and repeated training jobs all completed
- the new runtime fix appears effective: `beta_instrument_statistics_refresh` no longer trapped the supervisor, and the full overnight pipeline reached `beta_daily_training` and `beta_daily_potential_gains_review` after the `01:48` restart
- data growth remains heavy but controlled: the DB added ~`3.12 GB`, including `+7.08M` feature rows, `+1.67M` label rows, and `+28.4K` minute bars
- the new hypothesis governance is materially stricter than before: although discovery/backtest volume increased sharply, the belief book has been almost completely culled into `31` `REJECTED` and `3` `DEGRADED` states, with no remaining `PROMISING` or `CANDIDATE` beliefs
- training is active but not yet promotive: `16` new challenger models were trained overnight, yet none were activated because validation and walk-forward accuracy continue to miss activation floors and usually do not beat baseline policies
- the execution layer is instrumented and producing labels, but it is still not generating tradeable breadth: the last 24 hours were almost entirely `IBM` `NO_ACTION` events, with `0` demo positions and `0` active signal candidates at snapshot
- recommendation flow is now dominated by hard governance outcomes rather than soft uncertainty: `hypothesis_rejected` and `hypothesis_degraded` account for `4,483` of the last-day decision reasons, which is consistent with the new closed-loop validation logic actively filtering rather than promoting
