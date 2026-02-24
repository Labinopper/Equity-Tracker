# TODO - Backlog Inbox

## Active Backlog (Unprocessed)
- [ ] Release governance: for each shipped change, bump SemVer (`MAJOR.MINOR.PATCH`) in `PROJECT_STATUS.md`.
- [ ] Release governance: add a matching changelog entry in `PROJECT_STATUS.md` and technical note in `PROJECT_REFERENCE.md`.
- [ ] Release governance: attach targeted + full test evidence to release notes in `todo.md`.
- [ ] Deliver ET20-EPIC-04 calendar timeline surfaces (`/calendar` + `/api/calendar/events`).
- [ ] Run UI polish debt cleanup pass:
  - remove remaining inline style usage in templates
  - remove remaining mojibake/encoding artifacts
  - keep responsive/keyboard behavior intact.
- [ ] Expand analytics dashboard beyond Phase 1 (Groups A+B completion including tax-position charts).
- [ ] Deliver ET20-EPIC-01 tax-year realization planner.
- [ ] Deliver ET20-EPIC-02 dividend net-return and tax-drag dashboard.
- [ ] Deliver ET20-EPIC-07 portfolio/per-scheme QoL features:
  - portfolio quick filters and scenario sorting
  - formula breakdown hover/expand
  - persistent table preferences + focus mode
  - per-scheme visibility toggle (`Show x scheme`).
- [ ] Deliver ET20-EPIC-05 scenario lab for multi-lot decisions.
- [ ] Enable ET20-EPIC-08 Group C charts (risk stress + forfeiture-at-risk widgets).
- [ ] Enable ET20-EPIC-08 Group D charts (calendar timeline widgets).
- [ ] Generalize FX path beyond USD->GBP.

---

## Processed

### Patch Release `v2.0.3` - ET20-EPIC-08 Phase 1 Analytics Foundation (Completed 2026-02-24)

- [x] Add analytics service/router/template foundation:
  - `/analytics` page shell with widget toggles and table fallbacks
  - `/api/analytics/summary`
  - `/api/analytics/portfolio-over-time`
  - shared Chart.js theme partial and base-template script block support.
- [x] Wire analytics route in app navigation and router registration.
- [x] Add analytics service/API/UI tests.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_analytics_service.py tests/test_api/test_analytics_api.py`
  - Result: `9 passed`.
- [x] Run full regression after EPIC-08 Phase 1.
  - Evidence (full): `python -m pytest -q`
  - Result: `471 passed, 3 skipped`.

### Patch Release `v2.0.2` - CF-05 TemplateResponse Request-First Migration (Completed 2026-02-24)

- [x] Migrate UI/risk `TemplateResponse` call paths to request-first signature.
- [x] Validate UI rendering stability after signature migration.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_api/test_risk_api.py`
  - Result: `79 passed`.
- [x] Run full regression after CF-05 migration.
  - Evidence (full): `python -m pytest -q`
  - Result: `462 passed, 3 skipped`.

### Patch Release `v2.0.1` - CF-04 Global Hide Values Mode (Completed 2026-02-24)

- [x] Add persisted `hide_values` setting across model/schema/API/UI paths.
- [x] Add global privacy indicator and context-aware monetary masking behavior.
- [x] Extend settings and UI workflow test coverage for hide-values mode.
  - Evidence (targeted): settings/risk/ui targeted subsets passed.
- [x] Run full regression after CF-04 delivery.
  - Evidence (full): `python -m pytest -q`
  - Result: `462 passed, 3 skipped`.

### Minor Release `v2.0.0` - ET20-EPIC-03 Risk Panel (Completed 2026-02-24)

- [x] Add risk service aggregation for concentration/liquidity/stress views.
- [x] Add `/risk` UI and `/api/risk/summary` API route + schemas.
- [x] Add risk service/API/UI regression coverage.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_risk_service.py tests/test_api/test_risk_api.py`
  - Result: `6 passed`.
- [x] Run full regression after risk panel delivery.
  - Evidence (full): `python -m pytest -q`
  - Result: `461 passed, 3 skipped`.

### Patch Release `v1.9.17` - Daily Ticker Snapshot Freshness + Market-Closed Countdown (Completed 2026-02-24)

- [x] Add DB infrastructure for per-refresh daily ticker display history:
  - new table `price_ticker_snapshots` (via Alembic revision `004`)
  - repository methods for latest snapshot + current-price run start (last price-change time).
- [x] Persist ticker snapshot rows during price refresh flows (`fetch_all` and `fetch_and_store`) with displayed GBP price and daily direction/percent metadata.
- [x] Add market-aware portfolio daily ticker freshness hints:
  - show warning only when market is open and price has not changed past threshold (`No change ... (market open)`).
  - show closed-session status with opening countdown (`market closed (opening in ...)`) instead of stale warning.
- [x] Add regression coverage for:
  - snapshot run-start logic in repository
  - snapshot writes during `fetch_all`
  - UI behavior for open-session no-change warning and closed-session opening countdown.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py`
  - Result: `120 passed`.
- [x] Run full regression after ticker freshness/staleness update.
  - Evidence (full): `python -m pytest -q`
  - Result: `450 passed, 3 skipped`.

### Patch Release `v1.9.16` - Portfolio Security Collapse Persistence on Refresh (Completed 2026-02-24)

- [x] Persist per-security portfolio lot collapse state (hidden/shown) in browser local storage keyed by security id.
- [x] Ensure `Refresh Prices` and normal page reloads restore each security card to the previously selected open/closed state.
- [x] Add UI contract coverage for security-id wiring + persistence script hooks.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py`
  - Result: `116 passed`.
- [x] Run full regression after persistence fix.
  - Evidence (full): `python -m pytest -q`
  - Result: `446 passed, 3 skipped`.

### Patch Release `v1.9.15` - Portfolio Collapse + Top-Level Proceeds Context (Completed 2026-02-24)

- [x] Remove `Securities` tile from portfolio top stats; retain the remaining seven financial tiles.
- [x] Add per-security collapsible lot sections so rows can be hidden/shown per security.
- [x] Ensure collapsed state keeps a single-line lot composition summary (active lots, position rows, qty, cost basis, true cost, market value/unavailable).
- [x] Expand `Est. Net Proceeds` dropdown content to include top-level relevant fields:
  - `Total Quantity`
  - `Total Cost Basis`
  - `Total True Cost`
  - `Gross Market Value`
  - `P&L (Cost Basis)` / `P&L (Economic)`
  - `Estimated Employment Tax`
  - `Est. Net Proceeds`.
- [x] Add regression coverage for collapsible security card contract + updated proceeds panel fields.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py`
  - Result: `115 passed`.
- [x] Run full regression after portfolio UX update.
  - Evidence (full): `python -m pytest -q`
  - Result: `445 passed, 3 skipped`.

### Patch Release `v1.9.14` - Daily Change Storage + Dashboard Tracker (Completed 2026-02-24)

- [x] Extend price refresh pipeline to backfill missing historical daily closes to earliest lot acquisition date for held securities.
- [x] Persist backfilled rows in existing `price_history` infrastructure (`source=yfinance_history`), while keeping live latest rows sourced from Google Sheets.
- [x] Add prior-close repository lookup support needed for daily move computation.
- [x] Add per-ticker portfolio dashboard tracker showing daily direction, percent move, and quantity-weighted GBP value move (`Up/Down/Flat` with arrows).
- [x] Add regression tests for:
  - repository prior-close/earliest-date helpers
  - history backfill behavior in `PriceService.fetch_all`
  - portfolio daily-change badge rendering.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py`
  - Result: `114 passed`.
- [x] Run full regression after daily-change infrastructure/UI update.
  - Evidence (full): `python -m pytest -q`
  - Result: `444 passed, 3 skipped`.

### Patch Release `v1.9.13` - Portfolio Sellable Liquidity Semantics (Completed 2026-02-24)

- [x] Replace portfolio homepage gross-derived liquidation summary tile with sellable-only metric:
  - label: `Est. Net Liquidity (Sellable)`
  - value: sum of sellable `Net If Sold Today` outcomes only.
- [x] Add companion `Blocked/Restricted Value` tile to surface non-realizable value:
  - locked lot market value
  - forfeiture-restricted matched-share value.
- [x] Update portfolio row aggregations so sell-now gain/liquidity totals exclude locked rows.
- [x] Add regression test coverage for sellable-only liquidity + blocked value behavior.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py`
  - Result: `70 passed`.
- [x] Run full regression after portfolio liquidity semantics update.
  - Evidence (full): `python -m pytest -q`
  - Result: `440 passed, 3 skipped`.

### Patch Release `v1.9.12` - IA/Navigation Contract + Liquidity Semantics (Completed 2026-02-24)

- [x] Document approved six-tab decision-engine IA baseline:
  - `Decide`, `Liquidity`, `Schemes`, `Risk`, `Simulate`, `Advanced`.
- [x] Codify mandatory decision surfaces for primary UX:
  - `Net If Sold Today`
  - `Gain vs True Cost`
  - `Net If Held (Next Milestone)`
  - `Net If Long-Term (5+ Years)`.
- [x] Define liquidity metric rule:
  - `Est. Net Liquidity` sums sellable-only `Net If Sold Today`.
  - locked and forfeited/non-realizable shares are excluded from liquidity totals and shown as blocked/restricted value.
- [x] Sync architecture/contract wording across:
  - `PROJECT_STATUS.md`
  - `PROJECT_REFERENCE.md`
  - `v2_implementation_plan.md`
  - `v2 Strategy.md`.
- [x] Validation:
  - Evidence (full): `python -m pytest -q`
  - Result: `439 passed, 3 skipped`.

### Patch Release `v1.9.11` - Portfolio Divider/Actions Alignment Fix (Completed 2026-02-24)

- [x] Move desktop row-level lot actions (`...`) from `Scheme` cell to end-of-row `Actions` column.
- [x] Normalize lot-menu alignment/positioning in table rows to prevent Scheme-cell divider drift.
- [x] Preserve all existing lot actions (`Edit`, `Transfer`, `History`) and decision-table behavior.
- [x] Validate portfolio/UI workflows after alignment fix.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py`
  - Result: `69 passed`.
- [x] Run full regression after portfolio table alignment update.
  - Evidence (full): `python -m pytest -q`
  - Result: `439 passed, 3 skipped`.

### Patch Release `v1.9.10` - Decision-Focused Portfolio Lot Table v2 (Completed 2026-02-24)

- [x] Restructure portfolio lot table to a decision-first scenario surface:
  - snapshot fields (`Date`, `Scheme`, `Status`, `Qty`, `Market Value`, `True Cost`)
  - sell-now state (`Net If Sold Today`, `Gain If Sold Today`)
  - next structural milestone state (`Net If Held (Next Milestone)`, `Gain If Held`)
  - long-term structural maturity state (`Net If Long-Term (5+ Years)`, `Gain If Long-Term`)
  - concise `Notes` for structural context.
- [x] Keep tax/scheme behavior in existing engine and apply constant-current-price assumption for hold scenarios.
- [x] Preserve detail visibility in expandable per-row detail panels while removing noisy duplicate breakdowns from the main view.
- [x] Add decision-scenario view-model outputs and update portfolio UI tests for new headers/labels.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py`
  - Result: `69 passed`.
- [x] Run full regression after portfolio decision-table v2 restructure.
  - Evidence (full): `python -m pytest -q`
  - Result: `439 passed, 3 skipped`.

### Patch Release `v1.9.2` - Tax Year Band Horizon Extension (Completed 2026-02-24)

- [x] Add published 2026-27 UK IT/NI/Student-Loan values to tax bands.
- [x] Extend supported tax-year window through 2035-36.
- [x] Implement deterministic carry-forward rule for unpublished years (reuse latest published year values).
- [x] Add regression tests for tax-band availability and forward-year carry-forward behavior.
  - Evidence (targeted): `python -m pytest -q tests/test_tax_engine/test_bands.py`
  - Result: `3 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_portfolio_api.py -k "tax_years_no_db_required or cgt_report_invalid_tax_year"`
  - Result: `2 passed`.
- [x] Run full regression after tax-year support update.
  - Evidence (full): `python -m pytest -q`
  - Result: `425 passed, 3 skipped`.

### Patch Release `v1.9.1` - ESPP FIFO Transfer Quantity + Lot Split/Merge (Completed 2026-02-24)

- [x] Extend transfer workflow to accept explicit transfer quantity.
- [x] Enforce ESPP transfer constraints:
  - quantity must be whole shares only
  - selected lot must be FIFO head
  - quantity may span multiple ESPP lots in FIFO order.
- [x] Implement ESPP partial transfer lot behavior:
  - keep source ESPP remainder as independent lot
  - create/update BROKERAGE lot per source lot
  - merge later same-source remainder transfer into existing broker lot.
- [x] Keep RSU/ESPP+ transfer behavior full-lot only and preserve existing forfeiture/tax-note semantics.
- [x] Update API/UI transfer contracts and transfer UX copy/summary for quantity + FIFO behavior.
- [x] Add/adjust transfer tests across service/API/UI.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_portfolio_service.py -k "transfer"`
  - Result: `10 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_portfolio_api.py -k "transfer"`
  - Result: `5 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "transfer"`
  - Result: `6 passed`.
- [x] Run full regression after transfer rule update.
  - Evidence (full): `python -m pytest -q`
  - Result: `422 passed, 3 skipped`.

### Patch Release `v1.8.3` - Portfolio Qty Readability Format (Completed 2026-02-24)

- [x] Render portfolio quantities as fixed 2dp for readability (security summary + desktop table + mobile cards).
- [x] Keep backend quantity precision/calculation behavior unchanged (display-only template change).
- [x] Validate portfolio UI behavior after formatting update.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"`
  - Result: `15 passed`.
- [x] Run full regression after UI formatting update.
  - Evidence (full): `python -m pytest -q`
  - Result: `399 passed, 3 skipped`.

### Patch Release `v1.8.2` - Portfolio Status-Chip Noise Reduction (Completed 2026-02-24)

- [x] Remove `Sellable` badge from portfolio decision table/mobile cards (implicit default state).
- [x] Show `Locked until` only when no `Forfeiture Risk` badge is present on the same row.
- [x] Keep RSU pre-vest lock visibility intact for non-forfeiture rows.
- [x] Update/extend portfolio UI tests for new badge visibility contract.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"`
  - Result: `15 passed`.
- [x] Run full regression after badge-contract update.
  - Evidence (full): `python -m pytest -q`
  - Result: `399 passed, 3 skipped`.

### Patch Release `v1.8.1` - Decision Table Usability Polish (Completed 2026-02-24)

- [x] Fix portfolio header vertical alignment drift.
- [x] Simplify grouped ESPP+ row presentation:
  - scheme label shows `ESPP+` only
  - qty shows total only (no paid/match breakdown in main cell)
  - net cash cell shows summed result only (forfeiture still reflected in calculation).
- [x] Keep decision economics semantics clear:
  - `True Cost` remains net/economic cost
  - `Cost Basis` remains gross/tax basis.
- [x] Move `Signal` column to immediately follow `Scheme`.
- [x] Stack sellability chips for cleaner scanability.
- [x] Re-run portfolio UI and full regression after polish.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"`
  - Result: `15 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py`
  - Result: `56 passed`.
  - Evidence (full): `python -m pytest -q`
  - Result: `399 passed, 3 skipped`.

### Minor Release `v1.8.0` - Portfolio Decision-Table Refactor (Completed 2026-02-24)

- [x] Group ESPP+ paid + matched legs into one decision row per security/acquisition-date event.
- [x] Introduce view-layer `PositionGroupRow` contract (no DB schema changes; no tax engine changes).
- [x] Refactor portfolio table into identity / snapshot / performance / decision zones:
  - renamed decision columns to `Net Cash If Sold` and `Economic Result If Sold`
  - renamed performance labels to `P&L (Tax Basis)` and `P&L (Economic)`
  - moved `Tax Year`, `Acq. Price`, and `True Cost/sh` into expandable detail content.
- [x] Add decision indicator icon column and explicit match-effect handling (`INCLUDED`, `FORFEITED`, `LOCKED`, `NONE`).
- [x] Add sticky desktop table behavior (header + first column) and mobile no-horizontal-scroll card layout.
- [x] Preserve existing tax engine and persistence logic (view/model restructuring only).
- [x] Add/adjust portfolio UI tests for grouping, sell-decision semantics, labels, and formatting paths.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"`
  - Result: `15 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py`
  - Result: `56 passed`.
- [x] Run full regression after decision-table refactor.
  - Evidence (full): `python -m pytest -q`
  - Result: `399 passed, 3 skipped`.

### Patch Release `v1.7.3` - Portfolio Header Actions + Lot Overflow Menu (Completed 2026-02-24)

- [x] Keep portfolio header primary actions (`Refresh Prices`, `+ Security`, `+ Lot`) consistently right-aligned.
- [x] Replace inline lot action links with compact `...` overflow menu (`Edit`, `Transfer`, `History`).
- [x] Validate portfolio/UI workflows after menu structure change.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio or refresh or transfer or edit"`
  - Result: `20 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_portfolio_api.py`
  - Result: `30 passed`.
- [x] Run full regression after dashboard action/menu updates.
  - Evidence (full): `python -m pytest -q`
  - Result: `395 passed, 3 skipped`.

### Patch Release `v1.7.2` - Home Dashboard Tidy-Up (Completed 2026-02-24)

- [x] Refine dashboard header controls and refresh countdown presentation.
- [x] Improve refresh diagnostics readability (pill layout with stable state labels).
- [x] Reduce heavy P&L cell tinting to improve table legibility.
- [x] Clean up lot action link rendering (`Edit | Transfer | History`) for scanability.
- [x] Correct net-panel disclosure marker rendering consistency.
- [x] Validate dashboard/UI behavior via targeted tests.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio or refresh"`
  - Result: `13 passed`.
- [x] Run full regression after UI polish.
  - Evidence (full): `python -m pytest -q`
  - Result: `395 passed, 3 skipped`.

### Patch Release `v1.7.1` - Transfer Rule Hardening (Completed 2026-02-24)

- [x] Enforce scheme-specific transfer constraints and semantics:
  - `RSU` transfer only after vest.
  - `ESPP+` transfer must originate from employee lot.
  - linked in-window `ESPP+` matched lots are forfeited on employee-lot transfer.
  - transfer-time employment tax eligibility is recorded for `ESPP+`.
  - direct transfer into `ISA` is blocked (`dispose -> Add Lot` path required).
- [x] Update transfer UI copy and validation messaging to reflect canonical rules.
- [x] Add targeted tests for transfer guards and forfeiture behavior.
  - Evidence (targeted): `python -m pytest -q tests/test_services/test_portfolio_service.py -k "transfer"`
  - Result: `5 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_portfolio_api.py -k "transfer"`
  - Result: `3 passed`.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py -k "transfer"`
  - Result: `4 passed`.
- [x] Run full regression after transfer workflow hardening.
  - Evidence (full): `python -m pytest -q`
  - Result: `395 passed, 3 skipped`.

### S1-S7 Usability + UI Quality Bar (Completed 2026-02-24)

- [x] S1: Lot-level `Est. Net Proceeds` value-or-reason (no silent blank states).
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py`
  - Result: pass (included in integrated S7 targeted gate).
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

- [x] S2: Reliable 60s refresh + diagnostics/freshness visibility.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py`
  - Result: pass (integrated gate).
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

- [x] S3: Badge semantics/wording correction.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_services/test_phase_e_forfeiture.py`
  - Result: pass (phase-e coverage remains green in full regression).
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

- [x] S4: Lot edit workflow with audit traceability.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_portfolio_api.py tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py`
  - Result: pass (integrated gate).
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

- [x] S5: Transfer workflow (`RSU`/`ESPP`/`ESPP_PLUS` -> `BROKERAGE`) as non-disposal.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_portfolio_api.py tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py`
  - Result: pass (integrated gate).
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

- [x] S6: Decision visibility columns (`Sellability Status`, cash/economic sell-now).
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py`
  - Result: pass (integrated gate).
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

- [x] S7: `ISA` as first-class scheme across UI/model/service/reporting.
  - Evidence (targeted): `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py tests/test_services/test_report_service.py tests/test_api/test_portfolio_api.py`
  - Result: `144 passed`.
  - Evidence (full): `python -m pytest -q`
  - Result: `390 passed, 3 skipped`.

### Previously Completed Foundations

- [x] RSU add-lot vest-date requirement and pre-vest sell lock.
- [x] Simulate MAX uses sellable quantity only.
- [x] ESPP+ matched-share default + override behavior.
- [x] Simulate/commit uses stored true-cost path in UI flow.
- [x] Settings includes DB reset action.
- [x] Versioning/changelog framework added to roadmap docs (`v1.7.0` baseline).
