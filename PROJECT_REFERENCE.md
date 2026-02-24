# Equity Tracker - Project Reference

Last updated: 2026-02-24

This file is the detailed companion to `PROJECT_STATUS.md`.

## 1) Architecture and Runtime

Stack:
- Python 3.13
- FastAPI + server-rendered Jinja templates
- SQLAlchemy + SQLite
- pytest test suite

Primary layers:
1. UI/API routers (`equity_tracker/src/api/routers/*`)
2. Services (`equity_tracker/src/services/*`)
3. Repositories (`equity_tracker/src/db/repository/*`)
4. Models (`equity_tracker/src/db/models.py`)
5. Tax/FIFO engines (`equity_tracker/src/core/*`)

## 2) Versioning and Release Log

Current version:
- `v1.9.17`

SemVer rules:
- `MAJOR`: breaking behavior/contracts/data semantics.
- `MINOR`: user-visible roadmap feature delivery.
- `PATCH`: bug fixes, docs, tests, and non-breaking technical cleanup.

Release update rules:
1. Every shipped change updates `PROJECT_STATUS.md` current version and changelog table.
2. `PROJECT_REFERENCE.md` records the same version with technical detail.
3. `todo.md` records the test evidence for that release.
4. Keep entries append-only and newest-first for skimmability.

Detailed release log:
| Version | Date | Scope | Validation |
|---|---|---|---|
| `v1.9.17` | 2026-02-24 | Daily ticker freshness/staleness infrastructure: add `price_ticker_snapshots` table + repository methods, persist per-refresh displayed GBP ticker values from `PriceService.fetch_all/fetch_and_store`, and render market-aware freshness hints (`No change ... (market open)` vs `market closed (opening in ...)`) in portfolio daily-change badges | `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py` + `python -m pytest -q` |
| `v1.9.16` | 2026-02-24 | Portfolio collapse-state persistence: security lot-section hide/show is stored per security id (`portfolio.security_visibility.v1`) and restored on load so refresh does not reopen hidden sections | `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py` + `python -m pytest -q` |
| `v1.9.15` | 2026-02-24 | Portfolio UI structure cleanup: remove top `Securities` stat tile, add per-security collapsible lot sections with single-line collapsed summaries, and expand `Est. Net Proceeds` details to include top-level fields (`Total Quantity`, `Cost Basis`, `True Cost`, `Market Value`, both unrealised P&L views, tax, net proceeds) | `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py` + `python -m pytest -q` |
| `v1.9.14` | 2026-02-24 | Daily move infrastructure + UI signal: `PriceService.fetch_all()` now backfills missing pre-existing daily history to earliest acquisition date into `price_history` (`source=yfinance_history`), and portfolio security cards render per-ticker daily move badges from latest vs prior stored close (percent + GBP value impact) | `python -m pytest -q tests/test_services/test_price_service.py tests/test_api/test_ui_workflows.py` + `python -m pytest -q` |
| `v1.9.13` | 2026-02-24 | Portfolio homepage liquidity semantics update: replace gross-derived `Est. Net Liquidation` tile with sellable-only `Est. Net Liquidity (Sellable)` and add `Blocked/Restricted Value`; aggregate sell-now gain/liquidity over sellable rows only | `python -m pytest -q tests/test_api/test_ui_workflows.py` + `python -m pytest -q` |
| `v1.9.12` | 2026-02-24 | Documentation contract update for IA/navigation redesign and decision-metric semantics: six-tab decision-engine model + `Est. Net Liquidity` defined as sellable-only | `python -m pytest -q` |
| `v1.9.11` | 2026-02-24 | Portfolio decision-table alignment fix: move desktop lot `...` actions from `Scheme` to end-of-row `Actions`; normalize lot-menu positioning/alignment to keep table divider lines level | `python -m pytest -q tests/test_api/test_ui_workflows.py` + full regression |
| `v1.9.10` | 2026-02-24 | Decision-focused portfolio lot table v2: snapshot columns plus 3-state scenario outcomes (`Sell Today`, `Next Milestone`, `Long-Term 5+ Years`) with concise structural notes | `python -m pytest -q tests/test_api/test_ui_workflows.py` + full regression |
| `v1.9.2` | 2026-02-24 | Tax-year band horizon extension: add published `2026-27` IT/NI/Student-Loan values and auto-carry-forward support through `2035-36` for unpublished years | `python -m pytest -q tests/test_tax_engine/test_bands.py` + `python -m pytest -q tests/test_api/test_portfolio_api.py -k "tax_years_no_db_required or cgt_report_invalid_tax_year"` + full regression |
| `v1.9.1` | 2026-02-24 | Transfer workflow refinement: ESPP supports editable whole-share FIFO quantity transfer with partial split and same-source broker merge semantics; RSU/ESPP+ remain full-lot transfer-only | `python -m pytest -q tests/test_services/test_portfolio_service.py -k "transfer"` + `python -m pytest -q tests/test_api/test_portfolio_api.py -k "transfer"` + `python -m pytest -q tests/test_api/test_ui_workflows.py -k "transfer"` + full regression |
| `v1.8.3` | 2026-02-24 | Portfolio UI-only quantity formatting to fixed 2dp in security summary and decision rows (desktop/mobile) | `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"` + full regression |
| `v1.8.2` | 2026-02-24 | Portfolio status chip behavior refinement: remove `Sellable` chip, suppress `Locked until` when forfeiture-risk badge is present | `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"` + full regression |
| `v1.8.1` | 2026-02-24 | Portfolio decision-table polish: header offset fix, ESPP+ scheme/qty/net-cash simplification, signal column moved after scheme, stacked sellability chips | targeted portfolio UI gates + full regression |
| `v1.8.0` | 2026-02-24 | Portfolio decision-table refactor: grouped ESPP+ paid+match rows, renamed decision columns, sticky desktop headers/identity, mobile card layout | targeted portfolio UI gates + full regression |
| `v1.7.3` | 2026-02-24 | Portfolio UI tidy follow-up: right-aligned top action buttons and compact per-lot `...` actions menu | targeted UI workflow gates + full regression |
| `v1.7.2` | 2026-02-24 | Dashboard UI polish (refresh chip layout, diagnostics pills, reduced P&L cell over-highlighting, cleaner lot action links) | targeted UI workflow gate + full regression |
| `v1.7.1` | 2026-02-24 | Transfer rule hardening (`RSU` vest lock, `ESPP+` matched forfeiture/tax-at-transfer semantics, ISA transfer boundary messaging) | targeted transfer gates + full regression |
| `v1.7.0` | 2026-02-24 | S7 `ISA` first-class support | `python -m pytest -q` -> `390 passed, 3 skipped` |
| `v1.6.0` | 2026-02-24 | S6 decision columns and sellability status | full regression pass |
| `v1.5.0` | 2026-02-24 | S5 non-disposal transfer workflow | full regression pass |
| `v1.4.0` | 2026-02-24 | S4 lot-edit workflow with audit traceability | full regression pass |
| `v1.3.0` | 2026-02-24 | S3 badge semantics/wording normalization | full regression pass |
| `v1.2.0` | 2026-02-24 | S2 refresh reliability and diagnostics | full regression pass |
| `v1.1.0` | 2026-02-24 | S1 non-blank lot net-proceeds contract | full regression pass |
| `v1.0.0` | 2026-02-24 | usable baseline prior to S1-S7 completion | baseline tests green |

## 3) Scheme Model and Behavior (Current)

Active UI schemes:
- `RSU`
- `ESPP`
- `ESPP_PLUS`
- `BROKERAGE`
- `ISA`

Model enum (`VALID_SCHEME_TYPES`) includes:
- `RSU`, `ESPP`, `ESPP_PLUS`
- `SIP_PARTNERSHIP`, `SIP_MATCHING`, `SIP_DIVIDEND`
- `BROKERAGE`, `ISA`

Current behavioral rules:
- `RSU`: locked before vest date (`Pre-Vest Lock`), IT-at-vest semantics.
- `ESPP`: immediate sellability, no SIP-style qualifying badge.
- `ESPP_PLUS`: employee + matched lot model, matched lots can lock and create forfeiture risk.
- `BROKERAGE`: immediate sellability.
- `ISA`: treated as tax-sheltered in portfolio/net-value/reporting contexts.

Transfer behavior rules:
- Destination is `BROKERAGE` only.
- `RSU` transfer is blocked before vest date.
- `RSU` transfer requires full remaining lot quantity.
- `ESPP` transfer preserves original lot economics (non-disposal custody movement) with FIFO behavior:
  - quantity is editable but must be whole shares.
  - selected lot must be the oldest active ESPP lot (FIFO head).
  - transfer may span multiple ESPP lots in FIFO order.
  - each source lot creates/updates its own BROKERAGE lot (same-source partials merge).
  - source ESPP lot quantity is reduced (or exhausted) without disposal events.
- `ESPP_PLUS` transfer must be initiated from the employee lot, not a matched lot.
- `ESPP_PLUS` transfer requires full remaining lot quantity.
- `ESPP_PLUS` transfer forfeits linked matched lots still in forfeiture window.
- `ESPP_PLUS` transfer marks transfer-time employment-tax eligibility (estimated when settings/price permit).
- Transfer into `ISA` is not allowed; required workflow is `dispose -> Add Lot` in `ISA`.

## 3.1 IA / Navigation Baseline (Approved)

Top-level tabs are constrained to six primary surfaces:
- `Decide`
- `Liquidity`
- `Schemes`
- `Risk`
- `Simulate`
- `Advanced`

Decision-surface contract (core outcomes that remain first-class):
- `Net If Sold Today`
- `Gain vs True Cost`
- `Net If Held (Next Milestone)`
- `Net If Long-Term (5+ Years)`

Liquidity contract:
- `Est. Net Liquidity` is a sellable-only measure.
- It sums `Net If Sold Today` for positions that can actually be sold now.
- Locked inventory and forfeited/non-realizable matched shares are excluded and should be shown separately as blocked/restricted value.

Implemented portfolio-view aggregation behavior:
- portfolio homepage liquidity tile computes sellable-only cash from decision rows (`net_cash_if_sold`) and excludes locked rows.
- portfolio homepage blocked/restricted tile aggregates:
  - market value of locked rows
  - forfeited matched-share value on immediate-sale scenarios.

## 4) S1-S7 Delivery Record

### S1 - Non-blank lot `Est. Net Proceeds`
- Lot cells now render value-or-reason.
- Deterministic reasons for unavailable states (locked/no price/settings-required).

### S2 - Reliable 60s refresh + diagnostics
- Refresh diagnostics state added:
  - `last_success_at`
  - `last_error`
  - `next_due_at`
- Portfolio UI now shows explicit refresh states and countdown resilience.

### S3 - Badge semantics/wording correction
- Canonical badge language enforced:
  - `Forfeiture Risk`
  - `Pre-Vest Lock`
  - `Tax Impact Window`
- Incorrect RSU/ESPP qualifying-window usage removed.

### S4 - Lot edit workflow
- Added edit UI + POST flow and API patch support for safe fields.
- Confirmation and validation feedback in UI.
- Audit UPDATE entries with before/after values.

### S5 - Transfer workflow
- Added non-disposal transfer (`RSU`/`ESPP`/`ESPP_PLUS` -> `BROKERAGE`).
- Transfer UI with confirmation and metadata notes.
- Audit UPDATE entries include source/destination context.
- Enforced transfer guards:
  - pre-vest `RSU` blocked
  - matched `ESPP_PLUS` lot cannot be direct transfer source
  - in-window matched `ESPP_PLUS` lots are forfeited automatically on linked employee-lot transfer
  - transfer note includes forfeiture and transfer-time tax-eligibility context.

### S6 - Decision columns
- Added deterministic lot sellability statuses:
  - `SELLABLE`
  - `LOCKED`
  - `AT_RISK`
- Added `Sell Now (Cash)` and `Sell Now (Economic)` lot outputs.

### S7 - ISA first-class support
- Enabled `ISA` in model constraints and UI scheme lists.
- Add-lot path accepts and persists `ISA`.
- Portfolio/net-value display includes ISA badge and tax-sheltered labeling.
- Report service excludes ISA components from taxable totals and exposes exempt totals metadata.

## 5) Canonical Badge Taxonomy

Use these badges consistently across Portfolio and Net Value:
- `Forfeiture Risk`: ESPP+ risk window only.
- `Pre-Vest Lock`: RSU pre-vest lock.
- `Tax Window`: SIP/ESPP+ tax-window semantics only.
- `Tax-sheltered`: ISA context.
- `Sellable` / `Locked until <date>` / `At-risk`: sellability status chips.

Portfolio-specific rendering rule:
- Portfolio status stack is explicit:
  - render primary status as `Sellable`, `Locked`, or `Forfeiture Risk`.
  - render `Locked until <date>` only when no forfeiture-risk badge is present for that row.

Deprecated wording:
- `SIP Period`
- `SIP Qualifying Period`

## 6) UI/Data Contracts (Current)

### 6.1 LotSummary additions
- `est_net_proceeds_reason: str | None`
- `sellability_status: str` (`SELLABLE` | `LOCKED` | `AT_RISK`)
- `sellability_unlock_date: date | None`
- `sell_now_economic_gbp: Decimal | None`

### 6.2 SecuritySummary additions
- `price_as_of: date | None`
- `fx_as_of: str | None`
- `price_is_stale: bool`
- `fx_is_stale: bool`
- `refresh_last_success_at: str | None`
- `refresh_last_error: str | None`
- `refresh_next_due_at: str | None`

### 6.3 Router/template context contracts
- Portfolio route includes `refresh_diag` object (state, success/error/next due).
- Portfolio route now includes `security_daily_changes` (latest-vs-prior daily direction/percent/GBP move or explicit unavailability reason).
- Daily-change freshness now derives from DB-backed per-refresh snapshots (`price_ticker_snapshots`) and emits market-aware UI hints:
  - during open sessions: `Updated ... ago` or warning `No change ... (market open)` after threshold.
  - during closed sessions: `market closed (opening in ...)` with last-change context.
- Portfolio template now renders each security body inside a collapsible `security-lots-toggle` details block; the summary line is intentionally single-line with lot composition context while collapsed.
- Portfolio template script persists the `security-lots-toggle` open/closed state in local storage and reapplies it at page load before user interaction.
- Edit lot page carries field-level values plus summary confirmation requirement.
- Transfer lot page carries candidate list, validation feedback, and confirmation requirement.

### 6.4 Value-or-reason rule
- Decision cells must render either:
  - numeric value, or
  - explicit unavailability reason.
- No silent blank state for key decision metrics.

### 6.5 Portfolio decision row contract (view layer)
`PositionGroupRow` drives the portfolio decision table and mobile cards:
- identity: `group_id`, `acquisition_date`, `scheme_display`
- quantity/value split: `paid_qty`, `match_qty`, `total_qty`, `paid_mv`, `match_mv`, `total_mv`
- cost bases: `paid_true_cost`, `paid_cost_basis` (matched true cost remains zero)
- status: `sellability_status`, `sellability_unlock_date`, `forfeiture_risk_days_remaining`
- sell decision fields:
  - `sell_now_cash_paid`
  - `sell_now_match_effect` (`INCLUDED` | `FORFEITED` | `LOCKED` | `NONE`)
  - `sell_now_forfeited_match_value`
  - `sell_now_economic_result`
- hold-state decision fields:
  - `next_milestone_net`, `next_milestone_gain`, `next_milestone_reason`
  - `long_term_net`, `long_term_gain`, `long_term_reason`
- notes/context fields:
  - `notes` (`Locked until ...`, `Match preserved in ...`, `Next tax window in ...`, `Fully matured`)
- render helpers: `pnl_tax_basis`, `pnl_economic`, `net_cash_if_sold`, `reason_unavailable`, decision signal/icon metadata.

Grouping behavior:
- ESPP+ paid + matched lots are grouped by `security` + `acquisition_date`.
- non-ESPP+ schemes remain one row per lot.
- grouping is view-only; no DB schema changes.

## 7) Reporting Semantics (Updated for ISA)

`ReportService` now separates taxable vs ISA-exempt components for CGT/economic summaries:
- `disposal_lines` contain taxable-only aggregates.
- Report totals (`proceeds`, `gains`, `losses`) are taxable totals.
- ISA-exempt metadata is exposed:
  - CGT: `isa_exempt_proceeds_gbp`, `isa_exempt_gain_gbp`
  - Economic: `isa_exempt_proceeds_gbp`, `isa_exempt_economic_gain_gbp`

UI report templates now show an informational banner when ISA-exempt activity exists.

Tax-year support window:
- published bands included through `2026-27`.
- forward support `2027-28` to `2035-36` is generated by carrying forward the latest published year values until HMRC confirms new figures.

## 8) Test Evidence

Latest full regression:
- Command: `python -m pytest -q`
- Result: `440 passed, 3 skipped`

Latest tax-band gates:
- `python -m pytest -q tests/test_tax_engine/test_bands.py` -> `3 passed`
- `python -m pytest -q tests/test_api/test_portfolio_api.py -k "tax_years_no_db_required or cgt_report_invalid_tax_year"` -> `2 passed`

Latest transfer-targeted gates:
- `python -m pytest -q tests/test_services/test_portfolio_service.py -k "transfer"` -> `10 passed`
- `python -m pytest -q tests/test_api/test_portfolio_api.py -k "transfer"` -> `5 passed`
- `python -m pytest -q tests/test_api/test_ui_workflows.py -k "transfer"` -> `6 passed`

Latest dashboard polish gate:
- `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio or refresh"` -> `13 passed`

Latest dashboard actions/menu gate:
- `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio or refresh or transfer or edit"` -> `20 passed`

Latest decision-table refactor gates:
- `python -m pytest -q tests/test_api/test_ui_workflows.py -k "portfolio"` -> `15 passed`
- `python -m pytest -q tests/test_api/test_ui_workflows.py` -> `56 passed`

S7 targeted gate command:
- `python -m pytest -q tests/test_api/test_ui_workflows.py tests/test_services/test_portfolio_service.py tests/test_services/test_report_service.py tests/test_api/test_portfolio_api.py`
- Result: `144 passed`

New/updated ISA-focused tests include:
- UI add-lot validation/success for `ISA`
- Portfolio/net-value ISA labeling checks
- Portfolio service ISA net-proceeds behavior without settings
- Report service ISA exclusion checks
- Portfolio API acceptance of `ISA` lot creation

## 9) Open Technical Debt (Detailed)

1. UI encoding debt remains:
- mojibake artifacts still present (current scan: `28` matches across key UI/service/router files).

2. Inline presentation debt remains:
- template inline style usage still present (current scan: `71` `style=` occurrences).

3. Framework migration debt:
- Starlette `TemplateResponse` request-first signature deprecation warnings still active.

4. ESPP+ add-lot atomicity:
- employee + matched lot creation still uses separate write calls.

5. FX generalization:
- valuation flow remains primarily USD->GBP-oriented.

6. ESPP+ transfer tax-event modeling:
- transfer-time employment tax eligibility is currently recorded in lot notes/audit text only.
- no structured tax event exists yet for reporting/reconciliation workflows.

## 10) Next Technical Roadmap (with Version Targets)

1. `v1.9.0` Global privacy mode (`Hide values`) with percentage visibility preserved.
2. `v1.10.0` UI debt cleanup pass (encoding + inline style extraction into shared CSS).
3. `v1.10.1` `TemplateResponse` deprecation migration.
4. `v1.11.0` ESPP+ dual-lot transactional add method.
5. `v1.12.0` FX multi-currency generalization.
