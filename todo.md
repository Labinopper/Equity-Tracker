# TODO - Backlog Inbox

## Scope of This File
- Keep only active backlog and recent stage evidence.
- Do not keep full historical release archives here (use git history and `PROJECT_REFERENCE.md`).

## Backlog Hygiene Check (2026-02-25)
- Stale outstanding items were reconciled against delivered work.
- Duplicate user additions were mapped into the owning EPICs.

## Active Backlog (Outstanding Only)
- [ ] `ET20-EPIC-05` Scenario Lab for multi-lot decisions.
- [ ] `ET20-EPIC-08 Group C` risk stress + forfeiture-at-risk widgets.
- [ ] `ET20-EPIC-08 Group D` calendar timeline widgets.
- [ ] `ET20-EPIC-08 UX follow-on`: graph density/layout pass (smaller cards, multiple per row).
- [ ] `ET20-EPIC-08 UX follow-on`: usability review of analytics charts against project decision-support goals.
- [ ] `ET20-EPIC-06 Phase B` data reliability + generalized multi-currency hardening.
- [ ] `ET20-EPIC-06 Phase B` currency workflow next to Add Lot (user addition mapped).
- [ ] `ET20-EPIC-09` reporting QoL: refine CGT tax-year selection UX.

## Completed in Working Tree (No Longer Outstanding)
- [x] `ET20-EPIC-01B` compensation-aware tax-plan refinement (IT/NI/SL + pension what-if).
- [x] `ET20-EPIC-07` portfolio/per-scheme QoL package (quick filters/sort, formula expanders, persistent view prefs, focus mode, per-scheme visibility toggles).
- [x] `ET20-EPIC-08` analytics expansion Groups A+B.
- [x] `ET20-EPIC-01` tax-year realization planner.
- [x] `ET20-EPIC-02` dividend dashboard + dividend workflow data entry.
- [x] `CF-06` UI polish debt cleanup.

## Recent Completed Evidence

### `v2.5.0` ET20-EPIC-07 Portfolio + Per-Scheme QoL (working tree)
- Targeted: `python -m pytest -q tests/test_api/test_ui_workflows.py` -> `79 passed`.
- Full regression: `python -m pytest -q` -> `515 passed, 3 skipped`.

### `v2.4.1` ET20-EPIC-01B Compensation-Aware Tax Plan
- Targeted: `python -m pytest -q tests/test_services/test_tax_plan_service.py tests/test_api/test_tax_plan_api.py` -> `10 passed`.
- Full regression: `python -m pytest -q` -> `514 passed, 3 skipped`.

### `v2.4.0` ET20-EPIC-02 Dividend Dashboard
- Targeted: `python -m pytest -q tests/test_tax_engine/test_dividend_tax.py tests/test_services/test_dividend_service.py tests/test_api/test_dividends_api.py tests/test_db/test_repositories.py` -> `48 passed`.
- Full regression: `python -m pytest -q` -> `511 passed, 3 skipped`.
