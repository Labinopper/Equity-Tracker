# Equity Tracker - Project Status

Last updated: 2026-02-25  
Current released version: `v2.1.1`

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
| `v2.1.1` | 2026-02-24 | ET20-EPIC-06 Phase A broker currency tracking: broker holding currency lifecycle (`USD`/`GBP`) across add/edit/transfer plus native+GBP visibility and explicit FX basis context. |
| `v2.1.0` | 2026-02-24 | ET20-EPIC-04 calendar timeline delivery (`/calendar`, `/api/calendar/events`) for vest/forfeiture/tax event visibility. |
| `v2.0.3` | 2026-02-24 | ET20-EPIC-08 Phase 1 analytics foundation (`/analytics`, summary/time-series APIs, chart theme). |
| `v2.0.2` | 2026-02-24 | CF-05 TemplateResponse request-first migration. |
| `v2.0.1` | 2026-02-24 | CF-04 global hide-values privacy mode. |
| `v2.0.0` | 2026-02-24 | ET20-EPIC-03 risk panel (`/risk`, `/api/risk/summary`). |

## Current Delivery Status
- S1-S7 usability baseline is implemented.
- v2 shipped through `v2.1.1`: Risk panel, Hide Values, TemplateResponse migration, Analytics Phase 1, Calendar timeline, and Broker Currency Phase A.
- Working tree implementation has progressed through `v2.6.0` (Scenario Lab multi-leg planning with sensitivity/comparison/export tooling), pending release-note/version sync.
- Latest released full regression: `487 passed, 3 skipped` (`python -m pytest -q`, 2026-02-24).
- Latest working-tree full regression: `524 passed, 3 skipped` (`python -m pytest -q`, 2026-02-25).
- Next planned stage: `v2.6.1` ET20-EPIC-08 Group C risk widgets.

## In-Scope Capability Summary
- Portfolio, per-lot, and per-scheme decision surfaces in GBP.
- Scheme-aware behavior: `RSU`, `ESPP`, `ESPP_PLUS`, `BROKERAGE`, `ISA`.
- Add lot supports input currency selection (`GBP`/`USD`) with GBP-normalized storage and retained acquisition FX metadata.
- Deterministic FIFO simulation/commit plus non-disposal transfer workflow with scheme guardrails.
- Validation Output Suite (`/admin/validation_report` API + CLI) for auditable recomputation.
- Risk (`/risk`) and analytics foundation (`/analytics`) pages are live.
- Global hide-values mode is live.

## Known Gaps (Open)
1. Analytics Groups C/D widgets are pending, plus graph-density/usability follow-ons.
2. FX architecture still needs Phase B generalization/hardening and dedicated currency workflow UX.
3. CGT reporting tax-year selection UX refinement remains pending.

## Roadmap (Ordered)
1. `v2.6.1` ET20-EPIC-08 Group C risk widgets.
2. `v2.6.2` ET20-EPIC-08 Group D timeline widgets.
3. `v2.6.3` ET20-EPIC-08 analytics UX follow-on (graph density + usability alignment).
4. `v2.7.0` ET20-EPIC-06 Phase B reliability and multi-currency hardening (+ currency workflow UX).
5. `v2.7.1` ET20-EPIC-09 CGT reporting QoL (tax-year selector refinement).

## Working Rules
- Keep this file short and decision-focused.
- Keep detailed technical behavior in `PROJECT_REFERENCE.md`.
- Keep `todo.md` focused on active backlog + short recent evidence only.
- Use `CODEX_PROGRESS.md` checkpoint logging (stage-level) instead of per-file logs.
