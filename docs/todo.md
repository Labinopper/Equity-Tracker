# Next-Step TODO (Objective-Aligned)

Scope guardrails:
- Deterministic modelling only.
- No market prediction features.
- No buy/sell advice language.
- Every change must improve at least one of: clarity, risk visibility, retained-wealth realism, hidden drag visibility.

## Progress Update (2026-03-04)

- Completed implementation: `T01`, `T02`, `T05`.
- `T02` semantic fix applied: Net Value employment-tax wording now matches implementation (sellable-lot estimate in sell-all context).
- Tax-input health warning now appears consistently on Portfolio, Net Value, Simulate, and Tax Plan.
- Stage 2 baseline started: `/sell-plan` UI shipped with deterministic calendar-tranche plan creation and tranche status updates (`planned`/`due`/`executed`/`cancelled`).
- Calendar linkage shipped: sell-plan tranche events now appear in `/calendar` with deep links and sell-plan filters (`sell_plan_id`, `method`, `status`).
- Stage 2 core extended: sell-plan constraint engine now enforces sellable-only quantity, lock/forfeiture exclusion, minimum spacing, and daily quantity/notional caps with explicit breach reasons.
- Stage 2 impact preview shipped: per-tranche deterministic projections now include `gross proceeds`, `employment tax estimate`, `CGT estimate`, `fees`, `net cash`, and cumulative totals.
- Sell-plan to simulate bridge shipped: tranche rows now open `/simulate` with security/quantity/price prefilled and backlink context to the originating plan.
- Simulate-first handoff shipped: simulation results now link directly to prefilled sell-plan creation (`security`, `quantity`, `reference price`), with guidance to simulate before planning.
- Sell-plan lifecycle control added: plans can now be deleted directly from `/sell-plan` with explicit confirmation.
- Sellability alignment shipped: Sell Plan now follows Simulate sellability semantics for ESPP+ (paid shares included; matched shares included once past forfeiture window).
- Whole-share MAX behavior shipped in Simulate and Sell Plan (`MAX` floors to full shares, and fractional simulation quantities are blocked in UI flow).
- Stage 3 shipped: sell-plan method modes (`Calendar Tranches`, `Threshold Bands`, `Limit Ladder`, `Broker Algo TWAP/VWAP`), default `Hybrid De-Risk` profile inputs/rationale, approval gating, and IBKR staging CSV export with deterministic external IDs.
- Stage 4 shipped: `/cash` multi-currency ledger (BROKER/ISA/BANK), append-only auditable cash entries, and GBP-only ISA transfer workflow with mandatory FX conversion provenance for non-GBP sources.
- Stage 5 shipped: `/capital-stack` wealth-reality stack now reconciles Gross -> Locked -> Forfeitable -> Hypothetical Liquid -> (Employment Tax + CGT + Fees) -> Net Deployable Today with explicit deterministic formula notes.
- Dual-cost policy shipped: `True Cost (Acquisition)` remains immutable and `Dividend-Adjusted Capital at Risk` is now surfaced separately (Portfolio + Capital Stack + Glossary), without changing tax-report cost logic.
- Stage 6 shipped: Portfolio now surfaces top-holding and employer concentration (gross + sellable), split `Locked` vs `Forfeitable` value buckets, deployable-capital metrics including GBP cash sidecar, and deterministic Employer Dependence Ratio with transparent formula components.

## Execution Status (Source of Truth)

| Stage | Scope | Status | Notes |
|---|---|---|---|
| 1 | `T01`, `T02`, `T05` | Complete | Label/semantics/tax-warning baseline is live. |
| 2 | `T45`, `T46`, `T47`, `T53` | Complete | Sell-plan core + constraints + impact preview + calendar linkage are live. Includes simulate-first handoff, plan delete, whole-share controls, and ESPP+ sellability alignment. |
| 3 | `T48`, `T49`, `T50` | Complete | Method modes, approval workflow, and deterministic IBKR staging export are live. |
| 4 | `T36`, `T37` | Complete | Multi-currency cash ledger and GBP-only ISA transfer conversion workflow are live. |
| 5 | `T38`, `T33` | Complete | Capital stack and dual-cost dividend-adjusted capital-at-risk policy are live. |
| 6 | `T03`, `T04`, `T39`, `T40` | Complete | Portfolio and Risk now show concentration (gross/sellable), locked/forfeitable split, cash-aware deployable metrics, and employer dependence ratio. |
| 7 | `T06`, `T07`, `T08`, `T09`, `T43`, `T51`, `T52` | Next | Decision-surface hardening and execution governance. |
| 8 | `T10`, `T11`, `T12` | Pending | Cross-surface clarity and regression lock-in. |
| 9 | `T34`, `T35`, `T41`, `T42`, `T44` | Pending | Dividend attribution and optionality extensions. |
| 10 | `T13`-`T32` | Pending | Remaining strategic expansion pages/features. |

## Core v1 (Critical)

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

## Priority Additions (Dividend Economics + Multi-Currency Cash)

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

## Priority Additions (Deterministic Sell Execution / Trickle Sell)

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

## v2 Strategic Upgrade (Material)

| ID | Priority | Size | Type | Task | Objective Alignment | Acceptance Criteria |
|---|---|---|---|---|---|---|
| T13 | P2 | M | Major | Add wrapper-level allocation strip (`ISA` vs non-ISA, tax-sheltered vs taxable) on Portfolio and Risk. | ISA efficiency visibility | ISA is visually first-class, not only a scheme label. |
| T14 | P2 | M | Major | Add FX risk attribution widget (`native move` vs `FX move`) to Analytics/Risk. | FX exposure visibility | Users can separate stock movement from FX contribution. |
| T15 | P2 | L | Major | Add sequential-leg execution mode to Scenario Lab (optional) for order-sensitive FIFO outcomes. | Decision realism, FIFO integrity | Scenario results can run independent or sequential with clear mode indicator. |
| T16 | P2 | M | Major | Add lock-window shading and non-sellable markers in History/Security History charts. | Liquidity realism over time | Historical charts visually identify non-sellable periods. |
| T17 | P2 | M | Major | Add audit-to-surface trace links (from key totals to contributing lots and recent audit mutations). | Transparency and trust | User can trace a number to source rows and change history in <=3 clicks. |
| T18 | P2 | S | Minor | Add Add Security data-quality checks (duplicate symbol+currency conflicts, required metadata warnings). | Data integrity | Invalid/ambiguous instrument setup is blocked or warned before save. |

## Missing Scope Expansion (Feasible, Objective-Aligned)

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
- Prioritize T33, T36, T37, and T38 first (dividend economics + currency cash + deployable-capital reality).
- Sell execution additions (`T45`-`T53`) are execution-policy tooling, not return forecasting or trade recommendation logic.

## Execution Order (Source of Truth)

1. `T01`, `T02`, `T05` (Completed).
2. `T45`, `T46`, `T47`, `T53` (Completed).
3. `T48`, `T49`, `T50` (Completed).
4. `T36`, `T37` (Completed).
5. `T38`, `T33` (Completed).
6. `T03`, `T04`, `T39`, `T40` (Completed).
7. `T06`, `T07`, `T08`, `T09`, `T43`, `T51`, `T52` (Next active stage).
8. `T10`, `T11`, `T12`.
9. `T34`, `T35`, `T41`, `T42`, `T44`.
10. `T13`-`T32`.

## Definition of Done (Next Step)

- Core v1 items (T01-T12) merged.
- Sell execution core (`T45`-`T50`, `T53`) merged.
- Priority additions T33, T36, T37, and T38 merged.
- All affected pages explicitly separate actionable vs hypothetical values.
- Concentration and forfeiture exposure visible from primary decision surfaces.
- Tax estimation dependencies and assumptions are explicit at point-of-use.
- Sell-plan outputs are deterministic, auditable, and reconciled to executed transactions.
- Sell-plan tranches are visible and navigable in `/calendar` with synchronized status.
- Multi-currency cash and ISA conversion constraints are modelled and auditable.
- Dividend-adjusted capital-at-risk metric is visible without mutating canonical true cost.
- Regression coverage updated for all changed calculation displays and labels.
