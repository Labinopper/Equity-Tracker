# Strategic Question Table

Legend: `Y` = directly addressed, `P` = partially/implicitly addressed, `N` = not addressed.

| Page | Primary Strategic Question | Secondary Questions | Liquidity Clarity | Tax Visibility | Forfeiture Risk | Concentration Risk | ISA Efficiency | True Cost Modelling | FIFO Integrity | FX Exposure |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio (`/`) | What can be sold now, what is blocked, and what is retained after employment tax? | What is lot-level net/gain if sold now vs held to milestone vs long term? | Y | Y | Y | P | P | Y | P | Y |
| Net Value (`/net-value`) | If everything were hypothetically liquidated today, what is gross vs estimated net? | Which rows are locked/at risk and why is headline value not actionable? | Y | Y | Y | P | P | P | N | Y |
| Per Scheme (`/per-scheme`) | How do current and realised outcomes differ by scheme? | Which scheme carries post-tax drag and forfeiture burden? | Y | Y | Y | Y (scheme-level) | P | Y | N | P |
| Simulate Disposal (`/simulate`) | For a proposed sale, what does FIFO allocate and what is net after tax/fees? | What forfeiture and shortfall are triggered before commit? | Y | Y | Y | N | P | Y | Y | N |
| Tax Plan (`/tax-plan`) | What is current-year vs next-year incremental tax drag on realised sales? | How do IT/NI/SL + CGT interact under compensation what-if inputs? | P | Y | N | N | Y | P | N | P |
| Risk (`/risk`) | How concentrated and illiquid is the portfolio right now? | What is sellable vs locked vs at-risk market value? | Y | N | P | Y | P | N | N | N |
| Analytics (`/analytics`) | Which cross-domain risk/tax/liquidity patterns are visible in one surface? | How do concentration, tax-year position, forfeiture, and timeline interact? | Y | Y | Y | Y | P | Y | N | P |
| Calendar (`/calendar`) | Which upcoming vest/forfeiture/tax-year events change decision optionality? | How many days remain and what value is at stake? | P | P | Y | N | N | N | N | N |
| Scenario Lab (`/scenario-lab`) | Across multiple disposal legs, what net/economic/tax outcomes result under price shock? | Where are shortfalls and forfeiture concentrated in a scenario? | Y | Y | Y | P | P | Y | Y | N |
| History (`/history`) | How has portfolio value and gain-if-sold evolved over time using reconstructed holdings? | How much history is priced, and where are drawdowns/activity clusters? | Y | P | P | P | P | Y | N | P |
| Security History (`/history/{security_id}`) | For one security, how do price, true cost, sellable value, and gain-if-sold evolve? | How much of this line item has been non-sellable through time? | Y | P | P | N | N | Y | N | Y |
| CGT Report (`/cgt`) | What are taxable realised gains/losses for a UK tax year? | What is the estimated CGT due under current income context? | N | Y | N | N | Y | N | Y (realised) | N |
| Economic Gain Report (`/economic-gain`) | What was realised economic gain (true-cost basis) by tax year? | How large is the structural gap vs CGT basis outcomes? | N | P | N | N | Y | Y | Y (realised) | N |
| Dividends (`/dividends`) | What dividend cashflow is taxable vs ISA-exempt and what is estimated tax drag? | What is trailing, forecast, and year-by-year net dividend outcome? | P | Y | N | N | Y | N | N | N |
| Audit Log (`/audit`) | What deterministic data mutations occurred and when? | Which lot/transaction edits changed model outputs? | N | P | N | N | N | P | P | N |
| Add Lot (`/portfolio/add-lot`) | How is acquisition data captured so true cost, tax, and restrictions are model-correct? | Which scheme-specific fields are mandatory for deterministic modelling? | N | Y | Y | N | Y | Y | N | Y |
| Transfer Lot (`/portfolio/transfer-lot`) | What non-disposal reclassification is allowed without breaking scheme rules? | What FIFO/forfeiture/employment-tax consequences does transfer trigger? | Y | Y | Y | N | Y | P | Y | Y |
| Edit Lot (`/portfolio/edit-lot`) | How can lot corrections be made without losing auditability? | How do corrected quantity/cost/true-cost values affect downstream reports? | P | Y | N | N | P | Y | P | Y |
| Add Security (`/portfolio/add-security`) | Is instrument identity and currency metadata valid before any economic modelling starts? | Is the security catalog-linked or manual override? | N | N | N | N | N | N | N | Y |
| Settings (`/settings`) | Which global assumptions control tax, privacy, and staleness behaviour? | Are tax defaults and stale-price/FX thresholds aligned to user context? | P | Y | N | N | P | P | N | Y |
| Glossary (`/glossary`) | Are economic/tax/liquidity terms defined with model-specific meanings? | Do users see difference between true cost, basis, and hypothetical liquidity? | Y | Y | Y | P | Y | Y | P | P |
| Login (`/auth/login`) | Is access authenticated before portfolio data is exposed? | Is session entry deterministic and rate-limited? | N | N | N | N | N | N | N | N |
| Locked (`locked.html`) | Is the system state explicit when DB is unavailable? | What operational action is needed to restore model availability? | N | N | N | N | N | N | N | N |

# Page-by-Page Documentation

## Portfolio (`/`)
1. Purpose: Answers what is sellable now, what is blocked, and what post-tax cash and gain are structurally available today.
2. Economic Model Behind the Page: Models market value, true cost, cost basis, sellability state (`SELLABLE`/`LOCKED`/`AT_RISK`), estimated employment tax on sellable rows, forfeiture value, and FX-converted GBP totals. Top-card liquidity is actionable (sellable only), not theoretical. Values mix pre-tax market values and post-tax sell-now estimates explicitly.
3. Illusion vs Reality Risks: `Total Gross Market Value` can still anchor perception; ESPP+ grouped rows can look partially liquid when matched shares are still forfeitable; concentration is visible by security cards but not forced as a headline risk ratio.
4. Decision Support Role: Supports lot-level disposal timing, transfer checks, and structural de-risking triage. Does not provide directional market prediction or trading recommendations.
5. Structural Improvements:
- Core v1: Add a persistent "Actionable vs Theoretical" strip near top cards showing `Sellable Net Liquidity` vs `Gross Market Value` delta and blocked percentage.
- v2 Strategic Upgrade: Add explicit employer concentration badge in top cards using current `RiskService` outputs (top holding %, employer ticker %).

## Net Value (`/net-value`)
1. Purpose: Answers hypothetical retained value if all active lots were liquidated now, including locked and restricted lots.
2. Economic Model Behind the Page: Uses all-lot gross market value, subtracts estimated employment tax (estimated on sellable lots in service logic), and presents hypothetical net. Includes lot-level risk flags for lock/forfeiture/tax-window context. The page is theoretical, not executable.
3. Illusion vs Reality Risks: Largest headline numbers are gross/theoretical and can be read as cash reality. "All lots" wording around employment tax can be interpreted as tax on locked lots even though service estimation is sellable-driven.
4. Decision Support Role: Supports balance-sheet realism and stress framing, not execution planning.
5. Structural Improvements:
- Core v1: Relabel tax card to "Estimated Employment Tax (Sellable Lots in Hypothetical Sell-All)" and show locked-lot tax assumption explicitly as zero-by-non-sellability.
- v2 Strategic Upgrade: Add top-card `Hypothetical - Actionable` gap ratio to quantify liquidity illusion directly.

## Per Scheme (`/per-scheme`)
1. Purpose: Answers which scheme structures are creating or destroying retained value across current and realised history.
2. Economic Model Behind the Page: Current metrics reuse Portfolio sell-now logic (including ESPP+ forfeiture effects). Historic metrics come from committed disposals (`LotDisposal`). Lifetime economic P&L combines realised and current post-tax economic results when computable.
3. Illusion vs Reality Risks: Users may compare current market value and realised proceeds as equivalent "performance" without noticing current rows include hypothetical tax effects and historic rows are factual.
4. Decision Support Role: Supports scheme-level de-risking and policy-level capital allocation. Does not prescribe any disposal strategy.
5. Structural Improvements:
- Core v1: Add explicit "Current = hypothetical if sold now; Historic = realised" header badges for each scheme card.
- v2 Strategic Upgrade: Add scheme-level "forfeiture burden % of scheme value" metric from existing forfeiture totals.

## Simulate Disposal (`/simulate`)
1. Purpose: Answers what a specific proposed disposal would do under FIFO, including tax, broker fees, and forfeiture warnings, before any write.
2. Economic Model Behind the Page: Deterministic FIFO allocation by lot with optional scheme filter, disposal price input, broker fee allocation, and employment-tax estimates (IT/NI/SL) where applicable. Returns both CGT-basis and true-cost economic gain. Actionable preview; commit step writes only after full allocation.
3. Illusion vs Reality Risks: Manual price entry can produce unrealistic certainty if user forgets it differs from latest stored price. Missing settings can hide tax estimate and understate drag.
4. Decision Support Role: Supports execution validation and error prevention (shortfall, forfeiture, partial allocation). Does not optimize for "best trade."
5. Structural Improvements:
- Core v1: Force explicit acknowledgement when tax estimate is unavailable before allowing commit.
- v2 Strategic Upgrade: Add side-by-side "entered price vs latest stored price" delta card to expose input sensitivity.

## Tax Plan (`/tax-plan`)
1. Purpose: Answers current-vs-next UK tax-year incremental drag for disposals, including compensation interactions.
2. Economic Model Behind the Page: Uses realised CGT baseline, projected additional gains/losses from sellable taxable pool, payroll tax components (IT/NI/SL), ANI/PA taper mechanics, and before/after-April comparisons. Primarily pre-disposal tax projection; partially actionable for planning.
3. Illusion vs Reality Risks: Sale-gain assumption uses weighted pool heuristics rather than exact future FIFO path; outputs can look precise despite assumption dependence.
4. Decision Support Role: Supports tax-year sequencing and compensation interaction planning. Does not model market movement or advise timing trades.
5. Structural Improvements:
- Core v1: Add a bold "assumption quality" indicator (`exact` vs `weighted estimate`) for every scenario output.
- v2 Strategic Upgrade: Integrate optional exact-lot scenario input from Scenario Lab legs for tighter CGT projection coherence.

## Risk (`/risk`)
1. Purpose: Answers concentration and liquidity-constraint exposure in one deterministic snapshot.
2. Economic Model Behind the Page: Uses market-value concentration by security/scheme, liquidity split (`sellable/locked/at-risk`), and fixed shock stress points. Values are current-state and priced-lot dependent; actionable for risk framing, not execution.
3. Illusion vs Reality Risks: No direct tax overlay; users may over-read gross concentration without post-tax liquidity context.
4. Decision Support Role: Supports risk awareness and de-risk prioritization. Does not recommend target allocations or trades.
5. Structural Improvements:
- Core v1: Add employer-ticker concentration callout when a single ticker exceeds a threshold.
- v2 Strategic Upgrade: Add concentration-to-sellable ratio (concentration measured on actionable capital, not only gross).

## Analytics (`/analytics`)
1. Purpose: Answers cross-system structural questions by combining concentration, liquidity, tax-year, forfeiture, and event timeline widgets.
2. Economic Model Behind the Page: Aggregates outputs from Portfolio, Risk, Calendar, and tax reports; supports hide-values mode and per-widget visibility controls. Mixed theoretical/actionable widgets are presented in one surface.
3. Illusion vs Reality Risks: Widget grid can mask priority hierarchy; users can focus on trend visuals while missing actionable-liquidity constraints.
4. Decision Support Role: Supports pattern detection and structural monitoring across domains. Does not produce recommendations or forecasts.
5. Structural Improvements:
- Core v1: Add widget-level tags (`Actionable`, `Hypothetical`, `Realised`) to prevent context mixing.
- v2 Strategic Upgrade: Add an always-on "risk stack" strip (concentration + blocked + forfeiture + tax drag) above the widget grid.

## Calendar (`/calendar`)
1. Purpose: Answers when constraints change (vest dates, forfeiture-end dates, tax-year boundary).
2. Economic Model Behind the Page: Event timeline from current lots with countdowns and value-at-stake where priced. Values are future-dated state markers, not sale projections.
3. Illusion vs Reality Risks: Value-at-stake can be interpreted as guaranteed value; stale/missing prices reduce reliability silently unless notes are read.
4. Decision Support Role: Supports timing awareness and operational planning around unlock points.
5. Structural Improvements:
- Core v1: Add explicit stale/missing-value badge per event row near value-at-stake.
- v2 Strategic Upgrade: Add event impact class (`Unlocks Liquidity`, `Ends Forfeiture`, `Tax-Year Boundary`) for faster prioritization.

## Scenario Lab (`/scenario-lab`)
1. Purpose: Answers multi-leg disposal composition under a single shock assumption and compares scenarios.
2. Economic Model Behind the Page: Runs independent FIFO simulations per leg, aggregates net/economic/tax totals, persists scenario snapshots in memory, and compares A vs B outputs. Actionable as planning; non-destructive by design.
3. Illusion vs Reality Risks: Uniform shock can imply model completeness while ignoring path/timing variance and inter-leg sequencing effects.
4. Decision Support Role: Supports structured what-if composition and sensitivity checks. Does not optimize or predict.
5. Structural Improvements:
- Core v1: Add explicit warning that legs run independently and do not mutate each other's FIFO state.
- v2 Strategic Upgrade: Optional "sequential-leg execution" mode to mirror real execution order effects.

## History (`/history`)
1. Purpose: Answers how portfolio value and gain-if-sold evolved using deterministic holdings reconstruction.
2. Economic Model Behind the Page: Reconstructs historical quantities via add-back future disposals; overlays priced coverage, sellable gain, drawdown, and activity. History is explanatory, not executable.
3. Illusion vs Reality Risks: Trend lines can encourage recency bias; gain-if-sold history depends on stored prices and settings availability for historical tax estimate paths.
4. Decision Support Role: Supports state-awareness and validation of long-term behaviour under constraints.
5. Structural Improvements:
- Core v1: Add chart subtitle labels differentiating realised history vs reconstructed hypothetical series.
- v2 Strategic Upgrade: Add decomposition panel showing price effect vs quantity-change effect on major moves.

## Security History (`/history/{security_id}`)
1. Purpose: Answers how one security's price, true cost, and sellable/non-sellable value evolved through time.
2. Economic Model Behind the Page: Uses daily deduped price history with weighted true-cost overlays, lot acquisition events, and sellable gain reconstruction. Mixes native currency display and GBP valuation.
3. Illusion vs Reality Risks: Price-vs-true-cost line can be over-read as a signal rather than a structural accounting overlay.
4. Decision Support Role: Supports security-level forensic review (cost structure, lock burden, temporal gain profile).
5. Structural Improvements:
- Core v1: Add per-series badges (`Market Price`, `True Cost Overlay`, `Sellable-Only Gain`).
- v2 Strategic Upgrade: Add lock-window shading on charts for visually explicit non-sellable periods.

## CGT Report (`/cgt`)
1. Purpose: Answers UK tax-year realised CGT position for taxable disposals.
2. Economic Model Behind the Page: Uses committed disposal records, taxable gains/losses, AEA application, optional CGT due via current income context, and explicit ISA-exempt exclusion from taxable totals.
3. Illusion vs Reality Risks: Users may treat estimated CGT due as filing-ready in all cases despite dependency on settings and simplified assumptions.
4. Decision Support Role: Supports tax reporting and year-position review. Does not replace formal filing workflow.
5. Structural Improvements:
- Core v1: Add "estimate confidence" note when settings are default/zero.
- v2 Strategic Upgrade: Add direct link to lot-level origin rows for each disposal line for audit traceability.

## Economic Gain Report (`/economic-gain`)
1. Purpose: Answers realised performance on true-cost basis, not CGT basis.
2. Economic Model Behind the Page: Uses same disposal set as CGT report but aggregates `realised_gain_economic_gbp`; excludes ISA taxable impact from totals while disclosing exempt amounts.
3. Illusion vs Reality Risks: Without side-by-side delta context, users may not internalize why economic and CGT results differ structurally.
4. Decision Support Role: Supports retained-wealth evaluation of disposal history.
5. Structural Improvements:
- Core v1: Add per-line and total `Economic - CGT` delta column.
- v2 Strategic Upgrade: Add scheme-level realised economic attribution for concentration-aware review.

## Dividends (`/dividends`)
1. Purpose: Answers taxable vs ISA-exempt dividend flow and estimated dividend-tax drag.
2. Economic Model Behind the Page: Manual dividend entries bucketed by UK tax year, dividend allowance application, estimated tax and net flows, and forecast handling for future-dated entries.
3. Illusion vs Reality Risks: Forecast entries can be read as expected outcomes rather than manual assumptions.
4. Decision Support Role: Supports income drag visibility and wrapper efficiency tracking.
5. Structural Improvements:
- Core v1: Add explicit `Actual` vs `Forecast` split totals in summary cards.
- v2 Strategic Upgrade: Add security-level dividend concentration table (taxable flow concentration).

## Audit Log (`/audit`)
1. Purpose: Answers what changed in data inputs that drive all deterministic outputs.
2. Economic Model Behind the Page: No financial modelling; append-only mutation history for lots, securities, transactions, and lot_disposals.
3. Illusion vs Reality Risks: JSON diff detail can be skipped, reducing effective trust/auditability despite availability.
4. Decision Support Role: Supports model governance and correction accountability.
5. Structural Improvements:
- Core v1: Add filter presets for high-impact mutations (`lot quantity`, `true_cost`, `scheme_type`).
- v2 Strategic Upgrade: Add linked jump from audit rows to affected UI pages.

## Add Lot (`/portfolio/add-lot`)
1. Purpose: Answers whether incoming lot data is sufficient and scheme-correct for deterministic downstream modelling.
2. Economic Model Behind the Page: Scheme-aware field requirements, true-cost derivation paths (RSU/ESPP/ESPP_PLUS/BROKERAGE/ISA), currency conversion to GBP with FX metadata retention, and ESPP+ matched-share lock metadata.
3. Illusion vs Reality Risks: Auto-derived values can be accepted without user validating tax-input assumptions.
4. Decision Support Role: Supports input correctness; prevents structural garbage-in for all later pages.
5. Structural Improvements:
- Core v1: Add pre-submit deterministic preview row (`cost basis`, `true cost`, `lock/forfeiture flags`) using exact persisted rules.
- v2 Strategic Upgrade: Add validation warnings when settings are missing but tax-sensitive scheme is selected.

## Transfer Lot (`/portfolio/transfer-lot`)
1. Purpose: Answers whether a non-disposal transfer is allowed under scheme constraints and what structural side effects occur.
2. Economic Model Behind the Page: Enforces scheme constraints (ESPP FIFO/whole-share transfer, RSU post-vest, ESPP+ employee-lot path), preserves pricing metadata, may trigger forfeiture and transfer-time employment-tax event capture.
3. Illusion vs Reality Risks: "Transfer" may feel neutral while it can create forfeiture and tax effects for ESPP+.
4. Decision Support Role: Supports custody reclassification with deterministic safeguards.
5. Structural Improvements:
- Core v1: Add mandatory quantified pre-confirmation impact block (`forfeited qty/value`, `estimated transfer tax`).
- v2 Strategic Upgrade: Add post-transfer reconciliation card linking source and destination lots.

## Edit Lot (`/portfolio/edit-lot`)
1. Purpose: Answers how to correct lot data while maintaining audit integrity.
2. Economic Model Behind the Page: Allows controlled edits to acquisition date, quantity, tax year, cost basis, true cost, FMV, and broker currency where relevant; enforces quantity floor vs disposed amount.
3. Illusion vs Reality Risks: Manual edits to true cost can materially alter reports; impact may not be visible before save.
4. Decision Support Role: Supports deterministic model correction, not planning.
5. Structural Improvements:
- Core v1: Add computed impact preview (`portfolio net liquidity delta`, `CGT delta`) before submit.
- v2 Strategic Upgrade: Add structured "reason code" for correction in audit note.

## Add Security (`/portfolio/add-security`)
1. Purpose: Answers whether instrument identity and currency metadata are valid for pricing and valuation.
2. Economic Model Behind the Page: Captures ticker/name/currency/exchange/ISIN/precision with catalog path or manual override; no tax or gain modelling directly.
3. Illusion vs Reality Risks: Manual override can degrade data quality if incorrect currency/exchange is entered.
4. Decision Support Role: Supports foundation-level data integrity for all value, FX, and concentration outputs.
5. Structural Improvements:
- Core v1: Add duplicate-symbol/currency conflict warning before submit.
- v2 Strategic Upgrade: Add lightweight metadata completeness score (`catalog-linked`, `ISIN present`, `exchange present`).

## Settings (`/settings`)
1. Purpose: Answers which global assumptions drive tax calculations, privacy mode, and staleness risk handling.
2. Economic Model Behind the Page: Stores gross income/pension/other income/student loan plan/default tax year and stale thresholds; these drive employment-tax estimates, tax-plan outputs, and stale-data warnings.
3. Illusion vs Reality Risks: Zero/default income can systematically understate employment tax if user forgets to configure.
4. Decision Support Role: Supports model calibration and data-trust control.
5. Structural Improvements:
- Core v1: Add "calculation health" banner summarizing whether tax-sensitive pages are fully configured.
- v2 Strategic Upgrade: Add per-page dependency matrix showing which fields affect which outputs.

## Glossary (`/glossary`)
1. Purpose: Answers what each core financial term means in this specific deterministic model.
2. Economic Model Behind the Page: Defines true cost vs cost basis vs economic gain, employment tax components, and liquidity terminology used elsewhere.
3. Illusion vs Reality Risks: If not visited, users may still misread terms in high-level cards.
4. Decision Support Role: Supports semantic consistency and user interpretation discipline.
5. Structural Improvements:
- Core v1: Add deep links from each page card label directly to matching glossary term anchor.
- v2 Strategic Upgrade: Add compact "term cards" inline on high-risk pages (Portfolio/Net Value/Tax Plan) using shared glossary source.

## Login (`/auth/login`)
1. Purpose: Answers whether user authentication is satisfied before exposing portfolio data.
2. Economic Model Behind the Page: No financial model; TOTP-based session entry with rate limits and signed session cookies.
3. Illusion vs Reality Risks: None in valuation context.
4. Decision Support Role: Access control only.
5. Structural Improvements:
- Core v1: None required for wealth-modelling clarity.
- v2 Strategic Upgrade: Optional display of current session expiry timer post-login.

## Locked (`locked.html`)
1. Purpose: Answers system state when the data model cannot run because DB is not open.
2. Economic Model Behind the Page: No financial model; operational dependency state only.
3. Illusion vs Reality Risks: None in valuation context.
4. Decision Support Role: Operational continuity only.
5. Structural Improvements:
- Core v1: Add explicit "no calculations available while locked" line.
- v2 Strategic Upgrade: Add safe diagnostics link to `/admin/status`.

# System Alignment Review

## Does any page create liquidity illusion?
- Yes. Portfolio partially mitigates it with `Est. Net Liquidity (Sellable)` and `Blocked/Restricted Value`, but `Total Gross Market Value` remains a strong anchor.
- Yes. Net Value intentionally uses hypothetical sell-all framing, but headline cards still risk being interpreted as actionable cash unless users read tooltips/labels.

## Is ISA clearly structurally differentiated?
- Partially. ISA is correctly excluded from taxable CGT totals and dividend tax lines, and is handled in scheme logic.
- Gap: Portfolio and Risk do not consistently elevate ISA as a strategic wrapper-level capital bucket; it is mostly visible as a scheme label, not as a first-class allocation lens.

## Is employer concentration visible enough?
- Partially. Risk and Analytics show concentration explicitly. Portfolio page still lacks top-level concentration ratios, which is where action decisions start.

## Is employment tax treated transparently?
- Mostly yes. Employment tax components and settings dependency are repeatedly surfaced in Portfolio, Simulate, Tax Plan, and reports.
- Gap: Net Value card wording implies "all lots" tax while service estimation is sellable-driven; this should be clarified.

## Are theoretical vs sellable values clearly separated?
- Partially. Portfolio vs Net Value separation exists and is conceptually correct.
- Gap: Cross-page comparability is still cognitively expensive; users can still compare unlike metrics (actionable sellable vs hypothetical full liquidation) as if equivalent.

## Is forfeiture exposure made emotionally visible?
- Mostly yes in Simulate, Portfolio badges, Calendar, and Analytics forfeiture widget.
- Gap: Portfolio top-level forfeiture burden is folded into `Blocked/Restricted` rather than shown as a distinct, unavoidable loss category.

## Is FX risk visible or implicit?
- Mostly implicit. FX freshness and native/GBP values appear on Portfolio/Net Value and setup flows, but no dedicated "FX contribution/risk" metric exists in Risk or top cards.

# Behavioural Risk Surface

- Large headline gross numbers (`Gross Market Value`, `Total Market Value`) can reinforce wealth illusion and confirmation bias if users ignore sellability/tax overlays.
- Mixed metric types across pages (actionable, hypothetical, realised) can encourage over-confidence through inappropriate comparisons.
- Concentration risk can be masked by aggregation when users stay on Portfolio and do not visit Risk/Analytics concentration widgets.
- Tax drag can be understated when income settings are default/zero; warnings exist but are easy to dismiss.
- Manual price entry in Simulate and uniform-shock assumptions in Scenario Lab can create false precision and overconfidence.
- Forecast dividend entries can create comfort bias if forecast vs actual is not mentally separated.
- Forfeiture risk is visible but not always top-priority in aggregate cards; users can discount it until near event dates.

# Core v1 vs v2 Summary Recommendations

## Core v1 (critical)
1. Standardize metric labels across Portfolio/Net Value to mark `Actionable`, `Hypothetical`, and `Realised` consistently.
2. Fix Net Value tax wording to match implementation (`sellable-lot employment tax estimate`).
3. Add top-card concentration signal on Portfolio (top holding % and employer ticker %).
4. Add explicit forfeiture burden card (`forfeitable value now`) separate from generic blocked value.
5. Add tax-configuration health banner whenever settings materially reduce estimate fidelity.
6. Add exact assumption-quality labels on Tax Plan outputs where weighted gain approximation is used.

## v2 Strategic Upgrade (optional, materially useful)
1. Introduce wrapper-aware allocation strip (`ISA vs non-ISA`, `tax-sheltered vs taxable`) on Portfolio and Risk.
2. Add sequential-leg option in Scenario Lab for execution-order-aware FIFO impact.
3. Add chart-level lock-window shading and actionable tags in History/Security History.
4. Add FX risk widget in Risk/Analytics (`native move vs FX move contribution`).
5. Add audit-linked provenance jump from key numbers to source lot rows and last mutation entries.
