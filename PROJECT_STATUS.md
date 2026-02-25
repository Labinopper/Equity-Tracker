# Equity Tracker - Project Status

Last updated: 2026-02-25 (refinement pass audit)  
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
- Working tree implementation has progressed through `v2.7.1` (ET20-EPIC-06 Phase B reliability + generalized multi-currency workflow and ET20-EPIC-09 tax-year selector QoL), pending release-note/version sync.
- Latest released full regression: `487 passed, 3 skipped` (`python -m pytest -q`, 2026-02-24).
- Latest working-tree full regression: `533 passed, 3 skipped` (`python -m pytest -q`, 2026-02-25).
- Next planned stage: release-note/version sync for completed working-tree stages (`v2.1.2` through `v2.7.1`).

## In-Scope Capability Summary
- Portfolio, per-lot, and per-scheme decision surfaces in GBP.
- Scheme-aware behavior: `RSU`, `ESPP`, `ESPP_PLUS`, `BROKERAGE`, `ISA`.
- Add lot supports generalized input-currency workflows with GBP-normalized storage and retained acquisition FX metadata.
- Deterministic FIFO simulation/commit plus non-disposal transfer workflow with scheme guardrails.
- Validation Output Suite (`/admin/validation_report` API + CLI) for auditable recomputation.
- Risk (`/risk`) and analytics dashboard (`/analytics`) Groups A-D are live with configurable widget focus/visibility and table fallbacks.
- Global hide-values mode is live.
- CGT and economic-gain reports expose a tax-year selector with previous/next navigation controls.

## Known Gaps (Open)

### BUG-A01 — Analytics page non-functional (Critical)
- **Symptom:** Charts not rendering, widget visibility toggles not working, focus buttons inert.
- **Root cause:** JavaScript syntax error in `analytics.html` line 1462 — `});` instead of `}` closing the `wireControlActions` function's inner `if` block. Causes the entire IIFE to fail to parse.
- **Fix:** Change `});` to `}` on that line. One-character fix.

### BUG-A02 — Analytics chart init order (Minor, masked by BUG-A01)
- After BUG-A01 is fixed, verify charts render at correct dimensions when `applyVisibilityAndFocus` hides widgets before Chart.js measures canvases.

### Refinement pass (v2.8.x) — 16 label/clarity items identified
- See `v2_implementation_plan.md` Refinement Pass section for full list.
- Summary: SF/SL terminology inconsistency, ANI undefined, forfeiture badge wording, income-zero warning missing on Portfolio, Net Value stat scope confusion, Tax Plan delta labels lack directional context.
- All items are template-only changes. No service or schema changes required.

## Roadmap (Ordered)
1. Release-note/version sync for completed working-tree stages (`v2.1.2` through `v2.7.1`).
2. BUG-A01 fix (`v2.8.0`) — analytics JS syntax error.
3. Refinement pass (`v2.8.1`–`v2.8.5`) — labels, clarity, cross-screen consistency.
4. Next functional roadmap item to be promoted from backlog after refinement pass.

## Working Rules
- Keep this file short and decision-focused.
- Keep detailed technical behavior in `PROJECT_REFERENCE.md`.
- Keep `todo.md` focused on active backlog + short recent evidence only.
- Use `CODEX_PROGRESS.md` checkpoint logging (stage-level) instead of per-file logs.
