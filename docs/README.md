# Documentation Index

Last updated: `2026-03-07`

## Source of Truth Files

- `docs/STRATEGIC_DOCUMENTATION.md`
- `docs/todo.md`
- `docs/todo_archive.md`
- `docs/IMPLEMENTATION_REVIEW_2026-03-07.md`

## What Each File Owns

| File | Ownership |
|---|---|
| `docs/STRATEGIC_DOCUMENTATION.md` | Strategic question mapping by page; deterministic economic model documentation; system alignment review; behavioural risk surface; structural recommendations. |
| `docs/todo.md` | Compact current backlog and deferred prioritization source of truth. |
| `docs/todo_archive.md` | Archived task-history snapshot, delivered scope tables, and pre-tidy detailed backlog reference. |
| `docs/IMPLEMENTATION_REVIEW_2026-03-07.md` | Dated implementation review against the strategic docs; current-state assessment; priority refinements; aligned roadmap and next actions. |

## Current Stage Snapshot

- Stages `1`-`8`: complete.
- Stage `9`: complete (`T34`, `T35`, `T41`, `T42`, `T44`).
- Stage `10`: complete (`T13`-`T32` shipped).
- Refinement Waves complete: Wave A (`T58`-`T69`), Wave B (`T70`-`T77`), Wave C (`T78`-`T79`).

## Current Execution Mode

- Refinement/hardening pass is closed (effective `2026-03-06`).
- Active refinement scope `T58`-`T79` is complete; no in-process refinement waves remain.
- Pension domain `T83` is now live.
- Deferred hardening items `T84`-`T89` are now complete.
- Shared `as_of` mode `T54` is now live across Portfolio, Net Value, Risk, Calendar, and Scenario Lab.
- Shared provenance-badge coverage `T55` is now live across Calendar and Risk event rows.
- Shared reconcile drift-explainer upgrade `T56` is now live with explicit cause buckets and direct trace links.
- Shared alert lifecycle `T57` is now live across Portfolio guardrails and the Risk alert center.
- Decision-brief export `T80` is now live as a deterministic JSON pack across Portfolio, Net Value, Capital Stack, Tax Plan, and Risk.
- Next prioritization source: remaining deferred sections in `docs/todo.md`, with `T81` now next.

## Recent Documentation Sync (`2026-03-07`)

- Dividends flow documented as lot-first input with hidden maintenance UI controls.
- Deployable cash semantics updated to GBP-equivalent (including non-GBP cash conversion via FX).
- Portfolio page docs updated for removed view controls and default-collapsed Model Scope.
- Deferred roadmap extended with pension planning domain (`T83`) covering contribution tracking and deterministic projection scenarios.
- Pension page is now live at `/pension` with append-only contribution tracking, deterministic return scenarios, and tracked-wealth context.
- Expanded Stage-10 strategic regression coverage across seeded route states and representative filter/query combinations.
- Added trace-workflow friction coverage for `Portfolio/Net Value/Tax Plan -> Reconcile -> Audit`, plus clearer audit filter context on filtered destinations.
- Added shared `as_of` mode across Portfolio, Net Value, Risk, Calendar, Scenario Lab, and adjacent linked pages, with preserved navigation and on-or-before price selection.
- Added row-visible price/FX provenance badges across Calendar and Risk event rows.
- Added explicit `/reconcile` drift trace links from cause buckets into basis timeline and filtered audit windows.
- Added shared server-side alert lifecycle with snooze/dismiss/reactivate state, deterministic expiry, and audit-backed transitions across Portfolio guardrails and the Risk alert center.
- Added a deterministic decision-brief export pack across the main decision surfaces, including assumptions and trace links.
- Added a dated implementation review addendum capturing alignment findings, priority fixes, and the next delivery roadmap.
- Split the TODO docs into a compact working backlog (`docs/todo.md`) and a historical archive (`docs/todo_archive.md`).
