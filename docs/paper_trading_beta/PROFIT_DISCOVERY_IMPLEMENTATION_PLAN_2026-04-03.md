# Profit Discovery Implementation Plan

Last updated: `2026-04-03`

Purpose: define a repo-specific plan for learning where the beta can make money reliably with minimal risk.

This plan uses the `Bob/` material as input, but it intentionally narrows the scope. The immediate goal is not "make beta production-ready." The immediate goal is:

1. identify narrow contexts where the system has repeatable post-cost edge
2. give the model a governed way to explore thousands of candidate patterns without brute-force overfitting
3. prove the strongest contexts survive live-forward observation
4. only then expand into broader automation

## Starting Point

Current evidence from [BETA_DB_BENCHMARK_2026-04-03_14-02-50Z.md](/C:/Users/labin/OneDrive/Documents/Equity-Tracker/docs/paper_trading_beta/BETA_DB_BENCHMARK_2026-04-03_14-02-50Z.md):

- the runtime is no longer frozen, but it is still a research system rather than a trade-effective system
- daily training is active, but `0` models are activated
- average validation sign accuracy remains `48.9021%`
- the execution layer is alive, but mostly emits `NO_ACTION` / `HOLD`
- there are still `0` live-forward trades
- the one thing that looks mildly promising is not "the whole beta"; it is a few narrow execution pockets in historical evidence

That means the safest high-value question is:

`Where do we have small, repeatable, post-cost edge with limited downside, and how can we prove it live before scaling anything?`

## Current Capability Boundary

### What Beta Can Test Reliably Today

The current beta can test:

- bounded daily hypotheses generated from the existing family/template system
- bounded intraday execution hypotheses generated from the existing execution templates
- realized post-cost outcomes for already-defined daily and intraday signal types
- narrow operating universes, especially symbols already supported by the live intraday path
- opening, midday, VWAP, gap, and closing-session behaviors already represented in the feature store

In practice, this means the beta is currently best suited to:

- testing specific profit pockets
- testing whether a pocket is real after costs
- testing whether a pocket survives live-forward observation
- testing whether a pocket improves downside-adjusted outcomes versus a baseline hold or no-action policy

### What Beta Cannot Test Reliably Yet

The current beta cannot reliably test:

- "any and all" possible profit opportunities
- open-ended strategy discovery across arbitrary features or arbitrary action spaces
- broad multi-symbol live-forward trading with trustworthy scaling conclusions
- portfolio-level profit optimization across many simultaneously active pockets
- strategy ideas that require data or features the current pipeline does not capture
- event types that require order-flow, block-trade, or external-news features the current pipeline does not yet collect

That boundary is intentional. The discovery funnel is template-bounded, budget-bounded, and promotion-bounded. This is a governance choice, not just a missing feature. It keeps the runtime searchable and reduces false discovery risk, but it also means we are searching a governed subset of the profit space rather than the full space.

## Target Expansion

The desired end state is not "keep the search tiny forever." The desired end state is:

- let the model explore thousands of candidate patterns
- keep that exploration structured enough that the results remain interpretable
- prevent the search process from rewarding noise, one-off events, or hidden lookahead

So the right evolution is not open-ended freeform strategy invention. The right evolution is controlled wide exploration.

That means:

- broad candidate generation
- narrow promotion
- harsh out-of-sample pruning
- tiny live-forward deployment

## What "Thousands Of Patterns" Should Mean

The system should eventually be able to explore many thousands of candidate patterns, but those patterns should be built from governed primitives rather than invented from scratch each run.

A pattern should be expressible as:

- an anchor event
- a context filter set
- an action hypothesis
- an outcome window
- a risk profile

Examples:

- after an abnormal selloff, does the next 15-90 minutes mean-revert or continue?
- after a dry low-volume compression period, does price break with enough follow-through after costs?
- after repeated failed VWAP reclaims, is late-day weakness more reliable than opening weakness?
- after unusually large relative-volume selling pressure, is the best action to buy, sell, wait, or avoid exiting at the worst point?

This is the right way to let the model search broadly while still keeping the search measurable.

## Pattern Primitive Library

To support broad exploration safely, the repo should introduce a pattern primitive library that the discovery system can compose into thousands of candidate tests.

Primitive categories should include:

- time anchors
  - opening window
  - midday lull
  - closing window
  - post-gap open
  - post-earnings day
- price behavior anchors
  - gap up
  - gap down
  - opening drive
  - VWAP reclaim
  - VWAP rejection
  - range compression
  - range expansion
- volume and participation anchors
  - abnormal relative volume
  - low-volume dry spell
  - sudden volume burst
  - repeated heavy selling proxy
  - repeated heavy buying proxy
- state and regime filters
  - symbol
  - sector
  - recent trend direction
  - realized volatility bucket
  - liquidity bucket
  - spread or slippage bucket
  - prior-day move bucket
- outcome definitions
  - next 15-minute return
  - next 30-minute return
  - close-to-close return
  - max adverse excursion
  - max favorable excursion
  - exit-quality improvement versus baseline

This is the mechanism that should allow thousands of tests without collapsing into arbitrary strategy sprawl.

## Principles

1. Risk-first, not coverage-first.
2. Prefer "do less, but better" over adding more signal types.
3. Focus on post-cost edge, not raw hit rate.
4. Require live-forward confirmation before any broader trust.
5. Optimize for interpretable profit pockets, not model count.
6. Keep the runtime lightweight enough that measurement remains trustworthy.

## What Success Looks Like

We should consider this plan successful if, within a focused validation window, we can name a small number of conditions like:

- "midday gap-down recovery shorts are not reliable enough"
- "held-position trim signals after specific opening-gap states reduce downside without materially harming upside"
- "a narrow long or short setup with specific state, regime, and liquidity constraints has stable post-cost edge live-forward"

The target output is a ranked evidence set of profit pockets, not a general claim that "the beta works."

## Where Profits May Be Hiding

The most plausible near-term opportunities are not broad market-wide alpha claims. They are small, recurrent micro-contexts where:

- behavior is structurally repeated
- execution quality matters
- costs can be estimated realistically
- downside can be bounded tightly

Right now the strongest place to look is the intraday layer, especially on symbols where the beta already has dense minute-bar coverage and a known live operating path.

The important shift is that "profit pockets" should not be read as "only a few hand-picked ideas." A pocket is the unit of measurement. The system should be able to generate many thousands of pockets as long as they are composed from interpretable primitives and judged under strict evidence rules.

## Event Families Worth Exploring

The next expansion should explicitly support event families like:

- post-selloff behavior
  - after sharp directional selling
  - after repeated red bars with rising relative volume
  - after a large intraday drawdown from the local high
- post-dry-period behavior
  - after low realized volatility
  - after narrow range compression
  - after low participation or stagnant VWAP distance
- post-exhaustion behavior
  - after failed breakouts
  - after repeated inability to hold VWAP
  - after late-session trend stalls
- post-imbalance behavior
  - after unusual opening gaps
  - after late-day ramps
  - after abrupt volume shocks

Those are exactly the kinds of situations where profits may be hiding, but they need to be represented as measurable event families rather than informal intuition.

## IBM As The First Search Surface

IBM deserves explicit focus in the next phase for four reasons:

1. it is already the dominant live intraday operating symbol in the current beta evidence
2. the current system already computes opening-bias and closing-bias features that are directly relevant to your observation
3. the execution layer and intraday feature layer already contain IBM-focused paths and tests
4. IBM appears to have meaningful start-of-day and end-of-day variation, which is exactly the kind of repeatable microstructure pocket the beta is better suited to test than broad daily alpha

That does not mean "IBM definitely has edge." It means IBM is the most practical first place to search for it.

It also does not mean IBM is the long-term boundary. IBM should be the first deep instrument study used to prove the wider exploration framework on one symbol before expanding that same framework across more names.

### IBM Pocket Candidates To Test First

These should be treated as explicit candidate pockets, not assumptions:

1. Opening weakness repair
   - cases where IBM sells off early, reclaims VWAP, and punishes low-quality panic exits
   - likely best framed first as an execution-quality / harm-reduction pocket rather than a fresh-entry pocket

2. Opening gap continuation versus gap fade
   - whether positive or negative opening gaps in IBM tend to continue, mean-revert, or flip after the first 15-30 minutes
   - especially useful if the payoff is asymmetric and adverse excursion stays contained

3. Late-session ramp confirmation
   - whether IBM's final 30 minutes show persistent continuation after specific midday states
   - especially relevant because the feature layer already computes historical closing bias

4. VWAP hold versus VWAP breakdown
   - whether IBM behaves predictably when price reclaims and holds VWAP versus losing VWAP after strength
   - likely useful as a "hold / trim / avoid bad exit" pocket

5. Midday gap-down recovery
   - the current benchmark already hints that `MIDDAY__GAP_DOWN_RECOVERY` may be better than `OPEN__GAP_DOWN_RECOVERY`
   - this is a good example of why pockets must be separated by session segment instead of lumped into one state family

### IBM-Specific Questions The Plan Should Answer

For IBM specifically, the next evidence pass should answer:

- Is the opening hour systematically weak, strong, or just volatile?
- Does the first 15-minute direction help predict the next 30-120 minutes?
- Are opening reversals better for reducing exit error than for generating new entries?
- Is the closing 30-minute drift positive enough after costs to matter?
- Which IBM states are misleading because they look good on tiny samples?
- Which IBM states stay stable across weeks instead of being driven by a few abnormal sessions?

## Non-Goals For This Phase

- broad buy-side recommendation engine
- portfolio-wide automated sizing for dozens of hypotheses
- production deployment of the beta as a user-facing trading advisor
- maximizing signal volume

## Plan

### Phase 0: Stabilize The Measurement Surface

Objective: ensure the runtime and evidence pipeline are reliable enough that we can trust any profitability conclusions.

Work:

- keep the recent prediction-accuracy logging and reconciliation fixes in place for both daily and intraday paths
- add a compact beta profitability snapshot job that records:
  - signal counts
  - realized post-cost returns
  - max adverse move
  - max favorable move
  - directional accuracy
  - calibration by confidence band
  - sample size by pocket
- add a "measurement freshness" panel to the beta UI that shows:
  - latest prediction log timestamp
  - latest realized outcome timestamp
  - latest live-forward trade timestamp
  - stale subsystems if any leg stops updating
- keep memory pressure visible and bounded so a stalled runtime cannot masquerade as a healthy one

Deliverables:

- trustworthy daily evidence snapshots
- runtime-health flags tied to profitability measurement, not just heartbeat

Exit criteria:

- prediction logs and realized outcomes update automatically for both daily and execution signals
- no silent gap between signal generation and realized-outcome recording
- runtime can run for multiple sessions without measurement stalls

### Phase 1: Define Profit Pockets Explicitly

Objective: move from "hypotheses" to "tradeable context buckets" that can be judged independently.

Work:

- introduce a profit-pocket analysis layer over existing signal and execution data
- introduce a pattern primitive registry and candidate-generation service so the model can compose many thousands of candidate pockets from governed event families
- group realized outcomes by a stable pocket key such as:
  - engine: `DAILY` or `INTRADAY`
  - action family
  - symbol
  - state code / state family
  - session state
  - session segment: `OPENING`, `MIDDAY`, `CLOSING`
  - event trigger code
  - direction
  - market
  - liquidity/volume bucket
  - volatility bucket
  - hold window bucket
- compute per-pocket metrics:
  - sample size
  - post-cost mean return
  - median return
  - win rate
  - downside tail
  - max adverse excursion
  - calibration quality
  - recency-weighted performance
  - stability across subwindows
- rank pockets by "reliability under risk" rather than pure return
- deduplicate near-identical candidate pockets so the search does not overcount slight variants of the same idea
- require train / validation / holdout separation so large candidate sets do not turn noise into false confidence
- record which primitive combination produced each pocket so the best pockets remain explainable

Suggested ranking formula:

- positive expected post-cost edge
- acceptable max adverse excursion
- acceptable drawdown profile
- enough recent samples
- low regime fragility
- low dependence on one symbol or one date cluster

Deliverables:

- a persisted pocket leaderboard
- a persisted candidate-pattern registry
- a pocket-level review doc or beta UI table
- a clear "top 5 promising / bottom 5 misleading" list

Exit criteria:

- we can point to specific pockets that are positive, stable, and interpretable
- we can also explicitly retire pockets that look attractive only because of sparse or concentrated history
- the system can search a large governed candidate space without exploding runtime or overwhelming the review surface

### Phase 1A: Add Event-Driven Pattern Exploration

Objective: let the model explore a much wider family of opportunities without pretending that "anything imaginable" is testable immediately.

Work:

- add event extractors for:
  - selloff intensity
  - buy-pressure intensity
  - dry-period compression
  - abnormal range expansion
  - abnormal relative-volume bursts
  - late-session acceleration
- define proxy features for ideas that are intuitively useful but not directly observable yet
  - "large sale of shares" should initially be represented as price-impact plus abnormal selling volume, not as true block-trade knowledge
  - "dry period" should be represented as low range, low realized volatility, low volume, and low VWAP drift
- let the discovery system compose:
  - anchor event
  - context filters
  - direction
  - action
  - hold window
  - stop or risk rule
- budget exploration per family so one event class cannot flood the search space
- store failure reasons for rejected candidates so the system learns which families are producing only noise

Deliverables:

- event-family registry
- candidate-generation logs
- top pattern families by signal quality, not just top individual pockets

Exit criteria:

- the system can examine thousands of interpretable patterns
- the search remains reproducible
- candidate quality is reviewable at both pocket level and family level

### Phase 2: Minimal-Risk Live-Forward Validation

Objective: test only the best pockets in live-forward mode with tight risk and zero ambition to scale fast.

Work:

- create a live-forward "micro deployment" mode:
  - disabled by default
  - only allows whitelisted pockets
  - only one or two active pockets at a time
  - tiny paper position size
  - strict per-day and per-pocket limits
- prioritize low-risk use cases first:
  - held-position execution overlays
  - avoid-selling-into-panic style risk reduction
  - trim timing where downside asymmetry is favorable
- defer broad entry/buy recommendations until a pocket proves itself
- track live-forward versus historical expectation:
  - realized post-cost return
  - adverse excursion
  - slippage / latency sensitivity
  - hypothesis drift

Guardrails:

- no activation if sample size is below threshold
- no activation if live calibration is poor
- no activation if one symbol dominates the pocket
- automatic pause after consecutive losses or adverse-excursion breach

Deliverables:

- live-forward evidence on a very small number of pockets
- dashboard comparing expected versus actual pocket behavior

Exit criteria:

- at least one pocket remains positive and stable in live-forward evidence
- or we conclude that the apparent edge was historical-only and retire it quickly

### Phase 3: Profit Preservation Before Profit Expansion

Objective: if any pocket survives, expand in the safest direction first.

Order of expansion:

1. execution overlays that reduce bad exits
2. execution overlays that modestly improve good exits
3. narrow entry timing for already-approved daily theses
4. only then consider independent buy-signal expansion

Work:

- build a "harm reduction" score for held-position signals:
  - does the signal reduce adverse outcome frequency?
  - does it preserve enough upside after costs?
- compare three policies for the same pocket:
  - baseline hold
  - current beta action
  - constrained action with tighter guardrails
- only keep the version that improves downside-adjusted results

Deliverables:

- a decision memo for each surviving pocket:
  - keep
  - constrain
  - retire

Exit criteria:

- we can show that a surviving action improves downside-adjusted outcomes against a clear baseline

### Phase 4: Broader System Decisions

Objective: decide what beta should become once the evidence is real.

Possible outcomes:

- focused execution assistant for held positions
- narrow strategy engine for a few validated pockets
- research-only sandbox with no production ambition
- broader signal generator, only if evidence becomes much stronger than it is today

This phase should happen only after the earlier phases produce live-forward evidence worth trusting.

## Concrete Build Order

Recommended implementation order inside the repo:

1. extend `prediction_accuracy_service.py` outputs into a persisted pocket snapshot model/service
2. add a pattern primitive registry and event-family extractor service
3. add pocket-level post-cost outcome aggregation for execution signals and daily observations
4. add a beta UI page or panel for pocket leaderboard, candidate families, and live-forward pocket status
5. add a whitelist-based live-forward micro deployment mode in the supervisor/runtime settings
6. add automatic retirement / pause rules for failing pockets

## Metrics That Matter

The core scorecard for this plan should be:

- pocket sample size
- pocket recency-weighted post-cost edge
- pocket median post-cost return
- pocket directional accuracy
- pocket calibration error
- pocket max adverse excursion
- pocket worst subwindow performance
- symbol concentration within pocket
- live-forward versus historical drift

Metrics that should not drive decisions by themselves:

- total model count
- total hypotheses generated
- raw signal count
- raw win rate without cost and excursion context

## Proposed Decision Gates

### Gate 1: Measurement Trust

Pass if:

- evidence capture is complete and fresh
- profitability stats are reproducible across runs
- runtime is stable enough that missed cycles are rare and visible

Fail action:

- stop feature expansion and fix the runtime/instrumentation surface first

### Gate 2: Historical Pocket Quality

Pass if:

- at least a small number of pockets show positive post-cost edge
- those pockets are not driven by one symbol, one week, or one unusual market slice
- downside profile is acceptable

Fail action:

- prune aggressively and simplify the search space rather than adding more hypotheses

### Gate 3: Live-Forward Pocket Quality

Pass if:

- at least one whitelisted pocket remains positive and stable live-forward
- drift versus history is tolerable
- adverse excursion stays inside limits

Fail action:

- retire the pocket and continue only if another pocket has stronger evidence

### Gate 4: Role Decision

Decide whether beta should be:

- a risk-reduction execution layer
- a narrow profit-seeking engine
- a research-only environment

## Repo-Specific Recommendations

Based on the current code and benchmark state, these are the most sensible immediate priorities:

- prioritize execution-layer pocket analysis before broad buy-signal work
- treat daily-hypothesis validation as a ranking input, not a source of tradable trust by itself
- keep memory/runtime work in scope because unstable measurement invalidates research conclusions
- prefer whitelisted live-forward tests over broad "turn on paper trading"
- keep the product framing modest until evidence exists
- use IBM as the first deep instrument study for open/close pocket discovery before broadening to other names
- represent intuitive ideas like "large sale of shares" and "dry period" as explicit event families with measurable proxies instead of leaving them as human-only observations

## What I Would Not Do Yet

- build a broad recommendation engine for beta trading actions
- add more hypothesis families just to increase coverage
- optimize for activation rate before understanding pocket quality
- treat `48.9%` validation sign accuracy as close enough to proceed
- expand to many simultaneous live-forward pockets

## Suggested Next Implementation Slice

The next concrete slice should be:

1. add a `profit pocket` snapshot/aggregation service for existing realized outcomes
2. add a pattern primitive and event-family registry for intraday exploration
3. expose a ranked pocket leaderboard in the beta UI
4. add an IBM-specific open/midday/close plus selloff/dry-period breakdown to that leaderboard
5. add whitelist-only live-forward evaluation for the top one or two pockets

That will answer the real objective fastest:

`where can profits be made reliably with minimal risk, if anywhere?`
