# Next-Step TODO (Objective-Aligned)

Last updated: `2026-03-14`

Scope guardrails:
- Deterministic modelling only for core product surfaces.
- No market prediction features on core deterministic pages.
- No buy/sell advice language on core deterministic pages.
- Experimental exception: the segregated paper-trading beta workstream is tracked separately under `docs/paper_trading_beta/` and must remain isolated from the deterministic core.
- Every change must improve at least one of: clarity, risk visibility, retained-wealth realism, hidden drag visibility.

Execution mode (`2026-03-11`):
- Refinement and hardening closure remains complete.
- Active implementation scope: closed (`T58`-`T79` complete).
- Deterministic-core prioritization source: closed. The tracked workflow and product-expansion candidates are now complete.
- Historical deterministic-core prioritization reference: `docs/EXECUTION_BACKLOG_2026-03-11.md`.
- Experimental predictive beta prioritization source: foundation workstream approved for phased implementation planning.

## Current Status

- Stages `1`-`10` are complete.
- Refinement Waves `A`-`C` are complete.
- `2026-03-07` maintenance updates are live: lot-first dividends input, hidden dividend maintenance controls, deployable cash FX conversion to GBP-equivalent, dividend cash FX metadata persistence, and Portfolio UI simplification.
- `2026-03-07` pension page is live: append-only contribution ledger, deterministic projection scenarios, retirement-target lens, and tracked-wealth context.
- `2026-03-07` shared foundation upgrades are live: `as_of` mode, Risk/Calendar provenance badges, reconcile drift trace links, and persisted alert lifecycle.
- Strategic hardening items `T84`-`T89` are complete and under regression coverage.
- `2026-03-14` exploratory paper-trading beta planning docs are now in place, including strategy, runtime architecture, technical implementation plan, and split-database schema.
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

- No tracked backlog items remain for the deterministic core strategic plan.
- Historical deterministic-core prioritization reference: `docs/EXECUTION_BACKLOG_2026-03-11.md`.
- The current prioritized next work is the segregated experimental predictive beta workstream (`T91`-`T106`).

## Experimental Predictive Beta Workstream

This workstream is a controlled exception to the deterministic-core scope guardrails.

Boundary rules:

- it remains paper-only and segregated from the deterministic core product;
- it must use separate storage/runtime boundaries;
- it must be disableable without affecting normal site operation;
- it must persist evidence, potential signals, and AI-audit artifacts for later verification;
- it must not weaken or relabel the deterministic pages as predictive surfaces.

Current status:

- planning complete enough to move into implementation sequencing;
- technical implementation not yet started;
- not live;
- not available to normal users.

### Beta Implementation Backlog

| Task | Scope | Status | Notes |
|---|---|---|---|
| `T91` | Adopt beta carve-out into core docs and governance language. | Complete | Core docs now explicitly treat the beta as a segregated experimental domain. |
| `T92` | Beta runtime settings, operating modes, and kill switches. | Planned | Must support `OFF`, `OBSERVE_ONLY`, `SHADOW_ONLY`, `DEMO_NO_LEARN`, and `FULL_INTERNAL_BETA`. |
| `T93` | Separate `beta_research.db`, `BetaContext`, and beta-only migration path. | Planned | Must not block deterministic site startup if unavailable. |
| `T94` | Separate beta supervisor/background runtime with non-blocking startup. | Planned | Heavy research/training must stay out of the main FastAPI process. |
| `T95` | Beta reference domain: instruments, exchanges, calendars, sectors, benchmark mappings, research corpus, and live-paper universe governance. | Planned | Must separate broad research corpus from narrow active live universe. |
| `T96` | US+UK daily research corpus ingestion plus benchmark and FX context. | Planned | Target broad multi-year daily learning corpus before minute-heavy work. |
| `T97` | Corporate actions and event-calendar ingestion aligned to retained history. | Planned | Required for label integrity and replay. |
| `T98` | News ingestion pipeline using RSS/official feeds first, with budget-aware paid enrichment only where justified. | Planned | Must preserve timestamps, linkage, deduplication, and explainable classifications. |
| `T98A` | Verify actual runtime behavior of Twelve Data high-credit filing/fundamental endpoints under the current `55` credits/minute quota. | Planned | Test whether `100`-credit endpoints return data, hard-fail, or expose any useful headers/partial behavior; record the exact response and use it to finalize the filing/fundamental source policy. |
| `T99` | Feature store, feature lineage, and incremental/batch feature builders. | Planned | Reusable versioned features, not model-embedded-only logic. |
| `T100` | Label store and canonical target materialization. | Planned | Must persist explicit outcomes and benchmark-relative returns. |
| `T101` | Dataset manifests, hypothesis registry, experiment registry, model registry, and strategy registry. | Planned | Freeze research datasets and prevent undocumented drift. |
| `T102` | Shadow scoring pipeline and full-universe score tape. | Planned | Must score all eligible live-universe symbols prospectively. |
| `T103` | Potential-signal candidate tracking and AI-audit evidence packs. | Planned | Required so later reviews can independently verify opportunity claims. |
| `T104` | Recommendation gating, allocation layer, immutable demo-trade lane, and paper ledger. | Planned | Paper-only; no broker execution path. |
| `T105` | Evaluation summaries, calibration, attribution, replay bundles, backup, and archive automation. | Planned | Includes strategy summaries, regime scorecards, and replay support. |
| `T106` | Beta admin controls, health/status views, pause/disable controls, and restricted access surfaces. | Planned | Internal-only beta controls; must not interfere with normal site routes. |

### Immediate Beta Priority Slice

Recommended first build sequence:

1. `T92`
2. `T93`
3. `T94`
4. `T95`
5. `T96`

### Beta Foundation Exit Criteria

- the deterministic site starts and operates normally with the beta fully disabled;
- beta startup or migration failure does not block the core site;
- beta storage is separate from the portfolio database;
- observation, learning, shadow scoring, and demo execution can be disabled independently where required;
- no beta page or control is exposed outside the intended internal-test boundary.

## Page Review Track

Use the `Page Template Standard (Reference: Portfolio)` in `docs/STRATEGIC_DOCUMENTATION.md` as the review baseline for each page below.

| Review | Page | Status | Notes |
|---|---|---|---|
| R1 | Portfolio (`/`) | Complete | Current reference template. |
| R2 | Net Value (`/net-value`) | Complete | Reframed as a hypothetical sell-all surface with deployable delta, deductions band, and bottom page context. |
| R3 | Capital Stack (`/capital-stack`) | Complete | Re-centered deployable reality, moved utilities/context down, and relegated formula/cash tables to collapsibles. |
| R4 | Cash (`/cash`) | Complete | Added an operational top band, kept entry workflows central, and moved audit-heavy ledger context down. |
| R5 | Sell Plan (`/sell-plan`) | Complete | Reframed as an execution-planning surface, promoted plan state counts, and pushed glossary/context links to the bottom. |
| R6 | Simulate Disposal (`/simulate`) | Complete | Reworked as a pre-commit disposal surface with clearer inputs, FIFO/result hierarchy, and bottom-page context. |
| R7 | Per Scheme (`/per-scheme`) | Complete | Reframed as scheme decomposition, added top summary stats, and converted each scheme into a drill-down card. |
| R8 | Tax Plan (`/tax-plan`) | Complete | Reworked to a timing-first summary, kept what-if planning central, and pushed assumptions/trace/export into lower sections. |
| R9 | Risk (`/risk`) | Complete | Reordered to structural-risk-first, kept alerts central, and pushed dense formulas/tables into lower collapsibles. |
| R10 | Analytics (`/analytics`) | Complete | Reframed as a signal surface, promoted focus/visibility controls, and pushed notes/context down. |
| R11 | Calendar (`/calendar`) | Complete | Reworked as an operational timing surface, moved filters below the primary countdown band, and lowered notes/context. |
| R12 | Scenario Lab (`/scenario-lab`) | Complete | Reframed around deterministic comparison first, moved model scope/context lower, and promoted scenario/result hierarchy. |
| R13 | History (`/history`) | Complete | Reframed as a trend surface, promoted summary stats and charts, and pushed securities/table/context into lower drill-downs. |
| R14 | Security History (`/history/{security_id}`) | Complete | Reframed as a single-name forensic surface with top summary stats, lower decomposition sections, and bottom page context. |
| R15 | CGT Report (`/cgt`) | Complete | Reframed around filing-year realised totals first, with disposal ledger and context lowered. |
| R16 | Economic Gain Report (`/economic-gain`) | Complete | Reframed around realised economic outcome first, with CGT comparison and ledger detail below. |
| R17 | Dividends (`/dividends`) | Complete | Reframed around confirmation workflow first, promoted actual/forecast summary, and pushed reminder/maintenance context lower. |
| R18 | Insights (`/insights`) | Complete | Converted from a page directory into a signal-first synthesis surface with urgent items, input-pressure summary, and strategic links below. |
| R19 | Capital Efficiency (`/capital-efficiency`) | Complete | Reworked around structural-drag summary first, with components/context lower and clearer action links. |
| R20 | Employment Exit (`/employment-exit`) | Complete | Reframed around exit scenario output first, with inputs and comparison/context separated below. |
| R21 | ISA Efficiency (`/isa-efficiency`) | Complete | Reworked around wrapper-efficiency signal first, with assumptions/context lowered. |
| R22 | Fee Drag (`/fee-drag`) | Complete | Reframed around drag signal first, with detail/context moved into lower sections. |
| R23 | Data Quality (`/data-quality`) | Complete | Reworked as a remediation-first surface with diagnostics pushed below the primary status band. |
| R24 | Employment Tax Events (`/employment-tax-events`) | Complete | Reframed as an event-trail surface with top summary stats, lower tax-year/event ledgers, and bottom page context. |
| R25 | Reconcile (`/reconcile`) | Complete | Reworked around reconciliation path first, with trace sections demoted into drill-down areas. |
| R26 | Basis Timeline (`/basis-timeline`) | Complete | Reframed as a basis-attribution surface with summary stats first and dense tables moved lower. |
| R27 | Pension (`/pension`) | Complete | Reworked around retirement-position summary first, with assumptions, ledger, and context lowered. |
| R28 | Audit Log (`/audit`) | Complete | Reworked as a mutation-trail surface with filters and ledger in structured collapsibles. |
| R29 | Add Lot (`/portfolio/add-lot`) | Complete | Reframed as a lot-input workflow with clearer purpose, actions, and bottom-page context. |
| R30 | Transfer Lot (`/portfolio/transfer-lot`) | Complete | Reworked around transfer impact first, with rules and downstream context demoted below the core workflow. |
| R31 | Edit Lot (`/portfolio/edit-lot`) | Complete | Reframed as a correction workflow with explicit downstream impact and audit context. |
| R32 | Add Security (`/portfolio/add-security`) | Complete | Reworked around catalogue-first instrument selection with manual override and lower page context. |
| R33 | Settings (`/settings`) | Complete | Reworked around modelling inputs first, with diagnostics/completeness as lower operational sections. |
| R34 | Glossary (`/glossary`) | Complete | Reframed as a support/reference surface with clearer reverse-link context and model-scope relegation. |
| R35 | Login (`/auth/login`) | Complete | Kept minimal auth flow primary and collapsed recovery guidance into optional support detail. |
| R36 | Locked (`locked.html`) | Complete | Kept recovery path primary and collapsed secondary recovery detail into structured guidance. |

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
