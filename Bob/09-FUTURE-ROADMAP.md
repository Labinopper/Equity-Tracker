# Future Roadmap

**Last Updated:** 2026-04-03
**Status**: Recommendations based on comprehensive project review

## Table of Contents

1. [Overview](#overview)
2. [Core Objective Alignment](#core-objective-alignment)
3. [Recommendation Engine (Priority #1)](#recommendation-engine-priority-1)
4. [Bug Fixes & Technical Debt](#bug-fixes--technical-debt)
5. [Code Quality Improvements](#code-quality-improvements)
6. [Feature Enhancements](#feature-enhancements)
7. [Performance Optimizations](#performance-optimizations)
8. [Security Hardening](#security-hardening)
9. [Developer Experience](#developer-experience)
10. [Long-Term Strategic Items](#long-term-strategic-items)

## Overview

This roadmap identifies opportunities to enhance the Equity Tracker project based on a comprehensive technical review. Items are categorized by type and prioritized by impact and effort.

**Core Mission**: Enable users to manage their equity compensation and investments **without needing deep financial expertise**—the system should proactively recommend what to do and when, handling all complexity in the background.

**Priority Levels:**
- ⭐ **Strategic**: Directly advances core mission (effortless guidance)
- 🔴 **Critical**: Should be addressed soon
- 🟡 **High**: Important but not urgent
- 🟢 **Medium**: Nice to have
- 🔵 **Low**: Future consideration

## Core Objective Alignment

The project currently excels at **Objective #1 (Know What You Own)** with comprehensive visibility into holdings, sellability, and constraints. It partially addresses **Objective #2 (Protect Your Finances)** through alerts and guardrails. However, **Objective #3 (Clear Next Steps)** is the critical gap—users can simulate scenarios but the system doesn't proactively say "You should do X because Y."

### Current State vs. Desired State

| Objective | Current State | Desired State | Gap |
|-----------|---------------|---------------|-----|
| **#1: Know What You Own** | ✅ Excellent - Portfolio, Risk, Analytics pages provide comprehensive visibility | Continue maintaining | Minor enhancements only |
| **#2: Protect Your Finances** | 🟡 Good - Alerts exist but require user interpretation | Proactive warnings with recommended actions | Need actionable guidance |
| **#3: Clear Next Steps** | ❌ Weak - Simulation tools exist but no recommendations | "Do this next" dashboard with prioritized actions | **Critical gap** |

### Strategic Priority

The **#1 strategic priority** is building a **Recommendation Engine** that transforms the system from a "show me data" tool into a "tell me what to do" advisor. This is the key to achieving the ultimate vision: users can manage finances confidently by following clear, trustworthy recommendations without needing to understand every detail.

## Recommendation Engine (Priority #1)

### ⭐ RE-1: Unified Recommendation Dashboard

**Description**: Central "What Should I Do Next?" page that prioritizes and explains all recommended actions.

**Core Features**:

1. **Action Priority Queue**
   ```
   🔴 URGENT (Next 7 Days)
   - Sell 25 AAPL shares before lock expiry (3 days)
   - Transfer £5,000 to ISA before tax year end (5 days)
   
   🟡 IMPORTANT (Next 30 Days)
   - Rebalance: Reduce MSFT by 50 shares (concentration risk)
   - Harvest tax loss: Sell 100 META shares (save £200 CGT)
   
   🟢 OPTIMIZE (Next 90 Days)
   - Move £10,000 from brokerage to ISA (save £400/year tax)
   - Consider selling RSU vest to diversify (40% employer exposure)
   ```

2. **Recommendation Types**
   - **Forfeiture Prevention**: "Sell X shares by DATE to avoid forfeiture"
   - **Tax Optimization**: "Harvest loss / Use CGT allowance / Transfer to ISA"
   - **Risk Reduction**: "Reduce concentration / Diversify employer exposure"
   - **Liquidity Management**: "Lock expiring soon / Vest approaching"
   - **Deadline Reminders**: "Tax year end / Dividend ex-date / Sell plan tranche"

3. **Recommendation Scoring**
   ```python
   class RecommendationScore:
       urgency: int        # Days until deadline (lower = higher priority)
       impact: Decimal     # Financial impact (£ saved or at risk)
       confidence: str     # HIGH/MEDIUM/LOW based on data quality
       effort: str         # EASY/MODERATE/COMPLEX
   ```

4. **One-Click Actions**
   - "Execute This Recommendation" → Pre-fills disposal/transfer form
   - "Simulate Impact" → Shows before/after comparison
   - "Snooze for 7 Days" → Temporarily hide
   - "Dismiss with Reason" → Mark as not applicable

**Implementation**:
```python
# src/services/recommendation_service.py
class RecommendationService:
    @staticmethod
    def get_recommendations(
        as_of: date | None = None,
        settings: AppSettings | None = None
    ) -> list[Recommendation]:
        """
        Generate prioritized recommendations.
        
        Process:
        1. Scan for forfeiture risks
        2. Check concentration thresholds
        3. Identify tax optimization opportunities
        4. Evaluate lock expiries
        5. Check sell plan adherence
        6. Score and prioritize all recommendations
        """
        recommendations = []
        
        # Forfeiture risks (highest priority)
        recommendations.extend(_check_forfeiture_risks())
        
        # Concentration risks
        recommendations.extend(_check_concentration())
        
        # Tax optimization
        recommendations.extend(_check_tax_opportunities())
        
        # Lock expiries
        recommendations.extend(_check_lock_expiries())
        
        # Score and sort
        return sorted(recommendations, key=lambda r: r.priority_score)
```

**Effort**: Very Large (4-6 weeks)

**Impact**: ⭐ **Transformational** - This is the key feature that enables the "don't think, just follow recommendations" vision.

---

### ⭐ RE-2: Smart Disposal Advisor

**Description**: When user wants to sell, system recommends optimal approach.

**Workflow**:
```
User: "I need £10,000 cash"

System Analyzes:
- Which securities to sell (tax efficiency, concentration)
- Which lots to use (FIFO vs UK matching)
- Timing (lock periods, tax year boundaries)
- Method (immediate vs staged via sell plan)

System Recommends:
"Sell 50 AAPL shares from ISA (tax-free) + 25 MSFT shares
from brokerage (uses CGT allowance). Execute in 2 tranches
over 2 weeks to reduce concentration gradually.
Net proceeds: £10,200 after fees."

[Execute This Plan] [Modify] [See Alternatives]
```

**Key Intelligence**:
- Prefer ISA disposals (tax-free)
- Use CGT allowance efficiently
- Minimize employment tax triggers
- Respect concentration limits
- Consider lock periods and forfeiture

**Effort**: Large (3-4 weeks)

**Impact**: ⭐ **High** - Makes disposal decisions effortless

---

### ⭐ RE-3: Proactive Tax Optimization Alerts

**Description**: System continuously monitors for tax-saving opportunities.

**Opportunities Detected**:

1. **CGT Allowance Utilization**
   ```
   "You have £2,500 unused CGT allowance this tax year.
   Consider selling 15 META shares (£2,400 gain) to use it.
   This costs you nothing in tax but resets your cost basis."
   ```

2. **Tax Loss Harvesting**
   ```
   "META is down £3,000 from your cost basis. Sell now to
   realize the loss (offset future gains), then rebuy after
   30 days to avoid wash sale rules. Tax saving: £600."
   ```

3. **ISA Transfer Timing**
   ```
   "You have £15,000 ISA allowance remaining this tax year.
   Transfer AAPL shares from brokerage to ISA before April 5
   to save £800/year in dividend tax."
   ```

4. **Vest Timing Optimization**
   ```
   "Your RSU vest next month will push you into higher tax band.
   Consider increasing pension contribution by £2,000 to stay
   in basic rate. Tax saving: £400."
   ```

**Effort**: Large (2-3 weeks)

**Impact**: ⭐ **High** - Automatic tax savings without user effort

---

### ⭐ RE-4: Intelligent Rebalancing Advisor

**Description**: Proactive recommendations to maintain healthy portfolio structure.

**Triggers**:
- Concentration exceeds threshold (e.g., >50% in one stock)
- Employer exposure too high (e.g., >40%)
- Sector imbalance
- Currency exposure skewed
- ISA vs brokerage allocation suboptimal

**Recommendation Format**:
```
🔴 CONCENTRATION RISK: AAPL is 55% of portfolio

Recommended Action:
Sell 100 AAPL shares (reduce to 45%) and redeploy:
- 50 shares → VOO (S&P 500 ETF) - diversify US exposure
- 30 shares → VWRL (World ETF) - add international
- 20 shares → Cash - increase liquidity

Impact:
- Concentration: 55% → 45% ✓
- Employer exposure: 40% → 35% ✓
- Tax cost: £450 CGT (within allowance)
- Net proceeds: £14,550

[Execute Plan] [Customize] [Dismiss]
```

**Effort**: Large (3-4 weeks)

**Impact**: ⭐ **High** - Maintains healthy portfolio automatically

---

### ⭐ RE-5: Weekly Review Automation

**Description**: Enhance existing weekly review with AI-generated insights and recommendations.

**Current State**: Manual checklist workflow

**Enhanced State**:
```
📊 WEEKLY REVIEW (Week of April 1, 2024)

🎯 TOP 3 ACTIONS THIS WEEK:
1. ⚠️ Sell 25 AAPL before lock expiry (3 days) - £3,750 at risk
2. 💰 Harvest £2,000 tax loss from META - save £400 CGT
3. 📈 Rebalance: Reduce MSFT concentration from 48% to 40%

📈 PORTFOLIO CHANGES:
- Value: £150,000 → £152,000 (+1.3%)
- Concentration: 52% → 48% (improving)
- Sellable: 70% → 72% (lock expired on GOOGL)

⚠️ NEW RISKS:
- AAPL lock expires in 3 days (25 shares, £3,750)
- Approaching higher tax band (£2,000 buffer remaining)

✅ COMPLETED LAST WEEK:
- Transferred £5,000 to ISA
- Sold 50 MSFT shares per sell plan

[Start Review] [View Details] [Snooze Until Next Week]
```

**Effort**: Medium (2-3 weeks)

**Impact**: ⭐ **High** - Makes weekly review actionable, not just informational

---

### ⭐ RE-6: Recommendation Explanation Engine

**Description**: Every recommendation includes clear, transparent reasoning.

**Explanation Format**:
```
RECOMMENDATION: Sell 50 AAPL shares

WHY NOW?
- Lock period expires in 5 days
- Currently at 55% concentration (threshold: 50%)
- £7,500 at risk if not sold before lock

WHY THIS AMOUNT?
- 50 shares reduces concentration to 48% (below threshold)
- Keeps you diversified while maintaining AAPL position
- Whole shares only (no fractional)

TAX IMPACT:
- Capital gain: £2,500
- CGT due: £250 (within basic rate band)
- Employment tax: £0 (lock period satisfied)
- Net proceeds: £7,250

ALTERNATIVES CONSIDERED:
- Sell 100 shares: Over-correction, unnecessary tax
- Sell 25 shares: Insufficient, still above threshold
- Wait: Risk forfeiture if lock not satisfied

CONFIDENCE: HIGH
- All data current (prices updated 2 hours ago)
- Lock dates confirmed from acquisition records
- Tax calculation based on current year position
```

**Effort**: Medium (2 weeks)

**Impact**: ⭐ **Critical** - Builds trust in recommendations

---

### Recommendation Engine Implementation Priority

1. **Phase 1 (Immediate)**: RE-6 (Explanation Engine) - Foundation for trust
2. **Phase 2 (Month 1)**: RE-1 (Unified Dashboard) - Central recommendation hub
3. **Phase 3 (Month 2)**: RE-3 (Tax Optimization) - High-value quick wins
4. **Phase 4 (Month 3)**: RE-2 (Disposal Advisor) - Core workflow enhancement
5. **Phase 5 (Month 4)**: RE-4 (Rebalancing) - Proactive portfolio health
6. **Phase 6 (Month 5)**: RE-5 (Weekly Review) - Integrate into existing workflow

## Bug Fixes & Technical Debt

### 🔴 Critical Issues

#### BF-1: Race Condition in Concurrent Disposal Simulation
**Issue**: Multiple simultaneous disposal simulations could read stale `quantity_remaining` values.

**Impact**: Incorrect lot allocation in high-concurrency scenarios.

**Solution**:
```python
# Add optimistic locking to lot updates
class Lot(Base):
    version = mapped_column(Integer, nullable=False, default=1)
    
# In disposal allocation
def allocate_with_lock(sess: Session, lot_id: str, quantity: Decimal):
    lot = sess.query(Lot).filter(
        Lot.id == lot_id,
        Lot.version == current_version
    ).with_for_update().first()
    
    if not lot:
        raise ConcurrencyError("Lot was modified by another transaction")
    
    lot.quantity_remaining -= quantity
    lot.version += 1
```

**Effort**: Medium (2-3 days)

#### BF-2: Missing Transaction Rollback on Audit Log Failure
**Issue**: If audit log write fails, main transaction still commits.

**Impact**: Loss of audit trail for some operations.

**Solution**:
```python
# Ensure audit logging is part of transaction
with AppContext.write_session() as sess:
    # Main operation
    lot = lot_repo.create(...)
    
    # Audit log (same transaction)
    audit_repo = AuditLogRepository(sess)
    audit_repo.log_insert("lots", lot.id, ...)
    
    # Both commit or both rollback
```

**Effort**: Small (1 day)

### 🟡 High Priority Issues

#### BF-3: Stale Price Detection Edge Cases
**Issue**: Price staleness calculation doesn't account for market holidays.

**Impact**: False staleness warnings on weekends/holidays.

**Solution**:
- Add market calendar support
- Skip non-trading days in staleness calculation
- Integrate with exchange-specific holiday calendars

**Effort**: Medium (3-4 days)

#### BF-4: FX Rate Graph Resolution Inefficiency
**Issue**: FX rate graph resolution can be slow with many currency pairs.

**Impact**: Slow conversion for portfolios with many currencies.

**Solution**:
- Cache graph structure
- Pre-compute common paths (USD→GBP, EUR→GBP)
- Add graph traversal optimization

**Effort**: Medium (2-3 days)

#### BF-5: Memory Leak in Long-Running Streaming Service
**Issue**: WebSocket streaming service accumulates eligibility cache over time.

**Impact**: Memory growth in long-running deployments.

**Solution**:
```python
# Add periodic cache cleanup
def _cleanup_old_entries(self, max_age_days: int = 90):
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    self._eligibility_cache = {
        symbol: data
        for symbol, data in self._eligibility_cache.items()
        if self._parse_iso_datetime(data.get("last_updated")) > cutoff
    }
```

**Effort**: Small (1 day)

## Code Quality Improvements

### 🟡 High Priority

#### CQ-1: Add Comprehensive Type Hints
**Current State**: ~80% type hint coverage

**Goal**: 100% type hint coverage with strict mypy

**Benefits**:
- Better IDE autocomplete
- Catch type errors at development time
- Improved code documentation

**Effort**: Large (1-2 weeks)

#### CQ-2: Extract Magic Numbers to Constants
**Issue**: Tax rates, thresholds, and limits scattered throughout code.

**Solution**:
```python
# src/core/tax_engine/constants.py
class TaxYear2024_25:
    PERSONAL_ALLOWANCE = Decimal("12570")
    BASIC_RATE_LIMIT = Decimal("50270")
    HIGHER_RATE_LIMIT = Decimal("125140")
    
    BASIC_RATE = Decimal("0.20")
    HIGHER_RATE = Decimal("0.40")
    ADDITIONAL_RATE = Decimal("0.45")
    
    CGT_ANNUAL_EXEMPT = Decimal("3000")
    CGT_BASIC_RATE = Decimal("0.10")
    CGT_HIGHER_RATE = Decimal("0.20")
```

**Effort**: Medium (3-4 days)

#### CQ-3: Standardize Error Messages
**Issue**: Inconsistent error message formats across services.

**Solution**:
```python
# src/core/errors.py
class ErrorMessages:
    @staticmethod
    def not_found(entity: str, id: str) -> str:
        return f"{entity} not found: {id}"
    
    @staticmethod
    def validation_failed(field: str, reason: str) -> str:
        return f"Validation failed for {field}: {reason}"
    
    @staticmethod
    def insufficient_quantity(available: Decimal, requested: Decimal) -> str:
        return f"Insufficient quantity: {available} available, {requested} requested"
```

**Effort**: Medium (2-3 days)

### 🟢 Medium Priority

#### CQ-4: Add Service-Level Logging
**Current State**: Minimal logging in services

**Goal**: Structured logging for debugging and monitoring

**Solution**:
```python
import logging
logger = logging.getLogger(__name__)

class PortfolioService:
    @staticmethod
    def get_summary(as_of: date | None = None):
        logger.info(f"Fetching portfolio summary as_of={as_of}")
        
        try:
            # ... logic
            logger.debug(f"Found {len(securities)} securities")
            return summary
        except Exception as e:
            logger.error(f"Portfolio summary failed: {e}", exc_info=True)
            raise
```

**Effort**: Medium (3-5 days)

#### CQ-5: Refactor Large Service Files
**Issue**: Some service files exceed 1000 lines (portfolio_service.py: 2671 lines)

**Solution**: Split into focused modules:
```
services/portfolio/
├── __init__.py
├── summary.py          # Summary calculations
├── lot_management.py   # Lot CRUD
├── disposal.py         # Disposal simulation/execution
└── valuation.py        # Valuation logic
```

**Effort**: Large (1 week)

## Feature Enhancements

### 🟡 High Priority Features

#### FE-1: Bulk Import/Export
**Description**: Import/export lots, disposals, dividends via CSV/Excel

**Use Cases**:
- Migrate from other systems
- Bulk data entry
- Backup/restore

**Implementation**:
```python
# src/services/import_export_service.py
class ImportExportService:
    @staticmethod
    def import_lots_csv(file_path: Path) -> ImportResult:
        """Import lots from CSV file."""
        
    @staticmethod
    def export_portfolio_csv(as_of: date) -> bytes:
        """Export portfolio to CSV."""
```

**Effort**: Large (1-2 weeks)

#### FE-2: Multi-Currency Cash Reconciliation
**Description**: Reconcile cash balances against broker statements

**Features**:
- Upload broker statements (CSV/PDF)
- Parse and match transactions
- Highlight discrepancies
- Suggest corrections

**Effort**: Large (2-3 weeks)

#### FE-3: Tax Loss Harvesting Suggestions
**Description**: Identify opportunities to realize losses for tax efficiency

**Algorithm**:
```python
def identify_tax_loss_opportunities(
    portfolio: Portfolio,
    target_loss: Decimal,
    min_holding_days: int = 30  # Avoid wash sales
) -> list[TaxLossOpportunity]:
    """
    Find lots with unrealized losses that could be sold.
    
    Considers:
    - Current unrealized loss
    - Holding period (avoid short-term if beneficial)
    - Wash sale rules (30-day window)
    - Liquidity constraints
    """
```

**Effort**: Large (2 weeks)

#### FE-4: Dividend Forecasting
**Description**: Project future dividend income based on historical patterns

**Features**:
- Historical dividend analysis
- Seasonal patterns
- Growth rate estimation
- Confidence intervals

**Effort**: Medium (1 week)

### 🟢 Medium Priority Features

#### FE-5: Portfolio Rebalancing Planner
**Description**: Plan rebalancing trades to achieve target allocation

**Features**:
- Define target allocation (by security, sector, geography)
- Calculate required trades
- Estimate tax impact
- Optimize for minimal tax drag

**Effort**: Large (2-3 weeks)

#### FE-6: Historical Performance Attribution
**Description**: Decompose portfolio returns into components

**Attribution Factors**:
- Security selection
- Sector allocation
- Currency effects
- Timing effects

**Effort**: Large (2 weeks)

#### FE-7: Mobile-Responsive UI
**Description**: Optimize UI for mobile devices

**Current State**: Desktop-focused layout

**Improvements**:
- Responsive CSS
- Touch-friendly controls
- Simplified mobile views
- Progressive Web App (PWA) support

**Effort**: Large (3-4 weeks)

#### FE-8: Notification System
**Description**: Email/push notifications for important events

**Notification Types**:
- Vest dates approaching
- Lock expiry warnings
- Forfeiture risk alerts
- Price target hits
- Tax year-end reminders

**Effort**: Large (2 weeks)

### 🔵 Low Priority Features

#### FE-9: Multi-User Support
**Description**: Support multiple users with separate portfolios

**Requirements**:
- User authentication (OAuth2)
- Row-level security
- User management UI
- Shared database with isolation

**Effort**: Very Large (4-6 weeks)

**Note**: Significant architectural change; requires careful planning

#### FE-10: Integration with Broker APIs
**Description**: Direct integration with broker APIs for automatic data sync

**Brokers**:
- Interactive Brokers (IBKR)
- Trading 212
- Freetrade
- Hargreaves Lansdown

**Effort**: Very Large (varies by broker)

## Performance Optimizations

### 🟡 High Priority

#### PO-1: Add Database Indexes
**Current State**: Basic indexes on foreign keys

**Recommended Indexes**:
```sql
-- Frequently queried date ranges
CREATE INDEX idx_lots_acquisition_date ON lots(acquisition_date);
CREATE INDEX idx_disposals_disposal_date ON disposals(disposal_date);
CREATE INDEX idx_dividends_payment_date ON dividends(payment_date);

-- Composite indexes for common queries
CREATE INDEX idx_lots_security_remaining 
    ON lots(security_id, quantity_remaining);
CREATE INDEX idx_prices_security_date 
    ON prices(security_id, price_date DESC);

-- Audit log queries
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX idx_audit_log_table_record 
    ON audit_log(table_name, record_id);
```

**Effort**: Small (1 day)

#### PO-2: Implement Query Result Caching
**Description**: Cache expensive query results with TTL

**Candidates**:
- Portfolio summary (5 min TTL)
- Price data (1 min TTL)
- FX rates (5 min TTL)
- Tax calculations (10 min TTL)

**Implementation**:
```python
from functools import lru_cache
from datetime import datetime, timedelta

class CachedService:
    _cache: dict[str, tuple[datetime, Any]] = {}
    _ttl = timedelta(minutes=5)
    
    @classmethod
    def get_cached(cls, key: str, fetch_fn: Callable) -> Any:
        now = datetime.now()
        
        if key in cls._cache:
            cached_at, value = cls._cache[key]
            if now - cached_at < cls._ttl:
                return value
        
        value = fetch_fn()
        cls._cache[key] = (now, value)
        return value
```

**Effort**: Medium (3-4 days)

#### PO-3: Optimize Large Portfolio Queries
**Issue**: Portfolio summary slow for 1000+ lots

**Solutions**:
- Eager loading with `joinedload`
- Batch price/FX queries
- Aggregate in database where possible
- Pagination for large result sets

**Effort**: Medium (1 week)

### 🟢 Medium Priority

#### PO-4: Async Database Operations
**Description**: Use async SQLAlchemy for non-blocking I/O

**Benefits**:
- Better concurrency
- Faster response times
- Efficient resource usage

**Challenges**:
- Requires async driver (asyncpg for PostgreSQL)
- SQLite async support limited
- Significant refactoring

**Effort**: Very Large (3-4 weeks)

**Note**: Consider only if migrating to PostgreSQL

## Security Hardening

### 🔴 Critical

#### SH-1: Add Rate Limiting to All Endpoints
**Current State**: Only login endpoint rate-limited

**Solution**: Apply rate limiting to all API endpoints
```python
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@router.get("/api/portfolio/summary")
@limiter.limit("100/minute")
async def get_summary():
    ...
```

**Effort**: Small (1-2 days)

#### SH-2: Implement CSRF Protection
**Description**: Add CSRF tokens for state-changing operations

**Implementation**:
```python
from fastapi_csrf_protect import CsrfProtect

@router.post("/api/portfolio/lots")
async def create_lot(
    request: Request,
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf(request)
    # ... create lot
```

**Effort**: Medium (2-3 days)

### 🟡 High Priority

#### SH-3: Add Input Sanitization
**Description**: Sanitize all user inputs to prevent injection attacks

**Areas**:
- SQL injection (use parameterized queries)
- XSS (escape HTML in templates)
- Path traversal (validate file paths)

**Effort**: Medium (3-4 days)

#### SH-4: Implement Audit Log Integrity
**Description**: Add cryptographic signatures to audit log entries

**Solution**:
```python
import hmac
import hashlib

def sign_audit_entry(entry: dict, secret: str) -> str:
    """Generate HMAC signature for audit entry."""
    message = json.dumps(entry, sort_keys=True)
    return hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

def verify_audit_entry(entry: dict, signature: str, secret: str) -> bool:
    """Verify audit entry hasn't been tampered with."""
    expected = sign_audit_entry(entry, secret)
    return hmac.compare_digest(expected, signature)
```

**Effort**: Medium (3-4 days)

#### SH-5: Add Security Headers
**Description**: Implement comprehensive security headers

**Headers**:
```python
# Already implemented: SecurityHeadersMiddleware
# Enhance with:
- Content-Security-Policy
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy
```

**Effort**: Small (1 day)

## Developer Experience

### 🟡 High Priority

#### DX-1: Add Pre-Commit Hooks
**Description**: Automate code quality checks before commit

**Tools**:
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  
  - repo: https://github.com/pre-commit/mirrors-mypy
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
```

**Effort**: Small (1 day)

#### DX-2: Add GitHub Actions CI/CD
**Description**: Automated testing and deployment

**Pipeline**:
```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - run: pip install -e ".[dev]"
      - run: pytest --cov
      - run: ruff check .
      - run: mypy src
```

**Effort**: Medium (2-3 days)

#### DX-3: Add API Client Library
**Description**: Python client library for programmatic access

**Example**:
```python
from equity_tracker_client import EquityTrackerClient

client = EquityTrackerClient(
    base_url="http://localhost:8000",
    totp_secret="JBSWY3DPEHPK3PXP"
)

# Authenticate
client.login()

# Get portfolio
summary = client.portfolio.get_summary()
print(f"Total value: {summary.total_market_value_gbp}")

# Create lot
lot_id = client.portfolio.create_lot(
    security_id="uuid",
    quantity=Decimal("100"),
    # ...
)
```

**Effort**: Large (2 weeks)

### 🟢 Medium Priority

#### DX-4: Add Interactive Documentation
**Description**: Enhanced API documentation with examples

**Tools**:
- Swagger UI (already enabled)
- Add request/response examples
- Add authentication guide
- Add common workflows

**Effort**: Medium (3-4 days)

#### DX-5: Add Development Docker Compose
**Description**: One-command development environment

```yaml
# docker-compose.dev.yml
version: '3.8'
services:
  app:
    build: .
    volumes:
      - ./equity_tracker:/app
      - equity-data:/data
    ports:
      - "8000:8000"
    environment:
      - EQUITY_DEV_MODE=true
      - EQUITY_DOCS_ENABLED=true
```

**Effort**: Small (1 day)

## Long-Term Strategic Items

### 🔵 Future Considerations

#### LS-1: Multi-Database Support
**Description**: Support PostgreSQL, MySQL in addition to SQLite

**Benefits**:
- Better concurrency
- Larger datasets
- Advanced features (full-text search, JSON queries)

**Effort**: Very Large (4-6 weeks)

#### LS-2: Microservices Architecture
**Description**: Split into separate services

**Services**:
- Core API
- Price service
- Tax calculation service
- Notification service
- Beta research service

**Effort**: Very Large (3-4 months)

**Note**: Only consider if scaling requirements justify complexity

#### LS-3: GraphQL API
**Description**: Add GraphQL endpoint alongside REST

**Benefits**:
- Flexible queries
- Reduced over-fetching
- Better for complex UIs

**Effort**: Large (3-4 weeks)

#### LS-4: Machine Learning Integration
**Description**: ML-powered features (beyond beta system)

**Potential Features**:
- Anomaly detection in transactions
- Spending pattern analysis
- Personalized tax optimization
- Risk tolerance assessment

**Effort**: Very Large (ongoing research)

## Implementation Priority Matrix

### ⭐ STRATEGIC PRIORITY: Recommendation Engine (5-6 Months)

**Mission-Critical Path to "Don't Think, Just Follow" Vision:**

**Phase 1 - Foundation (Month 1)**
1. ⭐ RE-6: Recommendation Explanation Engine
2. 🔴 BF-2: Transaction rollback fix
3. 🔴 SH-1: Rate limiting all endpoints
4. 🟡 PO-1: Database indexes

**Phase 2 - Core Dashboard (Month 2)**
1. ⭐ RE-1: Unified Recommendation Dashboard
2. 🔴 BF-1: Concurrency fix
3. 🟡 DX-1: Pre-commit hooks
4. 🟡 DX-2: CI/CD pipeline

**Phase 3 - Tax Intelligence (Month 3)**
1. ⭐ RE-3: Proactive Tax Optimization Alerts
2. 🟡 BF-3: Market calendar support
3. 🟡 CQ-2: Extract constants
4. 🟡 SH-2: CSRF protection

**Phase 4 - Disposal Intelligence (Month 4)**
1. ⭐ RE-2: Smart Disposal Advisor
2. 🟡 PO-2: Query caching
3. 🟡 FE-1: Bulk import/export

**Phase 5 - Portfolio Health (Month 5)**
1. ⭐ RE-4: Intelligent Rebalancing Advisor
2. 🟢 CQ-5: Refactor large files
3. 🟢 FE-7: Mobile-responsive UI

**Phase 6 - Workflow Integration (Month 6)**
1. ⭐ RE-5: Weekly Review Automation
2. 🟢 FE-4: Dividend forecasting
3. 🟢 FE-6: Performance attribution

---

### Post-Recommendation Engine (6+ Months)

**Enhancements & Polish:**
- 🟢 FE-8: Notification system
- 🟢 FE-2: Multi-currency reconciliation
- 🟢 DX-3: API client library
- 🟢 SH-4: Audit log integrity

**Long-Term Strategic:**
- 🔵 FE-9: Multi-user support
- 🔵 LS-1: Multi-database support
- 🔵 FE-10: Broker integrations
- 🔵 LS-3: GraphQL API

---

### Success Metrics

**After Recommendation Engine Completion:**

1. **User Confidence**: 90%+ of users report feeling confident making financial decisions based on recommendations
2. **Action Rate**: 70%+ of recommendations are executed or explicitly dismissed (not ignored)
3. **Time Saved**: Users spend <10 minutes/week on portfolio management (down from 30+ minutes)
4. **Tax Savings**: Average £500+/year in tax optimization from automated recommendations
5. **Risk Reduction**: 80%+ of concentration/forfeiture risks caught and resolved proactively

**Key Question**: Can a user manage their equity compensation effectively by simply following the system's recommendations without needing to understand the underlying complexity? **This is the north star.**

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - Current architecture
- [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md) - Development workflow
- [Deployment Guide](./06-DEPLOYMENT-GUIDE.md) - Production deployment