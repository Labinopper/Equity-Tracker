# Next-Step TODO (Objective-Aligned)

Scope guardrails:
- Deterministic modelling only.
- No market prediction features.
- No buy/sell advice language.
- Every change must improve at least one of: clarity, risk visibility, retained-wealth realism, hidden drag visibility.

Execution mode (`2026-03-06`):
- Refinement and hardening only.
- Active implementation scope: `T58`-`T79`.
- Deferred (not active): `T54`-`T57`, `T80`-`T82`.

## Progress Update (2026-03-06)

- Stages `1`-`7` remain complete as previously logged.
- Stage `8` complete: `T10` glossary deep links, `T11` analytics criticality metadata/order, and `T12` regression lock-in.
- Stage `9` complete: `T34`, `T35`, `T41`, `T42`, and `T44` are now fully shipped.
- Stage `10` complete: `T13`-`T32` shipped, including strategic pages, sequential/persisted Scenario Lab, lock-window history overlays, and cross-page traceability.
- Regression hardening pass completed for strategic surfaces: API/page smoke coverage and boundary validation added.

## Active Refinement Queue (Current)

| Wave | Scope | Status | Exit Criteria |
|---|---|---|---|
| Wave A (P1) | `T58`-`T69` | Ready | Portfolio and page-level clarity refinements merged with deterministic regression updates where behaviour/labels change. |
| Wave B (P2) | `T70`-`T77` | Queued | Traceability, decomposition, and input-integrity refinements merged with cross-surface reconciliation checks. |
| Wave C (P3) | `T78`-`T79` | Queued | Glossary reverse-linking and operational messaging refinements merged without scope expansion. |

## Execution Status (Source of Truth)

| Stage | Scope | Status | Notes |
|---|---|---|---|
| 1 | `T01`, `T02`, `T05` | Complete | Label/semantics/tax-warning baseline is live. |
| 2 | `T45`, `T46`, `T47`, `T53` | Complete | Sell-plan core + constraints + impact preview + calendar linkage are live. Includes simulate-first handoff, plan delete, whole-share controls, and ESPP+ sellability alignment. |
| 3 | `T48`, `T49`, `T50` | Complete | Method modes, approval workflow, and deterministic IBKR staging export are live. |
| 4 | `T36`, `T37` | Complete | Multi-currency cash ledger and GBP-only ISA transfer conversion workflow are live. |
| 5 | `T38`, `T33` | Complete | Capital stack and dual-cost dividend-adjusted capital-at-risk policy are live. |
| 6 | `T03`, `T04`, `T39`, `T40` | Complete | Portfolio and Risk now show concentration (gross/sellable), locked/forfeitable split, cash-aware deployable metrics, and employer dependence ratio. |
| 7 | `T06`, `T07`, `T08`, `T09`, `T43`, `T51`, `T52` | Complete | Decision-surface hardening and execution governance shipped. |
| 8 | `T10`, `T11`, `T12` | Complete | Glossary deep-linking, analytics criticality metadata/order, and regression lock-in are live. |
| 9 | `T34`, `T35`, `T41`, `T42`, `T44` | Complete | Dividend attribution, dividend-adjusted Per-Scheme/History surfaces, optionality timeline/index, and dividend FX provenance are live. |
| 10 | `T13`-`T32` | Complete | Strategic expansion pages, scenario persistence/mode controls, lock-window overlays, alert center/guardrails, reconciliation, and model-scope disclosures are live. |

## Task Delivery Snapshot (2026-03-06)

| ID | Status | Notes |
|---|---|---|
| T10 | Complete | High-risk metric labels now deep-link to glossary anchors. |
| T11 | Complete | Analytics widgets carry context/criticality metadata and priority order. |
| T12 | Complete | New tests cover wording/links and deterministic exposure/widget outputs. |
| T34 | Complete | Security-level dividend allocation with deterministic tax split and reconciliation totals. |
| T35 | Complete | Dividend-adjusted metrics now span Portfolio, Per-Scheme, History, and Security History with reconciliation tests. |
| T41 | Complete | Risk optionality timeline bands shipped in service/API/UI. |
| T42 | Complete | Optionality index shipped with transparent weighted component breakdown. |
| T44 | Complete | Dividend native-currency input and FX provenance fully supported end-to-end. |
| T13 | Complete | ISA/taxable wrapper allocation surfaced on Portfolio and Risk. |
| T14 | Complete | Analytics FX attribution widget shipped and wired into widget governance. |
| T15 + T29 | Complete | Scenario Lab supports independent/sequential execution and persists snapshots with reload/list APIs. |
| T16 | Complete | History and Security History now include lock-window/non-sellable overlays. |
| T17 | Complete | Key totals now deep-link to reconcile traces; `/reconcile` includes contributing lots and recent audit mutations; `/audit` supports record-level filters. |
| T18 | Complete | Add Security blocks duplicate/ambiguous instrument setup and surfaces pre-submit metadata-quality warnings. |
| T19-T25 | Complete | Strategic pages live: `/capital-efficiency`, `/employment-exit`, `/isa-efficiency`, `/fee-drag`, `/data-quality`, `/employment-tax-events`. |
| T26 + T20 + T31 | Complete | Top-nav alert center and concentration guardrails are configurable; Risk includes deterministic rebalance friction panel. |
| T27 | Complete | Forfeiture heatmap buckets now visible in Risk/Analytics. |
| T28 | Complete | `/reconcile` explains cross-surface delta components deterministically. |
| T30 | Complete | `/basis-timeline` exposes native-vs-FX basis attribution timeline. |
| T32 | Complete | Model Scope cards standardized across Portfolio, Net Value, Tax Plan, and Scenario Lab. |

## Core v1 (Critical, Delivered Archive)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T01 | P0 | S | Minor | Add explicit metric tags (`Actionable`, `Hypothetical`, `Realised`) to key cards/tables on Portfolio, Net Value, Tax Plan, and reports. | Clarity, liquidity realism | No major card can be misread without seeing one of the three tags. |
| T02 | P0 | S | Minor | Fix Net Value employment-tax wording to match implementation semantics (sellable-lot estimate inside hypothetical sell-all). | Tax visibility, clarity | Card label and tooltip match service behaviour exactly. |
| T03 | P0 | M | Major | Add top-level concentration exposure summary to Portfolio (top holding %, employer ticker %, percent of gross and percent of sellable). | Concentration risk visibility | Concentration is visible on the first screen without visiting Risk/Analytics. |
| T04 | P0 | M | Major | Split `Blocked/Restricted Value` into separate surfaced buckets: `Locked` and `Forfeitable`. | Liquidity clarity, forfeiture visibility | Top-level totals show both buckets and reconcile to existing totals. |
| T05 | P0 | S | Minor | Add tax-input health banner globally when settings are incomplete/zero and any tax-sensitive value is displayed. | Hidden tax drag visibility | Banner appears consistently on Portfolio, Net Value, Simulate, Tax Plan. |
| T06 | P1 | M | Major | Add assumption-quality indicator in Tax Plan (`Exact`, `Weighted Estimate`, `Unavailable`) for each projection block. | Clarity, tax realism | Every projected figure carries an assumption-quality status. |
| T07 | P1 | S | Minor | In Simulate, require explicit acknowledgement before commit when employment tax estimate is unavailable. | Hidden drag visibility | Commit path is blocked until acknowledgement if tax estimate is missing. |
| T08 | P1 | M | Major | Add quantified pre-confirm transfer impact panel (forfeit qty/value + estimated transfer tax where applicable). | Forfeiture and tax visibility | Transfer confirmation shows deterministic impact numbers before submit. |
| T09 | P1 | M | Major | Add deterministic pre-submit preview in Add Lot (`true cost`, `cost basis`, lock/forfeiture flags, FX basis). | Input clarity, true-cost modelling integrity | User sees persisted-equivalent derived values before creating lot. |
| T10 | P1 | S | Minor | Add glossary deep links from high-risk labels (Portfolio/Net Value/Tax Plan). | Terminology clarity | Clicking term labels opens exact glossary anchors. |
| T11 | P1 | M | Major | Add widget context labels in Analytics and default sort/prioritization by decision criticality. | Risk awareness | Mixed widgets no longer appear context-equivalent. |
| T12 | P1 | M | Major | Add regression tests for new liquidity/tax wording and concentration/forfeiture top-card calculations. | Determinism and correctness | CI includes assertions for all new core v1 semantics. |

## Priority Additions (Dividend Economics + Multi-Currency Cash, Delivered Archive)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T33 | P0 | M | Major | Add a dual-cost policy: keep `True Cost (Acquisition)` immutable, and add `Dividend-Adjusted Capital at Risk` as a separate metric. | Clarity, retained-wealth realism | Glossary and UI clearly distinguish both metrics; tax reports remain based on existing cost-basis/true-cost logic. |
| T34 | P1 | L | Major | Build deterministic dividend allocation to holdings (security-level first, lot-level mode optional) so received dividends can be attributed against still-held capital. | True economic exposure clarity | Allocated dividend totals reconcile exactly to dividend ledger totals. |
| T35 | P1 | M | Major | Add dividend-adjusted analytics surfaces (Portfolio/Per-Scheme/History): `Economic Gain`, `Economic Gain + Net Dividends`, and `Capital at Risk`. | Hidden drag and retained-wealth visibility | User can toggle baseline vs dividend-adjusted views with exact reconciliation. |
| T36 | P0 | L | Major | Implement multi-currency cash ledger (GBP/USD/other ISO): account balances by container (`BROKER`, `ISA`, optional `BANK`) via auditable cash transactions. | Deployable-capital realism, FX clarity | Deterministic balances per currency and account are visible and audit logged. |
| T37 | P0 | M | Major | Add GBP-only ISA transfer cash workflow with required conversion step when source cash is non-GBP (e.g., USD->GBP) including FX rate and fee provenance. | Priority operational requirement, tax/cash realism | ISA cash transfer cannot complete from non-GBP without explicit conversion transaction. |
| T38 | P0 | M | Major | Add `Capital Stack` page (`/capital-stack`) as primary wealth-reality surface: Gross -> Locked -> Forfeitable -> Hypothetical Liquid -> (Employment Tax + CGT + Fees) -> Net Deployable Today. | Liquidity illusion reduction, deployable-capital clarity | Stack totals reconcile to Portfolio/Net Value/Tax/Fee components and show formula breakdown. |
| T39 | P1 | M | Major | Integrate cash into deployable metrics and concentration metrics (deployable cash + sellable holdings) across Portfolio and Risk. | Concentration realism, deployable realism | `Deployable` metrics include cash and show employer share of deployable capital. |
| T40 | P1 | M | Major | Add Employer Dependence Ratio metric with transparent formula and optional income-dependency input from Settings. | Structural fragility visibility | Ratio shown with component breakdown (employer equity + optional income dependency proxy). |
| T41 | P1 | M | Major | Add deterministic future optionality timeline bands (`Now`, `6m`, `1y`, `3y`, `5y`) showing sellable %, locked %, forfeitable %, deployable %. | Long-horizon decision clarity | Timeline uses known lock/forfeiture dates only; no price forecasting. |
| T42 | P1 | S | Minor | Add Optionality Index (0-100) with transparent weighted components (sellability, forfeiture, concentration, ISA ratio, config completeness). | Behavioural risk mitigation | Score decomposes into visible inputs and user-adjustable weights. |
| T43 | P1 | S | Minor | Add rule-based behavioural guardrails: liquidity illusion warning, ISA underutilization warning, drag escalation warning, forfeiture imminence warning. | Bias reduction and risk salience | Warnings trigger deterministically from thresholds and can be configured or silenced. |
| T44 | P1 | M | Major | Extend dividends to support original currency + FX basis (not GBP-only input), preserving conversion provenance per entry. | FX and dividend realism | Dividend entries accept native currency amount and store FX metadata while keeping GBP-report compatibility. |

## Priority Additions (Deterministic Sell Execution / Trickle Sell, Delivered Archive)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T45 | P0 | M | Major | Add `Sell Plan` page (`/sell-plan`) for deterministic staged disposals with tranche scheduling (quantity/date pairs only, no price forecasting) and Calendar integration. | Liquidity realism, concentration risk reduction clarity | User can create a plan with fixed tranche count, cadence, and quantity caps; each tranche appears in `/calendar` with date, quantity, and link back to the plan. |
| T46 | P0 | M | Major | Add sell-plan constraint engine: sellable-only enforcement, lock/forfeiture exclusion, max daily quantity/notional caps, minimum spacing between tranches. | Risk control, forfeiture avoidance clarity | Plan cannot be approved if any tranche breaches constraints; each breach has explicit reason text. |
| T47 | P0 | M | Major | Add deterministic per-tranche impact preview (`gross proceeds`, `employment tax est.`, `CGT est.`, `fees`, `net cash`) with cumulative totals. | Tax visibility, hidden drag visibility | Tranche and cumulative totals reconcile to Simulate/Tax Plan assumptions for the same inputs/date. |
| T48 | P1 | M | Major | Add explicit execution method modes: `Calendar Tranches`, `Threshold Bands`, `Limit Ladder`, and broker-native `TWAP/VWAP` wrapper when available. | Clarity, execution discipline | Each method has transparent formula/inputs and no method depends on market prediction fields. |
| T49 | P1 | M | Major | Add IBKR-compatible order staging export from an approved sell plan (order ticket pack / CSV) with deterministic external IDs. | Operational feasibility, auditability | Export reproduces plan exactly and can be reconciled back to plan + executed transactions. |
| T50 | P1 | S | Minor | Add a product-level recommended default execution profile: `Hybrid De-Risk` (calendar tranches + concentration band trigger + limit-order guardrails). | Behavioural risk reduction, concentration clarity | New plans default to Hybrid profile with editable parameters and clear rationale text (non-advisory). |
| T51 | P1 | M | Major | Add plan adherence and drift panel: planned vs executed quantity, pending tranches, concentration reduction achieved, remaining tax budget, and calendar status. | Execution discipline, retained-wealth realism | User can see variance from plan at tranche and total level; calendar statuses (`planned`, `due`, `executed`, `cancelled`) are synchronized deterministically. |
| T52 | P2 | S | Minor | Add glossary/model-scope coverage for sell-execution methods (limit ladder, TWAP, VWAP, threshold trigger) and when each is applicable in-system. | Terminology clarity, decision confidence | Sell-plan page links directly to glossary anchors and scope/exclusion notes. |
| T53 | P0 | S | Minor | Add Calendar deep-linking and filters for sell plans (`Sell Plan ID`, `Method`, `Status`, `Next tranche due`) from `/calendar` to `/sell-plan`. | Operational clarity, execution follow-through | Calendar can filter sell-plan events and open the exact tranche/plan context in one click. |

Method basis used for scoping (non-advisory):
- SEC Investor.gov order types (market/limit/stop).
- IBKR order docs (TWAP/VWAP algorithmic execution modes).
- Fidelity rebalancing methods (calendar, threshold, hybrid).
- Investopedia scale-out concept (staggered exits).

## v2 Strategic Upgrade (Material, Delivered Archive)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T13 | P2 | M | Major | Add wrapper-level allocation strip (`ISA` vs non-ISA, tax-sheltered vs taxable) on Portfolio and Risk. | ISA efficiency visibility | ISA is visually first-class, not only a scheme label. |
| T14 | P2 | M | Major | Add FX risk attribution widget (`native move` vs `FX move`) to Analytics/Risk. | FX exposure visibility | Users can separate stock movement from FX contribution. |
| T15 | P2 | L | Major | Add sequential-leg execution mode to Scenario Lab (optional) for order-sensitive FIFO outcomes. | Decision realism, FIFO integrity | Scenario results can run independent or sequential with clear mode indicator. |
| T16 | P2 | M | Major | Add lock-window shading and non-sellable markers in History/Security History charts. | Liquidity realism over time | Historical charts visually identify non-sellable periods. |
| T17 | P2 | M | Major | Add audit-to-surface trace links (from key totals to contributing lots and recent audit mutations). | Transparency and trust | User can trace a number to source rows and change history in <=3 clicks. |
| T18 | P2 | S | Minor | Add Add Security data-quality checks (duplicate symbol+currency conflicts, required metadata warnings). | Data integrity | Invalid/ambiguous instrument setup is blocked or warned before save. |

## Missing Scope Expansion (Feasible, Objective-Aligned, Delivered Archive)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T19 | P2 | M | Major | Add `Capital Efficiency` deep-dive page (`/capital-efficiency`) decomposing structural drag rate (tax, fees, dividend tax, FX friction) and annualized drag % of capital. | Structural drag visibility | Drag components and aggregate drag rate are shown with deterministic formulas and historical basis windows. |
| T20 | P2 | M | Major | Add a `Concentration Guardrails` feature with threshold bands and deterministic alerts (e.g., top holding > X%, employer exposure > Y%). | Concentration risk visibility | User-configurable thresholds trigger visible non-predictive alerts in Portfolio/Risk. |
| T21 | P2 | L | Major | Add a `Leave Employment Scenario` page (`/employment-exit`) to model locked/forfeitable/tax-trigger outcomes under fixed assumptions and dates. | Liquidity realism, forfeiture visibility | Scenario produces deterministic before/after tables with no market forecasting. |
| T22 | P2 | M | Major | Add an `ISA Allocation Lens` page (`/isa-efficiency`) comparing current ISA share vs taxable share and showing potential sheltering headroom by tax year. | ISA efficiency | ISA/non-ISA breakdown is explicit and tax-year context is visible. |
| T23 | P2 | M | Major | Add a `Fee Drag Ledger` page (`/fee-drag`) aggregating broker fees from committed disposals and showing net impact vs gross proceeds/economic gain. | Hidden drag visibility | Fee drag totals reconcile to transactions and are separated from tax drag. |
| T24 | P2 | M | Major | Add `Data Quality` page (`/data-quality`) for stale prices, stale FX, missing prices, and modelling-impact counts by page/output. | Clarity, decision reliability | Users can see where data quality may distort outputs before making decisions. |
| T25 | P2 | M | Major | Add `Employment Tax Events` page (`/employment-tax-events`) exposing transfer/disposal tax events and estimated liabilities by tax year. | Tax visibility | Employment-tax event trail is queryable and reconciles to scenario/transfer outputs. |
| T26 | P2 | S | Minor | Add deterministic `Alert Center` panel in top nav for upcoming high-impact events (forfeiture ending soon, vest soon, stale tax inputs). | Risk visibility | Non-predictive alerts are generated from existing calendar/settings/state rules only. |
| T27 | P2 | M | Major | Add `Forfeiture Heatmap` widget (security x days-remaining buckets) to Risk/Analytics. | Forfeiture risk visibility | At-risk matched-share value is visible by timing bucket and security. |
| T28 | P2 | M | Major | Add `Cross-Page Reconciliation` utility (`/reconcile`) that shows why Portfolio, Net Value, Simulate, and Tax Plan numbers differ for same holdings/date. | Clarity | Deterministic reconciliation explains delta components (scope, taxes, lock status, assumptions). |
| T29 | P2 | M | Major | Persist Scenario Lab results to DB (instead of memory-only) with audit metadata and reproducible input snapshots. | Determinism, transparency | Scenario history survives restart and each scenario can be reloaded with full input provenance. |
| T30 | P3 | M | Major | Add `Price/FX Basis Timeline` page showing valuation basis changes over time and impact on GBP totals. | FX exposure visibility | Users can inspect when FX/price basis shifted and how much valuation changed from basis updates. |
| T31 | P2 | M | Major | Add deterministic `Rebalance Friction` panel that quantifies how much employer concentration could be reduced using only currently sellable lots and current tax assumptions. | Concentration and liquidity realism | Output reports friction as tax + lock constraints, with no return forecasting. |
| T32 | P3 | S | Minor | Add per-page `Model Scope` cards (inputs used, assumptions, exclusions) for Portfolio, Net Value, Tax Plan, Scenario Lab. | Clarity | Every major page has a compact, standardized scope disclosure. |

## Missing Scope Notes

- These items are intentionally deterministic extensions of existing services/data.
- None of these items require predictive AI, market timing, or buy/sell advice logic.
- Stage-10 items `T15`-`T32` are now implemented; future iterations should focus on hardening and regression coverage depth.
- Sell execution additions (`T45`-`T53`) are execution-policy tooling, not return forecasting or trade recommendation logic.

## Execution Order (Source of Truth)

1. `T01`, `T02`, `T05` (Completed).
2. `T45`, `T46`, `T47`, `T53` (Completed).
3. `T48`, `T49`, `T50` (Completed).
4. `T36`, `T37` (Completed).
5. `T38`, `T33` (Completed).
6. `T03`, `T04`, `T39`, `T40` (Completed).
7. `T06`, `T07`, `T08`, `T09`, `T43`, `T51`, `T52` (Completed).
8. `T10`, `T11`, `T12` (Completed).
9. `T34`, `T35`, `T41`, `T42`, `T44` (Completed).
10. `T13`-`T32` (Completed).

## Definition of Done (Current + Next)

- Completed baseline: Core v1 (`T01`-`T12`), sell execution core (`T45`-`T50`, `T53`), and priority additions (`T33`, `T36`, `T37`, `T38`) are merged.
- Stage 10 complete: `T13`-`T32` are merged with targeted deterministic tests for strategic reconcile and audit record filtering.
- Next exit criteria: close refinement backlog `T58`-`T79` with regression coverage for changed semantics, labels, traces, and reconciliation pathways.
- Feature expansion backlog remains deferred until refinement closure.

## Deferred Feature Backlog (Post-Refinement, Not Active)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T54 | P2 | M | Major | Add global `as_of` date mode across Portfolio, Net Value, Risk, Calendar, and Scenario Lab payloads/routes. | Clarity, deterministic comparability | A single selected date produces consistent lock/forfeiture/tax context across all major pages and exports. |
| T55 | P2 | M | Major | Add event-level provenance badges (price date, FX date, stale flags) in Calendar and Risk event rows. | Data-quality visibility, decision reliability | Every value-at-stake event row shows provenance/freshness metadata without leaving the page. |
| T56 | P2 | M | Major | Add `/reconcile` drift explainer comparing current vs prior snapshot deltas by cause (price, FX, quantity, settings/audit). | Transparency, behavioural risk reduction | Users can trace a headline delta to deterministic components and linked audit rows in <=3 clicks. |
| T57 | P2 | S | Minor | Add persisted alert lifecycle (server-side dismiss/snooze states with deterministic expiry semantics). | Risk salience, cross-session consistency | Alert state survives browser reset/device changes and is auditable by state transition. |

## Portfolio Template Refinements

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T58 | P1 | M | Major | Reorder Portfolio top cards into two explicit bands: `Actionable Today` first, `Hypothetical/Context` second. | Liquidity realism, behavioural bias reduction | First visible band contains deployable/net-liquidity/employment-tax action metrics only; hypothetical context is visually secondary. |
| T59 | P1 | S | Minor | Add top-level `Valuation Basis` strip on Portfolio (`price as of`, `FX as of`, stale-state badges). | Clarity, decision reliability | Every headline card is preceded by deterministic basis timestamps and freshness status in a single compact strip. |
| T60 | P2 | S | Minor | Add metric-level formula chips and trace links for key decision columns (`Net If Sold`, `Gain If Sold`, `Net If Held`). | Transparency, terminology clarity | Users can open formula basis and reconcile trace from each key decision metric in <=2 clicks. |
| T61 | P3 | S | Minor | Standardize wording/capitalization for market-state and decision badges (`Open`/`Closed`, `Sellable`, `Locked`, etc.). | Clarity, UX friction reduction | Status labels are consistent across table, mobile cards, and tooltips with no mixed-case drift. |
| T62 | P2 | M | Major | Move Portfolio view-control semantics from generic text to deterministic control help panel tied to active filters/sort. | Clarity, risk visibility | Control panel shows active filter/sort impact and affected-row count; text is specific to current selection. |

## Page-by-Page Refinement Backlog (Post Assessment 2026-03-06)

Reference: `docs/STRATEGIC_DOCUMENTATION.md` section `Individual Page Effectiveness Assessment (2026-03-06)`.

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T63 | P1 | S | Minor | Add explicit `Net Value vs Deployable Today` delta card + link from `/net-value` to `/capital-stack`. | Liquidity clarity | Net Value page surfaces non-actionable gap in one visible stat. |
| T64 | P1 | M | Major | Integrate cash sidecar into main `/capital-stack` output or add standardized combined-total card. | Deployable realism | Holdings deployable and cash deployable totals reconcile in one formula path. |
| T65 | P1 | M | Major | Add balance-level freshness/provenance metadata to `/cash` rows and transfer previews. | FX clarity, data trust | Every non-GBP affecting action shows FX/date/source confidence. |
| T66 | P1 | M | Major | Add planned-vs-committed reconciliation panel to `/sell-plan` tranches. | Execution discipline | Tranche-level variance is explainable with deterministic source links. |
| T67 | P1 | S | Minor | Add disposal-price provenance/staleness badge to `/simulate` input/result surfaces. | False precision reduction | Simulate output always states price basis and freshness. |
| T68 | P1 | M | Major | Enforce non-hideable critical widget floor in `/analytics` visibility controls. | Risk visibility | Critical widgets cannot all be hidden at once; user sees deterministic warning. |
| T69 | P1 | M | Major | Add event-level price/FX freshness badges in `/calendar` event rows. | Data-quality visibility | Each value-at-stake row includes provenance/freshness fields. |
| T70 | P2 | M | Major | Add `/scenario-lab` per-leg trace links to `/reconcile` and scenario template save/load presets. | Transparency | Scenario result rows open deterministic trace paths in <=2 clicks. |
| T71 | P2 | M | Major | Add decomposition overlays (`price`, `FX`, `quantity`, `dividends`) to `/history` and `/history/{security_id}`. | Causal clarity | Major time-series shifts are decomposed into deterministic components. |
| T72 | P2 | M | Major | Add reporting comparison refinements: `/cgt` assumption badges, `/economic-gain` CGT delta column, `/dividends` actual-vs-forecast split. | Tax clarity | Report pages expose basis differences and estimate quality directly in tables/stats. |
| T73 | P2 | M | Major | Improve strategic utilities (`/insights`, `/capital-efficiency`, `/employment-exit`, `/isa-efficiency`, `/fee-drag`, `/data-quality`, `/employment-tax-events`, `/basis-timeline`) with trend context and direct action links. | Decision flow, clarity | Each strategic page adds at least one trend/comparison + one fix/deep-link action. |
| T74 | P2 | M | Major | Extend `/reconcile` with prior-snapshot drift decomposition panel. | Cross-page trust | Delta causes are broken down by price/FX/quantity/settings/transactions. |
| T75 | P2 | S | Minor | Add structured diff highlighting and date-range filters to `/audit`. | Traceability | Users can isolate meaningful field-level changes without raw JSON scanning. |
| T76 | P2 | M | Major | Add downstream-impact previews to `/portfolio/add-lot`, `/portfolio/edit-lot`, `/portfolio/transfer-lot`, and conflict-resolution helper to `/portfolio/add-security`. | Input integrity | Users see affected totals/risk flags before commit on all mutation forms. |
| T77 | P2 | S | Minor | Add settings completeness and constrained-surface checklist to `/settings`. | Assumption transparency | Settings page lists which outputs are currently estimate-constrained. |
| T78 | P3 | S | Minor | Add reverse-link map from `/glossary` terms to primary consuming pages. | Terminology clarity | Each glossary anchor shows at least one source page link. |
| T79 | P3 | S | Minor | Improve operational messaging for `/auth/login` and `locked.html` (rate-limit help, recovery checklist). | Operational trust | Users receive actionable next steps without exposing sensitive auth internals. |

## Deferred Feature Candidates (Separate Track, Not Active)

These are capability expansions, not page refinements. Keep parked until the active refinement queue is complete.

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T80 | P2 | M | Major | Add deterministic decision-brief export pack (selected metrics + assumptions + trace links) from major surfaces. | Transparency, auditability | Export artifact includes reproducible inputs/metadata and deep links to traces. |
| T81 | P2 | M | Major | Add guided weekly review workflow spanning Portfolio, Risk, Calendar, and Reconcile with completion notes. | Behavioural discipline | Workflow state persists and can be resumed without reconfiguration. |
| T82 | P2 | M | Major | Add deterministic notification digest for threshold breaches, stale data, and upcoming forfeiture/tax events. | Risk salience | Digest entries are generated exclusively from existing deterministic rules/state. |
