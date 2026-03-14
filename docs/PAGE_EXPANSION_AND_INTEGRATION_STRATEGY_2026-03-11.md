# Page Expansion And Integration Strategy

Date: `2026-03-11`

Status: strategic expansion review

Scope: identify additional pages that are genuinely missing, identify existing features that should be enhanced inside current pages instead of split out, and define the decision rule for when a new page is justified.

## 1. Expansion Rule

A new page should exist only if it serves a meaningfully new decision purpose.

A page does justify its own route when all of the following are true:

1. It answers a distinct primary question that is not already the primary question of another page.
2. It has enough state, workflow, or audit depth that embedding it inside an existing page would overload that page.
3. It creates a reusable user pathway, not a one-off utility panel.
4. It improves decision clarity rather than scattering the user across more surfaces.

A page should not be created if it is primarily:

- a filter preset for existing data
- a different visualisation of an existing page’s main question
- a one-card enhancement that can sit inside an existing workflow
- a drill-down that should be reached from a current page

## 2. Current Surface Assessment

The app already covers most of the major strategic surfaces:

- current position
- deployable capital
- risk
- sell execution
- tax timing
- historical audit/reconciliation
- data quality
- wrapper and drag analysis
- retirement context
- behavioural review

So the main opportunity is not “add many more pages”.

The real opportunity is:

1. fill a small number of missing decision surfaces
2. add stronger drill-through inside existing pages
3. avoid creating duplicate analytical destinations

## 3. Recommended New Pages

These are the pages that appear justified because they would answer genuinely new questions.

### 3.1 Lot Explorer

Proposed route:

- `/lot-explorer`

Primary purpose:

- forensic lot-level inspection across all holdings and states

Why this is a new page:

- many current pages depend on lot-level truth, but lot inspection is scattered
- `Portfolio`, `Per Scheme`, `History`, `Dividends`, `Employment Tax Events`, and `Reconcile` all benefit from a single canonical lot-forensics destination
- this is a cross-cutting evidence surface, not a subsection of one page

Key questions:

1. Which lots drive current deployable capital?
2. Which lots are locked, forfeitable, ISA-sheltered, or tax-heavy?
3. Which lots carry unusual basis, FX, or transfer history?
4. Which lots are likely to be consumed next on disposal?

Content:

- one row per lot
- filters for scheme, security, sellability, wrapper, acquisition window, tax year
- columns for quantity remaining, cost basis, true cost, current market value, locked/forfeitable status, acquisition FX provenance, transfer history marker, disposal-consumption status
- links to `History`, `Reconcile`, `Audit`, `Transfer`, `Edit Lot`

Why not just embed:

- this would overload `Portfolio`
- this is an evidence/workbench surface, not a top-level decision band

Priority: high

### 3.2 Decision Trace

Proposed route:

- `/decision-trace`

Primary purpose:

- single destination for “why is this number what it is?”

Why this is a new page:

- traceability currently exists, but it is spread across `Reconcile`, `Audit`, `Basis Timeline`, page notes, and per-surface context
- a unified trace page would make the app easier to trust without turning each page into a debug console

Key questions:

1. What inputs produced this number?
2. Which assumptions, prices, FX rates, and ledger events are in scope?
3. Which pages are reading the same underlying state differently, and why?

Content:

- metric selector or deep-link query params from pages
- source inputs
- basis details
- assumptions used
- related mutations
- affected pages

Why not just embed:

- trace is a cross-page concern
- embedding this everywhere would make already-dense pages worse

Priority: medium-high

### 3.3 Actions Queue

Proposed route:

- `/actions`

Primary purpose:

- unify all actionable items that require intervention, not just review

Why this is a new page:

- `Notification Digest` is an attention queue
- `Weekly Review` is a behavioural workflow
- `Data Quality` is a maintenance queue
- `Calendar` is a timing queue

There is still no single operational execution queue for:

- items requiring a decision
- items requiring data repair
- items requiring a transaction
- items requiring assumption updates

Key questions:

1. What needs action now?
2. What type of action is required?
3. What page should I go to next?

Content:

- grouped actions: `dispose`, `plan`, `review`, `fix data`, `update settings`, `trace discrepancy`
- severity and due-date logic from deterministic sources only
- one-click link into the destination page

Why not just embed:

- this is cross-page orchestration
- it should not overload `Insights`, `Notification Digest`, or `Weekly Review`

Priority: medium

### 3.4 Assumption Impact

Proposed route:

- `/assumption-impact`

Primary purpose:

- show how settings and model assumptions constrain outputs across the app

Why this is a new page:

- `Settings` should remain the control surface
- `Data Quality` should remain the data-quality surface
- but there is no dedicated cross-app view for “which assumptions most distort which outputs?”

Key questions:

1. Which assumptions are missing, stale, or low-confidence?
2. Which pages are materially constrained as a result?
3. Which assumption updates would unlock the most accuracy?

Content:

- settings completeness
- assumption-to-page dependency map
- output sensitivity labels
- highest-value fixes first

Why not just embed:

- putting this all in `Settings` would make that page too operationally broad
- this is a distinct trust and model-governance surface

Priority: medium

## 4. Features That Should Stay Inside Existing Pages

These are important capabilities, but they should not become new pages.

### 4.1 Portfolio enhancements

Should stay inside `Portfolio`:

- top-card drill-through to contributing lots
- better action links from metrics
- more explicit delta against `Capital Stack`
- better estimate-quality signalling

Do not create:

- separate “deployable components” page
- separate “top holdings” page
- separate “sellable holdings” page

### 4.2 Capital Stack enhancements

Should stay inside `Capital Stack`:

- deduction-quality badges
- deduction trace links
- richer fee/tax block assumptions
- combined holdings-plus-cash decomposition

Do not create:

- separate “tax drag breakdown” page unless it becomes a fully cross-surface ledger

### 4.3 Simulate enhancements

Should stay inside `Simulate`:

- price provenance near entered price
- source/basis explanation
- links to reporting and reconcile
- clearer pre-commit vs commit separation

Do not create:

- separate “commit review” page
- separate “simulation details” page

### 4.4 Sell Plan enhancements

Should stay inside `Sell Plan`:

- richer planned-vs-committed reconciliation
- reduced plan-card density
- saved presets
- better tranche execution traceability

Do not create:

- separate “tranche manager” page
- separate “sell execution dashboard” unless the app starts supporting live broker execution state

### 4.5 Tax Plan enhancements

Should stay inside `Tax Plan`:

- clearer decision summary
- stronger exact-versus-estimate labels
- route to imported scenario assumptions
- stronger realised-baseline references

Do not create:

- separate ANI page
- separate pension-tax interplay page

### 4.6 Risk enhancements

Should stay inside `Risk`:

- trend overlays
- persistent guardrail history
- deeper optionality timeline interpretation

Do not create:

- separate concentration page
- separate optionality page

### 4.7 Data trust enhancements

Should stay inside existing pages:

- remediation links in `Data Quality`
- stronger explanation routing from `Reconcile`
- richer provenance in `Calendar`, `Basis Timeline`, and `Audit`

Do not create:

- separate “FX issues” page
- separate “stale price issues” page

### 4.8 Mutation workflow enhancements

Should stay inside:

- `Add Lot`
- `Transfer Lot`
- `Edit Lot`
- `Add Security`

Features:

- richer before/after previews
- record-creation previews
- downstream impact hints
- duplicate/conflict resolution

Do not create:

- separate “mutation preview” page

## 5. Features That Could Become New Pages Later, But Not Yet

These could justify a page only if the app expands materially beyond its current state.

### 5.1 Broker Execution Ledger

Possible future route:

- `/execution-ledger`

Only justified if:

- live broker-order lifecycle, fills, cancellations, and export/import round-trips become important

Not justified yet because:

- current sell execution remains deterministic planning plus commit, not broker-state operations

### 5.2 Income Events

Possible future route:

- `/income-events`

Only justified if:

- dividends, employment tax events, pension contributions, and future cash receipts need one unified income/cashflow evidence surface

Not justified yet because:

- `Dividends`, `Employment Tax Events`, `Cash`, and `Pension` already cover the current functional scope

### 5.3 Review History

Possible future route:

- `/review-history`

Only justified if:

- weekly review notes and completion history become long-lived comparative data

Not justified yet because:

- this can remain part of `Weekly Review` first

## 6. Recommended Integration Upgrades By Existing Page

### 6.1 Portfolio

Add:

- lot drill-through
- direct links to `Lot Explorer` once built
- stronger handoff to `Simulate`, `Sell Plan`, and `Capital Stack`

### 6.2 Risk

Add:

- direct links from concentration and optionality alerts to `Sell Plan`, `Calendar`, and `Allocation Planner`

### 6.3 Capital Stack

Add:

- deduction block deep links into `Tax Plan`, `Fee Drag`, `Cash`, `Employment Tax Events`, `Decision Trace`

### 6.4 Simulate

Add:

- links to `Decision Trace`, `CGT`, `Economic Gain`, `Reconcile`

### 6.5 Sell Plan

Add:

- stronger links into `Calendar`, `Simulate`, `Decision Trace`, and eventual `Actions Queue`

### 6.6 Data Quality / Settings

Add:

- tighter assumption and remediation routing
- links into eventual `Assumption Impact`

### 6.7 Notification Digest / Weekly Review / Insights

Add:

- convergence into a clear pattern:
  - `Insights` = what matters conceptually
  - `Notification Digest` = what is active now
  - `Weekly Review` = what I have reviewed
  - `Actions Queue` = what I need to do

## 7. Recommended Priority Order

### Priority 1

1. `Lot Explorer`
2. `Decision Trace`

Reason:

- both improve trust, auditability, and drill-through across almost every existing page
- neither duplicates an existing page’s primary purpose

### Priority 2

1. `Actions Queue`
2. `Assumption Impact`

Reason:

- both strengthen cross-page orchestration and model governance
- both are useful only after the trust and trace layers are clearer

### Priority 3

- defer all other candidate pages unless the product expands into new operational domains

## 8. Summary

The app does not need many more pages.

The best expansion strategy is:

1. add a small number of cross-cutting evidence/orchestration pages with genuinely new purposes
2. keep most enhancements inside current pages
3. resist splitting existing pages into duplicate analytical destinations

The strongest new-page candidates are:

1. `Lot Explorer`
2. `Decision Trace`
3. `Actions Queue`
4. `Assumption Impact`

Everything else identified in this review is better treated as an enhancement to an existing page or workflow rather than a new route.

