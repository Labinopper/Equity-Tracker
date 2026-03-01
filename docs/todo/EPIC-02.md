You are working on a locally hosted financial decision engine called EQUITY TRACKER.

System constraints:
- Python (FastAPI, SQLite, SQLAlchemy)
- Decimal only (never float)
- 2dp money, ROUND_HALF_UP
- FIFO lot accounting
- Deterministic calculations
- No market prediction
- No financial advice
- Long-term retained-wealth engine

User context:
Significant IBM concentration (RSU, ESPP, ESPP+, Brokerage).
Wants structured divestment over ~2 weeks.

We are building:

DIVESTMENT MODE

This must:
- NOT become a price-watching dashboard
- NOT introduce market timing logic
- Enforce discipline
- Be fully auditable

------------------------------------------------------------
OBJECTIVE
------------------------------------------------------------

Design and implement a Divestment Mode screen that:
- Supports structured sell-down plans
- Enforces hard deadlines
- Simulates tax + liquidity deterministically
- Tracks execution immutably
- Optionally includes an LLM “Decision Review” layer
  (plan critic only, never prediction)

------------------------------------------------------------
PART 1 — DIVESTMENT MODE SCREEN
------------------------------------------------------------

PURPOSE:
Structured reduction of employer concentration via:
- TIME-based plans
- THRESHOLD-based plans
- HYBRID plans

All plans MUST:
- Have a hard deadline
- Be auditable
- Be tranche-based
- Use FIFO simulation
- Show tax impact

------------------------------------------------------------
SCREEN LAYOUT
------------------------------------------------------------

Single page:
LEFT  = Plan Builder
RIGHT = Snapshot + Execution Panel

NO:
- live streaming
- auto-refresh loops
- flashing P&L

------------------------------------------------------------
SECTION A — POSITION SNAPSHOT
------------------------------------------------------------

Display:
- Total IBM qty
- Sellable qty
- Blocked qty
- Gross market value (total + sellable)
- Est net liquidation (sellable only)
- Est employment tax (sellable)
- Current concentration %
- Target concentration %

Calculations:
sellable_value = sum(sellable_lots.qty * price_gbp)
concentration_pct = ibm_total_value / total_portfolio_value

Money: 2dp ROUND_HALF_UP
Percent: 2dp

------------------------------------------------------------
SECTION B — GOAL & CONSTRAINTS
------------------------------------------------------------

Inputs:
Goal type:
- Reduce concentration to X%
- Sell £X
- Sell X shares

Start date (required)
End date (required)

Max tranches
Minimum tranche size
Account scope (ISA / TAXABLE / ALL)

Optional:
- Do not sell below price floor

Validation:
- Goal feasible vs sellable qty
- Deadline mandatory

------------------------------------------------------------
SECTION C — PLAN ENGINE
------------------------------------------------------------

1) TIME
Even distribution across N tranches between start and end.

2) THRESHOLD
Sell when price >= trigger.
Must include expiry date.
Expiry fallback required:
- SELL_ANYWAY
OR
- CONVERT_TO_TIME

3) HYBRID
Immediate tranche (e.g. 50%)
Remaining via threshold triggers
Hard deadline fallback

All plans:
- Deterministic tranche quantities
- Hard deadline enforced

------------------------------------------------------------
SECTION D — TRANCHE TABLE
------------------------------------------------------------

Columns:
- Tranche #
- Type
- Planned date
- Trigger price
- Expiry
- Planned qty
- Est gross
- Est tax
- Est net
- Status
- Actions

Statuses:
PLANNED
ELIGIBLE
TRIGGER_MET
EXECUTED
SKIPPED

Simulation:
Use FIFO across eligible sellable lots.

For each tranche:
est_gross = qty * price_gbp
est_tax   = employment_tax_engine(lots_consumed_fifo)
est_net   = est_gross - est_tax - fees

Decimal only.

------------------------------------------------------------
SECTION E — EXECUTION PANEL
------------------------------------------------------------

Display:
Next eligible tranche
Reason (date reached / trigger met / fallback)

Buttons:
- Simulate today
- Mark executed
- Skip tranche (requires reason)

Execution record (immutable):
- executed_at_utc
- executed_qty
- executed_price
- currency
- fx_rate
- fees
- notes
- source (MANUAL / IMPORT)

Audit log required.

------------------------------------------------------------
PART 2 — LLM DECISION REVIEW PANEL
------------------------------------------------------------

LLM is strictly:
- Plan critic
- Bias detector
- Consequence summariser

LLM must NOT:
- Predict price
- Recommend timing
- Output probabilities
- Forecast direction

------------------------------------------------------------
LLM INPUT JSON
------------------------------------------------------------

{
  "security": "IBM",
  "current_price_gbp": "...",
  "sellable_qty": "...",
  "blocked_qty": "...",
  "concentration_pct": "...",
  "plan": { ... },
  "tranches": [ ... ],
  "goal": "...",
  "deadline": "...",
  "account_scope": "...",
  "lot_summary": [ ... ]
}

------------------------------------------------------------
LLM OUTPUT (STRICT JSON ONLY)
------------------------------------------------------------

{
  "risk_flags": [
    {"severity":"high|medium|low","code":"...","message":"..."}
  ],
  "bias_flags": [
    {"code":"ANCHORING|DELAY|CONCENTRATION","message":"..."}
  ],
  "plan_quality": {
    "score": 0-100,
    "explanation":"..."
  },
  "suggested_variants": [
    {
      "type":"TIME|THRESHOLD|HYBRID",
      "parameters": {...},
      "rationale":"..."
    }
  ],
  "next_action_summary": "..."
}

Requirements:
- JSON only (no prose)
- temperature = 0
- All inputs/outputs logged for audit

------------------------------------------------------------
DATA MODEL ADDITIONS
------------------------------------------------------------

Tables:
- divestment_plan
- divestment_tranche
- divestment_execution
- audit_log

All money: Decimal(18,2)
All qty:   Decimal(18,6)

Must include:
- deadline
- trigger
- fallback action
- immutable execution record

------------------------------------------------------------
ACCEPTANCE CRITERIA
------------------------------------------------------------

1. Cannot save plan without deadline.
2. Deterministic tranche qty.
3. FIFO lot preview before execution.
4. Execution immutable.
5. LLM strictly JSON schema.
6. No streaming.
7. No auto-refresh.
8. No floats.
9. 2dp ROUND_HALF_UP everywhere.

------------------------------------------------------------
OUTPUT REQUIRED
------------------------------------------------------------

Provide:
1) Screen architecture
2) DB schema definitions
3) API endpoints
4) Deterministic calculation functions
5) LLM system + user prompt templates
6) Validation rules
7) Edge-case handling
8) Minimal frontend layout structure

No prediction logic.
No financial advice.
No black-box behaviour.

Focus on discipline, clarity, retained wealth protection.