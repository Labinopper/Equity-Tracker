# Paper Trading Beta Next-Step Proposal

Last updated: `2026-03-18`

This proposal is based on:

- the current implementation under `equity_tracker/src/beta/`
- the live beta DB at `C:\EquityTrackerData\portfolio.beta_research.db`
- the review and current-state notes written on `2026-03-18`

## 1. Executive View

The beta has moved past "can we ingest and score data?" and into a more specific problem:

- the runtime works
- the corpus is large enough to learn from
- the hypothesis engine is producing evidence
- the intraday execution layer is live

But the system is still not turning research into demo trades because it is too concentrated around one target design:

- one main research horizon: `fwd_5d_excess_return_pct`
- one dominant family: `EXHAUSTION_REVERSAL`
- one intraday operating symbol with meaningful live evidence: `IBM`

The next step should not be "add a lot more stuff everywhere." It should be:

1. turn the beta into a `multi-horizon daily research engine`
2. use `family-specific holding periods` instead of one default `5d`
3. treat intraday as an `execution and timing layer first`, not a full primary alpha engine
4. move from broad hypothesis generation to `targeted expansion plus pruning`

## 2. What We Have Learned So Far

## 2.1 The system is operational, but the trade path is still blocked

Validated on `2026-03-18`:

- `beta_daily_bars`: `1409676`
- `beta_feature_values`: `20914160`
- `beta_label_values`: `2023461`
- `beta_job_runs`: `6336`
- `beta_signal_observations`: `33521`
- `beta_demo_positions`: `0`

Interpretation:

- data ingestion and feature/label materialization are not the main blockers anymore
- the blocker is the bridge from evidence to promotion to recommendation to demo execution

## 2.2 The current research engine is over-centered on a single horizon

Current live state:

- only three label definitions exist
- all three are `5d`
- every hypothesis family and every hypothesis definition still targets `fwd_5d_excess_return_pct`

Interpretation:

- the system is forcing every family to express value over the same holding period
- that is likely suppressing good short-horizon mean reversion and good medium-horizon trend continuation

## 2.3 The current "winning" research is very concentrated

Current live family rollup:

- `EXHAUSTION_REVERSAL`: `10` definitions, `7` promising, `2` candidate
- nearly every other family is degraded, rejected, or still weak

Interpretation:

- the system does not primarily need "more random hypotheses"
- it needs:
  - more discriminating targets
  - better family-specific horizons
  - better feature support for non-exhaustion families

## 2.4 Recommendation gating is mostly blocking degraded hypotheses

Current live recommendation reasons:

- `hypothesis_degraded`: `6450`
- `hypothesis_rejected`: `2168`
- `belief_insufficient`: `1195`
- `hypothesis_not_validated`: `518`

Interpretation:

- the engine is spending a lot of effort repeatedly rediscovering why already-poor ideas are not promotable
- that is useful for audit, but it is not the best next use of runtime and cognitive budget

## 2.5 Intraday is useful today, but narrow

Current live intraday state:

- `beta_execution_signals`: `128`
- `NO_ACTION`: `122`
- `WAIT_FOR_CLOSE_CONFIRMATION`: `6`
- all current execution signals are for `IBM`
- `beta_minute_bars` are dominated by `IBM` minute coverage
- intraday feature outputs still leave `cumulative_volume_vs_expected`, `volume_last_15m_vs_expected`, and `distance_from_vwap_pct` as `None`

Interpretation:

- the intraday layer is already useful as a held-position execution monitor
- it is not yet broad or rich enough to be the primary source of discovery alpha

## 3. Core Recommendation

The next stage should be:

- `daily alpha discovery` remains the primary source of idea generation
- `intraday` becomes the timing and execution-quality layer for a daily thesis
- the research engine moves from one-horizon-fits-all to a `horizon matrix`

The practical target is:

- keep `5d` as the main comparison anchor
- add `3d`, `10d`, and eventually `20d` where they are justified by family behavior
- assign horizons by family, not globally

## 4. Horizon Strategy

## 4.1 What to do about 3-day, 10-day, and longer horizons

Yes, the beta should move beyond a pure `5d` target.

But it should not explode into every possible horizon at once. The right move is:

- keep `5d` as the current benchmark anchor
- add `3d` and `10d` next
- add `20d` only for trend/relative-strength families after `10d` is working

## 4.2 Proposed label expansion

Add these next:

- `fwd_3d_return_pct`
- `fwd_3d_excess_return_pct`
- `fwd_10d_return_pct`
- `fwd_10d_excess_return_pct`

Add later if the `10d` path works:

- `fwd_20d_return_pct`
- `fwd_20d_excess_return_pct`

Optional, but lower priority:

- sector-relative versions for `3d` and `10d`

Why this shape:

- `3d` captures fast mean reversion and post-event reactions better than `5d`
- `10d` captures slower trend, leadership, and sector-rotation effects better than `5d`
- `20d` is useful, but only once the system proves it can support trend families rather than overfitting short-term reversals

## 4.3 Proposed family-to-horizon mapping

Use a family-specific target map rather than one default horizon:

- `EXHAUSTION_REVERSAL`
  - primary: `3d`
  - secondary: `5d`
- `MEAN_REVERSION`
  - primary: `3d`
  - secondary: `5d`
- `PANIC_REVERSAL`
  - primary: `3d`
  - secondary: `5d`
- `CATALYST_CONFIRMATION`
  - primary: `3d`
  - secondary: `5d`
  - optional later: `10d`
- `TREND_PULLBACK_RECOVERY`
  - primary: `10d`
  - secondary: `5d`
- `RELATIVE_STRENGTH`
  - primary: `10d`
  - secondary: `20d`
- `SECTOR_MOMENTUM`
  - primary: `10d`
  - secondary: `20d`
- `ACCUMULATION`
  - primary: `10d`
  - secondary: `20d`
- `TREND_STABILITY`
  - primary: `10d`
  - secondary: `20d`
- `TREND_FAILURE`
  - primary: `3d`
  - secondary: `5d`
- `FAILED_REVERSAL`
  - primary: `3d`
  - secondary: `5d`
- `PANIC_TREND`
  - primary: `3d`
  - secondary: `5d`

The important rule is:

- do not make every family compete on the same outcome definition

## 5. Feature Expansion

## 5.1 What is already present

The daily feature layer already includes:

- `1d`, `5d`, `10d`, `20d` price returns
- `5d` and `20d` volatility
- mean-reversion distance and drawdown/rebound features
- `1d` and `5d` market, benchmark, and sector relative context
- `3d` and `7d` news context
- `7d` and `14d` official-release context

This is a decent base. The issue is not that the beta has no features. The issue is that the target structure is narrower than the feature structure.

## 5.2 What should be added next

Add the following daily features before adding lots of new families:

- `ret_3d_pct`
- `benchmark_ret_10d_pct`
- `benchmark_excess_10d_pct`
- `market_ret_10d_pct`
- `market_excess_10d_pct`
- `sector_ret_10d_pct`
- `sector_excess_10d_pct`
- `realized_vol_10d_pct`
- `distance_from_10d_mean_pct`

Add these event-freshness features:

- `news_count_1d`
- `news_sentiment_1d`
- `official_count_1d`
- `official_sentiment_1d`
- `days_since_latest_news`
- `days_since_latest_official_release`

Add these regime/context features:

- market volatility regime bucket
- sector volatility regime bucket
- benchmark trend regime bucket
- breadth-style proxy derived from same-market peer return dispersion

Why these matter:

- `3d` and `10d` targets need matching feature context
- catalyst families need `1d` freshness, not only `3d/7d`
- trend families need richer medium-horizon relative context

## 6. Hypothesis Strategy

## 6.1 Do we need more hypotheses?

Not in the generic sense.

The evidence says:

- the system already has `20` families and `34` definitions
- almost all current promising evidence comes from `EXHAUSTION_REVERSAL`

So the right next move is:

- `more targeted hypotheses`
- `fewer undifferentiated families in the demo lane`

## 6.2 What to expand

Expand first:

- `EXHAUSTION_REVERSAL`
  - split into `3d` and `5d` tracks
  - separate weak-market panic bounce from idiosyncratic oversold reversal
  - test second-day follow-through versus immediate snapback behavior

- `CATALYST_CONFIRMATION`
  - add freshness windows: `1d`, `3d`, `5d`
  - separate official-release driven setups from general news-driven setups
  - require stronger post-event price confirmation logic

- `TREND_PULLBACK_RECOVERY`
  - move primary testing to `10d`
  - use `10d/20d` trend context and `3d/5d` pullback depth as conditioning features

- `RELATIVE_STRENGTH` and `SECTOR_MOMENTUM`
  - move primary testing to `10d`
  - keep only if they remain stable after the new horizon is introduced

## 6.3 What to prune or freeze

Freeze from demo-eligibility until new features or horizons exist:

- degraded families with repeated poor evidence
- families that are only producing blocked decisions due to the same reason chain

Specifically, make these states operationally distinct:

- `research_only`
- `watch_only`
- `demo_eligible`

That avoids repeatedly emitting recommendation decisions for ideas we already know are not ready.

## 6.4 Family diversity goal

The goal for the next stage should be:

- at least `3` genuinely viable families
- at least one short-horizon family
- at least one event/catalyst family
- at least one medium-horizon trend or relative-strength family

Until that happens, adding even more families is probably noise.

## 7. Model And Promotion Strategy

## 7.1 Current problem

The live DB shows:

- challenger models are training
- no model is becoming active
- no strategy is active
- shadow scoring therefore stays in `validated_baseline_mode` or no-model mode

## 7.2 Recommended next-step change

Introduce a two-lane promotion design:

- `research lane`
  - keep full challenger training and walk-forward validation
- `demo lane`
  - allow a family-rule or validated-baseline strategy to become active even if a general ML model is not yet promotable

That means:

- do not block demo progression solely on the absence of a promoted general model
- allow a `family champion` strategy to be demo-eligible if:
  - it has enough support
  - its out-of-sample edge is positive
  - its stability score clears a threshold
  - it beats the appropriate baseline for its horizon

## 7.3 Training cadence

The system should reduce training churn while this stage is being stabilized.

Recommended:

- train once nightly by default
- allow one ad hoc retrain after major corpus/feature changes
- do not keep creating challengers every short interval when activation is consistently blocked

## 8. Recommendation And Demo-Trade Logic

## 8.1 Current issue

The current score path can produce many observations while still producing:

- zero recommended entries
- zero opened positions
- lots of blocked or dismissed decisions

## 8.2 Recommended change

Split scoring outcomes into clearer lanes:

- `research evidence`
- `watch candidate`
- `demo-eligible candidate`

Use much stricter rules for the third lane, but do not let the first two dominate runtime/UI noise.

## 8.3 Make trade expression family-aware

Current demo positions use static defaults:

- target return: `4%`
- stop loss: `-3%`
- planned horizon: `5 days`

That should change.

Use family-aware defaults such as:

- exhaustion / panic reversal
  - horizon `3d`
  - tighter stop
  - earlier scale-out into strength
- catalyst confirmation
  - horizon `3d` or `5d`
  - event-dependent stop and hold rules
- trend / relative strength
  - horizon `10d`
  - wider stop
  - fewer premature exits

Without this, even correct ideas are being expressed with the wrong exit logic.

## 9. Intraday Proposal

## 9.1 What the intraday layer should be in the next stage

The next stage should keep intraday as:

- `execution guidance`
- `timing confirmation`
- `risk-off / trim / hold management`

It should not yet become:

- a full-market standalone intraday alpha engine

That would be premature given current coverage and feature richness.

## 9.2 What the intraday layer needs next

Implement the missing intraday features first:

- `distance_from_vwap_pct`
- `cumulative_volume_vs_expected`
- `volume_last_15m_vs_expected`

Add these next:

- opening range breakout / fade markers
- time-since-open bucket
- time-since-last-event bucket
- intraday volatility versus recent session baseline
- trend persistence since first `30m`

These are higher priority than adding a large number of new execution hypotheses.

## 9.3 Intraday hypothesis expansion

After the missing features exist, expand the intraday execution set from `5` hypotheses to roughly `10-12`, focused on:

- opening gap continuation vs gap fade
- failed breakout after new session high
- failed breakdown after new session low
- trend day hold logic
- mean-reversion fade after overshoot
- post-event volatility compression versus expansion

But keep the scope narrow:

- held positions
- active thesis names
- names already validated by the daily layer

## 9.4 Use intraday as an entry-timing overlay

The best next use of intraday is:

- daily engine says: `this is a valid thesis`
- intraday engine says:
  - enter now
  - wait for better confirmation
  - avoid chasing
  - trim / hold / exit

That is a stronger intermediate step than trying to discover intraday alpha directly across the full universe.

## 9.5 Intraday data coverage target

Before intraday becomes more ambitious, reach this baseline:

- minute-bar capture for all held positions
- minute-bar capture for active thesis names
- at least `60-90` trading days of intraday history for the active execution universe
- execution labels across multiple symbols, not just one held name

## 10. Recommended Implementation Plan

## Phase 1: Horizon Expansion

Deliver:

- `3d` and `10d` benchmark-excess labels
- family-specific target metric mapping
- family-specific holding-period variants
- `3d` and `10d` daily feature support

Success condition:

- the hypothesis engine can evaluate the same family under multiple justified horizons

## Phase 2: Hypothesis Refocus

Deliver:

- promote `EXHAUSTION_REVERSAL` into a multi-horizon family
- expand `CATALYST_CONFIRMATION` with freshness-aware variants
- rebuild trend/relative-strength families against `10d`
- freeze persistently degraded families from demo eligibility

Success condition:

- at least `3` families show live promising/candidate evidence with differentiated roles

## Phase 3: Demo-Lane Activation

Deliver:

- family-aware trade expression
- clearer recommendation lanes
- active baseline or family-champion demo strategy even if the full ML model is still challenger-only

Success condition:

- the system opens controlled demo positions rather than remaining permanently in blocked-shadow mode

## Phase 4: Intraday Execution Upgrade

Deliver:

- VWAP and expected-volume intraday features
- richer execution hypotheses
- entry timing overlay for daily-approved names
- execution evaluation across multiple symbols

Success condition:

- intraday improves entry/exit quality measurably instead of just logging `NO_ACTION`

## 11. Concrete Success Metrics

The next stage should aim for these measurable outcomes:

- more than one active or demo-eligible hypothesis family
- at least one non-exhaustion family showing stable positive out-of-sample evidence
- non-zero recommended candidates in current shadow cycles
- first stable demo positions opened and managed end to end
- at least `20%` of recommendation decisions no longer blocked by degraded-family logic
- intraday execution signals generated across more than one symbol
- execution labels accumulating across at least `5` active thesis names

## 12. Bottom Line

The beta does not mainly need:

- a bigger pile of generic hypotheses
- a bigger pile of intraday rules
- more frequent challenger training

It mainly needs:

- `multi-horizon targets`
- `family-specific holding periods`
- `targeted expansion of the families that are already showing signal`
- `pruning of degraded families from the demo path`
- an intraday layer that improves execution quality for daily theses

The best next version of the beta is:

- a daily multi-horizon research engine
- with a narrower, smarter demo lane
- and an intraday execution overlay that helps good daily ideas trade better
