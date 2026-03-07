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
- Current prioritization source: remaining shared-foundation, workflow, and hardening candidates after pension delivery.

## Current Status

- Stages `1`-`10` are complete.
- Refinement Waves `A`-`C` are complete.
- `2026-03-07` maintenance updates are live: lot-first dividends input, hidden dividend maintenance controls, deployable cash FX conversion to GBP-equivalent, dividend cash FX metadata persistence, and Portfolio UI simplification.
- `2026-03-07` pension page is live: append-only contribution ledger, deterministic projection scenarios, retirement-target lens, and tracked-wealth context.
- Strategic hardening items `T84`, `T85`, `T86`, and `T88` are complete and under regression coverage.
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

1. Deeper hardening and shared foundations: `T87`, `T89`, `T57`, `T54`-`T56`.
2. Workflow and QoL expansion: `T80`-`T82`, `T90`.

## Definition of Done (Current + Next)

- Completed baseline: Core v1 (`T01`-`T12`), sell execution core (`T45`-`T50`, `T53`), and priority additions (`T33`, `T36`, `T37`, `T38`) are merged.
- Stage 10 complete: `T13`-`T32` are merged with targeted deterministic tests for strategic reconcile and audit record filtering.
- Refinement backlog closure achieved: `T58`-`T79` complete with regression coverage for changed semantics, labels, traces, reconciliation pathways, and operational messaging.
- Pension domain (`T83`) is live with deterministic assumptions, timeline projections, and append-only contribution tracking.
- Deferred hardening coverage now includes `T84`, `T85`, `T86`, and `T88`.
- Remaining work is limited to deferred foundation, workflow, product-expansion, and hardening candidates.
- If any shipped scope is reopened, use `docs/todo_archive.md` for the historical acceptance criteria and archived task detail.

## Deferred Backlog (Current Source of Truth)

### Recently Completed Deferred Items (`2026-03-07`)

- `T83`: pension tracking and projection surface with deterministic assumptions and append-only ledger.
- `T84`: strategic regression matrix coverage for Stage-10 strategic pages.
- `T85`: alert-threshold, alert-center, and trace-link end-to-end coverage.
- `T86`: documented wording, Model Scope, glossary-anchor, and trace-anchor copy contracts.
- `T88`: cross-surface delta-tolerance fixtures for price, FX, quantity, and settings changes.

### Shared Foundation Candidates

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T54 | P2 | M | Major | Add global `as_of` date mode across Portfolio, Net Value, Risk, Calendar, and Scenario Lab payloads/routes. | Clarity, deterministic comparability | A single selected date produces consistent lock/forfeiture/tax context across all major pages and exports. |
| T55 | P2 | M | Major | Add event-level provenance badges (price date, FX date, stale flags) in Calendar and Risk event rows. | Data-quality visibility, decision reliability | Every value-at-stake event row shows provenance/freshness metadata without leaving the page. |
| T56 | P2 | M | Major | Add `/reconcile` drift explainer comparing current vs prior snapshot deltas by cause (price, FX, quantity, settings/audit). | Transparency, behavioural risk reduction | Users can trace a headline delta to deterministic components and linked audit rows in <=3 clicks. |
| T57 | P2 | S | Minor | Add persisted alert lifecycle (server-side dismiss/snooze states with deterministic expiry semantics). | Risk salience, cross-session consistency | Alert state survives browser reset/device changes and is auditable by state transition. |

### Product Expansion Candidates

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T80 | P2 | M | Major | Add deterministic decision-brief export pack (selected metrics + assumptions + trace links) from major surfaces. | Transparency, auditability | Export artifact includes reproducible inputs/metadata and deep links to traces. |
| T81 | P2 | M | Major | Add guided weekly review workflow spanning Portfolio, Risk, Calendar, and Reconcile with completion notes. | Behavioural discipline | Workflow state persists and can be resumed without reconfiguration. |
| T82 | P2 | M | Major | Add deterministic notification digest for threshold breaches, stale data, and upcoming forfeiture/tax events. | Risk salience | Digest entries are generated exclusively from existing deterministic rules/state. |
| T90 | P2 | L | Major | Add deterministic reallocation candidate planner (future `/allocation-planner`) that identifies overweight holdings/exposures, quantifies trim amounts, and evaluates user-defined replacement candidates or target buckets for diversification/tax-wrapper improvement. | Concentration risk reduction, capital deployment discipline, decision support | Outputs remain non-advisory and traceable: the user defines the candidate universe or target allocation rules, the app shows why an exposure is overweight, how much capital could be reduced, which candidates improve target-fit metrics, and before/after concentration, FX, wrapper, and tax-friction deltas. |

### Hardening Candidates

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T87 | P2 | M | Major | Add broader strategic API/UI regression suite for all Stage-10 pages and filters/parameter combinations. | Reliability, extensibility | Stage-10 routes and representative query states are covered by stable smoke + semantic response checks. |
| T89 | P2 | S | Minor | Add UX-friction regression checks for key trace workflows (`Portfolio/Net Value/Tax Plan -> Reconcile -> Audit`). | Decision flow, usability | Critical trace journeys remain completable in <=3 clicks with visible context and no dead-end states. |

## Archive

- Historical delivery snapshots, shipped task tables, archived refinement details, and the pre-tidy TODO snapshot live in `docs/todo_archive.md`.
