# Equity Tracker - Project Status

Last updated: 2026-02-24  
Current released version: `v2.0.3`

## Document Ownership (Single Responsibility)
- `PROJECT_STATUS.md`: high-level source of truth (released version, current state, roadmap order).
- `PROJECT_REFERENCE.md`: technical contracts and implementation semantics.
- `todo.md`: active backlog and short recent test evidence.
- `CODEX_PROGRESS.md`: live execution checkpoint log (pause/resume state).
- `CODEX_QUESTIONS.md`: unresolved implementation decisions only.
- `docs/CODEX_SCOPE_AUDIT.md`: historical audit context, not day-to-day control.

## Objective
Deliver a reliable local decision-support app for equity holdings with clear views of:
- true cost
- sell-now liquidity
- tax/lock/forfeiture-adjusted economic outcomes

## Versioning Policy
- `MAJOR`: breaking behavior/contracts/data assumptions.
- `MINOR`: roadmap feature delivery.
- `PATCH`: bugfix/test/docs/internal cleanup with no intentional scope expansion.

## Release Snapshot (Recent)
| Version | Date | Summary |
|---|---|---|
| `v2.0.3` | 2026-02-24 | ET20-EPIC-08 Phase 1 analytics foundation (`/analytics`, summary/time-series APIs, chart theme). |
| `v2.0.2` | 2026-02-24 | CF-05 TemplateResponse request-first migration. |
| `v2.0.1` | 2026-02-24 | CF-04 global hide-values privacy mode. |
| `v2.0.0` | 2026-02-24 | ET20-EPIC-03 risk panel (`/risk`, `/api/risk/summary`). |
| `v1.9.17` | 2026-02-24 | Market-aware ticker freshness with snapshot history and open/closed session messaging. |

## Current Delivery Status
- S1-S7 usability baseline is implemented.
- v2 foundations shipped: Risk panel, Hide Values, TemplateResponse migration, Analytics Phase 1.
- Latest released full regression: `471 passed, 3 skipped` (`python -m pytest -q`, 2026-02-24).
- Working tree implementation has progressed beyond `v2.0.3` (calendar and broker-currency workstreams), but release/version sync is still pending review.

## In-Scope Capability Summary
- Portfolio, per-lot, and per-scheme decision surfaces in GBP.
- Scheme-aware behavior: `RSU`, `ESPP`, `ESPP_PLUS`, `BROKERAGE`, `ISA`.
- Add lot supports input currency selection (`GBP`/`USD`) with GBP-normalized storage and retained acquisition FX metadata.
- Deterministic FIFO simulation/commit plus non-disposal transfer workflow with scheme guardrails.
- Validation Output Suite (`/admin/validation_report` API + CLI) for auditable recomputation.
- Risk (`/risk`) and analytics foundation (`/analytics`) pages are live.
- Global hide-values mode is live.

## Known Gaps (Open)
1. UI polish debt: residual inline styles and mojibake/encoding artifacts.
2. FX architecture still needs broader generalization/hardening beyond narrow paths.
3. IA/navigation migration is partial (additive pages are live; full six-surface navigation rollout remains).
4. Analytics Groups B/C/D depend on subsequent EPIC delivery.

## Roadmap (Ordered)
1. `v2.1.0` ET20-EPIC-04 Calendar timeline (`/calendar`, `/api/calendar/events`).
2. `v2.1.1` ET20-EPIC-06 Phase A Broker Currency Tracking (urgent): broker holding currency lifecycle (`USD`/`GBP`), native+GBP visibility, explicit FX basis context.
3. `v2.1.2` CF-06 UI polish debt reduction (inline style and encoding cleanup).
4. `v2.2.0` ET20-EPIC-08 Analytics expansion (Groups A+B completion).
5. `v2.3.0` ET20-EPIC-01 Tax-Year Realization Planner.
6. `v2.4.0` ET20-EPIC-02 Dividend net-return/tax-drag dashboard.
7. `v2.5.0` ET20-EPIC-07 Portfolio + Per-Scheme QoL enhancements.
8. `v2.6.0` ET20-EPIC-05 Scenario Lab.
9. `v2.6.1` ET20-EPIC-08 Group C risk widgets.
10. `v2.6.2` ET20-EPIC-08 Group D timeline widgets.
11. `v2.7.0` ET20-EPIC-06 Phase B reliability and multi-currency hardening.

## Working Rules
- Keep this file short and decision-focused.
- Keep detailed technical behavior in `PROJECT_REFERENCE.md`.
- Keep `todo.md` focused on active backlog + short recent evidence only.
- Use `CODEX_PROGRESS.md` checkpoint logging (stage-level) instead of per-file logs.
