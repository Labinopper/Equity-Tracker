# TODO - Backlog Inbox

## Scope of This File
- Keep only active backlog and recent stage evidence.
- Do not keep full historical release archives here (use git history and `PROJECT_REFERENCE.md`).

## Backlog Hygiene Check (2026-02-25)
- Stale outstanding items were reconciled against delivered work.
- Duplicate user additions were mapped into the owning EPICs.

## Active Backlog (Outstanding Only)
- [x] Release-note/version sync for completed working-tree stages (`v2.1.2` through `v2.7.1`).
- [x] **BUG-A01 (v2.8.0)** Analytics page JS syntax error: `});` → `}` on line 1462 of `analytics.html` — breaks all charts and widget settings. One-character fix.
- [x] **v2.8.1** Label clarity: SF→SL (Student Loan), ANI expanded, forfeiture badge wording, Gain If Sold/Economic Gain label alignment, Unrealised prefix, CGT Allowable Cost.
- [x] **v2.8.2** Income-zero warning on Portfolio; FX staleness banner parity on Net Value; Blocked/Restricted Value hint.
- [x] **v2.8.3** Net Value stat rename; Per Scheme unavailable message; Economic Gain intro scope; Settings intro text.
- [x] **v2.8.4** Tax Plan delta direction hints; Simulate employment-tax dash disambiguation; cross-report links (CGT↔Economic Gain, Simulate→Calendar, Portfolio→Tax Plan).
- [x] **v2.8.5** Analytics chart init order (BUG-A02); "Why values differ" note; Glossary page; AEA threshold nudge.
- [x] **R09 router check** `fx_stale_after_minutes` confirmed added to net-value route context (v2.8.2).

## Completed in Working Tree (No Longer Outstanding)
- [x] `ET20-EPIC-09` reporting QoL: CGT/economic-gain tax-year selector with previous/next navigation controls.
- [x] `ET20-EPIC-06 Phase B` data reliability + generalized multi-currency hardening, including Add Lot currency workflow panel and configurable staleness thresholds.
- [x] `ET20-EPIC-08` Groups C+D plus UX follow-on (stress/forfeiture/timeline widgets, denser analytics layout, decision-focus controls).
- [x] `ET20-EPIC-05` Scenario Lab for multi-lot decisions (multi-leg run/retrieve API, scenario compare/export UI, price-shock sensitivity).
- [x] `ET20-EPIC-01B` compensation-aware tax-plan refinement (IT/NI/SL + pension what-if + sell-this-year vs sell-next-year timing deltas).
- [x] `ET20-EPIC-07` portfolio/per-scheme QoL package (quick filters/sort, formula expanders, persistent view prefs, focus mode, per-scheme visibility toggles).
- [x] `ET20-EPIC-08` analytics expansion Groups A+B.
- [x] `ET20-EPIC-01` tax-year realization planner.
- [x] `ET20-EPIC-02` dividend dashboard + dividend workflow data entry.
- [x] `CF-06` UI polish debt cleanup.

## Recent Completed Evidence

### `v2.7.1` ET20-EPIC-09 CGT Tax-Year Selector QoL (working tree)
- Targeted: `python -m pytest -q tests/test_api/test_ui_workflows.py` -> included in combined EPIC-06/09 targeted run, `187 passed`.
- Full regression: `python -m pytest -q` -> `533 passed, 3 skipped`.

### `v2.7.0` ET20-EPIC-06 Phase B Reliability + Multi-Currency (working tree)
- Targeted: `python -m pytest -q tests/test_services/test_fx_service.py tests/test_services/test_price_service.py tests/test_services/test_portfolio_service.py tests/test_api/test_ui_workflows.py` -> `187 passed`.
- Full regression: `python -m pytest -q` -> `533 passed, 3 skipped`.

### `v2.6.3` ET20-EPIC-08 Groups C+D + UX Follow-On (working tree)
- Targeted: `python -m pytest -q tests/test_services/test_analytics_service.py tests/test_api/test_analytics_api.py` -> `14 passed`.
- Full regression: `python -m pytest -q` -> `526 passed, 3 skipped`.

### `v2.6.0` ET20-EPIC-05 Scenario Lab (working tree)
- Targeted: `python -m pytest -q tests/test_services/test_scenario_service.py tests/test_api/test_scenario_api.py` -> `9 passed`.
- Full regression: `python -m pytest -q` -> `524 passed, 3 skipped`.

### `v2.5.1` ET20-EPIC-01B Compensation Timing Refinement (working tree)
- Targeted: `python -m pytest -q tests/test_services/test_tax_plan_service.py` -> `5 passed`.
- Targeted: `python -m pytest -q tests/test_api/test_tax_plan_api.py` -> `5 passed`.
- Full regression: `python -m pytest -q` -> `524 passed, 3 skipped`.

### `v2.5.0` ET20-EPIC-07 Portfolio + Per-Scheme QoL (working tree)
- Targeted: `python -m pytest -q tests/test_api/test_ui_workflows.py` -> `79 passed`.
- Full regression: `python -m pytest -q` -> `524 passed, 3 skipped`.

### `v2.4.1` ET20-EPIC-01B Compensation-Aware Tax Plan
- Targeted: `python -m pytest -q tests/test_services/test_tax_plan_service.py tests/test_api/test_tax_plan_api.py` -> `10 passed`.
- Full regression: `python -m pytest -q` -> `514 passed, 3 skipped`.

### `v2.4.0` ET20-EPIC-02 Dividend Dashboard
- Targeted: `python -m pytest -q tests/test_tax_engine/test_dividend_tax.py tests/test_services/test_dividend_service.py tests/test_api/test_dividends_api.py tests/test_db/test_repositories.py` -> `48 passed`.
- Full regression: `python -m pytest -q` -> `511 passed, 3 skipped`.
