# Architecture Overview

**Last Updated:** 2026-04-03

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Technology Stack](#technology-stack)
3. [Architectural Patterns](#architectural-patterns)
4. [Component Layers](#component-layers)
5. [Data Flow](#data-flow)
6. [Security Architecture](#security-architecture)
7. [Scalability Considerations](#scalability-considerations)

## System Architecture

Equity Tracker is a **monolithic, single-user, privacy-first** financial tracking application designed to run locally or on a private network. The architecture prioritizes determinism, auditability, and data privacy over distributed scalability.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client Layer                          │
│  (Browser - Jinja2 Templates + Vanilla JavaScript)          │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP/REST
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Auth Router  │  │ Portfolio    │  │ Strategic    │     │
│  │              │  │ Router       │  │ Routers      │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         Middleware (CORS, Security, Rate Limit)      │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Service Layer                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Portfolio    │  │ Tax Plan     │  │ Risk         │     │
│  │ Service      │  │ Service      │  │ Service      │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Price        │  │ FX           │  │ Dividend     │     │
│  │ Service      │  │ Service      │  │ Service      │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                    40+ Services Total                        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Repository Layer                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Lot          │  │ Disposal     │  │ Security     │     │
│  │ Repository   │  │ Repository   │  │ Repository   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Data Layer                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         SQLAlchemy ORM + Database Engine             │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │    SQLite/SQLCipher Database (Encrypted at Rest)     │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              Beta Research System (Isolated)                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Separate Database + Runtime + Services              │  │
│  │  (Paper Trading, Hypothesis Engine, Signal Discovery)│  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Technology Stack

### Core Technologies

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Language** | Python | 3.12+ | Application runtime |
| **Web Framework** | FastAPI | 0.115+ | REST API and routing |
| **ASGI Server** | Uvicorn | 0.32+ | Production server |
| **ORM** | SQLAlchemy | 2.0+ | Database abstraction |
| **Database** | SQLite | 3.x | Data persistence |
| **Encryption** | SQLCipher | 4.x | Database encryption |
| **Migrations** | Alembic | 1.13+ | Schema versioning |
| **Validation** | Pydantic | 2.7+ | Data validation |
| **Testing** | pytest | 8.2+ | Test framework |
| **Templates** | Jinja2 | 3.1+ | Server-side rendering |

### Key Dependencies

- **httpx**: HTTP client for external API calls (price/FX data)
- **pandas**: Data manipulation for analytics
- **argon2-cffi**: Password hashing
- **pyotp**: TOTP authentication
- **slowapi**: Rate limiting
- **itsdangerous**: Session signing
- **yfinance**: Yahoo Finance price data (fallback)
- **gspread**: Google Sheets integration (optional)

### Development Tools

- **ruff**: Linting and formatting
- **mypy**: Static type checking
- **pytest-cov**: Code coverage reporting

## Architectural Patterns

### 1. Layered Architecture

The application follows a strict **4-layer architecture**:

```
Presentation Layer (API Routes)
        ↓
Business Logic Layer (Services)
        ↓
Data Access Layer (Repositories)
        ↓
Data Layer (ORM + Database)
```

**Key Principles:**
- Each layer only depends on the layer directly below it
- No circular dependencies between layers
- Services contain all business logic
- Repositories handle all database operations

### 2. Repository Pattern

All database access goes through **repository classes** that encapsulate CRUD operations:

```python
class LotRepository:
    def __init__(self, session: Session):
        self._session = session
    
    def get_by_id(self, lot_id: str) -> Lot | None:
        return self._session.get(Lot, lot_id)
    
    def list_active(self) -> list[Lot]:
        return self._session.query(Lot).filter(
            Lot.quantity_remaining > 0
        ).all()
```

**Benefits:**
- Centralized query logic
- Easier testing with mock repositories
- Consistent error handling
- Transaction management

### 3. Service Layer Pattern

Business logic is encapsulated in **static service classes**:

```python
class PortfolioService:
    @staticmethod
    def get_summary(as_of: date | None = None) -> PortfolioSummary:
        with AppContext.read_session() as sess:
            # Business logic here
            return summary
```

**Characteristics:**
- Static methods (no instance state)
- Session management via context managers
- Pure functions where possible
- Comprehensive error handling

### 4. Singleton Pattern (AppContext)

The [`AppContext`](../equity_tracker/src/app_context.py:48) class manages the database connection as a **class-level singleton**:

```python
class AppContext:
    _engine: DatabaseEngine | None = None
    
    @classmethod
    def initialize(cls, engine: DatabaseEngine) -> None:
        cls._engine = engine
    
    @classmethod
    def write_session(cls) -> Generator[Session, None, None]:
        # Yields a session for write operations
```

**Rationale:**
- Single SQLCipher connection required for encryption
- Simplified session management
- Thread-safe for single-worker deployment

### 5. Immutable Domain Objects

Core domain entities follow **immutability principles**:

- **Lots**: Immutable after creation (only `quantity_remaining` updates)
- **Audit Log**: Strictly append-only, no updates/deletes
- **Transactions**: Corrections via reversal records, not updates

### 6. Append-Only Event Sourcing

Financial events are **never deleted**, only corrected:

```python
# Wrong: Deleting a disposal
disposal_repo.delete(disposal_id)

# Right: Creating a reversal
disposal_repo.create_reversal(
    original_disposal_id=disposal_id,
    reason="User correction"
)
```

## Component Layers

### API Layer ([`src/api/`](../equity_tracker/src/api/))

**Responsibilities:**
- HTTP request/response handling
- Authentication and authorization
- Input validation
- Rate limiting
- CORS management

**Key Components:**
- [`app.py`](../equity_tracker/src/api/app.py:1): FastAPI application setup
- [`auth.py`](../equity_tracker/src/api/auth.py:1): TOTP authentication
- [`dependencies.py`](../equity_tracker/src/api/dependencies.py:1): Dependency injection
- `routers/`: Endpoint definitions by domain

### Service Layer ([`src/services/`](../equity_tracker/src/services/))

**Responsibilities:**
- Business logic implementation
- Cross-entity operations
- Calculation engines
- External API integration

**Key Services:**
- [`portfolio_service.py`](../equity_tracker/src/services/portfolio_service.py:1): Portfolio aggregation and analysis
- [`tax_plan_service.py`](../equity_tracker/src/services/tax_plan_service.py:1): UK tax calculations
- [`risk_service.py`](../equity_tracker/src/services/risk_service.py:1): Risk metrics and guardrails
- [`price_service.py`](../equity_tracker/src/services/price_service.py:1): Price data management
- [`fx_service.py`](../equity_tracker/src/services/fx_service.py:1): Currency conversion

### Repository Layer ([`src/db/repository/`](../equity_tracker/src/db/))

**Responsibilities:**
- Database CRUD operations
- Query optimization
- Transaction management
- Audit logging

**Key Repositories:**
- `LotRepository`: Lot acquisition records
- `DisposalRepository`: Sale transactions
- `SecurityRepository`: Instrument metadata
- `PriceRepository`: Historical prices
- `AuditLogRepository`: Change tracking

### Data Layer ([`src/db/models.py`](../equity_tracker/src/db/models.py:1))

**Responsibilities:**
- ORM model definitions
- Database constraints
- Relationships
- Type definitions

**Key Models:**
- `Security`: Tradeable instruments
- `Lot`: Acquisition records
- `Disposal`: Sale transactions
- `Dividend`: Income events
- `CashEntry`: Cash movements
- `AuditLog`: Change history

### Core Domain Layer ([`src/core/`](../equity_tracker/src/core/))

**Responsibilities:**
- Tax calculation engines
- Lot matching algorithms (FIFO, UK matching)
- Domain-specific business rules

**Key Components:**
- [`lot_engine/fifo.py`](../equity_tracker/src/core/lot_engine/fifo.py:1): FIFO lot allocation
- [`lot_engine/uk_matching.py`](../equity_tracker/src/core/lot_engine/uk_matching.py:1): HMRC 30-day matching
- `tax_engine/`: UK tax calculations (IT, NI, CGT, SL)

## Data Flow

### Read Operation Flow

```
1. Client Request
   ↓
2. API Route Handler
   ↓
3. Authentication Check
   ↓
4. Service Method Call
   ↓
5. Repository Query
   ↓
6. Database Read
   ↓
7. ORM Model → Domain Object
   ↓
8. Service Business Logic
   ↓
9. Response Serialization
   ↓
10. Client Response
```

### Write Operation Flow

```
1. Client Request (with data)
   ↓
2. API Route Handler
   ↓
3. Authentication Check
   ↓
4. Input Validation (Pydantic)
   ↓
5. Service Method Call
   ↓
6. Business Logic Validation
   ↓
7. Repository Write Operation
   ↓
8. Database Transaction
   ↓
9. Audit Log Entry
   ↓
10. Commit or Rollback
   ↓
11. Response with Result
```

### Example: Adding a Lot

```python
# 1. API Route
@router.post("/portfolio/lots")
async def create_lot(request: CreateLotRequest):
    # 2. Validation happens automatically (Pydantic)
    
    # 3. Service call
    result = PortfolioService.create_lot(
        security_id=request.security_id,
        quantity=request.quantity,
        # ... other fields
    )
    
    # 4. Return response
    return {"lot_id": result.lot_id}

# Service Layer
class PortfolioService:
    @staticmethod
    def create_lot(...) -> CreateLotResult:
        with AppContext.write_session() as sess:
            # 5. Repository call
            lot_repo = LotRepository(sess)
            lot = lot_repo.create(...)
            
            # 6. Audit logging
            audit_repo = AuditLogRepository(sess)
            audit_repo.log_insert("lots", lot.id, ...)
            
            # 7. Transaction commits automatically
            return CreateLotResult(lot_id=lot.id)
```

## Security Architecture

### Authentication Flow

```
1. User enters TOTP code
   ↓
2. Server validates against TOTP secret
   ↓
3. Session cookie created (signed with SECRET_KEY)
   ↓
4. Subsequent requests include session cookie
   ↓
5. Middleware validates session signature
   ↓
6. Request proceeds or 401 Unauthorized
```

### Database Encryption

- **At Rest**: SQLCipher encrypts the entire database file
- **In Memory**: Decrypted only in application memory
- **Key Management**: Password-based key derivation (PBKDF2)

### Session Security

- **Signing**: `itsdangerous` signs session cookies
- **HTTP-Only**: Cookies not accessible to JavaScript
- **Secure Flag**: HTTPS-only in production (configurable)
- **Expiration**: Configurable session timeout

### Rate Limiting

- **Implementation**: `slowapi` middleware
- **Limits**: Per-endpoint rate limits
- **Storage**: In-memory (single-worker deployment)

## Scalability Considerations

### Current Constraints

1. **Single-User Design**: No multi-tenancy support
2. **Single-Worker**: Required for SQLCipher connection pooling
3. **Local Database**: No distributed database support
4. **Synchronous I/O**: Blocking database calls in async handlers

### Performance Optimizations

1. **Read Sessions**: Separate read-only sessions for queries
2. **Eager Loading**: Strategic use of `joinedload` for relationships
3. **Indexing**: Database indexes on frequently queried columns
4. **Caching**: In-memory caching for price/FX data
5. **Batch Operations**: Bulk inserts where appropriate

### Future Scalability Paths

If multi-user support is needed:

1. **Database**: Migrate to PostgreSQL with row-level security
2. **Workers**: Support multiple uvicorn workers with shared state
3. **Caching**: Redis for distributed session/cache storage
4. **Authentication**: OAuth2 or JWT tokens instead of sessions
5. **Async I/O**: Async database driver (asyncpg)

### Beta System Isolation

The experimental beta system runs in a **separate process** with:

- Separate SQLite database ([`beta_research.db`](../equity_tracker/src/beta/db/))
- Independent runtime manager
- Non-blocking startup (core system works if beta fails)
- Feature flags for complete disablement

## Design Decisions

### Why SQLite?

- **Privacy**: Local-first, no cloud dependencies
- **Simplicity**: No separate database server
- **Portability**: Single file, easy backup
- **Encryption**: SQLCipher provides transparent encryption
- **Performance**: Sufficient for single-user workloads

### Why FastAPI?

- **Modern**: Async support, type hints, automatic docs
- **Performance**: Fast ASGI server (uvicorn)
- **Validation**: Pydantic integration
- **OpenAPI**: Automatic API documentation
- **Ecosystem**: Rich middleware and extension support

### Why Static Service Methods?

- **Simplicity**: No instance state to manage
- **Testability**: Easy to mock and test
- **Clarity**: Explicit session management
- **Thread-Safety**: No shared mutable state

### Why Append-Only Audit Log?

- **Auditability**: Complete change history
- **Compliance**: Regulatory requirements
- **Debugging**: Trace all data mutations
- **Integrity**: Immutable record of changes

## Related Documentation

- [Database Schema](./02-DATABASE-SCHEMA.md) - Detailed data model
- [Service Layer](./04-SERVICE-LAYER.md) - Service responsibilities
- [API Reference](./03-API-REFERENCE.md) - Endpoint documentation
- [Deployment Guide](./06-DEPLOYMENT-GUIDE.md) - Setup and configuration