# Database Schema Documentation

**Last Updated:** 2026-04-03

## Table of Contents

1. [Overview](#overview)
2. [Core Tables](#core-tables)
3. [Entity Relationships](#entity-relationships)
4. [Data Types and Constraints](#data-types-and-constraints)
5. [Migration Strategy](#migration-strategy)
6. [Beta Research Schema](#beta-research-schema)

## Overview

Equity Tracker uses **SQLite** (with optional SQLCipher encryption) as its database engine. The schema is designed with the following principles:

- **Immutability**: Core records (lots, disposals) are immutable after creation
- **Append-Only Audit**: All changes tracked in audit log, never deleted
- **Decimal Precision**: All monetary values stored as TEXT (Decimal strings)
- **UUID Primary Keys**: 36-character UUID strings for portability
- **Referential Integrity**: Foreign keys with appropriate cascade rules

### Database Files

- **Main Database**: `portfolio.db` (or user-specified path)
- **Beta Database**: `beta_research.db` (separate, optional)
- **Settings**: `portfolio.db.settings.json` (unencrypted preferences)

## Core Tables

### securities

Tradeable instruments (stocks, ETFs, funds).

```sql
CREATE TABLE securities (
    id TEXT(36) PRIMARY KEY,
    ticker TEXT(20) NOT NULL,
    isin TEXT(12),
    name TEXT(200) NOT NULL,
    currency TEXT(3) NOT NULL CHECK(length(currency) = 3),
    exchange TEXT(20),
    units_precision INTEGER NOT NULL DEFAULT 0 
        CHECK(units_precision >= 0 AND units_precision <= 10),
    dividend_reminder_date DATE,
    catalog_id TEXT(36) REFERENCES security_catalog(id) ON DELETE SET NULL,
    is_manual_override BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL
);
```

**Key Fields:**
- `units_precision`: Decimal places for quantity (0 = whole shares, 8 = fractional)
- `catalog_id`: Link to external security catalog (Phase S)
- `is_manual_override`: User bypassed catalog validation

**Indexes:**
- `idx_securities_ticker` on `ticker`
- `idx_securities_isin` on `isin`

### lots

Immutable acquisition records for equity positions.

```sql
CREATE TABLE lots (
    id TEXT(36) PRIMARY KEY,
    security_id TEXT(36) NOT NULL REFERENCES securities(id) ON DELETE RESTRICT,
    scheme_type TEXT NOT NULL CHECK(scheme_type IN (
        'RSU', 'ESPP', 'ESPP_PLUS', 
        'SIP_PARTNERSHIP', 'SIP_MATCHING', 'SIP_DIVIDEND',
        'BROKERAGE', 'ISA'
    )),
    acquisition_date DATE NOT NULL,
    quantity TEXT NOT NULL,  -- Decimal string
    quantity_remaining TEXT NOT NULL,  -- Decimal string (only mutable field)
    cost_per_share_gbp TEXT NOT NULL,  -- Decimal string
    cost_per_share_native TEXT,  -- Decimal string
    native_currency TEXT(3),
    fx_rate_to_gbp TEXT,  -- Decimal string
    market_value_at_acquisition_gbp TEXT,  -- Decimal string
    employment_income_gbp TEXT,  -- Decimal string
    vest_date DATE,
    lock_expiry_date DATE,
    forfeiture_date DATE,
    notes TEXT,
    created_at DATETIME NOT NULL
);
```

**Key Constraints:**
- `quantity_remaining` ≤ `quantity` (enforced in application)
- Only `quantity_remaining` can be updated after creation
- All monetary values as TEXT (Decimal strings like "1234.56")

**Scheme Types:**
- **RSU**: Restricted Stock Units
- **ESPP**: Employee Stock Purchase Plan
- **ESPP_PLUS**: ESPP with forfeiture rules
- **SIP_PARTNERSHIP**: Share Incentive Plan (partnership shares)
- **SIP_MATCHING**: SIP matching shares
- **SIP_DIVIDEND**: SIP dividend shares
- **BROKERAGE**: Regular brokerage account
- **ISA**: Individual Savings Account (UK tax-advantaged)

**Indexes:**
- `idx_lots_security_id` on `security_id`
- `idx_lots_acquisition_date` on `acquisition_date`
- `idx_lots_scheme_type` on `scheme_type`

### disposals

Sale transactions (append-only, corrections via reversals).

```sql
CREATE TABLE disposals (
    id TEXT(36) PRIMARY KEY,
    security_id TEXT(36) NOT NULL REFERENCES securities(id) ON DELETE RESTRICT,
    disposal_date DATE NOT NULL,
    quantity TEXT NOT NULL,  -- Decimal string
    proceeds_per_share_gbp TEXT NOT NULL,  -- Decimal string
    total_proceeds_gbp TEXT NOT NULL,  -- Decimal string
    broker_fee_gbp TEXT,  -- Decimal string
    notes TEXT,
    is_reversal BOOLEAN NOT NULL DEFAULT 0,
    reverses_disposal_id TEXT(36) REFERENCES disposals(id),
    created_at DATETIME NOT NULL
);
```

**Key Fields:**
- `is_reversal`: Marks correction records
- `reverses_disposal_id`: Links to original disposal being corrected

**Indexes:**
- `idx_disposals_security_id` on `security_id`
- `idx_disposals_disposal_date` on `disposal_date`

### disposal_lot_allocations

FIFO lot allocation for each disposal.

```sql
CREATE TABLE disposal_lot_allocations (
    id TEXT(36) PRIMARY KEY,
    disposal_id TEXT(36) NOT NULL REFERENCES disposals(id) ON DELETE CASCADE,
    lot_id TEXT(36) NOT NULL REFERENCES lots(id) ON DELETE RESTRICT,
    quantity_allocated TEXT NOT NULL,  -- Decimal string
    cost_basis_gbp TEXT NOT NULL,  -- Decimal string
    employment_income_gbp TEXT,  -- Decimal string
    created_at DATETIME NOT NULL
);
```

**Cascade Rules:**
- Deleting a disposal cascades to allocations
- Deleting a lot is restricted if allocations exist

**Indexes:**
- `idx_disposal_lot_allocations_disposal_id` on `disposal_id`
- `idx_disposal_lot_allocations_lot_id` on `lot_id`

### dividends

Dividend income events.

```sql
CREATE TABLE dividends (
    id TEXT(36) PRIMARY KEY,
    security_id TEXT(36) NOT NULL REFERENCES securities(id) ON DELETE RESTRICT,
    lot_id TEXT(36) REFERENCES lots(id) ON DELETE SET NULL,
    payment_date DATE NOT NULL,
    amount_per_share_native TEXT NOT NULL,  -- Decimal string
    total_amount_native TEXT NOT NULL,  -- Decimal string
    native_currency TEXT(3) NOT NULL,
    amount_per_share_gbp TEXT,  -- Decimal string
    total_amount_gbp TEXT,  -- Decimal string
    fx_rate TEXT,  -- Decimal string
    fx_source TEXT,
    tax_treatment TEXT NOT NULL CHECK(tax_treatment IN ('TAXABLE', 'ISA_EXEMPT')),
    quantity_held TEXT,  -- Decimal string
    notes TEXT,
    created_at DATETIME NOT NULL
);
```

**Key Fields:**
- `lot_id`: Optional link to specific lot (lot-first input workflow)
- `tax_treatment`: TAXABLE (brokerage) or ISA_EXEMPT (ISA wrapper)
- `fx_rate`, `fx_source`: FX conversion provenance

**Indexes:**
- `idx_dividends_security_id` on `security_id`
- `idx_dividends_payment_date` on `payment_date`
- `idx_dividends_lot_id` on `lot_id`

### dividend_reference_events

Upcoming dividend dates for reminder tracking.

```sql
CREATE TABLE dividend_reference_events (
    id TEXT(36) PRIMARY KEY,
    security_id TEXT(36) NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    ex_dividend_date DATE NOT NULL,
    payment_date DATE,
    amount_per_share_native TEXT,  -- Decimal string
    native_currency TEXT(3),
    notes TEXT,
    created_at DATETIME NOT NULL,
    UNIQUE(security_id, ex_dividend_date)
);
```

### cash_entries

Multi-currency cash ledger (append-only).

```sql
CREATE TABLE cash_entries (
    id TEXT(36) PRIMARY KEY,
    entry_date DATE NOT NULL,
    container TEXT NOT NULL CHECK(container IN ('BROKER', 'ISA', 'BANK')),
    currency TEXT(3) NOT NULL,
    amount TEXT NOT NULL,  -- Decimal string (can be negative)
    description TEXT NOT NULL,
    category TEXT,
    fx_rate TEXT,  -- Decimal string
    fx_source TEXT,
    metadata TEXT,  -- JSON string
    created_at DATETIME NOT NULL
);
```

**Containers:**
- **BROKER**: Regular brokerage account
- **ISA**: Tax-advantaged ISA wrapper
- **BANK**: External bank account

**Indexes:**
- `idx_cash_entries_entry_date` on `entry_date`
- `idx_cash_entries_container_currency` on `(container, currency)`

### lot_transfer_events

Lot transfers between schemes (e.g., ESPP → ISA).

```sql
CREATE TABLE lot_transfer_events (
    id TEXT(36) PRIMARY KEY,
    source_lot_id TEXT(36) NOT NULL REFERENCES lots(id) ON DELETE RESTRICT,
    destination_lot_id TEXT(36) NOT NULL REFERENCES lots(id) ON DELETE RESTRICT,
    transfer_date DATE NOT NULL,
    quantity_transferred TEXT NOT NULL,  -- Decimal string
    market_value_gbp_at_transfer TEXT,  -- Decimal string
    employment_income_gbp TEXT,  -- Decimal string
    notes TEXT,
    created_at DATETIME NOT NULL
);
```

**Transfer Rules:**
- Source lot quantity decremented
- Destination lot created with transferred quantity
- Employment tax may be triggered (e.g., ESPP → ISA)

### prices

Historical price snapshots.

```sql
CREATE TABLE prices (
    id TEXT(36) PRIMARY KEY,
    security_id TEXT(36) NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    close_price_gbp TEXT NOT NULL,  -- Decimal string
    source TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE(security_id, price_date)
);
```

**Sources:**
- `MANUAL`: User-entered
- `YFINANCE`: Yahoo Finance
- `TWELVE_DATA`: Twelve Data API
- `GOOGLE_SHEETS`: Google Sheets integration
- `IBKR`: Interactive Brokers

**Indexes:**
- `idx_prices_security_date` on `(security_id, price_date)`

### price_ticker_snapshots

Native currency price snapshots (for FX-adjusted securities).

```sql
CREATE TABLE price_ticker_snapshots (
    id TEXT(36) PRIMARY KEY,
    security_id TEXT(36) NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    close_price_native TEXT NOT NULL,  -- Decimal string
    native_currency TEXT(3) NOT NULL,
    source TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE(security_id, snapshot_date)
);
```

### fx_rates

Foreign exchange rate history.

```sql
CREATE TABLE fx_rates (
    id TEXT(36) PRIMARY KEY,
    from_currency TEXT(3) NOT NULL,
    to_currency TEXT(3) NOT NULL,
    rate_date DATE NOT NULL,
    rate TEXT NOT NULL,  -- Decimal string
    source TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE(from_currency, to_currency, rate_date)
);
```

**Sources:**
- `MANUAL`: User-entered
- `YFINANCE`: Yahoo Finance
- `TWELVE_DATA`: Twelve Data API
- `GOOGLE_SHEETS`: Google Sheets
- `STREAM`: Real-time streaming data

### employment_tax_events

Employment tax liability records.

```sql
CREATE TABLE employment_tax_events (
    id TEXT(36) PRIMARY KEY,
    event_date DATE NOT NULL,
    security_id TEXT(36) REFERENCES securities(id) ON DELETE SET NULL,
    lot_id TEXT(36) REFERENCES lots(id) ON DELETE SET NULL,
    disposal_id TEXT(36) REFERENCES disposals(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    taxable_amount_gbp TEXT NOT NULL,  -- Decimal string
    income_tax_gbp TEXT,  -- Decimal string
    national_insurance_gbp TEXT,  -- Decimal string
    student_loan_gbp TEXT,  -- Decimal string
    notes TEXT,
    created_at DATETIME NOT NULL
);
```

**Event Types:**
- `VEST`: RSU vesting
- `ESPP_PURCHASE`: ESPP purchase discount
- `DISPOSAL`: Sale triggering employment tax
- `TRANSFER`: Lot transfer between schemes

### scenario_snapshots

Saved what-if disposal scenarios.

```sql
CREATE TABLE scenario_snapshots (
    id TEXT(36) PRIMARY KEY,
    name TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    execution_mode TEXT NOT NULL CHECK(execution_mode IN (
        'INDEPENDENT', 'SEQUENTIAL'
    )),
    payload TEXT NOT NULL,  -- JSON string
    created_at DATETIME NOT NULL
);
```

**Execution Modes:**
- **INDEPENDENT**: Each leg uses original portfolio state
- **SEQUENTIAL**: Legs execute in order, updating state

### portfolio_guardrail_state_events

Persisted alert lifecycle state.

```sql
CREATE TABLE portfolio_guardrail_state_events (
    id TEXT(36) PRIMARY KEY,
    lifecycle_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE', 'DISMISSED', 'SNOOZED')),
    transition_date DATETIME NOT NULL,
    expiry_date DATETIME,
    notes TEXT,
    created_at DATETIME NOT NULL
);
```

**States:**
- **ACTIVE**: Alert is visible
- **DISMISSED**: User dismissed (expires after N days)
- **SNOOZED**: Temporarily hidden (expires at specific date)

**Indexes:**
- `idx_guardrail_lifecycle_id` on `lifecycle_id`
- `idx_guardrail_transition_date` on `transition_date`

### audit_log

Append-only change tracking (never updated or deleted).

```sql
CREATE TABLE audit_log (
    id TEXT(36) PRIMARY KEY,
    table_name TEXT NOT NULL,
    record_id TEXT(36) NOT NULL,
    action TEXT NOT NULL CHECK(action IN (
        'INSERT', 'UPDATE', 'CORRECTION', 'REVERSAL'
    )),
    old_values TEXT,  -- JSON string
    new_values TEXT,  -- JSON string
    user_context TEXT,
    created_at DATETIME NOT NULL
);
```

**Actions:**
- **INSERT**: New record created
- **UPDATE**: Record modified (rare, only for mutable fields)
- **CORRECTION**: Correction record created
- **REVERSAL**: Reversal record created

**Indexes:**
- `idx_audit_log_table_record` on `(table_name, record_id)`
- `idx_audit_log_created_at` on `created_at`

### app_diagnostics_log

Application-level diagnostic events.

```sql
CREATE TABLE app_diagnostics_log (
    id TEXT(36) PRIMARY KEY,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    context TEXT,  -- JSON string
    created_at DATETIME NOT NULL
);
```

**Severity Levels:**
- `INFO`: Informational events
- `WARNING`: Potential issues
- `ERROR`: Error conditions
- `CRITICAL`: Critical failures

### security_catalog

External security catalog (Phase S).

```sql
CREATE TABLE security_catalog (
    id TEXT(36) PRIMARY KEY,
    ticker TEXT(20) NOT NULL,
    name TEXT(200) NOT NULL,
    currency TEXT(3) NOT NULL,
    exchange TEXT(20),
    country TEXT(2),
    type TEXT(50),
    isin TEXT(12),
    cusip TEXT(9),
    figi TEXT(12),
    composite_figi TEXT(12),
    last_synced_at DATETIME,
    created_at DATETIME NOT NULL,
    UNIQUE(ticker, exchange)
);
```

## Entity Relationships

### Core Relationships Diagram

```
┌──────────────┐
│  securities  │
└──────┬───────┘
       │
       ├─────────────────────────────────────┐
       │                                     │
       ▼                                     ▼
┌──────────────┐                    ┌──────────────┐
│     lots     │                    │  disposals   │
└──────┬───────┘                    └──────┬───────┘
       │                                   │
       │                                   │
       │         ┌─────────────────────────┘
       │         │
       ▼         ▼
┌────────────────────────────┐
│ disposal_lot_allocations   │
└────────────────────────────┘

┌──────────────┐
│  securities  │
└──────┬───────┘
       │
       ├──────────────┬──────────────┬──────────────┐
       │              │              │              │
       ▼              ▼              ▼              ▼
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│dividends │   │  prices  │   │fx_rates  │   │cash_     │
│          │   │          │   │          │   │entries   │
└──────────┘   └──────────┘   └──────────┘   └──────────┘
```

### Key Relationships

1. **Security → Lots** (1:N)
   - One security can have many acquisition lots
   - Cascade: RESTRICT (cannot delete security with lots)

2. **Security → Disposals** (1:N)
   - One security can have many disposals
   - Cascade: RESTRICT (cannot delete security with disposals)

3. **Disposal → Disposal Lot Allocations** (1:N)
   - Each disposal allocates from one or more lots
   - Cascade: CASCADE (deleting disposal removes allocations)

4. **Lot → Disposal Lot Allocations** (1:N)
   - Each lot can be allocated to multiple disposals
   - Cascade: RESTRICT (cannot delete lot with allocations)

5. **Security → Dividends** (1:N)
   - One security can have many dividend payments
   - Cascade: RESTRICT

6. **Lot → Dividends** (1:N, optional)
   - Dividends can optionally link to specific lot
   - Cascade: SET NULL (lot deletion preserves dividend)

7. **Lot → Lot Transfer Events** (1:N)
   - Lots can be transferred between schemes
   - Cascade: RESTRICT (cannot delete lot with transfers)

## Data Types and Constraints

### Monetary Values

**Storage Format:** TEXT (Decimal strings)

```python
# Correct
cost_per_share = "123.45"
quantity = "100.00000000"  # 8 decimal places for fractional shares

# Wrong
cost_per_share = 123.45  # Float (precision loss)
quantity = "1.23e2"  # Scientific notation (not allowed)
```

**Rationale:**
- Exact decimal precision (no floating-point errors)
- Portable across systems
- Human-readable in database

### Currency Codes

**Format:** ISO 4217 (3-character codes)

```sql
CHECK(length(currency) = 3)
```

**Examples:** `GBP`, `USD`, `EUR`, `JPY`

### Date Fields

**Storage:** TEXT in ISO 8601 format (`YYYY-MM-DD`)

```python
from datetime import date
acquisition_date = date(2024, 1, 15)  # Stored as "2024-01-15"
```

### DateTime Fields

**Storage:** TEXT in ISO 8601 format (naive UTC)

```python
from datetime import datetime, timezone
created_at = datetime.now(timezone.utc).replace(tzinfo=None)
# Stored as "2024-01-15T10:30:45.123456"
```

### UUID Primary Keys

**Format:** 36-character UUID v4 strings

```python
import uuid
id = str(uuid.uuid4())  # "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

### Boolean Fields

**Storage:** INTEGER (0 or 1)

```sql
is_manual_override BOOLEAN NOT NULL DEFAULT 0
```

SQLite stores booleans as integers; SQLAlchemy maps to Python `bool`.

### JSON Fields

**Storage:** TEXT (JSON-encoded strings)

```python
metadata = json.dumps({"source": "manual", "notes": "..."})
```

Used for:
- `audit_log.old_values` / `new_values`
- `cash_entries.metadata`
- `scenario_snapshots.payload`
- `app_diagnostics_log.context`

## Migration Strategy

### Alembic Migrations

Migrations live in [`equity_tracker/alembic/versions/`](../equity_tracker/alembic/versions/).

**Naming Convention:**
```
001_initial_schema.py
002_security_catalog.py
003_espp_forfeiture.py
...
```

**Migration Structure:**
```python
def upgrade() -> None:
    # Forward migration
    op.create_table(...)
    op.add_column(...)

def downgrade() -> None:
    # Rollback migration
    op.drop_column(...)
    op.drop_table(...)
```

### Migration Execution

**Automatic (on startup):**
```python
from src.db.migration_manager import ensure_migrated
ensure_migrated(engine)
```

**Manual:**
```bash
cd equity_tracker
alembic upgrade head
alembic downgrade -1
```

### Migration Best Practices

1. **Never modify existing migrations** - create new ones
2. **Test both upgrade and downgrade** paths
3. **Include data migrations** when schema changes affect existing data
4. **Document breaking changes** in migration docstrings
5. **Preserve audit trail** - never delete audit log entries

### Schema Versioning

Current schema version tracked in `alembic_version` table:

```sql
SELECT version_num FROM alembic_version;
```

## Beta Research Schema

The experimental beta system uses a **separate database** ([`beta_research.db`](../equity_tracker/src/beta/db/)) with its own schema.

### Key Beta Tables

- `beta_instruments`: Research universe instruments
- `beta_daily_bars`: Historical OHLCV data
- `beta_features`: Computed feature store
- `beta_labels`: Target labels for training
- `beta_hypotheses`: Hypothesis definitions
- `beta_experiments`: Experiment tracking
- `beta_models`: Model registry
- `beta_scores`: Prospective scoring results
- `beta_demo_trades`: Paper trading ledger

**Isolation:**
- No foreign keys to main database
- Independent migration path
- Can be disabled without affecting core system

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Service Layer](./04-SERVICE-LAYER.md) - Business logic using these tables
- [Beta Features](./05-BETA-FEATURES.md) - Beta schema details