# Developer Onboarding Guide

**Last Updated:** 2026-04-03

## Table of Contents

1. [Welcome](#welcome)
2. [Development Setup](#development-setup)
3. [Project Structure](#project-structure)
4. [Development Workflow](#development-workflow)
5. [Testing](#testing)
6. [Code Standards](#code-standards)
7. [Contributing Guidelines](#contributing-guidelines)

## Welcome

Welcome to the Equity Tracker development team! This guide will help you get started with local development, understand the codebase, and contribute effectively.

### What You'll Learn

- How to set up your development environment
- Project structure and key components
- Development workflow and best practices
- Testing strategies
- Code standards and contribution guidelines

## Development Setup

### Prerequisites

- **Python 3.12+**: [Download](https://www.python.org/downloads/)
- **Git**: [Download](https://git-scm.com/downloads)
- **Code Editor**: VS Code recommended with Python extension
- **Terminal**: PowerShell (Windows), Terminal (macOS/Linux)

### Initial Setup

#### 1. Clone Repository

```bash
git clone https://github.com/your-org/equity-tracker.git
cd equity-tracker
```

#### 2. Create Virtual Environment

```bash
# Windows
cd equity_tracker
python -m venv venv
venv\Scripts\activate

# macOS/Linux
cd equity_tracker
python3 -m venv venv
source venv/bin/activate
```

#### 3. Install Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Install package in editable mode
pip install -e .

# Install development dependencies
pip install -e ".[dev]"

# Install SQLCipher (for encryption)
pip install sqlcipher3-binary
```

#### 4. Configure Environment

Create `.env` file in `equity_tracker/` directory:

```bash
# Development configuration
EQUITY_DB_PATH=./data/dev_portfolio.db
EQUITY_DB_PASSWORD=dev-password
EQUITY_DB_ENCRYPTED=false  # Use plain SQLite for development
EQUITY_TOTP_SECRET=JBSWY3DPEHPK3PXP
EQUITY_SECRET_KEY=dev-secret-key-not-for-production
EQUITY_ALLOWED_ORIGINS=*
EQUITY_DOCS_ENABLED=true  # Enable /docs endpoint
EQUITY_DEV_MODE=true  # Allow HTTP cookies
EQUITY_BETA_MODE=OFF  # Disable beta features initially
```

#### 5. Initialize Database

```bash
# Create data directory
mkdir -p data

# Run migrations
alembic upgrade head

# Verify database
python -c "from src.app_context import AppContext; from src.db.engine import DatabaseEngine; engine = DatabaseEngine.create('./data/dev_portfolio.db', 'dev-password', encrypted=False); AppContext.initialize(engine); print('Database initialized')"
```

#### 6. Run Development Server

```bash
python run_api.py
```

**Access:**
- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### IDE Setup (VS Code)

#### Recommended Extensions

- **Python** (ms-python.python)
- **Pylance** (ms-python.vscode-pylance)
- **Ruff** (charliermarsh.ruff)
- **SQLite Viewer** (alexcvzz.vscode-sqlite)

#### Settings (`.vscode/settings.json`)

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/equity_tracker/venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": [
    "tests"
  ],
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "python.formatting.provider": "none",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  }
}
```

## Project Structure

### Directory Layout

```
equity-tracker/
├── equity_tracker/              # Main application directory
│   ├── src/                     # Source code
│   │   ├── api/                 # FastAPI routes and middleware
│   │   │   ├── app.py           # Application setup
│   │   │   ├── auth.py          # Authentication
│   │   │   ├── dependencies.py  # Dependency injection
│   │   │   └── routers/         # API route modules
│   │   ├── services/            # Business logic layer
│   │   │   ├── portfolio_service.py
│   │   │   ├── tax_plan_service.py
│   │   │   └── ...              # 40+ services
│   │   ├── db/                  # Database layer
│   │   │   ├── models.py        # ORM models
│   │   │   ├── engine.py        # Database engine
│   │   │   └── repository/      # Repository classes
│   │   ├── core/                # Core domain logic
│   │   │   ├── lot_engine/      # FIFO, UK matching
│   │   │   └── tax_engine/      # Tax calculations
│   │   ├── beta/                # Beta features (isolated)
│   │   ├── app_context.py       # Application context
│   │   ├── settings.py          # Settings management
│   │   └── env_bootstrap.py     # Environment loading
│   ├── tests/                   # Test suite
│   │   ├── conftest.py          # Test fixtures
│   │   ├── test_services/       # Service tests
│   │   ├── test_lot_engine/     # Lot engine tests
│   │   └── test_tax_engine/     # Tax engine tests
│   ├── alembic/                 # Database migrations
│   │   └── versions/            # Migration files
│   ├── scripts/                 # Utility scripts
│   ├── run_api.py               # Server entry point
│   ├── pyproject.toml           # Project configuration
│   └── alembic.ini              # Alembic configuration
├── docs/                        # Strategic documentation
└── Bob/                         # Technical documentation (this folder)
```

### Key Files

| File | Purpose |
|------|---------|
| [`src/api/app.py`](../equity_tracker/src/api/app.py:1) | FastAPI application setup, middleware, lifespan |
| [`src/app_context.py`](../equity_tracker/src/app_context.py:48) | Singleton database session manager |
| [`src/db/models.py`](../equity_tracker/src/db/models.py:1) | SQLAlchemy ORM models |
| [`src/settings.py`](../equity_tracker/src/settings.py:30) | User settings management |
| [`run_api.py`](../equity_tracker/run_api.py:1) | Server entry point |
| [`pyproject.toml`](../equity_tracker/pyproject.toml:1) | Dependencies and project metadata |

## Development Workflow

### Making Changes

#### 1. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
```

#### 2. Make Changes

Edit code following [Code Standards](#code-standards).

#### 3. Run Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_services/test_portfolio_service.py

# Run with coverage
pytest --cov=src --cov-report=html
```

#### 4. Format and Lint

```bash
# Format code
ruff format .

# Lint code
ruff check .

# Fix auto-fixable issues
ruff check --fix .

# Type check
mypy src
```

#### 5. Commit Changes

```bash
git add .
git commit -m "feat: add new feature"
```

**Commit Message Format:**
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Test additions/changes
- `refactor:` Code refactoring
- `chore:` Maintenance tasks

#### 6. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

### Database Migrations

#### Creating a Migration

```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "add new table"

# Create empty migration
alembic revision -m "custom migration"
```

#### Migration File Structure

```python
"""add new table

Revision ID: 018_add_new_table
Revises: 017_previous_migration
Create Date: 2024-01-15 10:30:45.123456
"""

from alembic import op
import sqlalchemy as sa

revision = '018_add_new_table'
down_revision = '017_previous_migration'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'new_table',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False)
    )

def downgrade() -> None:
    op.drop_table('new_table')
```

#### Applying Migrations

```bash
# Upgrade to latest
alembic upgrade head

# Downgrade one version
alembic downgrade -1

# Check current version
alembic current
```

### Adding a New Service

#### 1. Create Service File

```python
# src/services/example_service.py

from __future__ import annotations
from decimal import Decimal
from datetime import date
from ..app_context import AppContext
from ..db.repository import ExampleRepository

class ExampleService:
    """
    Service for managing examples.
    
    Responsibilities:
    - Example creation and validation
    - Example aggregation
    - Example reporting
    """
    
    @staticmethod
    def get_summary() -> dict:
        """
        Get example summary.
        
        Returns:
            Dictionary with summary data
        """
        with AppContext.read_session() as sess:
            repo = ExampleRepository(sess)
            items = repo.list_all()
            
            total = sum(item.value for item in items)
            
            return {
                "total": str(total),
                "count": len(items)
            }
    
    @staticmethod
    def create_example(name: str, value: Decimal) -> str:
        """
        Create a new example.
        
        Args:
            name: Example name
            value: Example value
            
        Returns:
            Created example ID
            
        Raises:
            ValueError: If validation fails
        """
        if not name:
            raise ValueError("Name is required")
        
        with AppContext.write_session() as sess:
            repo = ExampleRepository(sess)
            example = repo.create(name=name, value=value)
            return example.id
```

#### 2. Add Tests

```python
# tests/test_services/test_example_service.py

import pytest
from decimal import Decimal
from src.services.example_service import ExampleService

def test_get_summary(test_db):
    """Test example summary retrieval."""
    # Arrange
    ExampleService.create_example("Test", Decimal("100"))
    
    # Act
    summary = ExampleService.get_summary()
    
    # Assert
    assert summary["count"] == 1
    assert Decimal(summary["total"]) == Decimal("100")

def test_create_example_validation(test_db):
    """Test example creation validation."""
    with pytest.raises(ValueError, match="Name is required"):
        ExampleService.create_example("", Decimal("100"))
```

#### 3. Add API Endpoint

```python
# src/api/routers/example.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from decimal import Decimal
from ...services.example_service import ExampleService

router = APIRouter(prefix="/api/examples", tags=["examples"])

class CreateExampleRequest(BaseModel):
    name: str
    value: Decimal

@router.get("/summary")
async def get_summary():
    """Get example summary."""
    return ExampleService.get_summary()

@router.post("")
async def create_example(request: CreateExampleRequest):
    """Create a new example."""
    try:
        example_id = ExampleService.create_example(
            name=request.name,
            value=request.value
        )
        return {"example_id": example_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

#### 4. Register Router

```python
# src/api/app.py

from .routers import example

app.include_router(example.router)
```

## Testing

### Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_services/           # Service layer tests
│   ├── conftest.py          # Service fixtures
│   └── test_*.py            # Test files
├── test_lot_engine/         # Lot engine tests
├── test_tax_engine/         # Tax engine tests
└── test_api/                # API endpoint tests
```

### Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_services/test_portfolio_service.py

# Specific test function
pytest tests/test_services/test_portfolio_service.py::test_get_summary

# With coverage
pytest --cov=src --cov-report=html

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

### Writing Tests

#### Test Fixtures

```python
# tests/conftest.py

import pytest
from pathlib import Path
from src.app_context import AppContext
from src.db.engine import DatabaseEngine

@pytest.fixture
def test_db(tmp_path: Path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.db"
    engine = DatabaseEngine.create(db_path, "test-password", encrypted=False)
    AppContext.initialize(engine)
    
    # Run migrations
    from src.db.migration_manager import ensure_migrated
    ensure_migrated(engine)
    
    yield
    
    # Cleanup
    AppContext.lock()
```

#### Service Tests

```python
def test_portfolio_summary(test_db):
    """Test portfolio summary calculation."""
    # Arrange
    with AppContext.write_session() as sess:
        security = create_test_security(sess)
        lot = create_test_lot(sess, security.id, quantity=Decimal("100"))
        price = create_test_price(sess, security.id, price=Decimal("150"))
    
    # Act
    summary = PortfolioService.get_summary()
    
    # Assert
    assert summary.total_market_value_gbp == Decimal("15000")
    assert len(summary.securities) == 1
```

#### API Tests

```python
from fastapi.testclient import TestClient
from src.api.app import app

client = TestClient(app)

def test_get_portfolio_summary(test_db):
    """Test portfolio summary endpoint."""
    response = client.get("/api/portfolio/summary")
    
    assert response.status_code == 200
    data = response.json()
    assert "total_market_value_gbp" in data
```

### Test Coverage

```bash
# Generate coverage report
pytest --cov=src --cov-report=html

# View report
open htmlcov/index.html  # macOS
start htmlcov/index.html  # Windows
```

**Coverage Goals:**
- Overall: >80%
- Critical services: >90%
- Tax engine: >95%

## Code Standards

### Python Style

Follow **PEP 8** with these specifics:

- **Line Length**: 100 characters (configured in `pyproject.toml`)
- **Indentation**: 4 spaces
- **Quotes**: Double quotes for strings
- **Imports**: Organized by ruff

### Type Hints

Use type hints for all function signatures:

```python
from decimal import Decimal
from datetime import date

def calculate_gain(
    proceeds: Decimal,
    cost_basis: Decimal,
    disposal_date: date
) -> Decimal:
    """Calculate capital gain."""
    return proceeds - cost_basis
```

### Docstrings

Use Google-style docstrings:

```python
def complex_function(param1: str, param2: int) -> dict:
    """
    Brief description of function.
    
    Longer description if needed, explaining the purpose,
    behavior, and any important details.
    
    Args:
        param1: Description of param1
        param2: Description of param2
        
    Returns:
        Dictionary containing result data
        
    Raises:
        ValueError: If param1 is empty
        TypeError: If param2 is negative
        
    Example:
        >>> result = complex_function("test", 42)
        >>> print(result["status"])
        success
    """
    pass
```

### Error Handling

```python
# Good: Specific exceptions
if not security_id:
    raise ValueError("security_id is required")

# Bad: Generic exceptions
if not security_id:
    raise Exception("Error")

# Good: Context in error messages
raise ValueError(f"Security not found: {security_id}")

# Bad: Vague error messages
raise ValueError("Not found")
```

### Decimal Usage

**Always use Decimal for monetary values:**

```python
from decimal import Decimal

# Good
price = Decimal("150.00")
quantity = Decimal("100")
value = price * quantity

# Bad
price = 150.00  # Float (precision loss)
```

### Session Management

**Always use context managers:**

```python
# Good
with AppContext.read_session() as sess:
    data = repo.query(sess)

# Bad
sess = AppContext.get_session()
data = repo.query(sess)
sess.close()  # Easy to forget
```

## Contributing Guidelines

### Pull Request Process

1. **Create Feature Branch**
   ```bash
   git checkout -b feature/your-feature
   ```

2. **Make Changes**
   - Follow code standards
   - Add tests
   - Update documentation

3. **Run Quality Checks**
   ```bash
   pytest
   ruff format .
   ruff check .
   mypy src
   ```

4. **Commit Changes**
   ```bash
   git commit -m "feat: add new feature"
   ```

5. **Push and Create PR**
   ```bash
   git push origin feature/your-feature
   ```

6. **PR Review**
   - Address review comments
   - Ensure CI passes
   - Get approval from maintainer

### Code Review Checklist

- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Type hints present
- [ ] Docstrings complete
- [ ] Error handling appropriate
- [ ] No breaking changes (or documented)
- [ ] Performance considered
- [ ] Security implications reviewed

### Documentation Updates

When making changes, update relevant documentation:

- **Code Comments**: Inline explanations for complex logic
- **Docstrings**: Function/class documentation
- **Technical Docs**: [`Bob/`](.) folder documentation
- **Strategic Docs**: [`docs/`](../docs/) folder if applicable

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Database Schema](./02-DATABASE-SCHEMA.md) - Data models
- [API Reference](./03-API-REFERENCE.md) - Endpoint documentation
- [Service Layer](./04-SERVICE-LAYER.md) - Business logic patterns
- [Deployment Guide](./06-DEPLOYMENT-GUIDE.md) - Production deployment