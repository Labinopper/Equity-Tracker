SCOPE
Portfolio page + Net Value page only.
Do NOT implement CGT modelling.
Do NOT add forecasting.
Do NOT add percentage returns.

------------------------------------------------------------
GOAL
------------------------------------------------------------

Define a clear Screen Contract and refactor variables/labels so that:

1) Each screen has a single, unambiguous purpose.
2) The same metric means the same thing everywhere (no label drift).
3) Portfolio = “What can I extract today?”
4) Net Value = “Hypothetical full liquidation (including locked)” clearly marked as non-actionable.
5) Each screen is driven by ONE unified backend metrics object (no mixed sources).
6) Eliminate previous 1p drift caused by fragmented calculations.

------------------------------------------------------------
PART A — SCREEN PURPOSE DEFINITIONS
------------------------------------------------------------

SCREEN 1: PORTFOLIO (Primary Decision Screen)

Purpose:
“What do I gain if I sell what is actually sellable today, after estimated scheme employment taxes?”

This is the CAPITAL EXTRACTION view.

SCREEN 2: NET VALUE
Rename recommendation:
- “Sell-All Breakdown”
OR
- “Full Liquidation Lens”

Purpose:
“If everything were liquidated today (including locked / forfeiture-restricted lots), what would gross value and estimated scheme employment tax look like?”

This is NOT actionable liquidity.

If keeping the name “Net Value”:
- The subheading must clearly state it is hypothetical.
- Labels must explicitly state “Includes locked”.

------------------------------------------------------------
PART B — DATA CONTRACT PER SCREEN
------------------------------------------------------------

==============================
1) PORTFOLIO SCREEN
==============================

All top summary cards must be sourced from ONE backend object:
portfolio_metrics.*

No mixing summary.*, inline variables, or recomputed template math.

Top summary cards (economic-first order):

1) Est. Net Liquidity (Sellable)
   = Σ(net_liquidation_value_today) for sellable lots only.

2) Sellable True Economic Cost
   = Σ(true_cost) for sellable lots only.

3) Net Gain If Sold Today (absolute £ only)
   = Sellable Net Liquidity − Sellable True Economic Cost.
   Tooltip:
   “Sellable lots only. Locked/forfeitable excluded.”

4) Blocked / Restricted Market Value
   = Σ(market_value) for non-sellable lots.
   Tooltip:
   “Includes vesting locks and forfeiture windows.”

5) Total Gross Market Value (All Lots)
   = sellable_market_value + blocked_market_value.

6) Total True Economic Cost (All Lots)
   = Σ(true_cost) all lots.

7) Total Cost Basis (All Lots)
   = Σ(cost_basis) all lots.

8) Estimated Employment Tax (Sellable Only)
   = Σ(employment_tax_estimate) for sellable lots only.
   Tooltip:
   “IT + NIC + Student Loan where scheme rules apply.”

CRITICAL:
Eliminate CGT misnaming.

Example:
If templates currently use:
est_total_cgt_liability_gbp
for employment tax, this must be renamed to:

est_total_employment_tax_gbp

No CGT-prefixed variable may represent employment tax.

--------------------------------
Portfolio — Per Security Block
--------------------------------

Ensure consistent naming:

Display:
- Quantity
- Cost basis
- True economic cost
- Current price (native + GBP)
- Market value

Rename:
“Unrealised Gain (Sellable)”
→ “Economic Gain vs True Cost (Sellable Only)”

Must NOT represent cost-basis P&L.

Forfeiture risk and locked gain:
Keep separate lines.

--------------------------------
Portfolio — Position Table
--------------------------------

Decision-zone table must include:

- Market value
- True economic cost
- Net if sold today (sellable only; show LOCKED badge otherwise)
- Gain if sold today (vs true cost)

Optional:
Long-term placeholders allowed,
BUT must state:
“Hypothetical – assumes current price unless scenario applied.”

No broker fees unless explicitly modelled.

==============================
2) NET VALUE SCREEN
==============================

Must clearly state:
“Includes locked and forfeiture-restricted lots.”

All summary cards must source from:
sell_all_metrics.*

No mixing with portfolio metrics.

Top summary cards:

1) Gross Market Value (All Lots)

2) Estimated Employment Tax (All Lots)
   Must NOT use *_cgt_* naming.

3) Hypothetical Net Value (All Lots, incl. locked)
   = Gross Market Value − Estimated Employment Tax (All Lots)
   Tooltip:
   “Includes locked/forfeiture restricted shares; not actionable liquidity.”

4) Total Cost Basis (All Lots)

Optional:
- Total True Economic Cost (All Lots)
- Forfeiture Risk Market Value

--------------------------------
Net Value — Per Security Block
--------------------------------

Display:

- Quantity
- Gross market value (native + GBP)
- Cost basis
- (Optional) Accounting P&L vs cost basis
  Must be clearly labelled “Accounting P&L”

- Estimated employment tax (all lots)
- Hypothetical net proceeds (all lots)

--------------------------------
Net Value — Per Lot Table
--------------------------------

Columns must be:

- Acquisition date
- Scheme
- Sellability status
  (Sellable / Locked / Forfeiture Window)
- Tax year
- Qty remaining
- Cost basis
- Market value
- (Optional) Accounting P&L
- Estimated Employment Tax (per lot)
- Hypothetical Net Proceeds (per lot)
- Risk flags
  (Pre-vest lock, Forfeiture window, Tax window, ISA sheltered)

--------------------------------
Net Value — Disclaimer Block
--------------------------------

Remove any CGT references.

Replace with:

“This is an estimate only; not a filing calculation.”
“Employment tax rules are scheme-dependent.”

------------------------------------------------------------
PART C — VARIABLE NAMING STANDARDISATION (MANDATORY)
------------------------------------------------------------

Eliminate semantic drift.

No employment tax field may contain “cgt”.

Standardise to:

est_employment_tax_gbp
income_tax_component_gbp
nic_component_gbp
student_loan_component_gbp
est_net_proceeds_gbp
gross_market_value_gbp

If CGT DTOs exist:
Rename or replace with:

EmploymentTaxEstimate

NOT:
CGTEstimate

------------------------------------------------------------
PART D — UNIFIED BACKEND METRICS OBJECTS
------------------------------------------------------------

Implement two dedicated containers:

1) PortfolioMetrics
   Scope: Sellable-first capital extraction lens.

   Must include:
   - total_gross_market_value_gbp
   - total_true_cost_gbp
   - total_cost_basis_gbp
   - sellable_market_value_gbp
   - blocked_market_value_gbp
   - sellable_true_cost_gbp
   - est_employment_tax_sellable_gbp
   - est_net_liquidity_sellable_gbp
   - net_gain_if_sold_today_gbp

2) SellAllMetrics
   Scope: Full liquidation lens.

   Must include:
   - total_gross_market_value_gbp
   - total_true_cost_gbp
   - total_cost_basis_gbp
   - est_employment_tax_all_gbp
   - hypothetical_net_value_gbp
   - forfeiture_risk_market_value_gbp (if applicable)

UI templates must render exclusively from these objects.

No template-level recomputation.
No mixing legacy variables.
No dual-source calculations.

All money:
Decimal, 2dp, ROUND_HALF_UP.

------------------------------------------------------------
END REQUIREMENT
------------------------------------------------------------

The outcome must:

- Remove label ambiguity.
- Eliminate CGT naming confusion.
- Separate actionable liquidity from hypothetical liquidation.
- Prevent metric drift across screens.
- Preserve deterministic calculation integrity.