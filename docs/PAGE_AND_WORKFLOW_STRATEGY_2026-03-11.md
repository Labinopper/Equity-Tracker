# Page And Workflow Strategy

Date: `2026-03-11`

Status: strategic review and refinement strategy

Scope: page-by-page and workflow-by-workflow review of the current application against the documented purpose in `docs/STRATEGIC_DOCUMENTATION.md`, the implementation review in `docs/IMPLEMENTATION_REVIEW_2026-03-07.md`, and the current route/template surface in the codebase.

## 1. Program Purpose

Equity Tracker is not a generic portfolio dashboard. Its explicit purpose is to act as a deterministic equity-compensation and personal-capital decision system.

The core recurring questions are:

1. What do I actually own now?
2. What portion is sellable, locked, forfeitable, or already deployable?
3. What would remain after tax, fees, and scheme constraints?
4. How do today's actions change future liquidity, concentration, and tax outcomes?

The product therefore succeeds only if it keeps:

- actionable value separate from hypothetical value
- tax basis separate from true economic basis
- scheme rules separate from generic portfolio assumptions
- auditability separate from presentation convenience
- deterministic modelling separate from prediction

## 2. Review Standard Used

This review uses the `Page Template Standard (Reference: Portfolio)` from `docs/STRATEGIC_DOCUMENTATION.md` as the primary evaluation baseline.

That means the main test for each page is:

1. Is the first visible band the actual decision band?
2. Are actionable, hypothetical, and realised states explicit?
3. Are warnings visible while explanation is subordinate?
4. Can headline numbers be traced to source inputs?
5. Are diagnostics, settings, and exports pushed lower?

## 3. Current System Shape

The current application surface is broad and materially aligned with the documented purpose. The major route/template set includes:

- decision surfaces: `Portfolio`, `Net Value`, `Capital Stack`, `Cash`, `Risk`, `Tax Plan`, `Per Scheme`, `Analytics`, `Calendar`, `Scenario Lab`, `Sell Plan`, `Simulate`, `Allocation Planner`
- realised reporting: `CGT`, `Economic Gain`, `Dividends`, `Audit`, `Employment Tax Events`, `Basis Timeline`, `History`, `Security History`
- strategic deep dives: `Capital Efficiency`, `Employment Exit`, `ISA Efficiency`, `Fee Drag`, `Data Quality`, `Insights`, `Pension`, `Weekly Review`, `Notification Digest`
- mutation and control surfaces: `Add Lot`, `Transfer Lot`, `Edit Lot`, `Add Security`, `Settings`, `Glossary`, `Login`, `Locked`

The app is strongest where it already behaves like a deterministic control system:

- Portfolio and Capital Stack for liquidity reality
- Sell Plan and Simulate for execution discipline
- Tax Plan for timing-aware tax framing
- Data Quality and Reconcile for trust and auditability
- Pension and Allocation Planner for bounded strategic modelling

The main residual weakness is not broken functionality. It is structural density, repeated explanation, and inconsistency in how pages expose:

- estimate quality
- pathway assumptions
- audit trace depth
- primary versus secondary content

## 4. Strategic Diagnosis

### 4.1 What the product already does well

- It is lot-aware, scheme-aware, and audit-aware.
- It distinguishes gross value from deployable value.
- It has meaningful operational surfaces, not just summary charts.
- It now treats broker fees and UK CGT with much stronger realism than a generic tracker.
- It preserves a strong model of append-only financial state.

### 4.2 Where refinement pressure is still highest

- Some pages still feel like internal analysis consoles rather than decision briefs.
- Some pages still place context and methodology too close to the first read path.
- Some pathways are accurate but not narrated tightly enough.
- Some strategic pages are useful but still underdeveloped as decision layers.
- Some mutation flows remain strong functionally but can still surface stronger before/after consequence previews.

### 4.3 Main design principle for the next refinement cycle

Every major page should answer one dominant question in the first visible band, then let the user drill down into mechanism, history, and diagnostics without overwhelming the first pass.

## 5. Workflow Strategy

The product should be treated as a set of connected deterministic workflows, not a set of isolated pages.

### 5.1 Daily decision loop

Primary pathway:

1. `Portfolio`
2. `Risk`
3. `Calendar`
4. `Capital Stack`
5. `Simulate` or `Sell Plan`
6. `Reconcile` if trust or drift is questioned

Refinement goal:

- make this sequence more explicit with stronger cross-links and consistent language
- reduce duplicate conceptual explanation across the pages in this path

### 5.2 Disposal execution loop

Primary pathway:

1. `Portfolio` or `Capital Stack`
2. `Simulate`
3. `Sell Plan`
4. `Calendar`
5. commit disposal
6. `CGT`, `Economic Gain`, `Reconcile`

Refinement goal:

- keep pre-commit and post-commit worlds clearly separated
- keep simulation assumptions visible near execution actions
- keep realised-history pages explicitly downstream of committed state

### 5.3 Assumption and trust loop

Primary pathway:

1. `Settings`
2. `Data Quality`
3. `Reconcile`
4. `Audit`
5. `Basis Timeline`

Refinement goal:

- frame these as model-integrity pages
- make them easier to reach from decision pages when a number is estimate-heavy or stale

### 5.4 Long-horizon strategy loop

Primary pathway:

1. `Tax Plan`
2. `ISA Efficiency`
3. `Capital Efficiency`
4. `Fee Drag`
5. `Pension`
6. `Allocation Planner`

Refinement goal:

- keep these bounded and deterministic
- avoid letting them drift into pseudo-optimization or advisory language

## 6. Page-By-Page Strategy

### 6.1 Portfolio

Role:

- operational home page
- immediate ownership, sellability, concentration, and deployable-capital context

Keep:

- action-first framing
- sellability and constraint visibility
- guardrails and trace links

Improve:

- add more lot/source drill-through from top metrics
- keep lower narrative content collapsed by default
- strengthen direct links from top metrics into `Capital Stack`, `Risk`, `Calendar`, and `Reconcile`

Priority: high

### 6.2 Net Value

Role:

- hypothetical all-in liquidation surface

Risk:

- gross-value anchoring

Improve:

- make the gap versus `Capital Stack` visually unavoidable
- keep the page explicitly hypothetical in all headline framing
- reduce any wording that sounds like deployable reality

Priority: high

### 6.3 Capital Stack

Role:

- deployable-capital reality engine

Keep:

- deduction chain
- deployable cash inclusion
- formula transparency

Improve:

- add assumption-quality badges beside tax and fee blocks
- add direct trace links from deductions into `Tax Plan`, `Employment Tax Events`, `Fee Drag`, and `Reconcile`
- tighten the distinction between "current reality" and "if I later rebuy" caveats without overloading the top band

Priority: high

### 6.4 Cash

Role:

- operational cash reality and transfer control

Improve:

- add provenance freshness per balance row
- add more explicit before/after transfer outcome preview
- strengthen link from deployable cash into Capital Stack and ISA pathways

Priority: medium-high

### 6.5 Sell Plan

Role:

- deterministic staged execution planner

Keep:

- tranche discipline
- constraint handling
- adherence and reconciliation concepts

Improve:

- strengthen planned-to-committed reconciliation as a first-class section
- add clearer split between "plan constraints", "impact estimates", and "execution tracking"
- reduce visual density in plan cards when many plans exist

Priority: high

### 6.6 Simulate

Role:

- pre-commit consequence check

Keep:

- disposal preview
- employment tax gating
- fee estimate visibility

Improve:

- push more of the methodology into a lower section
- strengthen contrast between editable inputs and read-only price provenance
- add explicit quick links from result outputs to `CGT`, `Economic Gain`, and `Reconcile`

Priority: high

### 6.7 Per Scheme

Role:

- structural burden decomposition by scheme

Improve:

- add clearer navigation into scheme-contributing securities and lots
- separate current-state versus realised-history more strongly
- add compact summary narrative at top explaining which scheme is driving drag

Priority: medium

### 6.8 Tax Plan

Role:

- tax-year timing and compensation interaction planning

Keep:

- assumption quality
- ANI / pension / payroll context
- current versus next-year timing comparison

Improve:

- reduce the amount of visible scenario machinery above the fold
- add a stronger "decision statement" near the top
- improve linkages to exact realised baselines, especially `CGT` and `Reconcile`

Priority: high

### 6.9 Risk

Role:

- structural fragility and optionality surface

Keep:

- concentration and optionality logic
- timeline framing

Improve:

- strengthen trend-over-time framing for guardrail persistence
- keep wrappers, liquidity detail, and supporting decomposition secondary
- add stronger link from risk items into `Sell Plan`, `Calendar`, and `Allocation Planner`

Priority: high

### 6.10 Analytics

Role:

- cross-domain monitoring layer

Improve:

- lock in a minimum critical widget set
- keep optional widgets visibly subordinate
- convert the page from "many modules" to "top narrative + modules"

Priority: medium

### 6.11 Calendar

Role:

- timing and constraint change surface

Improve:

- emphasise next critical state changes over full event lists
- strengthen value-at-stake provenance and stale basis visibility
- add more direct execution links for time-critical items

Priority: medium-high

### 6.12 Scenario Lab

Role:

- deterministic multi-leg what-if engine

Improve:

- make execution-order assumptions more explicit
- add stronger path to `Reconcile`
- add reusable scenario templates and clearer saved-scenario lifecycle

Priority: medium-high

### 6.13 History and Security History

Role:

- forensic time-series context

Improve:

- add decomposition views for price / quantity / FX / dividends
- lead with plain-language summary of what changed materially
- ensure historical non-sellable windows remain visually obvious

Priority: medium

### 6.14 CGT, Economic Gain, Dividends

Role:

- realised reporting surfaces

Improve:

- keep filing-grade and economic-grade views distinct
- add stronger comparison affordances between tax and economic basis
- separate actual from forecast dividend flows more aggressively

Priority: medium-high

### 6.15 Insights

Role:

- synthesis layer

Current issue:

- still at risk of behaving like a navigation page

Improve:

- keep only top current signals
- make it a "start here now" page rather than a directory
- include signal severity, blocked capital, tax windows, concentration stress, and stale-data pressure

Priority: high

### 6.16 Capital Efficiency, Employment Exit, ISA Efficiency, Fee Drag, Data Quality, Employment Tax Events, Basis Timeline

Role:

- strategic utilities

Improve:

- for each page, add one clear top-line conclusion
- reduce explanatory clutter
- improve trace links into underlying facts

Specific priorities:

- `Data Quality`: high, because trust is cross-cutting
- `Fee Drag`: medium-high, because costs remain easy to underestimate
- `Employment Tax Events`: medium-high, because tax event traceability is critical
- `Basis Timeline`: medium, because it is useful but not yet sufficiently salient
- `Capital Efficiency`, `Employment Exit`, `ISA Efficiency`: medium

### 6.17 Pension

Role:

- deterministic long-horizon retirement context

Improve:

- add richer timeline visualisation
- tighten contribution-versus-growth storytelling
- keep the page clearly separate from deployable capital logic

Priority: medium

### 6.18 Weekly Review and Notification Digest

Role:

- behavioural discipline and deterministic attention management

Improve:

- make them more obviously downstream of core decision evidence
- add stronger links back into the underlying evidence pages
- avoid any implication that completion state equals health

Priority: medium

### 6.19 Allocation Planner

Role:

- trim-and-redeploy discipline tool

Improve:

- keep user-defined-universe boundaries explicit
- strengthen wrapper and friction consequence visibility
- add saved presets and bucket-level target models later, but only if they remain deterministic

Priority: medium-high

### 6.20 Mutation and Control Pages

Pages:

- `Add Lot`
- `Transfer Lot`
- `Edit Lot`
- `Add Security`
- `Settings`
- `Glossary`
- `Audit`
- `Login`
- `Locked`

Strategy:

- treat these as model-integrity surfaces
- make every mutation page show downstream effects before save
- keep settings completeness and constrained outputs explicit
- keep glossary reverse links strong
- keep audit and locked-state pages operationally clear, not decorative

Priority: high for `Settings`, `Add Lot`, `Transfer Lot`, `Edit Lot`; medium for the rest

## 7. Process-By-Process Refinement Strategy

### 7.1 Acquisition input process

Surfaces:

- `Add Security`
- `Add Lot`

Need:

- stronger persisted-record preview
- more duplicate/conflict resolution help
- more explicit consequence preview for scheme-specific lots

### 7.2 Transfer process

Surfaces:

- `Transfer Lot`
- `Employment Tax Events`
- `Audit`

Need:

- before/after custody map
- clearer tax-confidence or estimate-quality framing
- better deep links from transfer result into tax and audit evidence

### 7.3 Disposal planning process

Surfaces:

- `Portfolio`
- `Capital Stack`
- `Simulate`
- `Sell Plan`
- `Calendar`

Need:

- cleaner progression
- fewer duplicate explanations
- consistent estimate caveats and stronger execution-state reconciliation

### 7.4 Disposal reporting process

Surfaces:

- `CGT`
- `Economic Gain`
- `Tax Plan`
- `Reconcile`

Need:

- stronger separation between realised exact history and hypothetical projections
- explicit assumption language near scenario outputs
- easier cross-navigation between realised and projected outcomes

### 7.5 Trust and reconciliation process

Surfaces:

- `Data Quality`
- `Reconcile`
- `Audit`
- `Basis Timeline`
- `Settings`

Need:

- stronger single-path trust workflow
- direct remediation links
- easier drift diagnosis from a headline number

## 8. Structural / Engineering Strategy

The UI review implies some code-structure work, not only page polish.

### 8.1 Template decomposition

Highest candidates for partial extraction:

- `portfolio.html`
- `simulate.html`
- `sell_plan.html`
- `tax_plan.html`
- `risk.html`
- `calendar.html`

Why:

- they are dense
- they mix repeated stat-card structures and lower diagnostic panels
- they are likely to become maintenance hotspots

### 8.2 Service decomposition

Highest candidates:

- `portfolio_service.py`
- pricing and valuation aggregation services
- tax planning and reporting services

Why:

- too much cross-domain logic currently converges in a few large modules
- future growth in tax, fee, scenario, and trust logic will slow down unless these are split more aggressively

### 8.3 Documentation-to-implementation coupling

Keep strengthening:

- wording-critical tests
- trace-link tests
- assumption badge tests
- page-purpose semantics in regression coverage

## 9. Recommended Priority Order

### Priority 1: decision-band and pathway clarity

1. Strengthen `Portfolio -> Capital Stack -> Simulate -> Sell Plan` as the main disposal pathway.
2. Rework `Insights` into a true signal-first synthesis page.
3. Reduce visible density on the most information-heavy pages.

### Priority 2: trust and exact-vs-estimate visibility

1. Add stronger metric-level exact / weighted / unavailable signalling where still inconsistent.
2. Improve `Data Quality`, `Reconcile`, and provenance surfacing.
3. Keep realised versus hypothetical outcomes visually separate.

### Priority 3: mutation-flow consequence previews

1. Improve `Add Lot`, `Transfer Lot`, `Edit Lot`, and `Add Security`.
2. Expand before/after and downstream impact summaries.

### Priority 4: structural maintainability

1. Extract template partials.
2. Split oversized services.
3. Keep semantic regression coverage aligned with docs.

## 10. Concrete Strategy Deliverables

Recommended next delivery wave:

1. `Insights` redesign as a synthesis surface.
2. `Portfolio` top-metric drill-through and reduced visible density.
3. `Sell Plan` plan-card simplification plus stronger planned-versus-committed reconciliation.
4. `Tax Plan` top-band simplification and stronger realised-versus-hypothetical separation.
5. Input/mutation workflow impact-preview upgrades.
6. Template partial extraction for the largest decision pages.

## 11. Summary

The program’s explicit purpose is already coherent and mostly reflected in the current product. The app is strongest when it behaves like a deterministic control system for deployable capital, scheme constraints, tax drag, and execution timing.

The next stage should not be feature sprawl. It should be refinement of hierarchy, trust signalling, and workflow clarity:

- fewer competing signals above the fold
- stronger distinction between exact history and bounded estimates
- clearer progression between decision pages
- stronger traceability from headline metrics to inputs
- lower template and service complexity as the model grows

The strategy is therefore:

1. tighten page hierarchy
2. strengthen cross-page workflow coherence
3. improve trust and assumption signalling
4. deepen mutation consequence previews
5. reduce implementation complexity in the largest templates and services

