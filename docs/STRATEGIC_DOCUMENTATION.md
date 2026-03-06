# Strategic Question Table

Legend: `Y` = directly addressed, `P` = partially/implicitly addressed, `N` = not addressed.

| Page | Primary Strategic Question | Secondary Questions | Liquidity Clarity | Tax Visibility | Forfeiture Risk | Concentration Risk | ISA Efficiency | True Cost Modelling | FIFO Integrity | FX Exposure |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio (`/`) | What do I own now, what is sellable now, and what is retained after estimated drag? | How much capital is locked, forfeitable, or employer-concentrated? | Y | Y | Y | Y | Y | Y | P | Y |
| Net Value (`/net-value`) | If everything were hypothetically sold now, what gross vs estimated net remains? | How large is the theoretical-to-actionable gap? | Y | Y | Y | P | P | P | N | Y |
| Capital Stack (`/capital-stack`) | What is true deployable capital after structural deductions? | Which deduction layer is dominating retained-wealth loss? | Y | Y | Y | P | Y | Y | N | P |
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
5. Structural Improvements: Complete `T35` extension (`Economic Gain + Net Dividends` and `Capital at Risk` by scheme).

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
5. Structural Improvements: Add threshold-based concentration guardrail overlays (`T20`).

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
5. Structural Improvements: Implement sequential-leg mode (`T15`).

## History (`/history`) and Security History (`/history/{security_id}`)
1. Purpose: Time-series structural context.
2. Economic Model Behind the Page: Reconstructed holdings, gain-if-sold series, price/cost overlays, and security-specific progression.
3. Illusion vs Reality Risks: Chart narratives can encourage recency bias and hide non-sellable windows.
4. Decision Support Role: Forensic trend/context review, not prediction.
5. Structural Improvements: Add lock-window shading (`T16`) and complete dividend-adjusted history surfaces (`T35`).

## Reports (`/cgt`, `/economic-gain`, `/dividends`)
1. Purpose: Realised tax/economic reporting plus dividend drag/provenance.
2. Economic Model Behind the Page:
- `/cgt`: tax-year realised gains/losses and CGT estimate.
- `/economic-gain`: realised gain on true-cost basis.
- `/dividends`: tax-year summaries, taxable vs ISA-exempt split, native-currency + FX provenance per entry, security-level dividend allocation, capital-at-risk-after-dividends, and economic-gain-plus-net-dividends at security level.
3. Illusion vs Reality Risks: Forecast dividends and estimated tax fields can be interpreted as fixed outcomes.
4. Decision Support Role: Reporting-grade clarity on realised and income-driven drag.
5. Structural Improvements: Add explicit `Actual vs Forecast` summary split and link per-security allocation to Portfolio rows.

## Input and Operational Surfaces (`/portfolio/add-lot`, `/portfolio/transfer-lot`, `/portfolio/edit-lot`, `/portfolio/add-security`, `/settings`, `/audit`, `/glossary`, `/auth/login`, `locked.html`)
1. Purpose: Data quality, deterministic input controls, provenance, and operational trust.
2. Economic Model Behind the Page: These pages govern assumptions and inputs feeding all decision surfaces (not independent valuation engines).
3. Illusion vs Reality Risks: Incomplete settings or poor metadata can silently degrade downstream estimates.
4. Decision Support Role: Model integrity and auditability.
5. Structural Improvements: Add page-level dependency map in Settings showing which assumptions affect which outputs (`T32`).

# System Alignment Review

## Does any page create liquidity illusion?
- Reduced materially by `Capital Stack` and Portfolio split cards.
- Residual risk remains when users anchor on Net Value gross/hypothetical cards.

## Is ISA clearly structurally differentiated?
- Yes at a practical level: wrapper allocation is explicit in Portfolio and Risk, and GBP-only ISA cash transfer controls are enforced in Cash.
- Remaining gap: no dedicated ISA strategy page yet (`T22`).

## Is employer concentration visible enough?
- Improved: Portfolio, Risk, and sell-plan adherence now expose concentration directly.
- Remaining gap: no threshold-alert guardrails yet (`T20`).

## Is employment tax treated transparently?
- Yes: Simulate, Portfolio, Net Value context labels, Tax Plan assumption tags, and explicit missing-estimate acknowledgement are in place.

## Are theoretical vs sellable values clearly separated?
- Mostly yes: actionable/hypothetical tagging and Capital Stack improve clarity.
- Remaining gap: cross-page reconciliation surface is still missing (`T28`).

## Is forfeiture exposure made emotionally visible?
- Yes in Portfolio split buckets, Simulate/Sell Plan constraint engine, and Calendar events.
- Remaining gap: no forfeiture heatmap timing distribution yet (`T27`).

## Is FX risk visible or implicit?
- Improved: analytics FX attribution widget, dividend FX provenance, and cash conversion provenance are live.
- Remaining gap: no dedicated FX basis timeline (`T30`).

# Behavioural Risk Surface

- Confirmation bias via gross-value anchoring: still possible if users skip Capital Stack and focus on gross market value.
- Over-confidence in employer equity: reduced by concentration/employer dependence metrics, but still possible without guardrail alerts.
- Tax underestimation: reduced by assumption-quality tags and acknowledgement gates; still vulnerable when settings are incomplete.
- Risk masking through aggregation: improved via split liquidity/forfeiture cards; still present when users hide critical analytics widgets.
- Comfort from large headline numbers: mitigated, not eliminated; hypothetical and actionable values can still be conflated by inattentive usage.
- False precision risk: persists in manual price inputs and forecast dividend entries.

# Core v1 vs v2 Summary Recommendations

## Core v1
1. Complete `T35` rollout so dividend-adjusted retained-wealth metrics are consistent on Portfolio, Per-Scheme, History, and Security History.
2. Add deterministic cross-page reconciliation (`T28`) to explain why outputs differ across Portfolio, Net Value, Simulate, and Tax Plan.
3. Add concentration guardrail alerts (`T20`) with explicit non-predictive thresholds.

## v2 Strategic Upgrade
1. `T15`: sequential-leg Scenario Lab mode for order-sensitive FIFO realism.
2. `T16`: lock/forfeiture window overlays in history charts.
3. `T17`: traceability links from top cards to lot rows and audit mutations.
4. `T19`: capital-efficiency page decomposing annualized structural drag rate.
5. `T22`: ISA allocation lens with tax-year sheltering headroom.
