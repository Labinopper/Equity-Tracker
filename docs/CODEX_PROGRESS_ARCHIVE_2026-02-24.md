[2026-02-24 18:51] [v1.10.0] [STARTED] equity_tracker/tests - Baseline regression before CF-01 implementation - Starting required pre-change test gate
[2026-02-24 18:52] [v1.10.0] [COMPLETED] equity_tracker/tests - Baseline regression before CF-01 implementation - python -m pytest -q => 450 passed, 3 skipped
[2026-02-24 18:54] [v1.10.0] [STARTED] equity_tracker/src/services/portfolio_service.py - CF-01 atomic ESPP+ pair creation - Add transactional wrapper method
[2026-02-24 18:54] [v1.10.0] [STARTED] CODEX_QUESTIONS.md - CF-01 scope clarification - ui.py edit required to invoke atomic service path
[2026-02-24 18:54] [v1.10.0] [COMPLETED] CODEX_QUESTIONS.md - CF-01 scope clarification - Added Q1 and proceeding with best-guess ui.py invocation change
[2026-02-24 18:55] [v1.10.0] [COMPLETED] equity_tracker/src/services/portfolio_service.py - CF-01 atomic ESPP+ pair creation - Added transactional pair method and shared in-session lot writer
[2026-02-24 18:55] [v1.10.0] [STARTED] equity_tracker/src/api/routers/ui.py - CF-01 call-path update - Route ESPP+ add flow through atomic service method
[2026-02-24 18:55] [v1.10.0] [COMPLETED] equity_tracker/src/api/routers/ui.py - CF-01 call-path update - ESPP+ add flow now uses PortfolioService.add_espp_plus_lot_pair()
[2026-02-24 18:55] [v1.10.0] [STARTED] equity_tracker/tests/test_services/test_portfolio_service_espp_plus_atomicity.py - CF-01 regression tests - Add atomic rollback coverage
[2026-02-24 18:56] [v1.10.0] [COMPLETED] equity_tracker/tests/test_services/test_portfolio_service_espp_plus_atomicity.py - CF-01 regression tests - Added success and rollback atomicity coverage
[2026-02-24 18:56] [v1.10.0] [COMPLETED] equity_tracker/tests - CF-01 targeted tests - python -m pytest -q tests/test_services/test_portfolio_service_espp_plus_atomicity.py tests/test_api/test_ui_workflows.py -k 'espp_plus or add_lot' => 33 passed
[2026-02-24 18:57] [v1.10.0] [COMPLETED] equity_tracker/tests - CF-01 full regression - python -m pytest -q => 452 passed, 3 skipped
[2026-02-24 18:57] [v1.10.0] [COMPLETED] CF-01 - ESPP+ Dual-Lot Creation Atomicity - Implemented and validated; version bump/changelog updates pending user review
[2026-02-24 18:57] [v1.10.0] [STARTED] CODEX_QUESTIONS.md - CF-01 scope clarification cleanup - Remove control-character artifact in review path
[2026-02-24 18:58] [v1.10.0] [COMPLETED] CODEX_QUESTIONS.md - CF-01 scope clarification cleanup - Rewrote file as UTF-8 and fixed review-path text
[2026-02-24 18:58] [v1.10.0] [STARTED] CODEX_QUESTIONS.md - CF-01 scope clarification cleanup - Remove remaining control-character from markdown backticks
[2026-02-24 18:58] [v1.10.0] [COMPLETED] CODEX_QUESTIONS.md - CF-01 scope clarification cleanup - Removed remaining control-character and normalized markdown text
[2026-02-24 18:59] [v1.10.1] [STARTED] execution-policy - Standing instruction recorded - Continue implementation without waiting for approval unless blocked
[2026-02-24 18:59] [v1.10.1] [STARTED] equity_tracker/tests - Baseline regression before CF-02 implementation - Starting required pre-change test gate
[2026-02-24 19:00] [v1.10.1] [COMPLETED] equity_tracker/tests - Baseline regression before CF-02 implementation - python -m pytest -q => 452 passed, 3 skipped
[2026-02-24 19:02] [v1.10.1] [STARTED] CODEX_QUESTIONS.md - CF-02 scope clarification - Additional wiring/tests/migration files required
[2026-02-24 19:02] [v1.10.1] [STARTED] equity_tracker/src/db/models.py - CF-02 structured employment tax events - Add EmploymentTaxEvent model
[2026-02-24 19:02] [v1.10.1] [STARTED] equity_tracker/src/db/repository/employment_tax_events.py - CF-02 repository layer - Add EmploymentTaxEventRepository
[2026-02-24 19:02] [v1.10.1] [STARTED] equity_tracker/src/services/portfolio_service.py - CF-02 transfer flow - Persist structured employment tax event
[2026-02-24 19:02] [v1.10.1] [STARTED] equity_tracker/alembic/versions/005_employment_tax_events.py - CF-02 migration - Add employment_tax_events table
[2026-02-24 19:02] [v1.10.1] [COMPLETED] CODEX_QUESTIONS.md - CF-02 scope clarification - Added Q2 and proceeding with best-guess supporting file updates
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/src/db/models.py - CF-02 structured employment tax events - Added EmploymentTaxEvent model
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/src/db/repository/employment_tax_events.py - CF-02 repository layer - Added EmploymentTaxEventRepository
[2026-02-24 19:04] [v1.10.1] [STARTED] equity_tracker/src/db/repository/__init__.py - CF-02 repository wiring - Export EmploymentTaxEventRepository
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/src/db/repository/__init__.py - CF-02 repository wiring - Export updated
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/src/services/portfolio_service.py - CF-02 transfer flow - Structured employment tax event persisted; note-only path removed
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/alembic/versions/005_employment_tax_events.py - CF-02 migration - Added employment_tax_events table and indexes
[2026-02-24 19:04] [v1.10.1] [STARTED] equity_tracker/tests/test_services/test_portfolio_service.py - CF-02 service tests - Validate structured transfer tax events
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/tests/test_services/test_portfolio_service.py - CF-02 service tests - Updated transfer assertions + estimate-available coverage
[2026-02-24 19:04] [v1.10.1] [STARTED] equity_tracker/tests/test_db/test_repositories.py - CF-02 repository tests - Add EmploymentTaxEventRepository coverage
[2026-02-24 19:04] [v1.10.1] [COMPLETED] equity_tracker/tests/test_db/test_repositories.py - CF-02 repository tests - Added add/list coverage
[2026-02-24 19:05] [v1.10.1] [COMPLETED] equity_tracker/tests - CF-02 targeted tests - python -m pytest -q tests/test_db/test_repositories.py -k 'EmploymentTaxEventRepository' => 2 passed
[2026-02-24 19:05] [v1.10.1] [COMPLETED] equity_tracker/tests - CF-02 targeted tests - python -m pytest -q tests/test_services/test_portfolio_service.py::TestTransferLot::test_transfer_espp_plus_employee_forfeits_linked_matched_in_window tests/test_services/test_portfolio_service.py::TestTransferLot::test_transfer_espp_plus_records_structured_tax_event_with_estimate => 2 passed
[2026-02-24 19:05] [v1.10.1] [COMPLETED] equity_tracker/tests - CF-02 full regression - python -m pytest -q => 455 passed, 3 skipped
[2026-02-24 19:05] [v1.10.1] [COMPLETED] CF-02 - ESPP+ transfer-time structured employment tax event - Implemented and validated; version bump/changelog updates pending user review
[2026-02-24 19:06] [v1.11.0] [STARTED] equity_tracker/tests - Baseline regression before CF-03 implementation - Starting required pre-change test gate
[2026-02-24 19:06] [v1.11.0] [COMPLETED] equity_tracker/tests - Baseline regression before CF-03 implementation - python -m pytest -q => 455 passed, 3 skipped
[2026-02-24 19:07] [v1.11.0] [PAUSED] checkpoint - Stable pause requested by user; CF-03 baseline passed, no CF-03 code edits applied yet
[2026-02-24 19:20] [maintenance] [COMPLETED] .gitignore - Git upload readiness - Expanded ignore coverage for secrets/local artifacts
[2026-02-24 19:20] [maintenance] [COMPLETED] .gitattributes - Git upload readiness - Added line-ending normalization rules
[2026-02-24 19:20] [maintenance] [COMPLETED] .git - Git upload readiness - Initialized repository with default branch main
[2026-02-24 19:37] [policy] [COMPLETED] git-commit-granularity - Standing instruction set: commit each implementation step independently (no batched multi-step commits)
[2026-02-24 19:38] [v1.11.0] [STARTED] equity_tracker/src/core/tax_engine/sip_rules.py - CF-03 SIP NIC 3-5y correction - Update NI-liable base for 3-5 year branch
[2026-02-24 19:38] [v1.11.0] [STARTED] equity_tracker/tests/test_tax_engine/test_sip_rules.py - CF-03 tax-engine tests - Align 3-5 year NI expectations
[2026-02-24 19:38] [v1.11.0] [STARTED] equity_tracker/tests/test_services/test_portfolio_service.py - CF-03 service tests - Align 3-5 year employment tax expectations if affected
[2026-02-24 19:39] [policy] [COMPLETED] context-reset - Standing instruction set: after each completed stage, write checkpoint then re-read core source-of-truth files before continuing
[2026-02-24 19:46] [policy] [COMPLETED] NI-treatment-clarification - Confirmed NI is not due after 3 years; reverted in-progress NI-after-3y edits
[2026-02-24 19:46] [v1.11.0] [PAUSED] CF-03 - Prior NI-after-3y implementation path superseded by user clarification; existing no-NI-after-3y behavior retained
[2026-02-24 19:52] [planning] [COMPLETED] equity_tracker/tests - Pre-stage baseline regression - python -m pytest -q => 455 passed, 3 skipped
[2026-02-24 19:52] [planning] [STARTED] PROJECT_STATUS.md - Roadmap reconciliation - Remove superseded SIP NIC 3-5y limitation references per NI clarification
[2026-02-24 19:52] [planning] [STARTED] PROJECT_REFERENCE.md - Technical debt reconciliation - Remove superseded SIP NIC gap/roadmap item
[2026-02-24 19:52] [planning] [STARTED] todo.md - Backlog reconciliation - Remove superseded SIP NIC backlog item
[2026-02-24 19:52] [planning] [STARTED] v2_implementation_plan.md - Stage sequence reconciliation - Mark CF-03 superseded and reorder remaining sequence
[2026-02-24 19:52] [planning] [STARTED] docs/CODEX_SCOPE_AUDIT.md - Historical audit note update - Mark SIP NIC finding superseded by policy clarification
[2026-02-24 19:52] [planning] [STARTED] equity_tracker/src/core/lot_engine/fifo.py - Comment correction - Remove obsolete NIC-known-limitation note
[2026-02-24 19:52] [planning] [STARTED] equity_tracker/src/services/portfolio_service.py - Comment correction - Remove obsolete 3-5yr NIC limitation wording
[2026-02-24 19:54] [planning] [COMPLETED] PROJECT_STATUS.md - Roadmap reconciliation - Removed superseded SIP NIC limitation and roadmap item per NI-after-3y clarification
[2026-02-24 19:54] [planning] [COMPLETED] PROJECT_REFERENCE.md - Technical debt reconciliation - Removed superseded SIP NIC gap and roadmap fix item
[2026-02-24 19:54] [planning] [COMPLETED] todo.md - Backlog reconciliation - Removed superseded SIP NIC backlog item
[2026-02-24 19:54] [planning] [COMPLETED] v2_implementation_plan.md - Stage sequence reconciliation - Marked CF-03 superseded; removed CF-03 delivery/file-map rows
[2026-02-24 19:54] [planning] [COMPLETED] docs/CODEX_SCOPE_AUDIT.md - Historical audit note update - Marked SIP NIC finding superseded by clarified policy
[2026-02-24 19:54] [planning] [COMPLETED] equity_tracker/src/core/lot_engine/fifo.py - Comment correction - Replaced obsolete NIC-known-limitation note with rule-neutral wording
[2026-02-24 19:54] [planning] [COMPLETED] equity_tracker/src/services/portfolio_service.py - Comment correction - Removed obsolete 3-5yr NIC limitation wording
[2026-02-24 19:54] [planning] [COMPLETED] stage - Plan reconciliation stage complete - Commit checkpoint pending
[2026-02-24 19:54] [planning] [COMPLETED] equity_tracker/tests - Post-reconciliation full regression - python -m pytest -q => 455 passed, 3 skipped
[2026-02-24 19:55] [planning] [COMPLETED] git - Stage checkpoint - Committed and pushed NI-reconciliation stage (8d607ce)
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/tests - Baseline regression before ET20-EPIC-03 - python -m pytest -q => 455 passed, 3 skipped
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/src/services/risk_service.py - ET20-EPIC-03 risk service - Create concentration/liquidity/stress aggregation service
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/src/api/schemas/risk.py - ET20-EPIC-03 API contract - Add risk summary response schemas
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/src/api/routers/risk.py - ET20-EPIC-03 router - Add /risk UI and /api/risk/summary endpoint
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/src/api/templates/risk.html - ET20-EPIC-03 UI page - Add risk dashboard template
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/src/api/app.py - ET20-EPIC-03 wiring - Include risk router
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/src/api/templates/base.html - ET20-EPIC-03 navigation - Add Risk topbar link
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/tests/test_services/test_risk_service.py - ET20-EPIC-03 tests - Add service coverage for empty/data/stress cases
[2026-02-24 19:57] [v2.0.0] [STARTED] equity_tracker/tests/test_api/test_risk_api.py - ET20-EPIC-03 tests - Add API/UI coverage for risk routes
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/src/services/risk_service.py - ET20-EPIC-03 risk service - Added concentration/liquidity/stress aggregation outputs
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/src/api/schemas/risk.py - ET20-EPIC-03 API contract - Added risk summary schemas
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/src/api/routers/risk.py - ET20-EPIC-03 router - Added /risk UI and /api/risk/summary endpoint
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/src/api/templates/risk.html - ET20-EPIC-03 UI page - Added risk dashboard template
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/src/api/app.py - ET20-EPIC-03 wiring - Included risk router
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/src/api/templates/base.html - ET20-EPIC-03 navigation - Added Risk topbar link
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/tests/test_services/test_risk_service.py - ET20-EPIC-03 tests - Added service coverage for empty/data/unpriced/stress cases
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/tests/test_api/test_risk_api.py - ET20-EPIC-03 tests - Added API and UI route coverage
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/tests - ET20-EPIC-03 targeted tests - python -m pytest -q tests/test_services/test_risk_service.py tests/test_api/test_risk_api.py => 6 passed
[2026-02-24 20:01] [v2.0.0] [COMPLETED] equity_tracker/tests - ET20-EPIC-03 full regression - python -m pytest -q => 461 passed, 3 skipped
[2026-02-24 20:01] [v2.0.0] [COMPLETED] ET20-EPIC-03 - Concentration and Liquidity Risk Panel - Implemented and validated; version/changelog updates pending
[2026-02-24 20:02] [v2.0.0] [COMPLETED] git - Stage checkpoint - Committed and pushed ET20-EPIC-03 stage (038e543)
[2026-02-24 20:02] [policy] [STARTED] context-reset - Stage boundary re-read - Re-loading source-of-truth docs before next stage
[2026-02-24 20:02] [policy] [COMPLETED] context-reset - Stage boundary re-read - PROJECT_STATUS/v2_implementation_plan/CODEX_PROGRESS refreshed before CF-04
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/_templates.py - CF-04 hide-values core - Added global hide_values context processor and context-aware money masking filter
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/settings.py - CF-04 settings model - Added hide_values persistence field
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/schemas/settings.py - CF-04 API schema - Added hide_values to settings request/response contract
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/routers/settings.py - CF-04 API wiring - Persist hide_values from PUT /api/settings
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/routers/ui.py - CF-04 UI wiring - Persist hide_values from /settings form submit
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/templates/settings.html - CF-04 settings UI - Added Hide monetary values toggle
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/templates/base.html - CF-04 global UI - Added values-hidden body class and active privacy indicator
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/src/api/static/style.css - CF-04 styling - Added topbar privacy badge styling
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/tests/test_api/test_portfolio_api.py - CF-04 API tests - Extended settings tests for hide_values field
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/tests/test_api/test_ui_workflows.py - CF-04 UI tests - Added hide-values masking coverage on portfolio/risk pages
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/tests - CF-04 targeted tests - settings/risk/ui targeted subsets passed
[2026-02-24 20:08] [v2.0.1] [COMPLETED] equity_tracker/tests - CF-04 full regression - python -m pytest -q => 462 passed, 3 skipped
[2026-02-24 20:08] [v2.0.1] [COMPLETED] CF-04 - Global Hide Values mode - Implemented and validated; version/changelog updates pending
[2026-02-24 20:08] [v2.0.1] [COMPLETED] git - Stage checkpoint - Committed and pushed CF-04 stage (8bccf43)
[2026-02-24 20:08] [policy] [STARTED] context-reset - Stage boundary re-read - Re-loading source-of-truth docs before CF-05
[2026-02-24 20:09] [policy] [COMPLETED] context-reset - Stage boundary re-read - CF-05 scope reconfirmed from v2_implementation_plan
[2026-02-24 20:09] [v2.0.2] [STARTED] equity_tracker/src/api/routers/ui.py - CF-05 migration - Convert TemplateResponse calls to request-first signature
[2026-02-24 20:09] [v2.0.2] [STARTED] equity_tracker/src/api/routers/risk.py - CF-05 migration - Convert TemplateResponse calls to request-first signature
[2026-02-24 20:09] [v2.0.2] [STARTED] equity_tracker/tests/test_api/test_ui_workflows.py - CF-05 regression validation - Verify template rendering remains stable after signature migration
[2026-02-24 20:10] [v2.0.2] [COMPLETED] equity_tracker/src/api/routers/ui.py - CF-05 migration - Converted TemplateResponse calls to request-first signature + helper guard
[2026-02-24 20:10] [v2.0.2] [COMPLETED] equity_tracker/src/api/routers/risk.py - CF-05 migration - Converted TemplateResponse calls to request-first signature
[2026-02-24 20:10] [v2.0.2] [COMPLETED] equity_tracker/tests - CF-05 targeted tests - python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_api/test_risk_api.py => 79 passed
[2026-02-24 20:10] [v2.0.2] [COMPLETED] equity_tracker/tests - CF-05 full regression - python -m pytest -q => 462 passed, 3 skipped
[2026-02-24 20:10] [v2.0.2] [COMPLETED] CF-05 - TemplateResponse request-first migration - Implemented and validated; deprecation warnings removed from UI routes
[2026-02-24 20:11] [v2.0.2] [COMPLETED] git - Stage checkpoint - Committed and pushed CF-05 stage (e5adb27)
[2026-02-24 20:11] [policy] [STARTED] context-reset - Stage boundary re-read - Re-loading source-of-truth docs before EPIC-08 Phase 1
[2026-02-24 20:11] [policy] [COMPLETED] context-reset - Stage boundary re-read - EPIC-08 Phase 1 scope refreshed from v2_implementation_plan
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/src/services/analytics_service.py - EPIC-08 phase1 service - Build portfolio-over-time and widget summary payloads
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/src/api/routers/analytics.py - EPIC-08 phase1 router - Add /analytics and /api/analytics/* endpoints
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/src/api/templates/analytics.html - EPIC-08 phase1 UI - Add widget dashboard shell with Chart.js canvases and table fallbacks
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/src/api/templates/partials/chart_theme.html - EPIC-08 phase1 shared UI - Add Chart.js theme configuration partial
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/src/api/templates/base.html - EPIC-08 phase1 base template - Add optional extra_scripts block for page-specific script includes
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/src/api/app.py - EPIC-08 phase1 wiring - Include analytics router
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/tests/test_services/test_analytics_service.py - EPIC-08 tests - Add service coverage for empty/missing/partial history
[2026-02-24 20:11] [v2.0.3] [STARTED] equity_tracker/tests/test_api/test_analytics_api.py - EPIC-08 tests - Add endpoint and analytics page render coverage
[2026-02-24 20:12] [v2.0.3] [COMPLETED] equity_tracker/tests - Baseline regression before EPIC-08 phase1 - python -m pytest -q => 462 passed, 3 skipped
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/tests - Baseline regression before EPIC-08 phase1 resume - python -m pytest -q
[2026-02-24 20:17] [v2.0.3] [COMPLETED] equity_tracker/tests - Baseline regression before EPIC-08 phase1 resume - python -m pytest -q => 462 passed, 3 skipped
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/src/services/analytics_service.py - EPIC-08 phase1 service (resume) - Implement portfolio-over-time aggregation and Group A summary payload
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/src/api/routers/analytics.py - EPIC-08 phase1 router (resume) - Add /analytics, /api/analytics/summary, /api/analytics/portfolio-over-time
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/src/api/templates/analytics.html - EPIC-08 phase1 UI (resume) - Add widget dashboard with toggles and table fallbacks
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/src/api/templates/partials/chart_theme.html - EPIC-08 phase1 chart theme (resume) - Add Chart.js default theme config
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/src/api/templates/base.html - EPIC-08 phase1 base wiring (resume) - Add optional extra_scripts block
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/src/api/app.py - EPIC-08 phase1 routing (resume) - Mount analytics router
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/tests/test_services/test_analytics_service.py - EPIC-08 tests (resume) - Add empty/no-history/partial-history coverage
[2026-02-24 20:17] [v2.0.3] [STARTED] equity_tracker/tests/test_api/test_analytics_api.py - EPIC-08 tests (resume) - Add endpoint and analytics page coverage
[2026-02-24 20:19] [v2.0.3] [COMPLETED] equity_tracker/src/services/analytics_service.py - EPIC-08 phase1 service - Added portfolio-over-time aggregation, Group A summary payload, hide-values suppression, and explicit empty-state notes
[2026-02-24 20:19] [v2.0.3] [COMPLETED] equity_tracker/src/api/routers/analytics.py - EPIC-08 phase1 router - Added /analytics page plus /api/analytics/summary and /api/analytics/portfolio-over-time endpoints
[2026-02-24 20:22] [v2.0.3] [COMPLETED] equity_tracker/src/api/templates/base.html - EPIC-08 phase1 base template - Added Analytics nav link and optional extra_scripts block
[2026-02-24 20:22] [v2.0.3] [COMPLETED] equity_tracker/src/api/app.py - EPIC-08 phase1 wiring - Included analytics router
[2026-02-24 20:22] [v2.0.3] [COMPLETED] equity_tracker/src/api/templates/partials/chart_theme.html - EPIC-08 phase1 chart theme - Added Chart.js default palette mapping from CSS vars
[2026-02-24 20:22] [v2.0.3] [COMPLETED] equity_tracker/src/api/templates/analytics.html - EPIC-08 phase1 UI - Added widget dashboard, table fallbacks, toggle persistence, and chart rendering script
[2026-02-24 20:23] [v2.0.3] [COMPLETED] equity_tracker/tests/test_services/test_analytics_service.py - EPIC-08 service tests - Added empty/no-history/partial-history/hide-values summary coverage
[2026-02-24 20:23] [v2.0.3] [COMPLETED] equity_tracker/tests/test_api/test_analytics_api.py - EPIC-08 API/UI tests - Added analytics endpoint payload checks and /analytics render checks
[2026-02-24 20:24] [v2.0.3] [COMPLETED] equity_tracker/tests - EPIC-08 targeted tests - python -m pytest -q tests/test_services/test_analytics_service.py tests/test_api/test_analytics_api.py => 9 passed
[2026-02-24 20:24] [v2.0.3] [COMPLETED] equity_tracker/tests - EPIC-08 full regression - python -m pytest -q => 471 passed, 3 skipped
[2026-02-24 20:24] [v2.0.3] [COMPLETED] ET20-EPIC-08 Phase 1 - Chart infrastructure foundation - Implemented analytics service/router/UI + Chart.js theme + regression green; version/changelog updates pending
[2026-02-24 20:24] [v2.0.3] [COMPLETED] git - Stage checkpoint - Committed and pushed EPIC-08 Phase 1 stage (6d124b1)
[2026-02-24 20:24] [policy] [STARTED] context-reset - Stage boundary re-read - Re-loading source-of-truth docs before next stage
[2026-02-24 20:24] [policy] [COMPLETED] context-reset - Stage boundary re-read - PROJECT_STATUS/v2_implementation_plan/CODEX_PROGRESS refreshed after EPIC-08 Phase 1
[2026-02-24 21:58] [v2.0.3] [STARTED] roadmap-validation - Pre-commit verification - Validate next roadmap stages are not already silently implemented
[2026-02-24 21:59] [v2.0.3] [COMPLETED] roadmap-validation - Pre-commit verification - Confirmed future stages remain pending (no calendar/tax-plan/dividends/scenario-lab/multi-currency routes/services; analytics remains Phase 1)
[2026-02-24 21:59] [docs-sync] [STARTED] PROJECT_REFERENCE.md - Source-of-truth sync - Update current version/release log/test evidence/open debt/roadmap to v2.0.3 state
[2026-02-24 22:00] [docs-sync] [COMPLETED] PROJECT_REFERENCE.md - Source-of-truth sync - Updated to v2.0.3 with v2 release log entries, refreshed debt, and current roadmap
[2026-02-24 22:00] [docs-sync] [STARTED] todo.md - Backlog sync - Remove completed carry-forward tasks and add v2.0.0-v2.0.3 processed evidence
[2026-02-24 22:00] [docs-sync] [COMPLETED] todo.md - Backlog sync - Active backlog now reflects true pending v2 work; v2.0.0-v2.0.3 processed sections added
[2026-02-24 22:00] [docs-sync] [STARTED] v2_implementation_plan.md - Plan status sync - Mark completed CF stages and add execution snapshot
[2026-02-24 22:00] [docs-sync] [COMPLETED] v2_implementation_plan.md - Plan status sync - Added execution snapshot; CF-01/02/04/05 marked completed; CF-06 counts refreshed
[2026-02-24 22:00] [docs-sync] [STARTED] docs/CODEX_SCOPE_AUDIT.md - Audit sync - Mark resolved findings to match shipped v1.10/v2.0 updates
[2026-02-24 22:00] [docs-sync] [COMPLETED] docs/CODEX_SCOPE_AUDIT.md - Audit sync - TemplateResponse and ESPP+ atomic findings marked resolved; backlog reordered to active items
[2026-02-24 22:08] [v2.1.0] [STARTED] equity_tracker/tests - Baseline regression before ET20-EPIC-04 - python -m pytest -q
[2026-02-24 22:08] [v2.1.0] [COMPLETED] equity_tracker/tests - Baseline regression before ET20-EPIC-04 - python -m pytest -q => 471 passed, 3 skipped
[2026-02-24 22:10] [v2.1.0] [STARTED] equity_tracker/src/services/calendar_service.py - ET20-EPIC-04 calendar service - Build upcoming vest/forfeiture/tax timeline payload and countdowns
[2026-02-24 22:10] [v2.1.0] [COMPLETED] equity_tracker/src/services/calendar_service.py - ET20-EPIC-04 calendar service - Added timeline extraction for RSU vest, ESPP+ forfeiture-end, and UK tax-year markers with countdown/event payloads
[2026-02-24 22:11] [v2.1.0] [STARTED] equity_tracker/src/api/routers/calendar.py - ET20-EPIC-04 router - Add /calendar UI and /api/calendar/events endpoint
[2026-02-24 22:11] [v2.1.0] [COMPLETED] equity_tracker/src/api/routers/calendar.py - ET20-EPIC-04 router - Added /calendar page and /api/calendar/events with configurable horizon validation
[2026-02-24 22:11] [v2.1.0] [STARTED] equity_tracker/src/api/templates/calendar.html - ET20-EPIC-04 UI page - Add timeline table, countdown cards, and horizon controls
[2026-02-24 22:12] [v2.1.0] [COMPLETED] equity_tracker/src/api/templates/calendar.html - ET20-EPIC-04 UI page - Added countdown tiles, horizon selector, event timeline table, and notes section
[2026-02-24 22:12] [v2.1.0] [STARTED] CODEX_QUESTIONS.md - ET20-EPIC-04 scope clarification - calendar route discoverability requires app.py/base.html edits outside strict file table
[2026-02-24 22:12] [v2.1.0] [COMPLETED] CODEX_QUESTIONS.md - ET20-EPIC-04 scope clarification - Added Q5 and proceeding with minimal app.py/base.html routing discoverability updates
[2026-02-24 22:12] [v2.1.0] [STARTED] equity_tracker/src/api/app.py - ET20-EPIC-04 wiring - Include calendar router
[2026-02-24 22:12] [v2.1.0] [COMPLETED] equity_tracker/src/api/app.py - ET20-EPIC-04 wiring - Added calendar router import and include
[2026-02-24 22:12] [v2.1.0] [STARTED] equity_tracker/src/api/templates/base.html - ET20-EPIC-04 navigation - Add Calendar topbar link
[2026-02-24 22:13] [v2.1.0] [COMPLETED] equity_tracker/src/api/templates/base.html - ET20-EPIC-04 navigation - Added Calendar link in topbar
[2026-02-24 22:13] [v2.1.0] [STARTED] equity_tracker/tests/test_services/test_calendar_service.py - ET20-EPIC-04 service tests - Add empty/data/unpriced/validation coverage
[2026-02-24 22:13] [v2.1.0] [COMPLETED] equity_tracker/tests/test_services/test_calendar_service.py - ET20-EPIC-04 service tests - Added empty/data/unpriced payload and horizon validation coverage
[2026-02-24 22:13] [v2.1.0] [STARTED] equity_tracker/tests/test_api/test_calendar_api.py - ET20-EPIC-04 API/UI tests - Add endpoint payload, validation, and page render coverage
[2026-02-24 22:14] [v2.1.0] [COMPLETED] equity_tracker/tests/test_api/test_calendar_api.py - ET20-EPIC-04 API/UI tests - Added endpoint payload, days validation, and /calendar render coverage
[2026-02-24 22:14] [v2.1.0] [STARTED] equity_tracker/tests - ET20-EPIC-04 targeted tests - python -m pytest -q tests/test_services/test_calendar_service.py tests/test_api/test_calendar_api.py
[2026-02-24 22:14] [v2.1.0] [COMPLETED] equity_tracker/tests - ET20-EPIC-04 targeted tests - python -m pytest -q tests/test_services/test_calendar_service.py tests/test_api/test_calendar_api.py => 9 passed
[2026-02-24 22:14] [v2.1.0] [STARTED] equity_tracker/tests - ET20-EPIC-04 full regression - python -m pytest -q
[2026-02-24 22:15] [v2.1.0] [COMPLETED] equity_tracker/tests - ET20-EPIC-04 full regression - python -m pytest -q => 480 passed, 3 skipped
[2026-02-24 22:15] [v2.1.0] [COMPLETED] ET20-EPIC-04 - Vest/Forfeiture/Tax Calendar - Implemented and validated; version/changelog updates pending user review
[2026-02-24 22:17] [roadmap-update] [STARTED] PROJECT_STATUS.md - Urgent roadmap reprioritization - Add broker multi-currency tracking milestone and resequence v2 stages
[2026-02-24 22:17] [roadmap-update] [COMPLETED] PROJECT_STATUS.md - Urgent roadmap reprioritization - Added v2.1.1 ET20-EPIC-06 Phase A broker currency tracking and shifted downstream sequencing
[2026-02-24 22:18] [roadmap-update] [STARTED] v2_implementation_plan.md - Roadmap sync - Insert urgent broker multi-currency phase and resequence delivery table
[2026-02-24 22:19] [roadmap-update] [COMPLETED] v2_implementation_plan.md - Roadmap sync - ET20-EPIC-06 split into urgent Phase A (v2.1.1) and Phase B (v2.7.0); CF-06 shifted to v2.1.2
[2026-02-24 22:19] [roadmap-update] [COMPLETED] docs-only-validation - No code-path changes - Tests not run (documentation-only roadmap reprioritization)
[2026-02-24 22:20] [roadmap-update] [COMPLETED] v2_implementation_plan.md - Roadmap sync finalized - Kept diff minimal after temporary encoding rollback; sequence now reflects urgent broker currency tracking
[2026-02-24 22:22] [v2.1.1] [STARTED] equity_tracker/tests - Baseline regression before ET20-EPIC-06 Phase A - python -m pytest -q
[2026-02-24 22:22] [v2.1.1] [COMPLETED] equity_tracker/tests - Baseline regression before ET20-EPIC-06 Phase A - python -m pytest -q => 480 passed, 3 skipped
[2026-02-24 22:26] [v2.1.1] [STARTED] CODEX_QUESTIONS.md - ET20-EPIC-06 Phase A scope clarification - Additional existing-file edits required for broker-currency tracking/UI/API contract coverage
[2026-02-24 22:26] [v2.1.1] [COMPLETED] CODEX_QUESTIONS.md - ET20-EPIC-06 Phase A scope clarification - Added Q6 and proceeding with additive broker-currency implementation across required existing files
[2026-02-24 22:27] [v2.1.1] [STARTED] equity_tracker/src/db/models.py - ET20-EPIC-06 Phase A model extension - Add lots.broker_currency field for broker holding currency tracking
[2026-02-24 22:27] [v2.1.1] [COMPLETED] equity_tracker/src/db/models.py - ET20-EPIC-06 Phase A model extension - Added additive lots.broker_currency field
[2026-02-24 22:27] [v2.1.1] [STARTED] equity_tracker/alembic/versions/006_broker_currency_tracking.py - ET20-EPIC-06 Phase A migration - Add additive lots.broker_currency column for existing DBs
[2026-02-24 22:27] [v2.1.1] [COMPLETED] equity_tracker/alembic/versions/006_broker_currency_tracking.py - ET20-EPIC-06 Phase A migration - Added revision 006 to create lots.broker_currency column (idempotent)
[2026-02-24 22:28] [v2.1.1] [STARTED] equity_tracker/src/services/portfolio_service.py - ET20-EPIC-06 Phase A service/core wiring - Add broker_currency lifecycle + native/GBP summary fields + FX basis context
[2026-02-24 22:30] [v2.1.1] [STARTED] equity_tracker/src/api/schemas/portfolio.py - ET20-EPIC-06 Phase A schema updates - Add broker_currency request/response fields and native+GBP summary fields
[2026-02-24 22:31] [v2.1.1] [STARTED] equity_tracker/src/api/routers/portfolio.py - ET20-EPIC-06 Phase A API wiring - Thread broker_currency through add/edit/transfer service calls
[2026-02-24 22:31] [v2.1.1] [COMPLETED] equity_tracker/src/api/schemas/portfolio.py - ET20-EPIC-06 Phase A schema updates - Added broker_currency request/response coverage and native+GBP summary fields
[2026-02-24 22:31] [v2.1.1] [COMPLETED] equity_tracker/src/api/routers/portfolio.py - ET20-EPIC-06 Phase A API wiring - add/edit/transfer now pass broker_currency into PortfolioService
[2026-02-24 22:31] [v2.1.1] [STARTED] equity_tracker/src/api/routers/ui.py - ET20-EPIC-06 Phase A UI workflow wiring - Add/edit/transfer broker_currency capture and transfer candidate defaults
[2026-02-24 22:32] [v2.1.1] [STARTED] equity_tracker/src/api/templates/add_lot.html - ET20-EPIC-06 Phase A UI text updates - Clarify broker holding currency behavior in add-lot workflow
[2026-02-24 22:32] [v2.1.1] [COMPLETED] equity_tracker/src/api/templates/add_lot.html - ET20-EPIC-06 Phase A UI text updates - Added broker-holding-currency guidance for BROKERAGE/ISA input currency
[2026-02-24 22:32] [v2.1.1] [STARTED] equity_tracker/src/api/templates/edit_lot.html - ET20-EPIC-06 Phase A edit workflow - Add broker_currency control and confirmation summary row
[2026-02-24 22:33] [v2.1.1] [COMPLETED] equity_tracker/src/api/templates/edit_lot.html - ET20-EPIC-06 Phase A edit workflow - Added BROKERAGE/ISA broker_currency selector and summary confirmation display
[2026-02-24 22:33] [v2.1.1] [STARTED] equity_tracker/src/api/templates/transfer_lot.html - ET20-EPIC-06 Phase A transfer workflow - Add destination broker currency selector and summary binding
[2026-02-24 22:33] [v2.1.1] [COMPLETED] equity_tracker/src/api/templates/transfer_lot.html - ET20-EPIC-06 Phase A transfer workflow - Added destination broker_currency selector and summary/JS synchronization
[2026-02-24 22:34] [v2.1.1] [STARTED] equity_tracker/src/api/templates/portfolio.html - ET20-EPIC-06 Phase A portfolio UI - Surface native+GBP values and explicit valuation/FX basis text
[2026-02-24 22:34] [v2.1.1] [COMPLETED] equity_tracker/src/api/templates/portfolio.html - ET20-EPIC-06 Phase A portfolio UI - Added valuation basis text and native+GBP security/market-value displays
[2026-02-24 22:35] [v2.1.1] [STARTED] equity_tracker/src/api/templates/net_value.html - ET20-EPIC-06 Phase A net-value UI - Add valuation basis callouts and native+GBP value presentation
[2026-02-24 22:35] [v2.1.1] [COMPLETED] equity_tracker/src/api/templates/net_value.html - ET20-EPIC-06 Phase A net-value UI - Added FX/valuation basis callout and native+GBP market value displays
[2026-02-24 22:35] [v2.1.1] [COMPLETED] equity_tracker/src/api/routers/ui.py - ET20-EPIC-06 Phase A UI workflow wiring - Added broker_currency helper/defaulting and threaded add/edit/transfer handlers to service
[2026-02-24 22:35] [v2.1.1] [COMPLETED] equity_tracker/src/services/portfolio_service.py - ET20-EPIC-06 Phase A service/core wiring - Implemented broker_currency lifecycle, native+GBP summary fields, and portfolio FX basis metadata
[2026-02-24 22:36] [v2.1.1] [STARTED] equity_tracker/tests/test_api/test_ui_workflows.py - ET20-EPIC-06 Phase A UI tests - Add broker_currency persistence and transfer/edit workflow assertions
[2026-02-24 22:37] [v2.1.1] [STARTED] equity_tracker/tests/test_api/test_portfolio_api.py - ET20-EPIC-06 Phase A API tests - Cover broker_currency contracts and native+GBP summary payload fields
[2026-02-24 22:38] [v2.1.1] [STARTED] equity_tracker/tests/test_services/test_portfolio_service.py - ET20-EPIC-06 Phase A service tests - Add broker_currency and native-value summary coverage
[2026-02-24 22:39] [v2.1.1] [COMPLETED] equity_tracker/tests/test_api/test_ui_workflows.py - ET20-EPIC-06 Phase A UI tests - Added broker_currency persistence checks for add/edit/transfer and transfer-form currency control assertions
[2026-02-24 22:39] [v2.1.1] [COMPLETED] equity_tracker/tests/test_api/test_portfolio_api.py - ET20-EPIC-06 Phase A API tests - Added broker_currency endpoint coverage and portfolio summary native+GBP/FX-basis assertions
[2026-02-24 22:39] [v2.1.1] [COMPLETED] equity_tracker/tests/test_services/test_portfolio_service.py - ET20-EPIC-06 Phase A service tests - Added broker_currency transfer assertions and native+GBP summary/FX basis coverage
[2026-02-24 22:39] [v2.1.1] [STARTED] equity_tracker/tests - ET20-EPIC-06 Phase A targeted tests - Running service/API/UI suites for broker_currency and native-value changes
[2026-02-24 22:40] [v2.1.1] [COMPLETED] equity_tracker/tests - ET20-EPIC-06 Phase A targeted tests - python -m pytest -q tests/test_services/test_portfolio_service.py tests/test_api/test_portfolio_api.py tests/test_api/test_ui_workflows.py => 171 passed
[2026-02-24 22:40] [v2.1.1] [STARTED] equity_tracker/tests - ET20-EPIC-06 Phase A full regression - Running python -m pytest -q before stage completion
[2026-02-24 22:40] [v2.1.1] [COMPLETED] equity_tracker/tests - ET20-EPIC-06 Phase A full regression - python -m pytest -q => 487 passed, 3 skipped
[2026-02-24 22:40] [v2.1.1] [COMPLETED] ET20-EPIC-06 Phase A - Broker Currency Tracking - Implemented add/edit/transfer broker_currency lifecycle, native+GBP visibility, FX basis metadata, migration 006, and green regression; version bump/changelog updates pending user review
