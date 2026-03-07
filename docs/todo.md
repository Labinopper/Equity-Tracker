# Next-Step TODO (Objective-Aligned)

Last updated: `2026-03-07`

Scope guardrails:
- Deterministic modelling only.
- No market prediction features.
- No buy/sell advice language.
- Every change must improve at least one of: clarity, risk visibility, retained-wealth realism, hidden drag visibility.

Execution mode (`2026-03-07`):
- Refinement and hardening closure remains complete.
- Active implementation scope: closed (`T58`-`T79` complete).
- Current prioritization source: none. The tracked workflow and product-expansion candidates are now complete.

## Current Status

- Stages `1`-`10` are complete.
- Refinement Waves `A`-`C` are complete.
- `2026-03-07` maintenance updates are live: lot-first dividends input, hidden dividend maintenance controls, deployable cash FX conversion to GBP-equivalent, dividend cash FX metadata persistence, and Portfolio UI simplification.
- `2026-03-07` pension page is live: append-only contribution ledger, deterministic projection scenarios, retirement-target lens, and tracked-wealth context.
- `2026-03-07` shared foundation upgrades are live: `as_of` mode, Risk/Calendar provenance badges, reconcile drift trace links, and persisted alert lifecycle.
- Strategic hardening items `T84`-`T89` are complete and under regression coverage.
- Detailed delivery history and the pre-tidy backlog snapshot now live in `docs/todo_archive.md`.

## Execution Status (Source of Truth)

| Stage | Scope | Status | Notes |
|---|---|---|---|
| 1 | `T01`, `T02`, `T05` | Complete | Label/semantics/tax-warning baseline is live. |
| 2 | `T45`, `T46`, `T47`, `T53` | Complete | Sell-plan core + constraints + impact preview + calendar linkage are live. Includes simulate-first handoff, plan delete, whole-share controls, and ESPP+ sellability alignment. |
| 3 | `T48`, `T49`, `T50` | Complete | Method modes, approval workflow, and deterministic IBKR staging export are live. |
| 4 | `T36`, `T37` | Complete | Multi-currency cash ledger and GBP-only ISA transfer conversion workflow are live. |
| 5 | `T38`, `T33` | Complete | Capital stack and dual-cost dividend-adjusted capital-at-risk policy are live. |
| 6 | `T03`, `T04`, `T39`, `T40` | Complete | Portfolio and Risk now show concentration, locked/forfeitable split, cash-aware deployable metrics, and employer dependence ratio. |
| 7 | `T06`, `T07`, `T08`, `T09`, `T43`, `T51`, `T52` | Complete | Decision-surface hardening and execution governance shipped. |
| 8 | `T10`, `T11`, `T12` | Complete | Glossary deep-linking, analytics criticality metadata/order, and regression lock-in are live. |
| 9 | `T34`, `T35`, `T41`, `T42`, `T44` | Complete | Dividend attribution, dividend-adjusted Per-Scheme/History surfaces, optionality timeline/index, and dividend FX provenance are live. |
| 10 | `T13`-`T32` | Complete | Strategic expansion pages, scenario persistence/mode controls, lock-window overlays, alert center/guardrails, reconciliation, and model-scope disclosures are live. |

## Next Prioritization Order

- No tracked backlog items remain from the current strategic plan.
- New work should be logged only after explicit reprioritization.

## Definition of Done (Current + Next)

- Completed baseline: Core v1 (`T01`-`T12`), sell execution core (`T45`-`T50`, `T53`), and priority additions (`T33`, `T36`, `T37`, `T38`) are merged.
- Stage 10 complete: `T13`-`T32` are merged with targeted deterministic tests for strategic reconcile and audit record filtering.
- Refinement backlog closure achieved: `T58`-`T79` complete with regression coverage for changed semantics, labels, traces, reconciliation pathways, and operational messaging.
- Pension domain (`T83`) is live with deterministic assumptions, timeline projections, and append-only contribution tracking.
- Shared foundation upgrades `T54`-`T57` are now live across the core strategic surfaces.
- Deferred hardening coverage now includes `T84`-`T89`.
- Deferred workflow and product-expansion candidates `T81`, `T82`, and `T90` are now live.
- If any shipped scope is reopened, use `docs/todo_archive.md` for the historical acceptance criteria and archived task detail.

## Deferred Backlog (Current Source of Truth)

### Recently Completed Deferred Items (`2026-03-07`)

- `T54`: shared `as_of` mode is now live across Portfolio, Net Value, Risk, Calendar, Scenario Lab, and adjacent linked pages; routes, APIs, and navigation now preserve the selected date and use the latest stored price on or before it.
- `T57`: persisted alert lifecycle is now shared across Portfolio guardrails and the Risk alert center, with server-side snooze/dismiss/reactivate state, deterministic expiry, and audit-backed transitions.
- `T83`: pension tracking and projection surface with deterministic assumptions and append-only ledger.
- `T84`: strategic regression matrix coverage for Stage-10 strategic pages.
- `T85`: alert-threshold, alert-center, and trace-link end-to-end coverage.
- `T86`: documented wording, Model Scope, glossary-anchor, and trace-anchor copy contracts.
- `T87`: broader strategic API/UI regression suite for Stage-10 pages, seeded route states, and representative filter/query combinations.
- `T88`: cross-surface delta-tolerance fixtures for price, FX, quantity, and settings changes.
- `T89`: UX-friction regression checks for `Portfolio/Net Value/Tax Plan -> Reconcile -> Audit`, including visible filter context on audit destinations.
- `T55`: event-level provenance badges are now live in Calendar and Risk event rows, including row-visible price/FX freshness context.
- `T56`: `/reconcile` drift explainer now exposes explicit cause buckets, explained vs residual change, and direct trace links into basis-timeline or filtered audit windows.
- `T80`: deterministic decision-brief JSON export is now live across Portfolio, Net Value, Capital Stack, Tax Plan, and Risk, including captured assumptions and deep links back to trace surfaces.
- `T81`: guided weekly review workflow is now live across Portfolio, Risk, Calendar, and Reconcile with persisted notes and resumable context.
- `T82`: deterministic notification digest is now live for threshold breaches, stale data, and upcoming forfeiture/tax/sell-plan timing items.
- `T90`: deterministic allocation planner is now live at `/allocation-planner`, with user-defined candidate universe management and before/after concentration, FX, wrapper, and friction deltas.

## Archive

- Historical delivery snapshots, shipped task tables, archived refinement details, and the pre-tidy TODO snapshot live in `docs/todo_archive.md`.
