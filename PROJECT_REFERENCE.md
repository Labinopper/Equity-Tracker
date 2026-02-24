# Equity Tracker - Project Reference

Last updated: 2026-02-24

This is the technical companion to `PROJECT_STATUS.md`.
Current released version: `v2.1.1`.

## 1) File Role
- Keep technical contracts, behavior rules, and architecture here.
- Keep release sequencing/version state in `PROJECT_STATUS.md`.
- Keep active execution items and recent test evidence in `todo.md`.

## 2) Architecture and Runtime
- Python 3.13
- FastAPI + Jinja templates
- SQLAlchemy + SQLite
- pytest regression suite

Primary layers:
1. Routers: `equity_tracker/src/api/routers/*`
2. Services: `equity_tracker/src/services/*`
3. Repositories: `equity_tracker/src/db/repository/*`
4. Models: `equity_tracker/src/db/models.py`
5. Engines: `equity_tracker/src/core/*`

## 3) Core Domain Contract (Current)

### Active schemes
- `RSU`
- `ESPP`
- `ESPP_PLUS`
- `BROKERAGE`
- `ISA`

### Canonical decision outputs
- `Net If Sold Today`
- `Gain vs True Cost`
- `Net If Held (Next Milestone)`
- `Net If Long-Term (5+ Years)`

### Liquidity contract
- `Est. Net Liquidity` is sellable-only.
- Locked and forfeiture-restricted value is shown separately as blocked/restricted value.

## 4) Transfer/Lock Behavioral Rules
- Destination transfer scheme is `BROKERAGE`.
- `RSU` transfer: blocked pre-vest; full-lot only.
- `ESPP` transfer: FIFO, whole shares, editable quantity, can span FIFO lots.
- `ESPP_PLUS` transfer: employee lot only; full-lot; in-window matched lots forfeited.
- `ESPP_PLUS` transfer writes structured `EmploymentTaxEvent` (estimated when inputs permit).
- Direct transfer into `ISA` is blocked (`dispose -> Add Lot`).

## 5) Data and UI Contracts (High-Value)

### Portfolio row/view contract
`PositionGroupRow` powers decision tables/cards and includes:
- identity (`group_id`, `acquisition_date`, `scheme_display`)
- quantity/value splits (`paid_qty`, `match_qty`, `total_qty`, related values)
- status (`sellability_status`, unlock/forfeiture context)
- sell-now and hold-state outputs
- notes/reason fields for explicit unavailability context

### Value-or-reason rule
Decision cells render either numeric values or explicit reason text. Silent blanks are not allowed.

### Refresh/freshness context
Portfolio routes include refresh diagnostics and daily-change freshness context derived from stored ticker snapshots.

### Currency visibility contract
- Broker holding currency (`USD`/`GBP`) is tracked for applicable holdings through add/edit/transfer flows.
- Portfolio and net-value surfaces expose native-currency and GBP-converted value context with explicit FX basis metadata.

## 6) Tax and Reporting Semantics
- ISA is treated as tax-sheltered in portfolio/reporting surfaces.
- Taxable report totals exclude ISA activity; ISA-exempt metadata is exposed alongside totals.
- Tax-year support includes published values through `2026-27`, with deterministic carry-forward through `2035-36` for unpublished years.

## 7) Current Technical Debt
1. Remaining template inline style usage.
2. Remaining mojibake/encoding artifacts.
3. Broader FX generalization and reliability hardening still pending.
4. IA/navigation rollout still partial.
5. Analytics Group B/C/D dependencies pending later EPIC stages.

## 8) Test Baseline Snapshot
- Latest release-synced full regression (`v2.1.1`): `python -m pytest -q` -> `487 passed, 3 skipped`.
- Latest working-tree evidence for subsequent stages is tracked in `todo.md` and `CODEX_PROGRESS.md`.

## 9) Technical Roadmap Dependencies (Next)
1. CF-06 (`v2.1.2`): inline style and encoding debt cleanup.
2. ET20-EPIC-08 expansion (`v2.2.0` onward): tax/risk/timeline chart groups.
3. ET20-EPIC-01 and ET20-EPIC-02 feed later planning/analytics/scenario work.
