# Strategic Question Table

Last updated: `2026-03-07`

Execution mode: refinement and hardening closure remains complete (`T58`-`T79` shipped). `2026-03-07` maintenance updates are now live (lot-first dividends input, deployable cash FX conversion to GBP-equivalent, Portfolio UI simplification, and pension tracking/projection); remaining feature-expansion tracks stay deferred pending reprioritization.

Legend: `Y` = directly addressed, `P` = partially/implicitly addressed, `N` = not addressed.

## Post-Closure Updates (`2026-03-07`)

1. Dividends add workflow is now lot-first: select active lot(s) first, then enter payout values.
2. Dividends maintenance actions (`one-time cash backfill`, `relink existing dividend`) are intentionally hidden from UI to reduce accidental historical rewrites; backend routes remain available for controlled recovery use.
3. Deployable cash now includes non-GBP BROKER/BANK balances converted to GBP-equivalent using current FX rates (used by Portfolio, Risk, and Capital Stack).
4. Dividend cash auto-post now writes FX metadata (`fx_rate`, `fx_source`) on cash entries to improve provenance clarity.
5. Portfolio page no longer shows `Portfolio View Controls`; `Model Scope` is now collapsed by default.
6. Pension page is now live at `/pension` with append-only contribution tracking, deterministic return scenarios, retirement-target comparison, and tracked-wealth context.

| Page | Primary Strategic Question | Secondary Questions | Liquidity Clarity | Tax Visibility | Forfeiture Risk | Concentration Risk | ISA Efficiency | True Cost Modelling | FIFO Integrity | FX Exposure |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio (`/`) | What do I own now, what is sellable now, and what is retained after estimated drag? | How much capital is locked, forfeitable, or employer-concentrated? | Y | Y | Y | Y | Y | Y | P | Y |
| Net Value (`/net-value`) | If everything were hypothetically sold now, what gross vs estimated net remains? | How large is the theoretical-to-actionable gap? | Y | Y | Y | P | P | P | N | Y |
| Capital Stack (`/capital-stack`) | What is true deployable capital after structural deductions? | Which deduction layer is dominating retained-wealth loss? | Y | Y | Y | P | Y | Y | N | Y |
| Cash (`/cash`) | What cash is deployable by account and currency, and what ISA transfer path is valid? | What FX conversion provenance exists for non-GBP to ISA flow? | Y | P | N | N | Y | N | N | Y |
| Sell Plan (`/sell-plan`) | How can disposal be executed deterministically in tranches under constraints? | Is the plan compliant with sellability, cadence, and cap constraints? | Y | Y | Y | Y | P | Y | P | N |
| Simulate Disposal (`/simulate`) | For a proposed disposal, what FIFO and tax/fee outcomes occur before commit? | What forfeiture and shortfall risks are triggered? | Y | Y | Y | P | P | Y | Y | N |
| Per Scheme (`/per-scheme`) | Which scheme structures are driving retained-wealth outcomes? | Where do tax/forfeiture burdens cluster by scheme? | Y | Y | Y | Y | P | Y | P | P |
| Tax Plan (`/tax-plan`) | How does disposal drag change across UK tax-year boundaries? | How sensitive are outputs to assumptions and compensation inputs? | P | Y | N | N | Y | P | N | P |
| Risk (`/risk`) | How structurally exposed is the portfolio to concentration and illiquidity? | How does optionality evolve across deterministic time bands? | Y | P | Y | Y | Y | P | N | P |
| Analytics (`/analytics`) | What cross-domain patterns matter most for near-term decisions? | Which widgets are critical vs supporting and why? | Y | Y | Y | Y | P | Y | N | Y |
| Calendar (`/calendar`) | When do lock/forfeiture/tax/sell-plan events change constraints? | What is due next and what capital is affected? | Y | P | Y | P | N | N | N | N |
| Scenario Lab (`/scenario-lab`) | Across multi-leg what-if disposals, what are net/economic outcomes? | Where are shortfalls, forfeiture, and tax burdens concentrated? | Y | Y | Y | P | P | Y | Y | N |
| History (`/history`) | How have value and gain-if-sold evolved under reconstructed holdings? | How much of history was non-sellable or data-limited? | P | P | P | P | P | Y | N | P |
| Security History (`/history/{security_id}`) | For one security, how did price vs cost and sellability evolve? | How much movement was stock vs FX-driven context? | P | P | P | N | N | Y | N | Y |
| CGT Report (`/cgt`) | What is UK tax-year realised CGT position? | What is the estimated CGT due under current settings? | N | Y | N | N | Y | N | Y (realised) | N |
| Economic Gain Report (`/economic-gain`) | What is realised performance on true-cost basis? | How different is economic vs tax basis outcome? | N | P | N | N | Y | Y | Y (realised) | N |
| Dividends (`/dividends`) | What dividend flow is taxable vs ISA-exempt, with FX provenance? | How do net dividends offset current capital at risk by security? | P | Y | N | P | Y | Y | N | Y |
| Audit Log (`/audit`) | What deterministic data mutations changed model outputs? | Which input changes altered downstream surfaces? | N | P | N | N | N | P | P | N |
| Pension (`/pension`) | What is the current pension pot, what has been paid in, and what could it reach by retirement under fixed assumptions? | How much is recorded inputs vs attributed growth, and what is the shortfall vs target? | N | P | N | N | N | N | N | N |
| Add Lot (`/portfolio/add-lot`) | Are acquisition inputs sufficient for deterministic cost/tax/lock modelling? | What values will be persisted before save? | N | Y | Y | N | Y | Y | P | Y |
| Transfer Lot (`/portfolio/transfer-lot`) | Can this transfer occur without violating scheme rules? | What transfer-time forfeiture and tax effects are triggered? | Y | Y | Y | N | Y | P | Y | Y |
| Edit Lot (`/portfolio/edit-lot`) | How can lot corrections be made with auditability intact? | What protected constraints apply to quantity/cost edits? | P | Y | N | N | P | Y | P | Y |
| Add Security (`/portfolio/add-security`) | Is instrument metadata valid for pricing/FX valuation? | Is catalog/manual provenance explicit? | N | N | N | N | N | N | N | Y |
| Settings (`/settings`) | Are tax/staleness/privacy assumptions configured for reliable outputs? | Which pages are currently assumption-constrained? | P | Y | N | N | P | P | N | Y |
| Glossary (`/glossary`) | Are terms defined in model-specific deterministic language? | Can users jump directly from page labels to definitions? | Y | Y | Y | P | Y | Y | P | P |
| Login (`/auth/login`) | Is access controlled before financial data is shown? | Are authentication controls active? | N | N | N | N | N | N | N | N |
| Locked (`locked.html`) | Is model unavailability made explicit when DB is locked? | Is operational recovery path clear? | N | N | N | N | N | N | N | N |

# Page-by-Page Documentation

## Portfolio (`/`)
1. Purpose: Current structural position and actionable retained capital view.
2. Economic Model Behind the Page: Market value, true cost (acquisition), dividend-adjusted capital-at-risk, cost basis, estimated net liquidity (sellable), locked/forfeitable splits, concentration (gross/sellable), deployable capital including cash, wrapper allocation, and employer dependence. Mixes actionable and hypothetical values, clearly tagged.
3. Illusion vs Reality Risks: Gross value can still anchor perception if users ignore locked/forfeitable and deployable cards.
4. Decision Support Role: Disposal triage, concentration awareness, and capital-at-risk framing. No market prediction or trade recommendation logic.
5. Structural Improvements: Core v1 complete for this page. Next material upgrade is decomposition drill-through from top cards to contributing lots/audit rows.

## Net Value (`/net-value`)
1. Purpose: Hypothetical full-liquidation framing.
2. Economic Model Behind the Page: All-lot gross market value, estimated employment-tax overlay in hypothetical context, and risk classifications. Primarily theoretical.
3. Illusion vs Reality Risks: Can be over-read as deployable cash if compared without Portfolio/Capital Stack.
4. Decision Support Role: Balance-sheet realism and stress framing.
5. Structural Improvements: Add explicit side-by-side delta to current deployable capital from Capital Stack.

## Capital Stack (`/capital-stack`)
1. Purpose: Deployable-capital reality engine.
2. Economic Model Behind the Page: Deterministic vertical stack: Gross -> Locked -> Forfeitable -> Hypothetical Liquid -> Employment Tax -> CGT -> Fees -> Net Deployable Today. Includes dividend-adjusted capital-at-risk context where available.
3. Illusion vs Reality Risks: Depends on estimate quality for tax and fee assumptions.
4. Decision Support Role: Primary retained-wealth clarity surface.
5. Structural Improvements: Add linked assumption-quality badges per deduction block.

## Cash (`/cash`)
1. Purpose: Multi-currency cash reality and transfer controls.
2. Economic Model Behind the Page: Append-only cash ledger by container (`BROKER`, `ISA`, `BANK`) and currency, with GBP-only ISA transfer rule requiring explicit FX rate/fee/source provenance.
3. Illusion vs Reality Risks: Missing FX provenance reduces confidence in converted balances.
4. Decision Support Role: Operational deployable-cash management and ISA transfer integrity.
5. Structural Improvements: Add per-balance provenance freshness indicators.

## Sell Plan (`/sell-plan`)
1. Purpose: Deterministic staged disposal execution planning.
2. Economic Model Behind the Page: Tranche scheduler with sellability constraints, spacing caps, daily caps, method profiles (`Calendar`, `Threshold`, `Limit Ladder`, `TWAP/VWAP wrapper`), tranche impact projections, adherence/drift tracking, calendar linkage, and IBKR staging export.
3. Illusion vs Reality Risks: Users can infer certainty from one reference price if market moves after planning.
4. Decision Support Role: Execution discipline and friction visibility. No optimization/recommendation engine.
5. Structural Improvements: Add reconciliation view from planned tranches to committed lot disposals.

## Simulate Disposal (`/simulate`)
1. Purpose: Pre-commit deterministic disposal preview.
2. Economic Model Behind the Page: FIFO lot allocation, estimated employment tax/CGT/fees, forfeiture/shortfall checks, and explicit acknowledgement gate when employment-tax estimate is unavailable. Whole-share enforcement on MAX/flow.
3. Illusion vs Reality Risks: Manual price input can imply false precision.
4. Decision Support Role: Verify consequences before commit; handoff to Sell Plan creation.
5. Structural Improvements: Add price-source provenance and stale warning inline with entered price.

## Per Scheme (`/per-scheme`)
1. Purpose: Scheme-structure economic comparison.
2. Economic Model Behind the Page: Current hypothetical sell-now metrics plus realised disposal history by scheme, with true-cost/economic framing.
3. Illusion vs Reality Risks: Current and realised blocks can be miscompared as like-for-like.
4. Decision Support Role: Identify scheme-level drag/constraint burden.
5. Structural Improvements: Add scheme-level drill-through from dividend-adjusted totals to contributing securities/lots.

## Tax Plan (`/tax-plan`)
1. Purpose: UK tax-year drag planning surface.
2. Economic Model Behind the Page: Payroll tax overlays (IT/NI/SL), ANI/PA interactions, CGT scenario outputs, and assumption-quality tags (`Exact`, `Weighted Estimate`, `Unavailable`) across major blocks.
3. Illusion vs Reality Risks: Weighted estimates can be interpreted as precise outcomes.
4. Decision Support Role: Timing and tax-awareness planning under deterministic assumptions.
5. Structural Improvements: Add explicit route to exact lot-driven scenario import from Scenario Lab.

## Risk (`/risk`)
1. Purpose: Structural fragility and optionality surface.
2. Economic Model Behind the Page: Concentration, liquidity split, wrapper allocation, deployable breakdown, employer dependence ratio, optionality timeline bands (`Now`/`6m`/`1y`/`3y`/`5y`), and optionality index with adjustable transparent weights.
3. Illusion vs Reality Risks: Score can be over-trusted if users ignore component table.
4. Decision Support Role: Risk salience and optionality tracking.
5. Structural Improvements: Add trend-over-time overlays for guardrail breaches and optionality shifts so persistent risk is separated from transient spikes.

## Analytics (`/analytics`)
1. Purpose: Cross-system monitoring with decision-critical ordering.
2. Economic Model Behind the Page: Widget set includes liquidity, concentration, forfeiture, tax position, timeline, stress, FX attribution, and valuation history. Each widget carries context, criticality, default visibility, and priority rank.
3. Illusion vs Reality Risks: Users may hide critical widgets and reduce decision quality.
4. Decision Support Role: High-level risk/tax/liquidity coherence checks.
5. Structural Improvements: Add lockable minimum critical-widget set in visibility controls.

## Calendar (`/calendar`)
1. Purpose: Time-constraint control surface.
2. Economic Model Behind the Page: Vest, forfeiture, tax-year boundary, and sell-plan tranche events with filtering and deep links.
3. Illusion vs Reality Risks: Event value-at-stake may be treated as guaranteed if pricing becomes stale.
4. Decision Support Role: Execution timing and forfeiture avoidance awareness.
5. Structural Improvements: Add explicit stale-price badge directly in event rows.

## Scenario Lab (`/scenario-lab`)
1. Purpose: Multi-leg disposal what-if composition.
2. Economic Model Behind the Page: Deterministic scenario legs with aggregated net/economic/tax outputs and comparisons, currently non-sequential by default.
3. Illusion vs Reality Risks: Independent-leg mode can be mistaken for execution-order realistic.
4. Decision Support Role: Structured scenario comparison before execution.
5. Structural Improvements: Add per-leg trace links to `/reconcile` and scenario-template reuse for recurring what-if workflows.

## History (`/history`) and Security History (`/history/{security_id}`)
1. Purpose: Time-series structural context.
2. Economic Model Behind the Page: Reconstructed holdings, gain-if-sold series, price/cost overlays, and security-specific progression.
3. Illusion vs Reality Risks: Chart narratives can encourage recency bias and hide non-sellable windows.
4. Decision Support Role: Forensic trend/context review, not prediction.
5. Structural Improvements: Expand source-level decomposition overlays (price vs quantity vs dividends vs FX) for major value shifts.

## Reports (`/cgt`, `/economic-gain`, `/dividends`)
1. Purpose: Realised tax/economic reporting plus dividend drag/provenance.
2. Economic Model Behind the Page:
- `/cgt`: tax-year realised gains/losses and CGT estimate.
- `/economic-gain`: realised gain on true-cost basis.
- `/dividends`: tax-year summaries, taxable vs ISA-exempt split, native-currency + FX provenance per entry, security-level dividend allocation, capital-at-risk-after-dividends, and economic-gain-plus-net-dividends at security level.
3. Illusion vs Reality Risks: Forecast dividends and estimated tax fields can be interpreted as fixed outcomes.
4. Decision Support Role: Reporting-grade clarity on realised and income-driven drag.
5. Structural Improvements: Add explicit `Actual vs Forecast` summary split and link per-security allocation to Portfolio rows.

## Pension (`/pension`)
1. Purpose: Long-horizon retirement capital tracking using deterministic assumptions.
2. Economic Model Behind the Page: Append-only employee/employer/adjustment ledger, current pension-value assumption, contribution-vs-growth attribution, tracked-wealth context against portfolio and deployable cash, and fixed-return projection checkpoints (`Now`, `5y`, `10y`, `Retirement`).
3. Illusion vs Reality Risks: Fixed-return scenarios can be over-read as forecasts if the user ignores model-scope notes and target assumptions.
4. Decision Support Role: Retirement-progress clarity and contribution discipline, not deployable-cash planning or advisory allocation logic.
5. Structural Improvements: Add richer timeline visualization and, if expanded later, direct trace from scenario rows into assumption history / prior contribution windows.

## Input and Operational Surfaces (`/portfolio/add-lot`, `/portfolio/transfer-lot`, `/portfolio/edit-lot`, `/portfolio/add-security`, `/settings`, `/audit`, `/glossary`, `/auth/login`, `locked.html`)
1. Purpose: Data quality, deterministic input controls, provenance, and operational trust.
2. Economic Model Behind the Page: These pages govern assumptions and inputs feeding all decision surfaces (not independent valuation engines).
3. Illusion vs Reality Risks: Incomplete settings or poor metadata can silently degrade downstream estimates.
4. Decision Support Role: Model integrity and auditability.
5. Structural Improvements: `T32` complete. Model Scope disclosures are now standardized across major decision pages.

# System Alignment Review

## Does any page create liquidity illusion?
- Reduced materially by `Capital Stack` and Portfolio split cards.
- Residual risk remains when users anchor on Net Value gross/hypothetical cards.

## Is ISA clearly structurally differentiated?
- Yes at a practical level: wrapper allocation is explicit in Portfolio and Risk, and GBP-only ISA cash transfer controls are enforced in Cash.
- Dedicated ISA strategy surface is now live at `/isa-efficiency` (`T22`).

## Is employer concentration visible enough?
- Improved: Portfolio, Risk, and sell-plan adherence now expose concentration directly.
- Threshold guardrails and alerting are now live via configurable settings and top-nav alert center (`T20`, `T26`).

## Is employment tax treated transparently?
- Yes: Simulate, Portfolio, Net Value context labels, Tax Plan assumption tags, and explicit missing-estimate acknowledgement are in place.

## Are theoretical vs sellable values clearly separated?
- Yes: actionable/hypothetical tagging, Capital Stack, and `/reconcile` now make scope deltas explicit (`T28`).

## Is forfeiture exposure made emotionally visible?
- Yes in Portfolio split buckets, Simulate/Sell Plan constraints, Calendar events, and forfeiture heatmap buckets in Risk/Analytics (`T27`).

## Is FX risk visible or implicit?
- Improved: analytics FX attribution, dividend FX provenance, cash conversion provenance, and `/basis-timeline` are live (`T14`, `T30`).

## Is long-horizon retirement capital represented deterministically?
- Yes: `/pension` now tracks recorded pension inputs separately from attributed growth, shows retirement-target shortfall under fixed assumptions, and keeps the output explicitly non-predictive.
- Residual risk remains if users leave current pot or contribution assumptions stale.

# Behavioural Risk Surface

- Confirmation bias via gross-value anchoring: still possible if users skip Capital Stack and focus on gross market value.
- Over-confidence in employer equity: reduced by concentration/employer dependence metrics plus guardrail alerts; residual risk remains if alerts are ignored.
- Tax underestimation: reduced by assumption-quality tags and acknowledgement gates; still vulnerable when settings are incomplete.
- Risk masking through aggregation: reduced by `/reconcile` trace sections and lot/audit drill-through; still present when users skip trace workflows.
- Comfort from large headline numbers: mitigated, not eliminated; hypothetical and actionable values can still be conflated by inattentive usage.
- False precision risk: persists in manual price inputs and forecast dividend entries.

# Core v1 vs v2 Summary Recommendations

## Core v1
1. Keep strategic pages (`/capital-efficiency`, `/employment-exit`, `/isa-efficiency`, `/fee-drag`, `/data-quality`, `/employment-tax-events`, `/reconcile`, `/basis-timeline`, `/pension`) under regression coverage as model formulas evolve.
2. Keep end-to-end alert-threshold, trace-link, and trace-friction tests current so deterministic decision paths remain completable with visible context.
3. Keep seeded Stage-10 API/UI regression coverage current as new filters, parameters, and page states are added.
4. Harden documentation/test coupling so wording changes remain aligned with implementation semantics.

## v2 Strategic Upgrade
1. Maintain reconciliation delta-tolerance fixtures across representative portfolio states as formulas evolve.
2. Extend the same regression discipline to future shared-foundation features (`as_of`, provenance badges, drift explainers) once they ship.
3. Keep improving trace destinations so filtered audit/reconcile workflows stay high-context even as new record types are added.

Backlog mirror and delivered-post-closure state: logged in `docs/todo.md`.

# Portfolio Refinement Focus (Current Template)

## PR1: Actionable-First Visual Hierarchy
1. Objective Gap: Actionable and hypothetical cards still compete in a single dense band.
2. Refinement: Split top cards into `Actionable Today` first, then `Hypothetical / Context` second.
3. Expected Outcome: Lower gross/hypothetical anchoring and faster decision flow for near-term actions.

## PR2: Explicit Valuation Basis Stamp
1. Objective Gap: Refresh diagnostics show process state but not a clear valuation basis for top metrics.
2. Refinement: Add top-level `Valuation Basis` strip (`price as of`, `FX as of`, stale flags) above headline cards.
3. Expected Outcome: Improved deterministic trust and reduced stale-data misinterpretation.

## PR3: Persistent Guardrail State
1. Status: Delivered on `2026-03-07`.
2. Implementation: Portfolio guardrails and the Risk alert center now use persisted, auditable server-side lifecycle state with snooze/dismiss/reactivate actions and deterministic expiry semantics.
3. Outcome: Cross-session/device consistency and reduced silent risk masking.

## PR4: Formula and Trace Clarity
1. Objective Gap: View-control formula language is generic and not deeply connected to metric semantics.
2. Refinement: Add compact formula chips linking directly to glossary and reconcile anchors per decision metric.
3. Expected Outcome: Stronger explainability and lower ambiguity in interpretation.

## PR5: Terminology and State Wording Consistency
1. Objective Gap: Minor label/state inconsistencies (`Open`/`closed`, similar semantic terms across sections).
2. Refinement: Standardize status capitalization and wording map for decision rows, badges, and tooltips.
3. Expected Outcome: Cleaner cognitive model and lower UI-friction during fast review.

# Individual Page Effectiveness Assessment (2026-03-06)

Legend: `H` = high objective alignment, `M` = moderate alignment (useful but with notable gap), `L` = low alignment.

| Page | Effectiveness | Objective-Aligned Assessment | Documented Refinement |
|---|---|---|---|
| Portfolio (`/`) | H | Strong actionable/hypothetical tagging, concentration and guardrail coverage, trace links, and model scope disclosure. | Keep actionable cards visually first; add persistent guardrail lifecycle and valuation-basis strip. |
| Net Value (`/net-value`) | M | Good theoretical framing and warnings, but still easy to anchor on gross headline numbers. | Add explicit `Net Value vs Deployable Today` delta card with direct link to `/capital-stack`. |
| Capital Stack (`/capital-stack`) | H | Clear deterministic formula chain from gross to deployable; strongest liquidity-illusion countermeasure. | Integrate cash sidecar directly into primary formula output or add explicit combined total card. |
| Cash (`/cash`) | H | Operationally strong with append-only ledger and GBP-only ISA transfer controls. | Add per-balance freshness/provenance badges and pre-submit transfer impact summary row. |
| Sell Plan (`/sell-plan`) | H | Comprehensive deterministic planner with constraints, adherence, and export. | Add direct reconciliation to committed disposals and variance explanation by tranche. |
| Simulate Disposal (`/simulate`) | H | Strong pre-commit FIFO/tax/forfeiture preview with acknowledgement gating. | Show price provenance (`price as of`, stale status) beside price input and results header. |
| Per Scheme (`/per-scheme`) | H | Useful scheme decomposition across current and historic economics. | Add drill-through from scheme totals to contributing securities/lots and reconcile anchors. |
| Tax Plan (`/tax-plan`) | H | High-value timing/ANI/CGT surface with assumption-quality badges. | Add one-click scenario import from Scenario Lab and explicit stale-data impact callout. |
| Risk (`/risk`) | H | Strong structural risk visibility (guardrails, optionality, forfeiture heatmap, friction). | Add trend-over-time for optionality/guardrail breaches to separate transient vs persistent risk. |
| Analytics (`/analytics`) | M | Rich coverage, but widget hiding can suppress critical signals. | Add non-hideable minimum critical widget set and warning when critical widgets are hidden. |
| Calendar (`/calendar`) | M | Good event timing and sell-plan integration; value-at-stake can look overly certain. | Add row-level price/FX freshness badges and event provenance tooltips. |
| Scenario Lab (`/scenario-lab`) | H | Strong deterministic what-if engine with comparison and persistence hooks. | Add per-leg trace links to `/reconcile` and saved scenario templates for repeat workflows. |
| History (`/history`) | H | Robust trend reconstruction and non-sellable shading. | Add decomposition view (`price`, `quantity`, `dividends`, `FX`) for major value shifts. |
| Security History (`/history/{security_id}`) | H | Good single-name forensic detail across price/cost/liquidity evolution. | Add explicit native-vs-FX contribution breakdown for each selected date window. |
| CGT Report (`/cgt`) | H | Clear realised CGT summary and filing-oriented detail. | Add assumption-quality indicator for `include tax due` path and link to missing setting fields. |
| Economic Gain Report (`/economic-gain`) | M | Clear realised true-cost economics but limited comparative context. | Add side-by-side `CGT basis vs Economic basis` delta column per disposal. |
| Dividends (`/dividends`) | H | Good tax-treatment split, FX provenance fields, and allocation layer. | Separate `actual vs forecast` totals visually and attach FX-source quality badges. |
| Insights (`/insights`) | M | Useful strategic-page hub, currently mostly navigational. | Add maturity/status badges and top active alerts to prioritize where to start. |
| Capital Efficiency (`/capital-efficiency`) | M | Useful snapshot of structural drag components. | Add drag trend chart and previous-window delta for each component. |
| Employment Exit (`/employment-exit`) | M | Strong deterministic snapshot for one exit date/shock pair. | Add matrix view (date x shock) and baseline comparison mode. |
| ISA Efficiency (`/isa-efficiency`) | M | Clear point-in-time wrapper and headroom lens. | Add headroom burn-down timeline and contribution source ledger linkage. |
| Fee Drag (`/fee-drag`) | M | Clear ledger and tax-year totals for fees vs proceeds/economic result. | Add outlier detection and grouping by execution method/source where available. |
| Data Quality (`/data-quality`) | H | Strong diagnostics and impact-by-surface framing. | Add direct remediation links (refresh prices, settings, missing data source). |
| Employment Tax Events (`/employment-tax-events`) | M | Useful event trail but limited traceability back to source records. | Add links to source lot/disposal/audit rows and provenance columns. |
| Reconcile (`/reconcile`) | H | Strong cross-surface explanation and trace sections. | Add snapshot-to-snapshot drift decomposition (`price`, `FX`, `quantity`, `settings`). |
| Basis Timeline (`/basis-timeline`) | M | Good tabular attribution but low visual salience. | Add charted timeline and top-contributor summary cards. |
| Pension (`/pension`) | H | Strong deterministic retirement-planning baseline with append-only inputs, explicit assumptions, and tracked-wealth context. | Add richer timeline visualization and optional assumption-history trace if the surface expands further. |
| Audit Log (`/audit`) | H | Strong append-only transparency with table/record filtering. | Add structured JSON diff highlighter and date-range filters. |
| Add Lot (`/portfolio/add-lot`) | H | Strong input workflow with derived/persisted preview and FX workflow context. | Add explicit `records to be created` preview (including matched lot creation effects). |
| Transfer Lot (`/portfolio/transfer-lot`) | H | Strong transfer impact panel and rule transparency. | Add before/after custody map and tax-estimate confidence badge in confirmation block. |
| Edit Lot (`/portfolio/edit-lot`) | H | Controlled correction flow with confirmation summary. | Add field-level diff against current values and downstream impact hints before save. |
| Add Security (`/portfolio/add-security`) | H | Strong catalog search + manual override + quality guardrails. | Add duplicate/conflict resolver view when symbol-currency collisions are detected. |
| Settings (`/settings`) | H | Core assumption controls are clear and complete for deterministic outputs. | Add assumption-completeness score and list of currently constrained pages. |
| Glossary (`/glossary`) | H | Strong deterministic term clarity and execution-method scope coverage. | Add reverse links from each term to pages using the term most heavily. |
| Login (`/auth/login`) | M | Secure minimal auth surface with explicit one-time-code flow. | Add generic rate-limit guidance text and lockout countdown (without leaking sensitive detail). |
| Locked (`locked.html`) | M | Clear locked-state messaging and recovery endpoint reference. | Add contextual diagnostics (which env/input is missing) and actionable recovery checklist. |

# Deferred Additional Features (Not in Active Scope)

These are capability expansions, not page-polish refinements. Active refinement backlog is closed; keep parked until explicit reprioritization.

## AF2: Event Provenance Everywhere
1. Scope: Standardized row-level provenance badges (`price as of`, `FX as of`, staleness) across Calendar, Risk, and strategic event tables.
2. Why Separate: Introduces a shared provenance framework component.

## AF3: Reconcile Drift Snapshot Engine
1. Scope: Compare current snapshot to prior snapshot and decompose delta by cause (`price`, `FX`, `qty`, `settings`, `transactions`).
2. Why Separate: Adds new temporal comparison capability and persistence requirements.

## AF5: Decision Brief Export Pack
1. Scope: Deterministic export bundle combining selected page metrics, assumptions, and trace links in PDF/JSON.
2. Why Separate: Adds a new reporting/output surface, not a page refinement.

## AF6: Guided Weekly Review Workflow
1. Scope: Cross-page checklist flow (`Portfolio -> Risk -> Calendar -> Reconcile`) with completion state and saved notes.
2. Why Separate: Adds orchestrated workflow behavior spanning multiple pages.

## AF7: Deterministic Notification Digest
1. Scope: Optional digest for threshold breaches, stale-data risks, and upcoming forfeiture/tax events.
2. Why Separate: Adds delivery/notification subsystem.
