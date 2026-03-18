# Beta DB Benchmark

Snapshot time: `2026-03-18 10:05:00 +00:00`

DB path: `C:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-17_18-13-52Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-17_18-13-52Z.md)

DB file size:
- `15,357,751,296` bytes
- `14646.29 MB`
- `14.3030 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `21916`
- last heartbeat at snapshot: `2026-03-18 10:01:26.697366`
- status row last updated at snapshot: `2026-03-18 10:01:26.697366`

The supervisor has been running continuously since `2026-03-17 18:20:29` with no restarts in this window.

## Corpus And Universe

- instruments: `2249`
- open memberships: `717`
- active memberships: `21`
- seed memberships: `696`
- daily bars: `1,409,676`
- intraday snapshots: `6,391`
- minute bars: `13,762`
- intraday feature snapshots: `13`
- position states: `1`
- feature values: `20,914,160`
- label values: `2,023,461`
- score tape rows: `32,952`
- signal candidates: `78`
- research ranking rows: `10,395`
- pipeline snapshots: `4,242`

Latest data dates:
- latest daily bar date: `2026-03-17`
- latest intraday observation: `2026-03-18 09:54:44.758770`
- latest minute bar: `2026-03-18 09:54:00.000000`
- latest intraday feature snapshot: `2026-03-17 20:38:20.402611`
- latest feature date: `2026-03-17`
- latest label date: `2026-03-10`
- latest score row: `2026-03-18 09:57:39.768027`
- latest ranking row: `2026-03-18 02:52:39.525800`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `34`
- hypothesis templates: `9`
- hypothesis discovery runs: `21`
- hypothesis discovery candidates: `2,835`
- hypothesis test runs: `702`
- hypothesis belief states: `34`
- signal observations: `33,347`
- recommendation decisions: `10,254`

Belief status breakdown:
- `DEGRADED = 19`
- `PROMISING = 7`
- `CANDIDATE = 4`
- `REJECTED = 4`

## Execution Layer

- execution hypothesis definitions: `5`
- execution signals: `86`
- execution label values: `85`

The execution layer continues to produce signals and labels. Execution signals have grown from 53 to 86 since the previous benchmark.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 16612`
- `BLOCKED = 4219`
- `REJECTED = 2082`
- `DISMISSED = 498`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 23411`

Recommendation status breakdown, last 24 hours:
- `BLOCKED = 4219`
- `REJECTED = 2082`
- `DISMISSED = 498`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_degraded = 3731`
- `hypothesis_rejected = 2082`
- `belief_insufficient = 498`
- `hypothesis_not_validated = 488`

## News And Filings

- news articles: `69`
- news article links: `53`
- filing events: `17`
- latest news timestamp: `2026-03-18 00:00:09.000000`
- latest filing timestamp: `2026-03-18 09:31:06.000000`

## Model And Training Governance

- model versions: `62`
- validation runs: `62`
- training decisions: `76`
- training runs that produced a model: `60`
- training runs that activated a model: `0`

No model has been auto-activated. All 60 trained models remain as challengers or have been superseded. The activation gate has not been passed — this is consistent with the hypothesis engine having no VALIDATED beliefs yet.

## Latest Successful Jobs At Snapshot

- `beta_tracked_core_shadow_cycle` at `2026-03-18 10:02:48.856796`
- `beta_execution_outcomes` at `2026-03-18 10:02:48.764990`
- `beta_intraday_execution_signals` at `2026-03-18 10:02:48.523005`
- `beta_intraday_execution_prepare` at `2026-03-18 10:02:48.458513`
- `beta_filing_sync` at `2026-03-18 10:00:06.473082`
- `beta_news_sync` at `2026-03-18 09:25:54.954124`
- `beta_daily_replay_pack` at `2026-03-18 07:59:39.641589`
- `beta_hypothesis_refresh` at `2026-03-18 07:59:39.619077`
- `beta_hypothesis_belief_refresh` at `2026-03-18 07:59:39.590071`
- `beta_hypothesis_backtests` at `2026-03-18 07:59:39.449559`
- `beta_hypothesis_discovery` at `2026-03-18 07:54:34.744317`
- `beta_hypothesis_definition_seed` at `2026-03-18 07:52:35.630510`
- `beta_daily_potential_gains_review` at `2026-03-18 07:52:35.587473`
- `beta_daily_training` at `2026-03-18 07:52:35.553497`
- `beta_live_evaluation` at `2026-03-18 07:30:25.923391`
- `beta_daily_shadow_cycle` at `2026-03-18 07:30:12.689179`
- `beta_research_universe_refresh` at `2026-03-18 07:30:06.728880`
- `beta_label_backlog_build` at `2026-03-18 07:30:01.592062`
- `beta_feature_backlog_build` at `2026-03-18 07:29:07.420449`
- `beta_tracked_core_label_build` at `2026-03-18 07:26:57.775836`
- `beta_tracked_core_feature_build` at `2026-03-18 07:26:11.400354`
- `beta_daily_observation_sync` at `2026-03-18 07:25:31.599000`
- `beta_learning_universe_sync` at `2026-03-18 02:52:44.158604`
- `beta_instrument_statistics_refresh` at `2026-03-17 20:37:50.589274`
- `beta_intraday_bar_backfill` at `2026-03-17 20:00:20.140382`
- `beta_eod_bar_fetch` at `2026-03-17 20:00:20.074876`
- `beta_supervisor_bootstrap` at `2026-03-17 18:20:29.970357`
- `beta_filing_source_seed` at `2026-03-17 18:20:28.834839`
- `beta_news_source_seed` at `2026-03-17 18:20:28.815485`
- `beta_hypothesis_seed` at `2026-03-17 18:20:28.740199`
- `beta_runtime_bootstrap` at `2026-03-17 18:20:28.721683`

## Comparison Vs 2026-03-17 18:13:52 +00:00

Key deltas:
- DB file size: `+7,950,655,488` bytes (`+7.4046 GB`)
- instruments: `+147`
- open memberships: `+49`
- seed memberships: `+49`
- daily bars: `+307,360`
- intraday snapshots: `+277`
- minute bars: `+1,230`
- intraday feature snapshots: `+5`
- feature values: `+7,029,695`
- label values: `+680,145`
- score tape rows: `+20,724`
- research ranking rows: `+1,511`
- pipeline snapshots: `+891`
- hypothesis definitions: `+2`
- hypothesis discovery runs: `+10`
- hypothesis discovery candidates: `+1,350`
- hypothesis test runs: `+331`
- hypothesis belief states: `+2`
- signal observations: `+23,409`
- recommendation decisions: `+6,798`
- execution signals: `+33`
- execution label values: `+33`
- news articles: `+2`
- news article links: `+3`
- filing events: `+2`
- model versions: `+30`
- validation runs: `+30`
- training decisions: `+30`

No change:
- active memberships, signal candidates, position states
- hypothesis families, hypothesis templates
- execution hypothesis definitions

Belief-state deltas:
- `DEGRADED: 21 -> 19`
- `CANDIDATE: 11 -> 4`
- `PROMISING: 0 -> 7`
- `REJECTED: 0 -> 4`

Interpretation:
- this is a ~16-hour window covering a full overnight daily cycle plus the start of a new intraday session
- the DB has grown dramatically by ~7.4 GB, driven by massive feature value accumulation (+7.0M) and label value growth (+680K) — this reflects the expanding universe (now 2249 instruments) and aggressive feature/label backlog processing
- 30 new model versions were trained overnight (32 -> 62), with 30 corresponding validation runs and training decisions — this is a burst of training activity, though none of the 60 total trained models have been auto-activated
- the hypothesis engine is showing meaningful evolution: 10 new discovery runs, +1350 candidates, +331 test runs, and 2 new hypothesis definitions — most importantly, 7 hypotheses have moved to PROMISING status (up from 0) while 4 have been REJECTED, indicating the belief system is differentiating signal quality
- the instrument universe grew by 147 (2102 -> 2249) with 49 new seed memberships added
- daily bars jumped by +307K reflecting corpus backfill catching up with the expanded universe
- the execution layer continues steady growth (+33 signals, +33 labels)
- the supervisor has been stable with no restarts in this window
- signal observation volume is significantly higher than the prior benchmark (+23K vs +1.4K), consistent with more hypotheses being evaluated and the larger universe
- no validated hypotheses yet, but the emergence of 7 PROMISING beliefs is a notable step forward from the previous all-DEGRADED/CANDIDATE state
