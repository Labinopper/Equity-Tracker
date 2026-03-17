# Beta DB Benchmark

Snapshot time: `2026-03-16 11:31:33 +00:00`

DB path: `D:\EquityTrackerData\portfolio.beta_research.db`

Compared against: [BETA_DB_BENCHMARK_2026-03-15_16-22-22Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-03-15_16-22-22Z.md)

DB file size:
- `7,053,619,200` bytes
- `6726.86 MB`
- `6.5692 GB`

## Runtime State At Snapshot

- supervisor status row at snapshot: `running`
- supervisor PID at snapshot: `26444`
- last heartbeat at snapshot: `2026-03-16 11:31:30.239302`
- status row last updated at snapshot: `2026-03-16 11:31:30.240296`

## Corpus And Universe

- instruments: `1890`
- open memberships: `618`
- active memberships: `21`
- seed memberships: `597`
- daily bars: `1033063`
- intraday snapshots: `5488`
- minute bars: `216`
- intraday feature snapshots: `1`
- position states: `1`
- feature values: `13415612`
- label values: `1297953`
- score tape rows: `10963`
- signal candidates: `78`
- research ranking rows: `7473`
- pipeline snapshots: `494`

Latest data dates:
- latest daily bar date: `2026-03-13`
- latest intraday observation: `2026-03-16 11:30:24.523369`
- latest minute bar: `2026-03-16 11:30:00.000000`
- latest intraday feature snapshot: `2026-03-16 11:30:41.778519`
- latest feature date: `2026-03-13`
- latest label date: `2026-03-06`
- latest score row: `2026-03-16 11:30:09.698699`
- latest ranking row: `2026-03-16 01:00:56.241571`

## Hypothesis Engine

- hypothesis families: `20`
- hypothesis definitions: `32`
- hypothesis templates: `9`
- hypothesis discovery runs: `10`
- hypothesis discovery candidates: `1350`
- hypothesis test runs: `339`
- hypothesis belief states: `32`
- signal observations: `8458`
- recommendation decisions: `2978`

Belief status breakdown:
- `DEGRADED = 21`
- `CANDIDATE = 11`
- `PROMISING = 0`
- `VALIDATED = 0`

## Execution Layer

- execution hypothesis definitions: `5`
- execution signals: `0`
- execution label values: `0`

The execution layer is now seeded and the held-position bridge is active, but no live execution signals have been persisted yet at this snapshot.

## Signal And Recommendation Flow

Signal status breakdown, last 24 hours:
- `MATCHED = 2880`
- `BLOCKED = 1021`
- `DISMISSED = 269`

Prediction source breakdown, last 24 hours:
- `VALIDATED_BASELINE = 4170`

Recommendation status breakdown, last 24 hours:
- `BLOCKED = 1021`
- `DISMISSED = 269`

Recommendation reason breakdown, last 24 hours:
- `hypothesis_degraded = 1021`
- `belief_insufficient = 269`

## News And Filings

- news articles: `42`
- news article links: `23`
- filing events: `12`
- latest news timestamp: `2026-03-16 09:30:34.000000`
- latest filing timestamp: `2026-03-13 14:02:43.000000`

## Model And Training Governance

- model versions: `31`
- validation runs: `31`
- training decisions: `45`

## Latest Successful Jobs At Snapshot

- `beta_execution_outcomes` at `2026-03-16 11:30:42.334184`
- `beta_intraday_execution_signals` at `2026-03-16 11:30:42.092110`
- `beta_intraday_execution_prepare` at `2026-03-16 11:30:41.912032`
- `beta_tracked_core_shadow_cycle` at `2026-03-16 11:30:09.704700`
- `beta_execution_outcomes` at `2026-03-16 11:29:38.172506`
- `beta_intraday_execution_signals` at `2026-03-16 11:29:37.956158`
- `beta_intraday_execution_prepare` at `2026-03-16 11:29:37.730956`
- `beta_execution_outcomes` at `2026-03-16 11:28:30.776157`
- `beta_intraday_execution_signals` at `2026-03-16 11:28:30.638065`
- `beta_intraday_execution_prepare` at `2026-03-16 11:28:30.393587`
- `beta_execution_outcomes` at `2026-03-16 11:27:26.198009`
- `beta_intraday_execution_signals` at `2026-03-16 11:27:25.996467`
- `beta_intraday_execution_prepare` at `2026-03-16 11:27:25.520568`
- `beta_execution_outcomes` at `2026-03-16 11:26:21.561849`
- `beta_intraday_execution_signals` at `2026-03-16 11:26:21.430741`

## Comparison Vs 2026-03-15 16:22:22 +00:00

Key deltas:
- DB file size: `+1,080,373,248` bytes (`+1.0062 GB`)
- instruments: `+413`
- open memberships: `+100`
- daily bars: `+191059`
- intraday snapshots: `+124`
- minute bars: `+124`
- feature values: `+1406466`
- label values: `+136080`
- score tape rows: `+3075`
- hypothesis definitions: `+3`
- hypothesis discovery runs: `+4`
- hypothesis discovery candidates: `+540`
- hypothesis test runs: `+123`
- signal observations: `+3607`
- recommendation decisions: `+1115`
- model versions: `+4`
- validation runs: `+4`
- training decisions: `+4`

Execution-layer deltas:
- position states: `0 -> 1`
- execution hypothesis definitions: `0 -> 5`
- execution signals: `0 -> 0`
- execution label values: `0 -> 0`

Belief-state deltas:
- `CANDIDATE: 8 -> 11`
- `DEGRADED: 21 -> 21`
- `PROMISING: 0 -> 0`
- `VALIDATED: 0 -> 0`

Interpretation:
- the system has continued building corpus depth and learning artifacts materially
- the execution layer is now present and prepared for held-position monitoring
- the engine is still not producing validated hypotheses or persisted execution signals yet
- live runtime behavior at this snapshot is dominated by intraday execution preparation and tracked-core monitoring, which is the intended market-hours profile
