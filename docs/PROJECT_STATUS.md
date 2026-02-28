# Equity Tracker - Project Status

Last updated: 2026-02-27 (security hardening for internet deployment complete)
Current released version: `v2.9.0`

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
| `v2.9.0` | 2026-02-27 | Security hardening for internet deployment: TOTP-only auth (1Password), signed session cookies (itsdangerous, 8h), rate limiting on login+unlock (slowapi, 5/15min), security headers middleware (CSP/X-Frame/nosniff/Referrer), CORS tightened, Swagger docs disabled by default, `scripts/setup_totp.py` for secret management, Dockerfile + Caddyfile + `.env.example` for production deployment. `docs/SECURITY.md` added. |
| `v2.8.5` | 2026-02-25 | Refinement pass complete: BUG-A01/A02 (analytics JS/chart-init), R01–R16 label/clarity across 10 templates, N01–N03 why-differ note/glossary/AEA nudge, E03–E07 cross-links, encoding fix in ui.py. |
| `v2.7.1` | 2026-02-25 | ET20-EPIC-09 CGT/Economic-Gain tax-year selector QoL: selector + previous/next navigation controls replacing tabbed year list. |
| `v2.7.0` | 2026-02-25 | ET20-EPIC-06 Phase B: provider-agnostic FX service (direct/inverse/multi-hop), configurable staleness thresholds, generalized broker/input currency workflow. |
| `v2.6.3` | 2026-02-25 | ET20-EPIC-08 Groups C+D + UX follow-on: stress/forfeiture/timeline widgets, decision-focus controls, denser responsive analytics layout. |
| `v2.6.0` | 2026-02-25 | ET20-EPIC-05 Scenario Lab (`/scenario-lab`): multi-lot decision builder, price-shock sensitivity, side-by-side compare and export. |
| `v2.5.1` | 2026-02-25 | ET20-EPIC-01B timing refinement: sell-this-year vs sell-next-year IT/NI/SL/CGT delta comparison. |
| `v2.5.0` | 2026-02-25 | ET20-EPIC-07 Portfolio+Per-Scheme QoL: quick filters, sort controls, formula expanders, persistent prefs, focus mode, scheme visibility toggles. |
| `v2.4.1` | 2026-02-25 | ET20-EPIC-01B Compensation-Aware Tax Plan: salary/bonus what-if with IT/NI/SL and pension-sacrifice tradeoff. |
| `v2.4.0` | 2026-02-24 | ET20-EPIC-02 Dividend net-return/tax-drag dashboard (`/dividends`) with `DividendEntry` persistence and dividend tax engine. |
| `v2.3.0` | 2026-02-24 | ET20-EPIC-01 Tax-Year Realization Planner (`/tax-plan`): AEA usage, per-lot CGT projection, cross-year comparison. |
| `v2.2.0` | 2026-02-24 | ET20-EPIC-08 Groups A+B analytics dashboard: portfolio-overview and tax-position chart widgets with table fallbacks. |
| `v2.1.2` | 2026-02-24 | CF-06 UI encoding/inline style debt cleanup. |
| `v2.1.1` | 2026-02-24 | ET20-EPIC-06 Phase A broker currency tracking: broker holding currency lifecycle (`USD`/`GBP`) across add/edit/transfer plus native+GBP visibility and explicit FX basis context. |
| `v2.1.0` | 2026-02-24 | ET20-EPIC-04 calendar timeline delivery (`/calendar`, `/api/calendar/events`) for vest/forfeiture/tax event visibility. |
| `v2.0.3` | 2026-02-24 | ET20-EPIC-08 Phase 1 analytics foundation (`/analytics`, summary/time-series APIs, chart theme). |
| `v2.0.2` | 2026-02-24 | CF-05 TemplateResponse request-first migration. |
| `v2.0.1` | 2026-02-24 | CF-04 global hide-values privacy mode. |
| `v2.0.0` | 2026-02-24 | ET20-EPIC-03 risk panel (`/risk`, `/api/risk/summary`). |

## Current Delivery Status
- S1-S7 usability baseline is implemented.
- v2 shipped through `v2.9.0`: all planned EPICs delivered, refinement pass complete, and internet-deployment security hardening added. See Release Snapshot for per-version details.
- Latest released full regression: `531 passed, 3 skipped` (`python -m pytest -q`, 2026-02-27). Two pre-existing UI test failures (`test_portfolio_shows_per_ticker_daily_change_badge`, `test_portfolio_cards_are_collapsible_and_net_panel_shows_top_level_fields`) are known and unrelated to security work.
- Next planned stage: next functional roadmap item to be promoted from backlog; or production deployment to cloud host.

## In-Scope Capability Summary
- Portfolio, per-lot, and per-scheme decision surfaces in GBP.
- Scheme-aware behavior: `RSU`, `ESPP`, `ESPP_PLUS`, `BROKERAGE`, `ISA`.
- Add lot supports generalized input-currency workflows with GBP-normalized storage and retained acquisition FX metadata.
- Deterministic FIFO simulation/commit plus non-disposal transfer workflow with scheme guardrails.
- Validation Output Suite (`/admin/validation_report` API + CLI) for auditable recomputation.
- Risk (`/risk`) and analytics dashboard (`/analytics`) Groups A-D are live with configurable widget focus/visibility and table fallbacks.
- Global hide-values mode is live.
- CGT and economic-gain reports expose a tax-year selector with previous/next navigation controls.
- Internet-deployment ready: TOTP login, session cookies, rate limiting, security headers, Dockerfile + Caddyfile (see `docs/SECURITY.md`).

## Known Gaps (Open)
- Two pre-existing UI test failures in `test_ui_workflows.py` (content assertion mismatches, unrelated to security). Not blocking deployment.
- Production deployment requires: domain name, server/VPS or Docker host, `EQUITY_TOTP_SECRET` and `EQUITY_SECRET_KEY` set in production `.env`.

## Roadmap (Ordered)
1. Production deployment to cloud host (Fly.io, Hetzner, or Oracle Free Tier — see `docs/SECURITY.md`).
2. Next functional roadmap item to be promoted from backlog.

## Working Rules
- Keep this file short and decision-focused.
- Keep detailed technical behavior in `PROJECT_REFERENCE.md`.
- Keep `todo.md` focused on active backlog + short recent evidence only.
- Use `CODEX_PROGRESS.md` checkpoint logging (stage-level) instead of per-file logs.
