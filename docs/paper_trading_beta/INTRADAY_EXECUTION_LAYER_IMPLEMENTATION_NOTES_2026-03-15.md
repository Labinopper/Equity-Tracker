# Intraday Execution Layer Implementation Notes

Date: 2026-03-15

## Architecture

The beta now has a two-horizon structure:

1. Daily thesis engine
   - discovers and validates multi-day trade theses
   - remains authoritative for entries

2. Intraday execution layer
   - tracks held positions
   - aggregates minute-level data into execution features
   - emits execution-only guidance for timing exits
   - never generates buy signals

## New persistent objects

- `beta_position_states`
- `beta_minute_bars`
- `beta_intraday_feature_snapshots`
- `beta_execution_hypothesis_definitions`
- `beta_execution_signals`
- `beta_execution_label_values`

## New services

- `position_registry.py`
- `intraday_priority_service.py`
- `intraday_aggregation_service.py`
- `intraday_feature_service.py`
- `execution_hypothesis_service.py`
- `execution_signal_service.py`
- `execution_outcome_service.py`

## Scheduler shape

The supervisor now runs intraday execution work ahead of the daily research loop:

1. prepare intraday held-symbol context
2. evaluate execution signals
3. update execution outcomes
4. run daily observation/scoring/evaluation
5. run daily hypothesis discovery/backtests/belief refresh

## Runtime discipline

The intraday layer stays inside the current constraints by:

- reusing raw `beta_intraday_snapshots`
- aggregating to minute bars incrementally
- limiting watchlists by held, active-thesis, and general tiers
- evaluating execution signals only for held positions
- using event-triggered evaluations instead of continuous whole-universe intraday scoring

## Future extensions

This design leaves room for:

- intraday hypothesis discovery
- liquidity-aware exit logic
- adaptive thesis horizon estimation
- execution reinforcement learning

The current implementation intentionally stops short of those pieces and keeps the intraday layer execution-only.
