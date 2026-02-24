# TODO - Backlog Inbox

## Scope of This File
- Keep only active backlog and recent release evidence.
- Do not keep full historical release archives here (use git history and `PROJECT_REFERENCE.md`).

## Release Sync Queue (Current)
- [x] Finalize release-note/version sync for ET20-EPIC-04 Calendar stage (`v2.1.0` released, 2026-02-24).
- [x] Finalize release-note/version sync for ET20-EPIC-06 Phase A Broker Currency stage (`v2.1.1` released, 2026-02-24).
- [x] Confirm latest release regression evidence (`487 passed, 3 skipped`) for `v2.1.1`.

## Active Backlog
- [x] CF-06 UI polish debt cleanup:
  - remove remaining inline `style=` usage in templates
  - remove remaining mojibake/encoding artifacts
  - keep responsive/keyboard behavior intact.
- [ ] ET20-EPIC-08 analytics expansion (Groups A+B completion including tax-position charts).
- [ ] ET20-EPIC-01 tax-year realization planner.
- [ ] ET20-EPIC-02 dividend net-return and tax-drag dashboard.
- [ ] ET20-EPIC-07 portfolio/per-scheme QoL package:
  - portfolio quick filters and scenario sorting
  - formula breakdown hover/expand
  - persistent table preferences + focus mode
  - per-scheme visibility toggle.
- [ ] ET20-EPIC-05 scenario lab for multi-lot decisions.
- [ ] ET20-EPIC-08 Group C charts (risk stress + forfeiture-at-risk widgets).
- [ ] ET20-EPIC-08 Group D charts (calendar timeline widgets).
- [ ] ET20-EPIC-06 Phase B data reliability + multi-currency hardening.

## User Additions (Sorted + Mapped)
- [ ] P1 (`ET20-EPIC-02`): Add Dividend workflow (input form/path to create dividend records), so dividend dashboard work has first-class data entry.
- [ ] P2 (`ET20-EPIC-06 Phase B`): Add Currency workflow next to Add Lot (mirrors Add Lot UX, tailored to currency balances/holdings).
- [ ] P3 low priority (reporting QoL, schedule after `ET20-EPIC-01`): Refine CGT view tax-year selection UX.

---

## Recent Completed Evidence

### CF-06 UI Polish Debt Cleanup (`v2.1.2` working tree)
- Baseline: `python -m pytest -q` -> `487 passed, 3 skipped`.
- Targeted: `python -m pytest -q tests/test_api/test_ui_workflows.py` -> `78 passed`.
- Full regression: `python -m pytest -q` -> `487 passed, 3 skipped`.

### ET20-EPIC-06 Phase A Broker Currency Tracking (Released `v2.1.1`)
- Baseline: `python -m pytest -q` -> `480 passed, 3 skipped`.
- Targeted: `python -m pytest -q tests/test_services/test_portfolio_service.py tests/test_api/test_portfolio_api.py tests/test_api/test_ui_workflows.py` -> `171 passed`.
- Full regression: `python -m pytest -q` -> `487 passed, 3 skipped`.

### ET20-EPIC-04 Calendar Timeline (Released `v2.1.0`)
- Baseline: `python -m pytest -q` -> `471 passed, 3 skipped`.
- Targeted: `python -m pytest -q tests/test_services/test_calendar_service.py tests/test_api/test_calendar_api.py` -> `9 passed`.
- Full regression: `python -m pytest -q` -> `480 passed, 3 skipped`.

### ET20-EPIC-08 Phase 1 Analytics Foundation (`v2.0.3`)
- Targeted: `python -m pytest -q tests/test_services/test_analytics_service.py tests/test_api/test_analytics_api.py` -> `9 passed`.
- Full regression: `python -m pytest -q` -> `471 passed, 3 skipped`.
