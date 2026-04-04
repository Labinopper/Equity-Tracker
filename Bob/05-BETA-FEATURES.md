# Beta Features Documentation

**Last Updated:** 2026-04-03

## Table of Contents

1. [Overview](#overview)
2. [Beta Architecture](#beta-architecture)
3. [Isolation Strategy](#isolation-strategy)
4. [Beta Components](#beta-components)
5. [Operating Modes](#operating-modes)
6. [Future Roadmap](#future-roadmap)

## Overview

The **Experimental Predictive Paper-Trading Beta** is a segregated research system designed to explore predictive signals and paper-trading strategies. It operates **completely independently** from the deterministic core product.

### Key Characteristics

- **Paper-Only**: No real broker execution
- **Isolated**: Separate database, runtime, and services
- **Disableable**: Can be turned off without affecting core system
- **Internal-Only**: Not exposed to normal users
- **Evidence-Based**: Preserves audit trail for AI/human review

### Hard Boundary Rules

1. ❌ No broker execution
2. ❌ No auto-trading
3. ❌ No live buy/sell instructions
4. ❌ No blending of beta language into deterministic pages
5. ✅ Separate storage and runtime
6. ✅ Feature flags and kill switches
7. ✅ Complete disablement capability

## Beta Architecture

### System Separation

```
┌─────────────────────────────────────────────────────────────┐
│                  Deterministic Core System                   │
│  (Portfolio, Tax, Risk, Analytics - Production)             │
│                                                              │
│  Database: portfolio.db                                      │
│  Runtime: Main FastAPI process                               │
└─────────────────────────────────────────────────────────────┘

                         ║ ISOLATION BOUNDARY ║

┌─────────────────────────────────────────────────────────────┐
│              Experimental Beta Research System               │
│  (Hypothesis Engine, Paper Trading - Internal Only)         │
│                                                              │
│  Database: beta_research.db (separate)                       │
│  Runtime: Supervisor process (non-blocking)                  │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
equity_tracker/src/beta/
├── __init__.py
├── context.py              # Beta-specific context manager
├── core_access.py          # Read-only access to core data
├── paths.py                # Beta file paths
├── runtime_manager.py      # Beta lifecycle management
├── settings.py             # Beta configuration
├── state.py                # Beta runtime state
├── supervisor_process.py   # Background supervisor
├── config/                 # Beta configuration files
│   ├── execution_event_triggers.json
│   ├── execution_hypothesis_definitions.json
│   ├── hypothesis_families.json
│   ├── hypothesis_seed_definitions.json
│   ├── hypothesis_template_specs.json
│   └── intraday_focus_symbols.json
├── db/                     # Beta database layer
│   ├── __init__.py
│   ├── bootstrap.py
│   ├── engine.py
│   └── models.py
└── services/               # Beta business logic
    ├── allocation_service.py
    ├── corpus_service.py
    ├── evaluation_service.py
    ├── execution_*.py      # Execution-focused services
    ├── feature_service.py
    ├── hypothesis_*.py     # Hypothesis engine services
    ├── intraday_*.py       # Intraday trading services
    └── observation_service.py
```

## Isolation Strategy

### Database Isolation

**Separate Database File:**
```python
# Core database
core_db_path = Path("portfolio.db")

# Beta database (completely separate)
beta_db_path = Path("beta_research.db")
```

**No Foreign Keys:**
- Beta tables never reference core tables
- Core tables never reference beta tables
- Data copied via read-only access layer

### Runtime Isolation

**Separate Process:**
```python
# Main process: FastAPI server
# Beta process: Supervisor (background)

# Beta startup failure does NOT block core
try:
    initialize_beta_runtime()
except Exception as e:
    logger.warning(f"Beta startup failed: {e}")
    # Core system continues normally
```

**Non-Blocking Startup:**
- Core system starts immediately
- Beta initializes asynchronously
- Beta failure logged but ignored

### Feature Flag Isolation

**Operating Modes:**
```python
class BetaOperatingMode(Enum):
    OFF = "OFF"                    # Completely disabled
    OBSERVE_ONLY = "OBSERVE_ONLY"  # Data collection only
    SHADOW_ONLY = "SHADOW_ONLY"    # Scoring without trades
    DEMO_NO_LEARN = "DEMO_NO_LEARN" # Paper trades, no learning
    FULL_INTERNAL_BETA = "FULL_INTERNAL_BETA"  # Full features
```

**Kill Switch:**
```python
# Environment variable
EQUITY_BETA_MODE=OFF

# Disables all beta functionality
# Core system unaffected
```

## Beta Components

### Beta Database Schema

**Key Tables:**

```sql
-- Research universe
CREATE TABLE beta_instruments (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    exchange TEXT,
    sector TEXT,
    market_cap_usd TEXT,
    is_active BOOLEAN DEFAULT 1
);

-- Historical data
CREATE TABLE beta_daily_bars (
    id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    bar_date DATE NOT NULL,
    open_price TEXT NOT NULL,
    high_price TEXT NOT NULL,
    low_price TEXT NOT NULL,
    close_price TEXT NOT NULL,
    volume TEXT NOT NULL,
    UNIQUE(instrument_id, bar_date)
);

-- Feature store
CREATE TABLE beta_features (
    id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    feature_date DATE NOT NULL,
    feature_name TEXT NOT NULL,
    feature_value TEXT NOT NULL,
    feature_version TEXT NOT NULL
);

-- Hypothesis registry
CREATE TABLE beta_hypotheses (
    id TEXT PRIMARY KEY,
    hypothesis_family TEXT NOT NULL,
    hypothesis_name TEXT NOT NULL,
    template_spec TEXT NOT NULL,  -- JSON
    status TEXT NOT NULL,  -- ACTIVE, SUSPENDED, ARCHIVED
    created_at DATETIME NOT NULL
);

-- Paper trading ledger
CREATE TABLE beta_demo_trades (
    id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    action TEXT NOT NULL,  -- BUY, SELL
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    hypothesis_id TEXT,
    is_immutable BOOLEAN DEFAULT 1
);
```

### Beta Services

#### Hypothesis Engine

**Location**: [`src/beta/services/hypothesis_service.py`](../equity_tracker/src/beta/services/hypothesis_service.py:1)

**Purpose**: Manage hypothesis definitions, templates, and lifecycle.

**Key Concepts:**
- **Hypothesis Family**: Category of signals (momentum, mean reversion, etc.)
- **Hypothesis Template**: Parameterized signal definition
- **Hypothesis Instance**: Specific parameter combination
- **Hypothesis Status**: ACTIVE, SUSPENDED, ARCHIVED

#### Feature Service

**Location**: [`src/beta/services/feature_service.py`](../equity_tracker/src/beta/services/feature_service.py:1)

**Purpose**: Compute and store reusable features for hypothesis testing.

**Feature Types:**
- Price-based: Returns, volatility, momentum
- Volume-based: Volume trends, liquidity
- Technical: Moving averages, RSI, MACD
- Fundamental: P/E, market cap, sector

#### Execution Services

**Location**: [`src/beta/services/execution_*.py`](../equity_tracker/src/beta/services/)

**Purpose**: Intraday execution logic and paper trading.

**Components:**
- `execution_hypothesis_service.py`: Execution-focused hypotheses
- `execution_signal_service.py`: Signal generation
- `execution_outcome_service.py`: Trade outcome tracking
- `intraday_simulated_trade_service.py`: Paper trade execution

#### Evaluation Service

**Location**: [`src/beta/services/evaluation_service.py`](../equity_tracker/src/beta/services/evaluation_service.py:1)

**Purpose**: Backtest hypotheses and evaluate performance.

**Metrics:**
- Win rate
- Sharpe ratio
- Maximum drawdown
- Benchmark-relative returns
- Calibration scores

### Beta Runtime Manager

**Location**: [`src/beta/runtime_manager.py`](../equity_tracker/src/beta/runtime_manager.py:1)

**Responsibilities:**
- Initialize beta database
- Start supervisor process
- Manage operating mode
- Handle graceful shutdown

**Lifecycle:**

```python
# Startup (non-blocking)
async def initialize_beta_runtime():
    try:
        # Check operating mode
        if BetaSettings.operating_mode() == "OFF":
            return
        
        # Initialize database
        beta_engine = BetaEngine.create()
        BetaContext.initialize(beta_engine)
        
        # Start supervisor (background)
        if BetaSettings.operating_mode() in ["FULL_INTERNAL_BETA"]:
            start_supervisor_process()
    
    except Exception as e:
        logger.warning(f"Beta initialization failed: {e}")
        # Core system continues

# Shutdown
async def shutdown_beta_runtime():
    try:
        stop_supervisor_process()
        BetaContext.close()
    except Exception as e:
        logger.error(f"Beta shutdown error: {e}")
```

### Core Access Layer

**Location**: [`src/beta/core_access.py`](../equity_tracker/src/beta/core_access.py:1)

**Purpose**: Provide **read-only** access to core portfolio data for beta research.

**Allowed Operations:**
- Read portfolio positions
- Read historical prices
- Read security metadata

**Forbidden Operations:**
- ❌ Modify core database
- ❌ Create lots or disposals
- ❌ Update prices or FX rates

**Implementation:**

```python
class CoreAccess:
    @staticmethod
    def get_portfolio_snapshot(as_of: date) -> dict:
        """
        Read-only snapshot of portfolio state.
        
        Used for:
        - Backtesting with historical positions
        - Paper trading context
        - Performance attribution
        """
        with AppContext.read_session() as sess:
            # Read-only queries
            lots = LotRepository(sess).list_active(as_of)
            prices = PriceRepository(sess).get_latest_batch(...)
            return build_snapshot(lots, prices)
```

## Operating Modes

### OFF

**Behavior:**
- Beta completely disabled
- No database initialization
- No supervisor process
- No API endpoints exposed

**Use Case:**
- Production deployment without beta
- Testing core system in isolation

### OBSERVE_ONLY

**Behavior:**
- Data collection enabled
- No hypothesis testing
- No paper trading
- No signal generation

**Use Case:**
- Building historical corpus
- Collecting features for future research

### SHADOW_ONLY

**Behavior:**
- Hypothesis testing enabled
- Signal generation enabled
- No paper trades executed
- Scoring recorded for analysis

**Use Case:**
- Evaluating hypothesis performance
- Calibrating signals
- Pre-production testing

### DEMO_NO_LEARN

**Behavior:**
- Paper trades executed
- No hypothesis learning
- Fixed hypothesis parameters
- Immutable trade ledger

**Use Case:**
- Demonstrating paper trading
- Testing execution logic
- Validating trade mechanics

### FULL_INTERNAL_BETA

**Behavior:**
- All features enabled
- Hypothesis learning active
- Paper trading active
- Full research workflow

**Use Case:**
- Internal research and development
- Signal discovery
- Strategy evaluation

## Future Roadmap

### Planned Beta Features (from docs/todo.md)

| Task | Description | Status |
|------|-------------|--------|
| T92 | Beta runtime settings and kill switches | Planned |
| T93 | Separate beta database and migration path | Planned |
| T94 | Supervisor process with non-blocking startup | Planned |
| T95 | Reference domain (instruments, sectors, benchmarks) | Planned |
| T96 | US+UK daily corpus ingestion | Planned |
| T97 | Corporate actions and event calendar | Planned |
| T98 | News ingestion pipeline | Planned |
| T99 | Feature store and lineage | Planned |
| T100 | Label store and target materialization | Planned |
| T101 | Dataset/hypothesis/experiment/model registries | Planned |
| T102 | Shadow scoring pipeline | Planned |
| T103 | Potential-signal tracking and AI-audit evidence | Planned |
| T104 | Recommendation gating and paper ledger | Planned |
| T105 | Evaluation summaries and replay bundles | Planned |
| T106 | Beta admin controls and health views | Planned |

### Beta Exit Criteria

Before beta can be considered production-ready:

1. ✅ Core system operates normally with beta disabled
2. ✅ Beta startup failure does not block core
3. ✅ Beta storage is separate from portfolio database
4. ✅ Operating modes can be toggled independently
5. ✅ No beta pages exposed outside internal boundary
6. ⏳ Comprehensive backtesting results available
7. ⏳ Signal calibration meets quality thresholds
8. ⏳ Paper trading demonstrates consistent edge
9. ⏳ AI-audit evidence packs are complete
10. ⏳ Human review validates signal claims

## Beta Documentation

### Strategic Documentation

- [`docs/paper_trading_beta/PAPER_TRADING_BETA_STRATEGY.md`](../docs/paper_trading_beta/)
- [`docs/paper_trading_beta/PAPER_TRADING_BETA_RUNTIME_ARCHITECTURE.md`](../docs/paper_trading_beta/)
- [`docs/paper_trading_beta/PAPER_TRADING_BETA_TECHNICAL_IMPLEMENTATION_PLAN.md`](../docs/paper_trading_beta/)
- [`docs/paper_trading_beta/PAPER_TRADING_BETA_DATABASE_SCHEMA.md`](../docs/paper_trading_beta/)

### Implementation Status

**Current State (2026-04-03):**
- Planning complete
- Technical design finalized
- Implementation not yet started
- Not live
- Not available to users

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Service Layer](./04-SERVICE-LAYER.md) - Core vs beta services
- [Deployment Guide](./06-DEPLOYMENT-GUIDE.md) - Beta configuration
- [Strategic Documentation](../docs/STRATEGIC_DOCUMENTATION.md) - Beta domain definition