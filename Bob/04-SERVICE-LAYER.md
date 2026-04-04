# Service Layer Documentation

**Last Updated:** 2026-04-03

## Table of Contents

1. [Overview](#overview)
2. [Service Architecture](#service-architecture)
3. [Core Services](#core-services)
4. [Strategic Services](#strategic-services)
5. [Integration Services](#integration-services)
6. [Service Patterns](#service-patterns)

## Overview

The service layer contains **40+ business logic services** that implement the core functionality of Equity Tracker. Services are organized by domain and follow consistent patterns for session management, error handling, and data transformation.

### Service Principles

1. **Static Methods**: All service methods are static (no instance state)
2. **Session Management**: Explicit session handling via `AppContext`
3. **Pure Business Logic**: No HTTP concerns, only domain logic
4. **Comprehensive Validation**: Input validation and business rule enforcement
5. **Deterministic**: Reproducible calculations, no hidden state

## Service Architecture

### Service Categories

```
Core Services (Portfolio, Tax, Risk)
    ↓
Strategic Services (Analytics, Planning, Scenarios)
    ↓
Integration Services (Prices, FX, External APIs)
    ↓
Support Services (Audit, Notifications, Diagnostics)
```

### Service Dependencies

Services can depend on:
- **Repositories**: For database access
- **Other Services**: For cross-domain operations
- **Core Domain Logic**: Tax engines, lot matching algorithms
- **External APIs**: Price/FX data providers

## Core Services

### PortfolioService

**Location**: [`src/services/portfolio_service.py`](../equity_tracker/src/services/portfolio_service.py:1)

**Responsibilities:**
- Portfolio aggregation and summary
- Lot creation and management
- Disposal simulation and execution
- Position tracking and valuation

**Key Methods:**

```python
class PortfolioService:
    @staticmethod
    def get_summary(
        as_of: date | None = None,
        hide_values: bool = False
    ) -> PortfolioSummary:
        """
        Aggregate portfolio holdings with current valuations.
        
        Returns:
        - Total market value, cost basis, unrealized gains
        - Sellable vs locked vs forfeitable breakdown
        - Per-security position details
        - Concentration metrics
        """
    
    @staticmethod
    def create_lot(
        security_id: str,
        scheme_type: str,
        acquisition_date: date,
        quantity: Decimal,
        cost_per_share_gbp: Decimal,
        # ... other fields
    ) -> CreateLotResult:
        """
        Create a new lot acquisition record.
        
        Validates:
        - Security exists
        - Scheme type is valid
        - Quantity precision matches security
        - Dates are logical (vest ≤ acquisition ≤ lock expiry)
        """
    
    @staticmethod
    def simulate_disposal(
        security_id: str,
        quantity: Decimal,
        price_per_share_gbp: Decimal,
        disposal_date: date,
        # ... tax context
    ) -> DisposalSimulation:
        """
        Simulate a disposal without committing.
        
        Returns:
        - FIFO lot allocations
        - Cost basis and capital gains
        - Employment tax estimates
        - CGT estimates
        - Broker fee estimates
        - Forfeiture warnings
        """
```

**Dependencies:**
- `LotRepository`, `DisposalRepository`, `SecurityRepository`
- `FIFOEngine` for lot allocation
- `TaxPlanService` for tax estimates
- `BrokerFeeService` for fee estimates

### TaxPlanService

**Location**: [`src/services/tax_plan_service.py`](../equity_tracker/src/services/tax_plan_service.py:1)

**Responsibilities:**
- UK tax year planning
- Income tax, NI, student loan calculations
- CGT scenario modeling
- Tax assumption management

**Key Methods:**

```python
class TaxPlanService:
    @staticmethod
    def get_tax_plan(
        tax_year: str,
        settings: AppSettings | None = None
    ) -> TaxPlan:
        """
        Calculate comprehensive tax position for a UK tax year.
        
        Returns:
        - Income tax (IT) breakdown
        - National Insurance (NI) contributions
        - Student loan repayments
        - CGT estimates on disposals
        - Assumption quality indicators
        """
    
    @staticmethod
    def project_disposal_tax(
        disposal_date: date,
        capital_gain_gbp: Decimal,
        employment_income_gbp: Decimal,
        settings: AppSettings
    ) -> TaxProjection:
        """
        Project tax impact of a hypothetical disposal.
        
        Considers:
        - Current tax year position
        - Marginal tax rates
        - CGT allowance utilization
        - ANI (Adjusted Net Income) interactions
        """
```

**Dependencies:**
- Tax engine modules: `income_tax`, `national_insurance`, `capital_gains`, `student_loan`
- `DisposalRepository` for realized gains
- `EmploymentTaxEventRepository` for employment income

### RiskService

**Location**: [`src/services/risk_service.py`](../equity_tracker/src/services/risk_service.py:1)

**Responsibilities:**
- Concentration risk analysis
- Liquidity breakdown
- Optionality timeline modeling
- Guardrail breach detection

**Key Methods:**

```python
class RiskService:
    @staticmethod
    def get_risk_summary(
        as_of: date | None = None,
        settings: AppSettings | None = None
    ) -> RiskSummary:
        """
        Comprehensive risk assessment.
        
        Returns:
        - Concentration (top holding, employer exposure)
        - Liquidity (sellable, locked, forfeitable)
        - Wrapper allocation (ISA, brokerage)
        - Optionality timeline (now, 6m, 1y, 3y, 5y)
        - Guardrail breach alerts
        - Forfeiture heatmap
        """
    
    @staticmethod
    def calculate_optionality_index(
        timeline_bands: list[RiskOptionalityTimelineBand],
        weights: dict[str, Decimal]
    ) -> Decimal:
        """
        Calculate weighted optionality score.
        
        Weights:
        - Now: 40%
        - 6 months: 25%
        - 1 year: 20%
        - 3 years: 10%
        - 5 years: 5%
        """
```

**Dependencies:**
- `PortfolioService` for position data
- `ExposureService` for concentration metrics
- `AlertLifecycleService` for guardrail state

### PriceService

**Location**: [`src/services/price_service.py`](../equity_tracker/src/services/price_service.py:1)

**Responsibilities:**
- Price data management
- Multi-source price fetching
- Staleness detection
- Historical price queries

**Key Methods:**

```python
class PriceService:
    @staticmethod
    def get_latest_price(
        security_id: str,
        as_of: date | None = None
    ) -> PriceQuote | None:
        """
        Get latest price on or before as_of date.
        
        Returns:
        - Price in GBP
        - Price date
        - Source (MANUAL, YFINANCE, TWELVE_DATA, etc.)
        - Staleness indicator
        """
    
    @staticmethod
    def refresh_prices(
        security_ids: list[str],
        source: str = "AUTO"
    ) -> RefreshResult:
        """
        Fetch fresh prices from external sources.
        
        Sources:
        - AUTO: Try Twelve Data, fallback to yfinance
        - TWELVE_DATA: Twelve Data API
        - YFINANCE: Yahoo Finance
        - IBKR: Interactive Brokers (if configured)
        """
```

**Dependencies:**
- `TwelveDataPriceService` for Twelve Data API
- `IBKRPriceService` for IBKR integration
- `yfinance` library for Yahoo Finance
- `PriceRepository` for persistence

### FxService

**Location**: [`src/services/fx_service.py`](../equity_tracker/src/services/fx_service.py:1)

**Responsibilities:**
- Currency conversion
- FX rate management
- Multi-source rate fetching
- Graph-based rate resolution

**Key Methods:**

```python
class FxService:
    @staticmethod
    def get_rate(
        from_currency: str,
        to_currency: str,
        as_of: date | None = None
    ) -> FxQuote:
        """
        Get FX rate with automatic path resolution.
        
        Resolution strategy:
        1. Direct rate (USD→GBP)
        2. Inverse rate (GBP→USD inverted)
        3. Graph path (USD→EUR→GBP)
        4. Live fetch if not found
        """
    
    @staticmethod
    def peek_live_rate(
        from_currency: str,
        to_currency: str
    ) -> FxQuote | None:
        """
        Fetch live rate without persisting.
        
        Used for:
        - Real-time conversion previews
        - Staleness checks
        - Manual entry validation
        """
```

**Dependencies:**
- `TwelveDataPriceService` for Twelve Data FX
- `yfinance` for Yahoo Finance FX
- `FxRateRepository` for persistence

## Strategic Services

### ScenarioService

**Location**: [`src/services/scenario_service.py`](../equity_tracker/src/services/scenario_service.py:1)

**Responsibilities:**
- Multi-leg disposal scenarios
- Sequential vs independent execution
- Scenario persistence and comparison

**Key Methods:**

```python
class ScenarioService:
    @staticmethod
    def run_scenario(
        name: str,
        legs: list[ScenarioLeg],
        execution_mode: str = "INDEPENDENT",
        as_of: date | None = None
    ) -> ScenarioResult:
        """
        Execute multi-leg disposal scenario.
        
        Execution modes:
        - INDEPENDENT: Each leg uses original portfolio state
        - SEQUENTIAL: Legs execute in order, updating state
        
        Returns:
        - Per-leg results (proceeds, gains, taxes)
        - Aggregated totals
        - Forfeiture warnings
        - Shortfall alerts
        """
```

### AnalyticsService

**Location**: [`src/services/analytics_service.py`](../equity_tracker/src/services/analytics_service.py:1)

**Responsibilities:**
- Cross-domain analytics widgets
- Decision-critical metric prioritization
- Historical trend analysis

**Key Widgets:**
- Liquidity breakdown
- Concentration risk
- Forfeiture timeline
- Tax position summary
- Stress testing
- FX attribution
- Valuation history

### CapitalStackService

**Location**: [`src/services/capital_stack_service.py`](../equity_tracker/src/services/capital_stack_service.py:1)

**Responsibilities:**
- Deployable capital calculation
- Deduction waterfall (locked → forfeitable → tax → fees)
- Cash integration

**Key Formula:**

```
Gross Market Value
  - Locked Value
  - Forfeitable Value
  = Hypothetical Liquid Value
  - Employment Tax Estimate
  - CGT Estimate
  - Broker Fees Estimate
  = Net Deployable from Portfolio
  + Deployable Cash (GBP equivalent)
  = Total Deployable Capital
```

### SellPlanService

**Location**: [`src/services/sell_plan_service.py`](../equity_tracker/src/services/sell_plan_service.py:1)

**Responsibilities:**
- Staged disposal planning
- Tranche scheduling
- Adherence tracking
- IBKR export generation

**Execution Methods:**
- **CALENDAR**: Fixed dates
- **THRESHOLD**: Price-triggered
- **LIMIT_LADDER**: Limit order ladder
- **TWAP/VWAP**: Time/volume-weighted

### AllocationPlannerService

**Location**: [`src/services/allocation_planner_service.py`](../equity_tracker/src/services/allocation_planner_service.py:1)

**Responsibilities:**
- Trim-and-redeploy planning
- Concentration reduction
- Before/after impact analysis

**Workflow:**
1. Identify overweight position
2. Calculate trim quantity (whole shares)
3. Estimate tax/fee drag
4. Allocate net proceeds across candidates
5. Show before/after metrics

## Integration Services

### TwelveDataPriceService

**Location**: [`src/services/twelve_data_price_service.py`](../equity_tracker/src/services/twelve_data_price_service.py:1)

**Responsibilities:**
- Twelve Data API integration
- Credit usage tracking
- Batch quote fetching
- Historical data retrieval

**Rate Limiting:**
- 55 credits per minute (configurable)
- Automatic credit tracking
- Graceful degradation on quota exhaustion

### TwelveDataStreamService

**Location**: [`src/services/twelve_data_stream_service.py`](../equity_tracker/src/services/twelve_data_stream_service.py:1)

**Responsibilities:**
- Real-time price streaming
- WebSocket connection management
- Symbol eligibility tracking
- Automatic rebalancing

**Features:**
- Max 12 concurrent streams (configurable)
- Rejection cooldown (24 hours)
- Automatic reconnection
- Priority-based symbol selection

### IBKRPriceService

**Location**: [`src/services/ibkr_price_service.py`](../equity_tracker/src/services/ibkr_price_service.py:1)

**Responsibilities:**
- Interactive Brokers integration
- Native currency price ingestion
- FX rate derivation

**Data Source:**
- Reads from IBKR Gateway database
- Extracts latest snapshots
- Derives FX rates from price pairs

### SheetsPriceService / SheetsFxService

**Location**: [`src/services/sheets_price_service.py`](../equity_tracker/src/services/sheets_price_service.py:1), [`src/services/sheets_fx_service.py`](../equity_tracker/src/services/sheets_fx_service.py:1)

**Responsibilities:**
- Google Sheets integration
- Manual price/FX entry workflow
- Bulk data import

**Use Case:**
- Manual price entry for illiquid securities
- Custom FX rates
- Bulk historical data import

## Service Patterns

### Session Management Pattern

```python
class ExampleService:
    @staticmethod
    def read_operation() -> Result:
        with AppContext.read_session() as sess:
            # Read-only queries
            repo = SomeRepository(sess)
            data = repo.query(...)
            return process(data)
    
    @staticmethod
    def write_operation() -> Result:
        with AppContext.write_session() as sess:
            # Write operations
            repo = SomeRepository(sess)
            record = repo.create(...)
            
            # Audit logging
            audit_repo = AuditLogRepository(sess)
            audit_repo.log_insert("table", record.id, ...)
            
            # Transaction commits automatically
            return Result(id=record.id)
```

### Error Handling Pattern

```python
@staticmethod
def risky_operation(param: str) -> Result:
    try:
        with AppContext.write_session() as sess:
            # Business logic
            if not valid(param):
                raise ValueError("Invalid parameter")
            
            result = perform_operation(param)
            return result
    
    except ValueError as e:
        # Business logic error - re-raise
        raise
    
    except IntegrityError as e:
        # Database constraint violation
        raise ValueError(f"Duplicate record: {e}")
    
    except Exception as e:
        # Unexpected error - log and re-raise
        logger.error(f"Unexpected error: {e}")
        raise
```

### Validation Pattern

```python
@staticmethod
def create_with_validation(data: dict) -> Result:
    # Input validation
    if not data.get("required_field"):
        raise ValueError("required_field is missing")
    
    # Business rule validation
    with AppContext.read_session() as sess:
        existing = repo.find_by_key(data["key"])
        if existing:
            raise ValueError("Duplicate key")
    
    # Proceed with creation
    with AppContext.write_session() as sess:
        record = repo.create(data)
        return Result(id=record.id)
```

### Aggregation Pattern

```python
@staticmethod
def get_aggregated_summary() -> Summary:
    with AppContext.read_session() as sess:
        # Fetch raw data
        lots = lot_repo.list_active()
        prices = price_repo.get_latest_batch(security_ids)
        
        # Aggregate in memory
        totals = defaultdict(Decimal)
        for lot in lots:
            price = prices.get(lot.security_id)
            if price:
                value = lot.quantity_remaining * price.close_price_gbp
                totals["market_value"] += value
                totals["cost_basis"] += lot.cost_basis_gbp
        
        return Summary(
            market_value=totals["market_value"],
            cost_basis=totals["cost_basis"],
            unrealized_gain=totals["market_value"] - totals["cost_basis"]
        )
```

### Caching Pattern

```python
_CACHE: dict[str, tuple[datetime, Any]] = {}
_CACHE_TTL = timedelta(minutes=5)

@staticmethod
def get_with_cache(key: str) -> Result:
    now = datetime.now(timezone.utc)
    
    # Check cache
    if key in _CACHE:
        cached_at, cached_value = _CACHE[key]
        if now - cached_at < _CACHE_TTL:
            return cached_value
    
    # Fetch fresh data
    with AppContext.read_session() as sess:
        value = expensive_operation(key)
    
    # Update cache
    _CACHE[key] = (now, value)
    return value
```

## Service Testing

### Unit Testing Pattern

```python
def test_portfolio_summary(test_db):
    # Arrange
    with AppContext.write_session() as sess:
        security = create_test_security(sess)
        lot = create_test_lot(sess, security.id)
        price = create_test_price(sess, security.id)
    
    # Act
    summary = PortfolioService.get_summary()
    
    # Assert
    assert summary.total_market_value_gbp > 0
    assert len(summary.securities) == 1
```

### Integration Testing Pattern

```python
def test_disposal_workflow(test_db):
    # Setup
    security_id = setup_test_portfolio()
    
    # Simulate
    simulation = PortfolioService.simulate_disposal(
        security_id=security_id,
        quantity=Decimal("50"),
        price_per_share_gbp=Decimal("150")
    )
    
    # Execute
    disposal_id = PortfolioService.execute_disposal(simulation)
    
    # Verify
    with AppContext.read_session() as sess:
        disposal = DisposalRepository(sess).get_by_id(disposal_id)
        assert disposal is not None
        assert disposal.quantity == Decimal("50")
```

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Database Schema](./02-DATABASE-SCHEMA.md) - Data models
- [API Reference](./03-API-REFERENCE.md) - HTTP endpoints
- [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md) - Development guide