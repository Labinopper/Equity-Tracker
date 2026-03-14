# Execution Backlog

Last updated: `2026-03-11`

## Purpose

This file converts the current strategy documents into an explicit implementation backlog.

It is the execution layer for:

- `docs/STRATEGIC_DOCUMENTATION.md`
- `docs/IMPLEMENTATION_REVIEW_2026-03-07.md`
- `docs/PAGE_AND_WORKFLOW_STRATEGY_2026-03-11.md`
- `docs/PAGE_EXPANSION_AND_INTEGRATION_STRATEGY_2026-03-11.md`

Use this file when deciding what to build next.

## Execution Rules

- Prefer refinement of existing decision pathways over feature sprawl.
- Add a new page only when it serves a genuinely new purpose.
- Keep `Actionable`, `Hypothetical`, and `Realised` states explicit.
- Prefer drill-through, traceability, and trust signalling over more summary metrics.
- Any implementation should improve at least one of: clarity, risk visibility, retained-wealth realism, hidden-drag visibility.

## Active Priority Order

### Priority 1: strengthen the main decision pathway

Objective:
Make the core path `Portfolio -> Capital Stack -> Simulate -> Sell Plan` clearer, faster to read, and easier to trust.

Backlog:

1. Redesign `Insights` into a true signal-first synthesis surface.
2. Add top-metric drill-through from `Portfolio` headline cards into underlying lots, drivers, and trace rows.
3. Reduce visible density on `Portfolio` without losing auditability.
4. Simplify `Sell Plan` plan cards and make planned-versus-committed state more explicit.
5. Simplify the `Tax Plan` top band and strengthen realised-versus-hypothetical separation.

Definition of done:

- `Insights` opens with prioritised decisions, risks, and data issues instead of navigation-heavy content.
- `Portfolio` headline metrics link cleanly to evidence.
- `Sell Plan` can be scanned quickly for what is planned, what is constrained, and what is already committed.
- `Tax Plan` no longer mixes filing reality and forward-looking what-if outputs in the same primary band.

### Priority 2: strengthen trust and evidence visibility

Objective:
Make it easier to see which numbers are exact, estimated, weighted, stale, or assumption-sensitive.

Backlog:

1. Standardise exact / estimate / weighted / unavailable badges across all decision surfaces.
2. Strengthen `Data Quality` and `Reconcile` as remediation-first trust surfaces.
3. Add deeper trace links from metric cards into supporting calculations, assumptions, and audit rows.
4. Make valuation basis, price provenance, FX provenance, and assumption scope more consistently visible.

Definition of done:

- The main strategic pages use the same trust vocabulary and badge treatment.
- Every top-band metric either has direct evidence or an explicit assumption/estimate stamp.
- `Data Quality` and `Reconcile` are usable as operational correction tools, not just diagnostics displays.

### Priority 3: strengthen mutation-flow consequence previews

Objective:
Make input and maintenance workflows show downstream impact before the user commits changes.

Backlog:

1. Improve `Add Lot`, `Transfer Lot`, `Edit Lot`, and `Add Security` impact previews.
2. Expand before/after summaries for cash, tax, wrapper, concentration, and deployable-capital effects.
3. Make downstream destination links clearer from mutation flows into affected analytical surfaces.

Definition of done:

- The user can see what a mutation changes before saving it.
- The resulting downstream consequences are framed in the same language used by strategic pages.
- Mutation workflows feel connected to decision surfaces rather than isolated forms.

### Priority 4: reduce implementation complexity

Objective:
Lower maintenance cost while the analytical model continues to expand.

Backlog:

1. Extract reusable template partials from the largest strategy-heavy templates.
2. Split oversized services, starting with portfolio aggregation, pricing, and tax-planning domains.
3. Keep semantic regression coverage aligned with documented page purpose and copy contracts.

Definition of done:

- The largest templates have smaller composable sections.
- Major service modules have clearer domain boundaries.
- Regression tests protect terminology, trace links, and key page-purpose semantics.

## New Page Candidates

These are the only new pages currently justified by the documentation review.

### N1. Lot Explorer

Priority: high

Purpose:
Provide a dedicated lot-forensics surface for cost basis, attribution, wrapper, tax state, sellability, and downstream traceability.

Why it is justified:

- It serves a genuinely new drill-through purpose.
- It complements `Portfolio`, `Capital Stack`, `Tax Plan`, `History`, and `Reconcile` without duplicating them.

Minimum acceptable scope:

- lot-level filters
- disposal eligibility and basis visibility
- links to tax, audit, and history evidence
- lot state explanation for locked, sellable, transferred, or adjusted positions

### N2. Decision Trace

Priority: high

Purpose:
Provide a dedicated evidence surface that explains how a decision-surface metric was constructed.

Why it is justified:

- It solves a cross-cutting trust problem that existing pages only partially cover.
- It gives a single destination for formula, assumptions, provenance, and evidence rows.

Minimum acceptable scope:

- metric entry point and context
- input assumptions
- price/FX/basis provenance
- links to source rows and relevant pages

### N3. Actions Queue

Priority: medium

Purpose:
Provide a single operational queue of required user actions across sell plans, data issues, timing events, and review workflows.

Implementation rule:
Do not start this until `Lot Explorer` and `Decision Trace` are live or clearly underway.

### N4. Assumption Impact

Priority: medium

Purpose:
Provide a dedicated surface for showing how settings and assumptions change outputs across the system.

Implementation rule:
Do not start this until assumption provenance is more consistent on current pages.

## Features That Should Stay Inside Existing Pages

Do not create new pages for the following.

- `Portfolio` drill-through, guardrails, and metric explanation
- `Capital Stack` deduction decomposition and deployable bridge explanation
- `Simulate` disposal explanation, fee/tax disclosure, and result trace links
- `Sell Plan` execution state clarity and reconciliation support
- `Tax Plan` realised-versus-hypothetical segmentation
- `Risk` structural decomposition and alert explanation
- `Data Quality` remediation pathways
- mutation-flow consequence previews

## Recommended Delivery Waves

### Wave 1

1. `Insights` redesign
2. `Portfolio` drill-through and density reduction
3. `Sell Plan` simplification and reconciliation clarity
4. `Tax Plan` top-band simplification

### Wave 2

1. mutation-flow consequence previews
2. cross-page trust badge standardisation
3. stronger metric trace links

### Wave 3

1. `Lot Explorer`
2. `Decision Trace`

### Wave 4

1. template partial extraction
2. service decomposition
3. semantic regression hardening

### Deferred unless reprioritised

- `Actions Queue`
- `Assumption Impact`
- any additional new pages not named above

## Immediate Action Set

If work starts now, use this order:

1. Update `docs/todo.md` to point to this file as the active prioritization source.
2. Execute `Wave 1`.
3. Reassess whether `Lot Explorer` or mutation-flow upgrades should come first based on pain observed during `Wave 1`.
4. Start the first justified new page only after the current decision pathway is cleaner.

## Summary

The current documentation does not justify broad feature expansion.

The required actions are:

1. tighten the existing decision pathway
2. strengthen trust and evidence visibility
3. improve mutation consequence previews
4. add only two high-priority new pages in the near term: `Lot Explorer` and `Decision Trace`
5. defer everything else unless the program expands into a new operational domain
