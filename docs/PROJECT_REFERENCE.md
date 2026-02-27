# Equity Tracker - Project Reference

Last updated: 2026-02-25 (release-note/version sync v2.1.2–v2.7.1)

This is the technical companion to `PROJECT_STATUS.md`.
Current released version: `v2.7.1`.

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
- Broker holding currency (3-letter ISO codes, with `USD`/`GBP` defaults) is tracked for applicable holdings through add/edit/transfer flows.
- Portfolio and net-value surfaces expose native-currency and GBP-converted value context with explicit FX basis metadata.
- Add Lot exposes explicit currency workflow context (input currency, security currency, FX path/as-of) when conversion is required.

## 6) Tax and Reporting Semantics
- ISA is treated as tax-sheltered in portfolio/reporting surfaces.
- Taxable report totals exclude ISA activity; ISA-exempt metadata is exposed alongside totals.
- Tax-year support includes published values through `2026-27`, with deterministic carry-forward through `2035-36` for unpublished years.

## 7) Current Technical Debt
1. IA/navigation rollout is still partial.
2. **BUG-A01:** `analytics.html` line 1462 — JS syntax error (`});` → `}`) breaks all analytics charts and widget controls. Fix in v2.8.0.
3. Refinement pass (v2.8.1–v2.8.5): 16 label/clarity items across templates. See `v2_implementation_plan.md` Refinement Pass section. All template-only.

## 10) Label and Terminology Reference (Canonical Meanings)

Definitions agreed during refinement audit (2026-02-25):

| Term | Definition |
|---|---|
| **True Cost** | What you effectively paid after accounting for tax events at acquisition (income tax at vest for RSU; employer subsidy for ESPP+; purchase price for ESPP/BROKERAGE/ISA). Used in Economic Gain. |
| **Cost Basis** | CGT allowable cost — the HMRC-recognised cost base for calculating capital gain on disposal. |
| **Allowable Cost** | Preferred HMRC term for Cost Basis in CGT filing context. |
| **Economic Gain** | Proceeds minus True Cost. Shows true investment return. May differ from CGT gain. |
| **CGT Gain** | Proceeds minus Cost Basis. The gain reported for tax self-assessment. |
| **Employment Tax** | Income Tax (IT) + National Insurance (NI) + Student Loan (SL) on scheme-eligible disposals (RSU, ESPP+). Abbreviated "IT + NI + SL". |
| **AEA** | Annual Exempt Amount — the CGT tax-free threshold for the year. |
| **ANI** | Adjusted Net Income — gross employment income minus pension sacrifice plus other income. Determines Personal Allowance taper above £100k. |
| **SL** | Student Loan repayment (canonical abbreviation for this app). Not "SF" (Student Finance). |
| **Est. Net Liquidity (Sellable)** | Sum of Net If Sold Today for **sellable rows only**. Locked and forfeiture-restricted lots are excluded. Used on Portfolio page. |
| **Hypothetical Full Liquidation** | Gross market value minus estimated employment tax across **all lots** (including locked). Used on Net Value page. Not directly comparable to Est. Net Liquidity. |
| **Blocked/Restricted Value** | Market value of locked lots (e.g. pre-vest RSU) + ESPP+ matched-share forfeiture-at-risk value. |
| **Forfeiture Window** | The period after ESPP+ lot acquisition during which selling the paired employee shares forfeits the matched shares. Badge "(Xd left)" means X days remaining until the window closes (safe to sell). |

## 8) Test Baseline Snapshot
- Latest release-synced full regression (`v2.7.1`): `python -m pytest -q` -> `533 passed, 3 skipped` (2026-02-25).

## 9) Technical Roadmap Dependencies (Next)
1. BUG-A01 fix (`v2.8.0`): analytics JS syntax error.
2. Refinement pass (v2.8.1–v2.8.5): 16 label/clarity items across templates.
3. Remaining IA/navigation expansion decisions to be promoted into the next scoped stage.
