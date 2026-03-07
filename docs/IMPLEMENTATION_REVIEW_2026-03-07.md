# Implementation Review Addendum

Date: `2026-03-07`

Status: advisory addendum aligned to `docs/STRATEGIC_DOCUMENTATION.md` and `docs/todo.md`

Update: the decision-brief export, weekly-review workflow, notification digest, and allocation planner discussed in this addendum are now live.

Purpose: capture the current implementation review, priority refinements, and a practical roadmap without changing the strategic source-of-truth documents.

## Intent Summary

Equity Tracker is intended to be a deterministic equity-compensation and personal-capital decision tool, not a generic portfolio viewer. The app is built to answer four recurring questions with auditability:

1. What do I actually own right now?
2. What portion is sellable, forfeitable, locked, or already deployable?
3. What would remain after tax, fees, and scheme constraints?
4. How do today's choices change future liquidity, concentration, and tax outcomes?

The strategic documentation is consistent about several design principles:

- actionable value must be separated from hypothetical liquidation value;
- scheme-aware rules matter more than headline market value;
- true economic cost is different from tax cost basis;
- retained wealth and deployable capital matter more than gross proceeds;
- tax visibility and audit trails must be preserved across every major view;
- forecasting should stay deterministic and assumption-led, not predictive.

## Current State Assessment

### Strongest Alignment

- The Portfolio, Capital Stack, Risk, Sell Plan, Scenario Lab, Tax Plan, Cash, Dividend, and Pension flows reflect the intended "decision-support first" model.
- The implementation already carries lot-aware and scheme-aware concepts through the stack, including FIFO disposal treatment, wrapper awareness, lock/forfeiture status, tax drag, and FX provenance.
- The app meaningfully distinguishes between gross market value, liquid value, estimated retained value, and longer-horizon pension context.
- Auditability is materially stronger than a typical personal finance tool because inputs, lot states, cash movements, tax events, and scenario outputs are preserved rather than collapsed into a single balance.

### Partial / Underpowered Areas

- `Net Value` naming is now aligned, but the page still under-emphasises the delta versus `Capital Stack`.
- `Insights` currently behaves more like a strategic link hub than a first-class synthesis surface.
- Several screens are information-rich but still visually dense enough to feel like internal analysis consoles rather than concise decision briefs.
- Some approximation-heavy calculations are correct in spirit but not always surfaced with enough emphasis for a financially sensitive tool.

### Main Misalignment Risks

- Users can still anchor on large hypothetical numbers even when the model warnings are technically present.
- Approximate tax outputs risk looking authoritative if their scope and assumptions are not restated near the result itself.
- Valuation provenance and FX completeness remain strategically important; broader strategic hardening is now in place, but valuation-basis trust still depends on the remaining provider and freshness coverage.

## Priority Fixes / Refinements

| Priority | Issue | Why it matters | Recommended change |
|---|---|---|---|
| 1 | `Net Value` still under-emphasises the gap to `Capital Stack` | The app explicitly separates hypothetical liquidation from deployable reality. If that delta is visually weak, users can still anchor on the larger number. | Add a more explicit `Net Value vs Deployable Today` delta treatment and keep the supporting copy close to the headline figure. |
| 2 | Price / FX provenance tests are stale or failing | Valuation basis and FX provenance are trust-critical in this product. Weak test coverage here undermines the core decision model. | Repair test coverage around `yfinance` provider migration, FX completeness signalling, and valuation-basis edge cases before adding more valuation features. |
| 3 | Approximate calculations are not always scoped aggressively enough in the UI | Indicative outputs can be mistaken for lot-exact figures, especially in tax and dividend-adjusted views. | Add stronger inline labelling for weighted / portfolio-level estimates and separate them visually from exact lot-derived outputs. |
| 4 | `Insights` does not yet behave like a synthesis layer | The app has many pages, but the strategic docs imply a clearer "what deserves attention now?" surface. | Convert `Insights` from link-heavy directory behaviour into a prioritised summary of concentration, tax windows, lock expiries, cash constraints, and data-quality warnings. |
| 5 | Large page templates and service modules are becoming maintenance hotspots | Growth will slow as pricing, tax, and scenario rules get more nuanced. | Split the largest templates into partials and break oversized service modules into narrower domain components. |
| 6 | Dense pages still mix primary decisions with secondary diagnostics | "Single pane of glass" should not become "single pane of clutter". | Keep decision-critical summaries visible, but move trace tables, methodology details, and low-frequency controls into collapsible or drill-down sections. |

## Recommended UX / Screen Refinements

### Portfolio

- Keep Portfolio as the "what do I own and what can I act on now?" page.
- Preserve the current action-first framing, but reduce visible secondary detail by default.
- Keep gross value, sellable value, retained estimate, concentration, and nearest lock/forfeiture alerts above the fold.
- Move deeper model notes, basis nuance, and long attribution tables into expandable sections or linked drill-down views.

### Net Value

- Keep this as the hypothetical all-in liquidation lens.
- Avoid wording that sounds like a recommended action.
- Emphasise the delta versus Capital Stack and explain why the number is not deployable cash today.

### Capital Stack

- Keep this as the deployable-capital reality engine.
- Elevate it as the main counterpart to Net Value whenever liquidity, tax, or scheme constraints are discussed.
- Continue showing blocked, locked, and forfeitable deductions clearly before any "after-tax deployable" number.

### Insights

- Reframe as a strategic summary page, not a directory.
- Show 5-7 high-signal cards only: concentration risk, upcoming vest/lock changes, tax-year planning opportunities, dividend receivables, stale or missing FX/price data, and sellability blockers.
- Leave supporting analysis to the underlying dedicated pages.

### History / Analytics / Risk

- Preserve depth, but sharpen default hierarchy.
- Lead each screen with a plain-language summary statement before charts and tables.
- Group historical explanation, methodology, and raw traces beneath the headline narrative.

## Recommended Data / Logic Refinements

- Make exact-versus-estimated status explicit at the metric level, especially for tax-plan and dividend-adjusted outputs.
- Tighten lot-level dividend attribution where portfolio-level dividend offsets are currently used in capital-at-risk or retained-value views.
- Preserve the current separation of `true cost`, `tax cost basis`, and `FMV at acquisition`, but surface those distinctions more consistently near disposal decisions.
- Strengthen valuation-basis completeness checks so missing FX, stale prices, or inferred fallback sources are unmistakable in the UI.
- Keep FIFO as the default realised-gain treatment, but continue documenting HMRC same-day and 30-day limitations anywhere tax estimates are shown.
- Consider first-class modelling for pending receivables where dividends or other cashflows are economically earned but not yet settled, if those values are already influencing decision views.

## Aligned Feature Opportunities

- Decision brief output: export a compact, dated summary of current sellable value, tax drag, concentration, and near-term choices.
- Review workflow: a weekly or month-end review surface that highlights what changed in sellability, deployable capital, concentration, and tax exposure.
- Data quality center: centralise stale prices, missing FX, unmatched transfers, and incomplete metadata into an actionable maintenance queue.
- Employment exit planner: deepen the existing strategic surface with a deterministic checklist of what changes on exit by scheme, lock status, and tax treatment.
- Receivables and timing view: show upcoming dividends, vest-driven cash needs, and tax-event timing in one place.

## Suggested Roadmap

### Quick Wins

- Make the `Net Value` versus `Capital Stack` delta more explicit on-page.
- Strengthen inline "estimate" badges and model-scope messaging where metrics are approximation-heavy.
- Rework `Insights` into a signal-first summary rather than a page directory.
- Fix failing valuation and FX test coverage.

### Near-Term Improvements

- Split oversized templates into reusable partials.
- Refactor pricing, tax-planning, and portfolio aggregation services into smaller domain modules.
- Improve lot-level dividend treatment and expose receivable timing where relevant.
- Add a central data-quality surface tied to missing price, FX, or metadata issues.

### Bigger vNext Features

- Exportable decision briefs with audit links.
- Structured review workflow with historical deltas and prompted decisions.
- Expanded employment-exit planning and deterministic what-if packs.
- Richer capital-efficiency tooling across ISA, taxable, and cash deployment paths.

## Concrete Next Actions

1. Add an explicit `Net Value vs Deployable Today` delta block with supporting copy.
2. Repair failing tests around price-source migration, FX completeness, and valuation-basis signalling.
3. Add inline exact / estimate badges to Portfolio, Capital Stack, Tax Plan, and Scenario outputs.
4. Refactor `Insights` into a prioritised summary of actionable risks, opportunities, and data issues.
5. Promote Capital Stack more clearly as the deployable-capital counterpart to Net Value.
6. Collapse or move secondary trace detail out of the default Portfolio view.
7. Extract the largest UI templates into partials to reduce duplication and page-level complexity.
8. Split oversized service modules, starting with portfolio, pricing, and tax-planning logic.
9. Improve dividend attribution and receivable visibility where retained-value or capital-at-risk outputs rely on portfolio-level offsets.
10. Add a dedicated data-quality workflow for stale prices, missing FX, and incomplete instrument metadata.
