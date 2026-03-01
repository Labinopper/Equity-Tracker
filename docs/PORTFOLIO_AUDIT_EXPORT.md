# Portfolio Audit Export

## Overview

The Portfolio Audit Export endpoint returns a single, deterministic JSON object
containing every raw input and calculated output used by the Portfolio page.
It is designed for external validation of the calculation pipeline — not as a
UI summary.

## Endpoint

```
GET /reports/portfolio-audit-export
```

- **Authentication**: session cookie required (same as all other API endpoints)
- **DB state**: database must be unlocked (`db_required` dependency)
- **Content-Type**: `application/json`
- **No paid APIs required**: uses DB-stored prices and FX rates only

## Accessing from the Portfolio page

A **"Download audit JSON"** button appears in the page-actions bar of the
Portfolio page (`/portfolio`).  Clicking it:
1. Calls `GET /reports/portfolio-audit-export`
2. Downloads the JSON as
   `equity-tracker_portfolio-audit_<YYYY-MM-DDTHH-mm-ssZ>.json`

## Output schema

The top-level keys are fixed and must not be altered between versions:

```json
{
  "metadata": { ... },
  "tax_settings": { ... },
  "fx_rates": [ ... ],
  "securities": [ ... ],
  "lots": [ ... ],
  "per_lot_calculations": [ ... ],
  "portfolio_aggregates": { ... },
  "tax_brackets_used": [ ... ],
  "additional_diagnostics": { ... } | null
}
```

### `metadata`

| Field | Description |
|---|---|
| `generated_at_utc` | ISO 8601 timestamp of when the export was generated |
| `as_of_used_utc` | ISO 8601 date used as the hypothetical disposal date |
| `db_path` | Absolute path to the database file |
| `db_encrypted` | `true` if the DB is SQLCipher-encrypted |
| `git_commit_hash` | Short git SHA (null if not in a git repo) |
| `app_versions` | `{"api": "..."}` |
| `rounding_rules` | Describes rounding strategy used |
| `tax_year_used` | UK tax year string derived from today's date (e.g. `"2025-26"`) |
| `employment_income_assumed_gbp` | The gross income figure from settings; null if not configured |
| `net_gain_definition` | Always `"SELLABLE_NET_MINUS_SELLABLE_TRUE_COST"` — documents the net gain formula |
| `net_liquidity_scope` | Always `"SELLABLE_ONLY"` — net liquidity and gain exclude locked lots |
| `assumptions` | Explicit list of calculation assumptions |

### `tax_settings`

Full UK tax band configuration for the tax year in use, including:
- Income tax thresholds and rates
- National Insurance thresholds and rates (employee)
- Student loan plan thresholds and rates (plan in use from settings)
- CGT annual exempt amount and rates
- ISA treatment flags
- Scheme rule descriptions (RSU, ESPP, ESPP_PLUS, SIP_*, BROKERAGE, ISA)
- `marginal_rates_at_configured_income` — derived marginal rates at the
  configured gross income (only present when settings are configured)

### `fx_rates`

All FX rate data relevant to the portfolio, drawn from three origins.
Each entry: `from_currency`, `to_currency`, `rate`, `timestamp_used`, `source`, `origin`.

| `origin` value | Description |
|---|---|
| `fx_rates_table` | Row from the `fx_rates` DB table (primary source) |
| `lot_acquisition_record` | FX rate embedded in a lot's `fx_rate_at_acquisition` field |
| `current_price_implied` | FX rate implied from `current_price_gbp / current_price_native` for non-GBP securities |

Entries are deduplicated by (from_currency, to_currency, rate).

### `securities`

One entry per security in the portfolio.  Includes latest price (native CCY
and GBP) and price timestamp.

### `lots`

One entry per active lot (quantity_remaining > 0 in normal view).  Includes
all cost basis fields, FX acquisition data, forfeiture fields, and scheme
metadata.

### `per_lot_calculations`

One entry per lot.  Each entry includes:

| Field | Description |
|---|---|
| `cost_basis_gbp` | `quantity_remaining × acquisition_price_gbp` |
| `true_economic_cost_gbp` | `quantity_remaining × true_cost_per_share_gbp` |
| `gross_market_value_gbp` | `quantity_remaining × current_price_gbp` (null if no price) |
| `unrealised_gain_gbp` | `gross_market_value − cost_basis` (null if no price) |
| `employment_tax_if_sold_today_gbp` | IT + NIC + SL estimate |
| `income_tax_component_gbp` | Income tax portion |
| `nic_component_gbp` | National Insurance portion |
| `student_loan_component_gbp` | Student Loan portion |
| `cgt_if_sold_today_gbp` | CGT estimate; 0 for ISA; null if no price/settings |
| `net_liquidation_value_today_gbp` | `gross_market_value − employment_tax` (matches portfolio display) |
| `forfeitable_value_gbp` | Market value of ESPP_PLUS matched shares in forfeiture window |
| `is_sellable_today` | `false` for RSU pre-vest, ESPP_PLUS pre-forfeiture-end |
| `calculation_steps` | Array of named formula steps with inputs and outputs |

**Null handling:** All fields are always present.  Fields that cannot be
computed (e.g. locked lot, missing price, missing settings) are explicitly
`null`.

### `portfolio_aggregates`

Computed as arithmetic sums of per-lot rounded values — guaranteed to reconcile
with `per_lot_calculations` to exactly £0.00.  Includes:

- `total_cost_basis_gbp`
- `total_true_economic_cost_gbp`
- `total_gross_market_value_gbp`
- `sellable_market_value_gbp` — Σ(gross_market_value where `is_sellable_today = true`)
- `blocked_market_value_gbp` — Σ(gross_market_value where `is_sellable_today = false`)
  - Invariant: `total_gross_market_value = sellable + blocked`
- `total_employment_tax_gbp`
- `total_income_tax_gbp`, `total_nic_gbp`, `total_student_loan_gbp`
- `total_cgt_gbp`
- `total_net_liquidation_value_gbp`
- `sellable_net_liquidity_gbp` — Σ(net_liquidation_value where `is_sellable_today = true`)
- `sellable_true_economic_cost_gbp` — Σ(true_economic_cost where `is_sellable_today = true`)
- `net_gain_if_sold_today_gbp` — `sellable_net_liquidity − sellable_true_economic_cost` (null if no sellable tax estimate)
- `total_forfeiture_risk_gbp`
- `concentration_by_security` — `[{security_id, ticker, pct_of_market_value}]`
- `concentration_by_scheme` — `[{scheme, pct_of_market_value}]`
- `reconciliation_checks` — internal consistency proof (see below)

#### `reconciliation_checks`

Five sub-checks, each confirming `per_lot_sum == portfolio_total` with
`difference == "0.00"` and `pass: true`.  Both sides derive from the same
`per_lot_calculations` data, so drift is always exactly zero.

| Check key | What it validates |
|---|---|
| `cost_basis` | Σ per-lot `cost_basis_gbp` == `total_cost_basis_gbp` |
| `true_cost` | Σ per-lot `true_economic_cost_gbp` == `total_true_economic_cost_gbp` |
| `market_value` | Σ per-lot `gross_market_value_gbp` (non-null) == `total_gross_market_value_gbp` |
| `employment_tax` | Σ per-lot `employment_tax_if_sold_today_gbp` (non-null) == `total_employment_tax_gbp` |
| `net_liquidity` | Σ per-lot `net_liquidation_value_today_gbp` (non-null) == `total_net_liquidation_value_gbp` |

### `tax_brackets_used`

One entry per (lot, tax_type) where tax > 0.  Each entry:

| Field | Description |
|---|---|
| `lot_id` | The lot this bracket applies to |
| `tax_type` | `"income_tax"`, `"nic"`, `"student_loan"`, or `"cgt"` |
| `band_name` | Descriptive name (e.g. `"marginal_rate_UNDER_THREE_YEARS"`) |
| `threshold_range_gbp` | Threshold range (null — marginal rates used, not band-by-band) |
| `rate` | Rate applied as a decimal fraction (e.g. `"0.40"`) |
| `taxable_amount_gbp` | Amount the rate was applied to |
| `tax_due_gbp` | `taxable_amount × rate` |
| `calculation_detail` | Human-readable explanation |

Note: the employment-tax engine uses **marginal rates**, not full band
decomposition.  `threshold_range_gbp` is therefore `null` for all employment
tax entries.

### `additional_diagnostics`

Optional section with audit/reconciliation checks.  Always `null` when the
portfolio is empty with no issues detected.  Otherwise contains:

- `reconciliation_cost_basis` — checks sum(per_lot.cost_basis) == portfolio service total
- `reconciliation_true_cost` — same for true economic cost
- `missing_price_warnings` — tickers with no current price
- `stale_price_warnings` — tickers with stale price data
- `stale_fx_warning` — when portfolio FX data is stale
- `lots_missing_tax_estimate` — sellable lots with no tax estimate
- `forfeiture_risk_summary` — ESPP_PLUS lots in forfeiture window

Each entry includes `name`, `why_it_matters`, `how_computed`, and `values`.

## Rounding rules

- All monetary values are output as **2dp `ROUND_HALF_UP` strings**.
- No floats appear anywhere in the calculation path.
- Internal calculation steps may carry higher precision; rounding is applied
  at the final output layer (noted as `"rounding_applied": "2dp ROUND_HALF_UP"`
  in `calculation_steps`).

## Determinism guarantee

Given identical DB state and settings file, the export output is deterministic.
The only non-deterministic field is `generated_at_utc`.

## How calculation values match the Portfolio page

`AuditExportService.get_portfolio_audit_export()` calls
`PortfolioService.get_portfolio_summary()` internally — the same function that
powers the Portfolio page.  Per-lot values (`cost_basis_total_gbp`,
`true_cost_total_gbp`, `market_value_gbp`, `est_net_proceeds_gbp`) are taken
directly from `LotSummary`.

The employment-tax component breakdown (IT/NI/SL) is derived by re-running the
employment-tax engine per lot using the same marginal rates derived from
settings.

## Extending to other pages

To add an audit export for another page (e.g. Tax Plan, Simulate):

1. Create a new method on `AuditExportService` (or a separate service) using
   the same pattern: call the page's service method, then assemble the schema.
2. Add an endpoint to the appropriate router (e.g. `/reports/tax-plan-audit-export`).
3. Add a download button to the relevant template following the same JS pattern
   as `portfolio.html`.
4. Add tests in `tests/test_api/`.

Keep the top-level schema keys identical across all audit exports.  Use the
`additional_diagnostics` key for page-specific diagnostics.

## Implementation files

| File | Role |
|---|---|
| `equity_tracker/src/services/audit_export_service.py` | Schema assembly (single entry point: `AuditExportService.get_portfolio_audit_export()`) |
| `equity_tracker/src/api/routers/reports.py` | `GET /reports/portfolio-audit-export` endpoint |
| `equity_tracker/src/api/templates/portfolio.html` | "Download audit JSON" button + JS download logic |
| `equity_tracker/tests/test_api/test_audit_export_api.py` | Smoke + reconciliation tests |
