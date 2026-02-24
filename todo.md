# TODO - Backlog Inbox

## Scope of This File
- Keep only active backlog and recent release evidence.
- Do not keep full historical release archives here (use git history and `PROJECT_REFERENCE.md`).

## Release Sync Queue (Current)
- [ ] Finalize release-note/version sync for ET20-EPIC-04 Calendar stage (`v2.1.0` target).
- [ ] Finalize release-note/version sync for ET20-EPIC-06 Phase A Broker Currency stage (`v2.1.1` target).
- [ ] Run post-merge full regression on a clean tree before tagging next release.

## Active Backlog
- [ ] CF-06 UI polish debt cleanup:
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

---

## Recent Completed Evidence

### ET20-EPIC-06 Phase A Broker Currency Tracking (Target `v2.1.1`)
- Baseline: `python -m pytest -q` -> `480 passed, 3 skipped`.
- Targeted: `python -m pytest -q tests/test_services/test_portfolio_service.py tests/test_api/test_portfolio_api.py tests/test_api/test_ui_workflows.py` -> `171 passed`.
- Full regression: `python -m pytest -q` -> `487 passed, 3 skipped`.

### ET20-EPIC-04 Calendar Timeline (Target `v2.1.0`)
- Baseline: `python -m pytest -q` -> `471 passed, 3 skipped`.
- Targeted: `python -m pytest -q tests/test_services/test_calendar_service.py tests/test_api/test_calendar_api.py` -> `9 passed`.
- Full regression: `python -m pytest -q` -> `480 passed, 3 skipped`.

### ET20-EPIC-08 Phase 1 Analytics Foundation (`v2.0.3`)
- Targeted: `python -m pytest -q tests/test_services/test_analytics_service.py tests/test_api/test_analytics_api.py` -> `9 passed`.
- Full regression: `python -m pytest -q` -> `471 passed, 3 skipped`.
