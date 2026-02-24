# v2 Implementation Plan
# equity-tracker → v2.0 Decision Support

**Date:** 2026-02-24
**Based on:** `v2 Strategy.md` (6 EPICs + EPIC-08) + v1 roadmap carry-forward, reviewed against `v1.9.16` codebase
**Constraint:** No changes to existing core functions unless absolutely essential.

**Execution status snapshot (2026-02-24):**
- Completed and release-synced: CF-01 (v1.10.0), CF-02 (v1.10.1), ET20-EPIC-03 (v2.0.0), CF-04 (v2.0.1), CF-05 (v2.0.2), ET20-EPIC-08 Phase 1 (v2.0.3).
- Implemented in working tree, pending release-note/version sync: ET20-EPIC-04 calendar (v2.1.0 target), ET20-EPIC-06 Phase A broker currency (v2.1.1 target).
- Next delivery stage after sync: CF-06 UI debt cleanup (v2.1.2).

---

## Viability Verdict

**The v2 strategy is viable and implementable additively.**

All EPICs can be delivered as new layers (new routes, new services, new templates) on top of the working v1 codebase. The existing FIFO engine, tax engine, pricing, and service contracts are sufficient to support every planned feature. The one area with minimal extension risk is ET20-EPIC-06 (multi-currency), and a safe additive path exists for that too.

---

## IA Navigation Baseline (Approved)

The v2 UX architecture is anchored to a six-tab decision-engine model:
- `Decide`
- `Liquidity`
- `Schemes`
- `Risk`
- `Simulate`
- `Advanced`

Mandatory decision surfaces across primary pages:
- `Net If Sold Today`
- `Gain vs True Cost`
- `Net If Held (Next Milestone)`
- `Net If Long-Term (5+ Years)`

Liquidity contract for all implementation work:
- `Est. Net Liquidity` is sellable-only.
- It sums `Net If Sold Today` for lots that can actually be sold now.
- Locked lots and forfeited/non-realizable matched shares are excluded and surfaced separately as blocked/restricted value.

This IA redesign is navigation/presentation restructuring only and must reuse existing tax/FIFO/scheme engines.

---

## Core Constraint — What "No Core Changes" Means

The following existing code is treated as **read-only** unless an absolute blocker is found during implementation:

- `src/core/tax_engine/*` — all tax calculation logic
- `src/core/lot_engine/fifo.py` — FIFO allocation engine
- `src/services/portfolio_service.py` — simulate/commit/transfer/edit logic
- `src/services/report_service.py` — CGT/economic report computation
- `src/db/models.py` — existing table definitions (additive new models only)
- All existing API routes and their response shapes

New EPICs are wired **around** these, not through them.

---

## Carry-Forward from v1 Roadmap

These items originate from the v1 roadmap and known-issues list. They are included in v2 planning because they are either **blockers for v2 correctness**, **prerequisites for specific EPICs**, or **incomplete features that should not carry into a new major version**.

### Priority 1 — Must resolve before v2.0.0 EPIC delivery begins

These are correctness or data-integrity issues. Shipping v2 EPICs on top of these would compound their impact.

---

#### CF-01 — ESPP+ Dual-Lot Creation Atomicity
**Origin:** v1 roadmap `v1.12.0` / Known Issue #3
**Type:** Data integrity bug
**Status:** Non-atomic — two separate write calls; a mid-operation failure leaves orphaned lots.

**Why it must ship first:**
Any v2 feature that reads ESPP+ lot state (EPIC-01 tax planner, EPIC-03 risk panel, EPIC-04 calendar) depends on consistent ESPP+ data. A partial write creates ghost lots that will silently corrupt v2 calculations.

**Implementation approach (no core changes):**
Wrap the existing two write calls in `portfolio_service.py` in a single SQLAlchemy `session.begin()` block. No logic changes — transactional boundary only.

**Files touched:** `src/services/portfolio_service.py` — transaction wrapper only (not a logic change to any existing function)
**Target:** `v1.10.0` (ship as last v1 MINOR before v2 work begins)

---

#### CF-02 — ESPP+ Transfer-Time Employment Tax Event
**Origin:** Known Issue #7 / todo.md
**Type:** Incomplete feature (calculation gap)
**Status:** Transfer-time employment tax eligibility is recorded as free text in lot notes/audit only — not a structured tax event.

**Why it must ship first:**
ET20-EPIC-01 (Tax-Year Planner) reads tax events to compute remaining allowance and marginal outcomes. If ESPP+ transfer tax events are unstructured, the planner will silently produce incorrect output for anyone who has transferred ESPP+ lots.

**Implementation approach (additive):**
Create a new `EmploymentTaxEvent` model (new table, additive) and populate it from the existing transfer path's note-writing code. The transfer service already computes the values; this structures what it already records. No tax engine function changes.

**Files touched:**
- `src/db/models.py` — additive `EmploymentTaxEvent` table
- `src/db/repository/employment_tax_events.py` — new file
- `src/services/portfolio_service.py` — replace note-write with structured event-write (same data, new destination)

**Target:** `v1.10.1` (PATCH after CF-01)

---

#### CF-03 — SIP-like NIC Gap for 3–5 Year Scenarios (Superseded)
**Status update (2026-02-24):** Superseded by clarified policy: NI is not due after 3 years in the current model. No corrective NIC stage is required.

---

### Priority 2 — Include in v2, not blockers for v2.0.0

These are incomplete or deferred v1 features that belong in v2 but do not block the first EPIC delivery.

---

#### CF-04 — Global `Hide Values` Privacy Mode
**Origin:** v1 roadmap `v1.10.0` / Known Issue #6
**Type:** Incomplete user-visible feature
**Status:** Not implemented.

**Why it belongs in v2:**
New v2 pages (risk panel, tax planner, dividends, scenario lab, analytics dashboard) all display monetary values. Adding them without privacy masking extends the exposure gap further. Should land in v2 before those pages ship.

**Implementation approach (additive, no core changes):**
- Add a boolean `hide_values` key to the Settings model (additive settings field).
- Pass flag via template context from all routers.
- Apply a CSS class and Jinja filter to monetary display cells. Percentages remain visible.
- Chart data endpoints should also respect the flag (return null values when hidden, chart renders placeholder).
- No calculation changes.

**Files touched:** `src/db/models.py` (settings field), `src/services/settings_service.py` (read/write), `src/api/routers/ui.py` (context injection), shared template partial.
**Target:** `v2.0.1` (PATCH after first EPIC ships, or bundle into v2.0.0 delivery)

---

#### CF-05 — Starlette `TemplateResponse` Request-First Migration
**Origin:** v1 roadmap `v1.11.1`
**Type:** Framework deprecation / technical debt
**Status:** Deprecation warnings active across all UI routes. Will eventually become an error.

**Why it belongs in v2:**
Every new v2 EPIC adds templates. Adding new templates on the deprecated call signature multiplies the migration surface. Fix it once before building more.

**Implementation approach:**
Mechanical search-and-replace across `src/api/routers/ui.py` and any other router files: change `TemplateResponse(name, {"request": request, ...})` to `TemplateResponse(request, name, {...})`. No logic changes.

**Files touched:** `src/api/routers/ui.py` (and other routers with `TemplateResponse`)
**Target:** `v2.0.2` (PATCH, bundle into v2 foundation sprint)

---

#### CF-06 — UI Encoding and Inline Style Debt
**Origin:** v1 roadmap `v1.11.0` / Known Issue #1 and #2
**Type:** Technical debt
**Status:** 28 mojibake artifacts, 71 inline `style=` occurrences across templates.

**Why it belongs in v2:**
New v2 pages should use the cleaned-up style system, not inherit the debt. Removing inline styles before adding new templates prevents the debt from growing.

**Implementation approach:**
- Identify and fix the 28 encoding artifacts (character replacement pass, templates only).
- Extract the 71 inline styles to the shared CSS file (no visual changes, class-based replacements).
- Full regression after each pass.

**Files touched:** Template files only. No Python logic changes.
**Target:** `v2.1.2` (after urgent multi-currency tracking phase)

---

### Priority 3 — Portfolio Enhancement EPIC (carry-forward opportunities)

The five follow-on items from `PROJECT_STATUS.md` (Portfolio Page Follow-On Opportunities) and the Per Scheme filter item from `todo.md` are grouped as ET20-EPIC-07 below.

---

## Per-EPIC Assessment

---

### ET20-EPIC-01 — Tax-Year Realization Planner
**Complexity:** M | **Core changes needed:** None
**Prerequisite:** CF-02 (ESPP+ structured tax event) must ship first for ESPP+ accuracy.

**What it does:** Shows remaining CGT annual exempt amount, per-lot projected gain/tax if sold today, and cross-year comparison (sell before vs after April).

**Why it is additive:**
All required inputs already exist: lots (via `portfolio_service`), prices (via `price_service`), tax bands (via `tax_engine/bands.py` and `tax_engine/capital_gains.py`), and settings. The planner is a new read-only computation surface over existing data.

**New files required:**
| File | Purpose |
|---|---|
| `src/services/tax_plan_service.py` | Compute remaining AEA, per-lot CGT projection, cross-year comparison |
| `src/api/routers/tax_plan.py` | `GET /api/tax-plan/summary` JSON endpoint |
| `src/templates/tax_plan.html` | `/tax-plan` UI page |

**Existing files consumed (read-only):**
- `tax_engine/bands.py` → tax year table lookups
- `tax_engine/capital_gains.py` → gain/tax calculations
- `portfolio_service.simulate_disposal()` → per-lot gain previews (already non-destructive)
- `settings` → income, allowance context
- `employment_tax_events` repository → ESPP+ transfer tax events (enabled by CF-02)

**Accepted limitations:**
Must clearly surface "assumptions" panel (income estimate, AEA usage to date) and flag stale price/FX inputs. Does not model HMRC share-matching rules (out of scope per v2 strategy).

---

### ET20-EPIC-02 — Dividend Net-Return and Tax Drag Dashboard
**Complexity:** M | **Core changes needed:** Additive model extension only

**What it does:** Shows trailing 12-month and forecast dividends, estimated dividend tax per tax year, and net yield in GBP by holding type.

**Why it requires a model extension (but no core changes):**
Dividends are not currently stored. A new `DividendEntry` model (additive table, no changes to existing tables) is required. Dividend tax bands (dividend allowance + rates) need to be added — this is additive data in a new `dividend_tax.py` file; the existing functions are unchanged.

**New files required:**
| File | Purpose |
|---|---|
| `src/db/models.py` (additive only) | Add `DividendEntry` table (date, security_id, amount_gbp, source) |
| `src/db/repository/dividends.py` | CRUD for DividendEntry |
| `src/services/dividend_service.py` | Net-return calculations, tax estimation, trailing/forecast views |
| `src/api/routers/dividends.py` | `GET /dividends` UI + `GET /api/dividends/summary` |
| `src/templates/dividends.html` | `/dividends` page |
| `src/core/tax_engine/dividend_tax.py` | Dividend allowance + rate computations (new file; existing files untouched) |

**Accepted limitations:**
Forecasting is manual input v1, labeled as estimated. ISA vs taxable dividend split must be explicit.

---

### ET20-EPIC-03 — Concentration and Liquidity Risk Panel
**Complexity:** S–M | **Core changes needed:** None

**What it does:** Top holding concentration, scheme concentration, locked/sellable/at-risk proportions, simple price-shock stress tests.

**Why it is fully additive:**
All required data (`portfolio_service.get_portfolio_summary()`) is already computed in the portfolio summary response. This epic is a new aggregation and display layer only.

**New files required:**
| File | Purpose |
|---|---|
| `src/services/risk_service.py` | Concentration ratios, sellability splits, stress-test calculations |
| `src/api/routers/risk.py` | `GET /risk` UI + `GET /api/risk/summary` |
| `src/templates/risk.html` | `/risk` page |

**Existing files consumed (read-only):**
- `portfolio_service.get_portfolio_summary()` → all lot/value data
- Price data → stress test base

**Accepted limitations:**
Stress tests labeled as hypothetical. All chart/total values must reconcile to portfolio totals (test requirement).

---

### ET20-EPIC-04 — Vest, Forfeiture, and Tax Calendar with Alerts
**Complexity:** S–M | **Core changes needed:** None

**What it does:** Timeline of upcoming events with value-at-stake, countdowns (forfeiture end, vest dates, tax-year end), optional local alerts.

**Why it is fully additive:**
All event dates are already in lot fields (`vest_date`, acquisition/lock metadata). Event extraction is a new read-only pass over the lot list.

**New files required:**
| File | Purpose |
|---|---|
| `src/services/calendar_service.py` | Extract upcoming events from lots, sort by date, attach value-at-stake |
| `src/api/routers/calendar.py` | `GET /calendar` UI + `GET /api/calendar/events` |
| `src/templates/calendar.html` | `/calendar` page |

**Alerts phase (ship after timeline):**
Opt-in alert configuration stored in settings (additive settings key). Delivery is local browser notification or optional email — no external API dependency for core functionality.

**Accepted limitations:**
Works without any external APIs. Alerts are opt-in and configurable.

---

### ET20-EPIC-05 — Scenario Lab for Multi-Lot Decisions
**Complexity:** M–L | **Core changes needed:** None (existing `/simulate` preserved as-is)
**Prerequisite:** ET20-EPIC-01 (Tax-Year Planner) must ship first for cross-year comparison inputs.

**What it does:** Multi-security/multi-lot scenario builder, side-by-side scenario comparison, sensitivity sliders (price +/-).

**Why it preserves existing code:**
The existing `/simulate` single-lot flow is **not modified**. The Scenario Lab is a new `/scenario-lab` page that calls `portfolio_service.simulate_disposal()` in a loop (once per lot/security in the scenario). No new engine logic is required — the FIFO engine and tax engine already handle each lot correctly. Multi-lot aggregation happens in a new service.

**New files required:**
| File | Purpose |
|---|---|
| `src/services/scenario_service.py` | Assemble multi-lot scenarios, aggregate results, compute comparisons |
| `src/api/routers/scenario_lab.py` | `GET /scenario-lab` UI + `POST /api/scenarios/run` + `GET /api/scenarios/{id}` |
| `src/templates/scenario_lab.html` | `/scenario-lab` page with builder + side-by-side comparison |

**Existing files consumed (read-only):**
- `portfolio_service.simulate_disposal()` → per-lot previews (already non-destructive)
- `report_service` → tax-year context for cross-year comparisons
- ET20-EPIC-01 `tax_plan_service` → cross-year comparison inputs

**Accepted limitations:**
Scenario results must reconcile to per-lot/tax reports. Export (JSON/CSV) required for auditability. Current `/simulate` page is untouched.

---

### ET20-EPIC-06 — Data Reliability and Multi-Currency Foundation
**Complexity:** M | **Core changes needed:** Minimal, contained, and additive
**Note:** Absorbs v1 roadmap item `v1.13.0` (FX generalization). No additional scope added.

**What it does:** Broker currency tracking (USD/GBP) plus explicit staleness thresholds, quota-aware refresh strategy, FX provider option, price fallback with budget display, and broader multi-currency FX path.

**Phased delivery decision (2026-02-24 update):**
- **Phase A (urgent functional scope, target `v2.1.1`)**: broker currency tracking for holdings/cash context where applicable (`USD`/`GBP`), native-currency plus GBP-converted visibility, and FX-basis timestamps in key decision surfaces.
- **Phase B (platform hardening, target `v2.7.0`)**: full data-reliability package (staleness controls, provider abstraction, graceful fallbacks) and generalized multi-currency expansion beyond the initial broker-currency scope.

**Where minimal extension is needed (and why it is safe):**
The existing `sheets_fx_service.py` and `price_service.py` handle USD→GBP only. Multi-currency requires a new `fx_service.py` abstraction that the price service can call. The existing USD→GBP path becomes the default implementation of that abstraction — no existing function logic changes, only a new wrapper/dispatcher is introduced.

Staleness thresholds and freshness indicators are entirely additive (new settings keys + new UI display logic).

**New files required:**
| File | Purpose |
|---|---|
| `src/services/fx_service.py` | Currency-pair-aware FX abstraction; USD→GBP delegates to existing `sheets_fx_service` |
| `src/services/staleness_service.py` | Staleness threshold evaluation, freshness badge logic |
| `src/templates/partials/freshness_badge.html` | Reusable freshness indicator partial |

**Existing files touched (minimal, safe):**
- `src/services/price_service.py` — add a call to `fx_service.get_rate(from_currency, to_currency)` in the valuation path, with `sheets_fx_service` remaining as the USD→GBP provider. This is a **wrapper call insertion only** — no existing logic is altered.

**Accepted limitations:**
Never silently uses stale data without flagging. All fetch failures must degrade gracefully to cached values with warnings. Multi-currency schema design must not break existing USD→GBP security valuations.

---

### ET20-EPIC-07 — Portfolio and Per-Scheme Enhancements
**Complexity:** S–M | **Core changes needed:** None
**Origin:** Carry-forward from `PROJECT_STATUS.md` Portfolio Page Follow-On Opportunities and `todo.md` Per Scheme item.

**What it does:** Quality-of-life improvements to the two most-used pages (Portfolio homepage and Per Scheme) without changing any calculation or service logic.

**Items in scope:**
1. Quick row filters for the portfolio table: `All`, `Warnings`, `Locked`, `Forfeiture Risk` — reduces scanning time on large holdings.
2. Optional sort controls for decision columns: `Net If Sold Today`, `Net If Held`, `Net If Long-Term`, `Signal` — with deterministic default order preserved.
3. Compact hover/expand formula breakdown for decision cells (cash, tax, forfeiture components) — reduces context-switching to Simulate.
4. Persistent table preferences (column visibility/order + filter state) — stored in browser localStorage.
5. One-click "focus mode" for decision columns on smaller laptop widths — reduces horizontal-scroll friction.
6. Per Scheme page: scheme visibility toggle (show/hide individual schemes) as a QoL setting.

**Why it is fully additive:**
All items are view-layer or settings-layer changes. No service, repository, or engine changes required. Filters and sorts operate on data already present in the template context.

**Files touched:**
- `src/templates/portfolio.html` — filter/sort controls, focus-mode toggle, expand formula cells
- `src/templates/per_scheme.html` — scheme visibility checkboxes
- `src/templates/partials/` — new formula-breakdown partial
- localStorage JS (client-side persistence, no schema change)

**Accepted limitations:**
Table preferences persistence is client-side (localStorage) to avoid schema changes. Server-side persistence can follow if user demand warrants it.

---

### ET20-EPIC-08 — Analytics Dashboard with Configurable Graphs
**Complexity:** M | **Core changes needed:** None
**Status:** New EPIC added 2026-02-24

**What it does:** A new `/analytics` page with a configurable, widget-based graph dashboard. Users control which charts are displayed. All charts use existing data — no new data sources. Provides visual decision support that complements the tabular views.

#### Chart Library Decision

**Selected library: [Chart.js](https://www.chartjs.org/) via CDN**

Rationale:
- No build tools required — a single `<script>` CDN tag in the base template
- Lightweight (~60KB) and actively maintained
- Supports all required chart types: line, bar, donut, horizontal bar, stacked bar
- Dark theme theming via global config (`Chart.defaults` overrides) — maps directly to the existing navy/teal/red palette (`--bg`, `--accent`, `--gain`, `--loss`)
- Works natively with existing vanilla JavaScript approach
- CDN version is compatible with the no-build-tool static file setup

The CDN tag goes in `base.html` (or a new `analytics_base.html` partial) and is loaded only on pages that use it, not globally.

#### Chart Catalogue

Each chart is a named widget. The user can toggle each on/off; the setting persists in localStorage. Default-on charts are marked `[DEFAULT]`.

##### Group A — Portfolio Overview (data from `GET /portfolio/summary`)

| Widget ID | Chart Type | Title | What it shows | Default |
|---|---|---|---|---|
| `portfolio-value-time` | Line | Portfolio Value Over Time | Daily total GBP portfolio value from `price_history` × lot quantities | ON |
| `scheme-concentration` | Donut | Value by Scheme | % of total market value per scheme (RSU/ESPP/ESPP+/BROKERAGE/ISA) | ON |
| `security-concentration` | Horizontal Bar | Top Holdings | Top 5–10 securities by % of total market value | ON |
| `liquidity-breakdown` | Stacked Donut | Sellable vs Locked vs At-Risk | Three-segment breakdown by sellability status | ON |
| `unrealised-pnl` | Grouped Bar | Unrealised P&L by Security | Cost basis vs true cost vs current market value per security | ON |

##### Group B — Tax and Returns (data from `GET /reports/cgt`, `GET /reports/economic-gain`)

| Widget ID | Chart Type | Title | What it shows | Default |
|---|---|---|---|---|
| `cgt-year-position` | Bar + line overlay | Tax-Year CGT Position | Annual exempt amount (line), gains realized this tax year (bar), remaining AEA (visual zone) | ON |
| `gain-loss-history` | Stacked Bar | Gain/Loss by Tax Year | Realized gains vs losses per tax year (CGT basis) | OFF |
| `economic-vs-tax` | Grouped Bar | Economic vs Tax P&L | Comparison of economic gain and CGT gain per disposal event/tax year | OFF |

##### Group C — Risk and Stress (data from `GET /api/risk/summary` — EPIC-03 prerequisite)

| Widget ID | Chart Type | Title | What it shows | Default |
|---|---|---|---|---|
| `stress-test` | Bar | Stress Test (Price Shocks) | Net liquidation value at -30%, -20%, -10%, 0%, +10%, +20% price moves | OFF |
| `forfeiture-at-risk` | Donut | Forfeiture-At-Risk Value | ESPP+ matched-share value at-risk if sold before window closes | OFF |

##### Group D — Timeline (data from `GET /api/calendar/events` — EPIC-04 prerequisite)

| Widget ID | Chart Type | Title | What it shows | Default |
|---|---|---|---|---|
| `events-timeline` | Horizontal bar (Gantt-style) | Upcoming Events Timeline | Vest dates, forfeiture windows, tax-year boundaries | OFF |

**Note on Group C and D prerequisites:** Charts in Group C and D are hidden and disabled until their prerequisite EPICs (EPIC-03 and EPIC-04) are shipped. The analytics page renders them as "Coming in a future release" placeholders. This keeps the dashboard forward-compatible without blocking the initial release.

#### Implementation Approach

**Phase 1 — Chart infrastructure (ships with EPIC-08 v1):**
1. Add Chart.js CDN to a new `analytics.html` base partial (not globally to avoid overhead on every page).
2. Create chart theme configuration (JS constant) mapping app CSS variables to Chart.js defaults.
3. Create `/api/analytics/portfolio-over-time` endpoint — aggregates `price_history` × lot quantities to produce daily GBP portfolio value time series.
4. Create `/api/analytics/summary` endpoint — returns all Group A widget data as a single JSON payload (to minimize page-load requests).
5. Create `/analytics` UI page with widget toggle controls and chart canvas containers.

**Phase 2 — Tax/report charts (ships alongside or after EPIC-01):**
6. Create `/api/analytics/tax-position` endpoint — reads from `report_service.cgt_summary()`.

**Phase 3 — Risk/calendar charts (ships after EPIC-03 and EPIC-04):**
7. Enable Group C and D widgets once their data endpoints exist.

#### Readability Principles

- **No chart without a table fallback.** Every chart has a "Show table" toggle below it that renders the same data as a plain accessible table. Users on small screens or with accessibility needs can always get the data in text form.
- **Chart title + subtitle.** Every chart shows a plain-English title and a one-line subtitle explaining what the metric means (e.g., "Scheme Concentration — % of current market value by holding scheme").
- **Freshness indicator.** Every chart that uses price data displays a "Prices as of [date]" label. If data is stale, a warning badge appears.
- **Empty state.** Charts that cannot compute (missing prices, no lots) render an explicit "Insufficient data — [reason]" message rather than an empty or broken chart.
- **Color accessibility.** Chart colors use the existing CSS variable palette and are supplemented with pattern fills (Chart.js plugin) for colorblind users. Gain/loss colors are never the only distinguishing attribute.

#### Widget Persistence
Widget on/off state is stored in `localStorage` under key `analytics.widget_visibility.v1`. No backend changes required.

**New files required:**
| File | Purpose |
|---|---|
| `src/services/analytics_service.py` | Portfolio-over-time aggregation, summary payload, tax-position data assembly |
| `src/api/routers/analytics.py` | `GET /analytics` UI + `GET /api/analytics/summary` + `GET /api/analytics/portfolio-over-time` + `GET /api/analytics/tax-position` |
| `src/templates/analytics.html` | `/analytics` page with widget grid, toggle controls, Chart.js canvas containers |
| `src/templates/partials/chart_theme.html` | Chart.js global theme config (reusable across any future chart page) |

**Existing files touched (minimal):**
- `src/templates/base.html` — add Chart.js CDN `<script>` tag behind a Jinja block (`{% block extra_scripts %}`) so it is only loaded on pages that declare the block. No impact on pages that do not use it.

**Test requirements:**
- Unit tests for `analytics_service.py` covering: empty portfolio, portfolio with no price history, partial price history (some securities missing dates).
- API tests for each `GET /api/analytics/*` endpoint: status 200, correct keys present, no 500 on empty DB.
- UI workflow tests for `/analytics` page: page renders without error, widget toggle controls are present, fallback table toggle is present.
- Full regression must pass before and after this EPIC ships.

---

## Recommended Delivery Sequence

### Pre-v2.0.0 (resolve carry-forward blockers as final v1.x releases)

| Version | Item | Type | Rationale |
|---|---|---|---|
| `v1.10.0` | CF-01 ESPP+ atomicity | Data integrity fix | Must land before any v2 feature reads ESPP+ data |
| `v1.10.1` | CF-02 ESPP+ structured tax event | Incomplete feature | Required for ET20-EPIC-01 accuracy |

### v2.x Delivery

| Version | Item | Rationale |
|---|---|---|
| `v2.0.0` | ET20-EPIC-03 Risk Panel | First v2 EPIC; fully additive, no new data model, lowest risk |
| `v2.0.1` | CF-04 Hide Values mode | Privacy; affects all pages including new v2 pages |
| `v2.0.2` | CF-05 TemplateResponse migration | Technical debt; fix before adding more templates |
| `v2.0.3` | Chart.js infrastructure (EPIC-08 Phase 1) | CDN tag + theme config + analytics service foundation; enables all subsequent chart work |
| `v2.1.0` | ET20-EPIC-04 Calendar | Fully additive, reads existing lot dates |
| `v2.1.1` | ET20-EPIC-06 Phase A Broker Currency Tracking | Urgent functional requirement: track broker USD/GBP currency context with native + GBP visibility |
| `v2.1.2` | CF-06 UI encoding + inline style debt | Polish debt; clean before further page additions |
| `v2.2.0` | ET20-EPIC-08 Analytics Dashboard (Groups A + B widgets) | Portfolio-over-time, scheme/security concentration, liquidity, unrealised P&L, CGT-year charts |
| `v2.3.0` | ET20-EPIC-01 Tax-Year Planner | Depends on CF-02; enables EPIC-08 Group B tax chart data |
| `v2.4.0` | ET20-EPIC-02 Dividends | New DividendEntry model (isolated); adds yield data to analytics |
| `v2.5.0` | ET20-EPIC-07 Portfolio + Per-Scheme Enhancements | QoL; independent, can shift if needed |
| `v2.6.0` | ET20-EPIC-05 Scenario Lab | Depends on ET20-EPIC-01; largest scope |
| `v2.6.1` | ET20-EPIC-08 Group C charts (Risk stress/forfeiture) | Enabled once EPIC-03 risk service is live |
| `v2.6.2` | ET20-EPIC-08 Group D charts (Timeline/calendar) | Enabled once EPIC-04 calendar service is live |
| `v2.7.0` | ET20-EPIC-06 Phase B Data Reliability + Multi-Currency hardening | Platform hardening and generalized expansion after urgent Phase A delivery |

---

## New File Map (Full Summary)

```
src/
  services/
    analytics_service.py         (EPIC-08)
    tax_plan_service.py          (EPIC-01)
    dividend_service.py          (EPIC-02)
    risk_service.py              (EPIC-03)
    calendar_service.py          (EPIC-04)
    scenario_service.py          (EPIC-05)
    fx_service.py                (EPIC-06)
    staleness_service.py         (EPIC-06)
  api/
    routers/
      analytics.py               (EPIC-08)
      tax_plan.py                (EPIC-01)
      dividends.py               (EPIC-02)
      risk.py                    (EPIC-03)
      calendar.py                (EPIC-04)
      scenario_lab.py            (EPIC-05)
  core/
    tax_engine/
      dividend_tax.py            (EPIC-02 — new file, existing files untouched)
  db/
    repository/
      analytics_repository.py    (EPIC-08 — price_history aggregation queries)
      dividends.py               (EPIC-02)
      employment_tax_events.py   (CF-02)
    models.py                    (EPIC-02 additive DividendEntry; CF-02 additive EmploymentTaxEvent)
  templates/
    analytics.html               (EPIC-08)
    tax_plan.html                (EPIC-01)
    dividends.html               (EPIC-02)
    risk.html                    (EPIC-03)
    calendar.html                (EPIC-04)
    scenario_lab.html            (EPIC-05)
    partials/
      chart_theme.html           (EPIC-08 — Chart.js global config, reused by all chart pages)
      freshness_badge.html       (EPIC-06)
      formula_breakdown.html     (EPIC-07)
```

**Existing files that require any change at all:**
| File | Change | Scope |
|---|---|---|
| `src/services/portfolio_service.py` | CF-01: wrap ESPP+ creation in transaction; CF-02: write structured event instead of note | Transaction boundary + destination change only |
| `src/db/models.py` | CF-02: additive `EmploymentTaxEvent`; EPIC-02: additive `DividendEntry` | Append-only, no existing class altered |
| `src/services/price_service.py` | EPIC-06: single call insertion to `fx_service.get_rate()` | One-line wrapper call only |
| `src/templates/base.html` | EPIC-08: add `{% block extra_scripts %}{% endblock %}` + Chart.js CDN in analytics pages only | Block insertion; no existing template content changed |
| `src/templates/portfolio.html` | EPIC-07: filter/sort controls, focus mode, formula expand | View layer only |
| `src/templates/per_scheme.html` | EPIC-07: scheme visibility toggles | View layer only |

All other existing files remain untouched.

---

## Guardrails Carried Forward from v2 Strategy

- All outputs are informational; no auto-trade or hidden automation.
- Every calculation shows assumptions and freshness flags.
- Missing prices, stale FX, and incomplete history must produce explicit confidence signals — never silent blanks.
- Stress tests and forecasts are labeled as hypothetical/estimated.
- All calculation results must be auditable (inputs + outputs reproducible or logged).
- Every chart has a plain-text table fallback.
- Charts that cannot render due to missing data must show an explicit reason, not a broken or empty canvas.

---

## Codex Execution Instructions (Lean)

These instructions reduce logging overhead while preserving reliable pause/resume handoff.

---

### 1. Progress Log (Checkpoint-Based)

Use `CODEX_PROGRESS.md` with this format:
```
[YYYY-MM-DD HH:MM] [VERSION/STREAM] [STATUS] [SCOPE] - [NOTE]
```

Minimum required entries per stage:
1. `STARTED` with stage/EPIC scope.
2. `TEST` baseline (`python -m pytest -q`) result.
3. `CHECKPOINT` only for blockers, major decisions, or scope changes.
4. `TEST` targeted suites (one consolidated entry per run group).
5. `TEST` full regression result.
6. `COMPLETED` with short changed-area summary and commit hash (or `not committed`).
7. `PAUSED` with exact resume pointer when handing off mid-stage.

Default: do not log per-file started/completed entries.

---

### 2. Existing Code Changes

Prefer planned additive files. If an unplanned existing file must be edited, log one `CHECKPOINT` and add a concise entry to `CODEX_QUESTIONS.md` describing why.

---

### 3. Testing Protocol

For each stage:
1. Baseline full regression before implementation.
2. Targeted tests after meaningful implementation batches.
3. Full regression before stage completion.

Do not mark a stage complete with failing tests.

---

### 4. Questions Handling

If uncertain, do not block execution:
1. Record the question in `CODEX_QUESTIONS.md`.
2. Implement the best-guess path.
3. Log a `CHECKPOINT` in `CODEX_PROGRESS.md`.

---

### 5. Stage Completion Checklist

- Required files/routes for the stage are implemented.
- Targeted and full tests are green.
- Any non-standard decisions are captured in `CODEX_QUESTIONS.md`.
- One `COMPLETED` entry exists in `CODEX_PROGRESS.md`.

---

### 6. Stage Sequencing

Work one roadmap stage at a time. Do not start the next stage until the current stage has either `COMPLETED` or an explicit `PAUSED` handoff entry.

---

### 7. Version Bumping

Version bump and release-note confirmation remain user-controlled. Codex should mark version sync as pending/completed in checkpoint logs.

