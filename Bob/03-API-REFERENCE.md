# API Reference

**Last Updated:** 2026-04-03

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Common Patterns](#common-patterns)
4. [Core Endpoints](#core-endpoints)
5. [Strategic Endpoints](#strategic-endpoints)
6. [Admin Endpoints](#admin-endpoints)
7. [Error Handling](#error-handling)

## Overview

Equity Tracker exposes a **REST API** built with FastAPI. The API serves both:
- **JSON responses** for programmatic access
- **HTML responses** (Jinja2 templates) for browser UI

### Base URL

```
http://localhost:8000
```

For LAN access, replace `localhost` with your machine's IP address.

### API Documentation

When `EQUITY_DOCS_ENABLED=true`:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI Schema**: `http://localhost:8000/openapi.json`

## Authentication

### TOTP Authentication

The API uses **Time-based One-Time Password (TOTP)** authentication with session cookies.

#### Login Flow

```http
POST /auth/login
Content-Type: application/json

{
  "totp_code": "123456"
}
```

**Response (Success):**
```json
{
  "status": "success",
  "message": "Authenticated"
}
```

Sets a signed session cookie (`equity_session`) valid for the configured duration.

**Response (Failure):**
```json
{
  "status": "error",
  "message": "Invalid TOTP code"
}
```

#### Logout

```http
POST /auth/logout
```

Clears the session cookie.

#### Session Validation

All protected endpoints require a valid session cookie. If missing or invalid:

```json
{
  "detail": "Not authenticated"
}
```

**Status Code:** `401 Unauthorized`

### Rate Limiting

Login endpoint is rate-limited to prevent brute force:
- **Limit**: 5 requests per minute per IP
- **Response**: `429 Too Many Requests`

## Common Patterns

### Query Parameters

#### `as_of` Date

Many endpoints support an `as_of` query parameter for historical views:

```http
GET /api/portfolio/summary?as_of=2024-01-15
```

- **Format**: `YYYY-MM-DD`
- **Default**: Today's date
- **Behavior**: Uses latest prices/FX rates on or before the specified date

#### `hide_values` Privacy Mode

```http
GET /api/portfolio/summary?hide_values=true
```

Masks monetary values in the response (shows `***` instead of amounts).

### Response Format

#### Success Response

```json
{
  "status": "success",
  "data": { ... },
  "generated_at": "2024-01-15T10:30:45.123456Z"
}
```

#### Error Response

```json
{
  "status": "error",
  "message": "Human-readable error message",
  "detail": "Technical error details (optional)"
}
```

### Pagination

Large result sets use cursor-based pagination:

```http
GET /api/audit?limit=100&cursor=abc123
```

**Response:**
```json
{
  "items": [...],
  "next_cursor": "def456",
  "has_more": true
}
```

## Core Endpoints

### Portfolio

#### Get Portfolio Summary

```http
GET /api/portfolio/summary
```

**Query Parameters:**
- `as_of` (optional): Date for historical view
- `hide_values` (optional): Privacy mode

**Response:**
```json
{
  "total_market_value_gbp": "150000.00",
  "total_cost_basis_gbp": "120000.00",
  "unrealized_gain_gbp": "30000.00",
  "sellable_value_gbp": "100000.00",
  "locked_value_gbp": "30000.00",
  "forfeitable_value_gbp": "20000.00",
  "securities": [
    {
      "security_id": "uuid",
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "quantity": "100.00",
      "market_value_gbp": "15000.00",
      "cost_basis_gbp": "12000.00",
      "unrealized_gain_gbp": "3000.00"
    }
  ]
}
```

#### Create Lot

```http
POST /api/portfolio/lots
Content-Type: application/json

{
  "security_id": "uuid",
  "scheme_type": "RSU",
  "acquisition_date": "2024-01-15",
  "quantity": "100.00",
  "cost_per_share_gbp": "120.00",
  "vest_date": "2024-01-15",
  "lock_expiry_date": "2024-07-15"
}
```

**Response:**
```json
{
  "lot_id": "uuid",
  "message": "Lot created successfully"
}
```

#### Simulate Disposal

```http
POST /api/portfolio/simulate-disposal
Content-Type: application/json

{
  "security_id": "uuid",
  "quantity": "50.00",
  "price_per_share_gbp": "150.00",
  "disposal_date": "2024-06-01"
}
```

**Response:**
```json
{
  "total_proceeds_gbp": "7500.00",
  "total_cost_basis_gbp": "6000.00",
  "capital_gain_gbp": "1500.00",
  "employment_tax_gbp": "0.00",
  "cgt_estimate_gbp": "150.00",
  "broker_fee_gbp": "10.00",
  "net_proceeds_gbp": "7340.00",
  "lot_allocations": [
    {
      "lot_id": "uuid",
      "quantity_allocated": "50.00",
      "cost_basis_gbp": "6000.00"
    }
  ],
  "warnings": []
}
```

### Securities

#### List Securities

```http
GET /api/securities
```

**Response:**
```json
{
  "securities": [
    {
      "id": "uuid",
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "currency": "USD",
      "exchange": "NASDAQ",
      "units_precision": 0
    }
  ]
}
```

#### Create Security

```http
POST /api/securities
Content-Type: application/json

{
  "ticker": "AAPL",
  "name": "Apple Inc.",
  "currency": "USD",
  "exchange": "NASDAQ",
  "isin": "US0378331005"
}
```

### Prices

#### Get Latest Prices

```http
GET /api/prices/latest
```

**Query Parameters:**
- `security_ids` (optional): Comma-separated UUIDs

**Response:**
```json
{
  "prices": {
    "uuid1": {
      "price_gbp": "150.00",
      "price_date": "2024-01-15",
      "source": "YFINANCE",
      "is_stale": false
    }
  }
}
```

#### Refresh Prices

```http
POST /api/prices/refresh
Content-Type: application/json

{
  "security_ids": ["uuid1", "uuid2"]
}
```

**Response:**
```json
{
  "refreshed": 2,
  "failed": 0,
  "results": [
    {
      "security_id": "uuid1",
      "status": "success",
      "price_gbp": "150.00"
    }
  ]
}
```

### FX Rates

#### Get FX Rate

```http
GET /api/fx/rate?from=USD&to=GBP&date=2024-01-15
```

**Response:**
```json
{
  "from_currency": "USD",
  "to_currency": "GBP",
  "rate": "0.7850",
  "rate_date": "2024-01-15",
  "source": "YFINANCE"
}
```

### Cash

#### Get Cash Dashboard

```http
GET /api/cash/dashboard
```

**Response:**
```json
{
  "balances": [
    {
      "container": "BROKER",
      "currency": "GBP",
      "balance": "5000.00"
    },
    {
      "container": "ISA",
      "currency": "GBP",
      "balance": "10000.00"
    }
  ],
  "total_gbp_equivalent": "15000.00"
}
```

#### Record Cash Entry

```http
POST /api/cash/entries
Content-Type: application/json

{
  "entry_date": "2024-01-15",
  "container": "BROKER",
  "currency": "GBP",
  "amount": "1000.00",
  "description": "Dividend payment",
  "category": "DIVIDEND"
}
```

### Dividends

#### List Dividends

```http
GET /api/dividends?tax_year=2024-25
```

**Response:**
```json
{
  "dividends": [
    {
      "id": "uuid",
      "security_id": "uuid",
      "ticker": "AAPL",
      "payment_date": "2024-02-15",
      "total_amount_gbp": "100.00",
      "tax_treatment": "TAXABLE"
    }
  ],
  "summary": {
    "total_taxable_gbp": "500.00",
    "total_isa_exempt_gbp": "200.00"
  }
}
```

#### Record Dividend

```http
POST /api/dividends
Content-Type: application/json

{
  "security_id": "uuid",
  "lot_id": "uuid",
  "payment_date": "2024-02-15",
  "amount_per_share_native": "0.25",
  "total_amount_native": "25.00",
  "native_currency": "USD",
  "tax_treatment": "TAXABLE"
}
```

## Strategic Endpoints

### Tax Plan

#### Get Tax Plan

```http
GET /api/tax-plan?tax_year=2024-25
```

**Response:**
```json
{
  "tax_year": "2024-25",
  "income_tax_gbp": "15000.00",
  "national_insurance_gbp": "5000.00",
  "student_loan_gbp": "1000.00",
  "cgt_estimate_gbp": "2000.00",
  "total_tax_gbp": "23000.00",
  "assumptions": {
    "gross_income_gbp": "80000.00",
    "pension_sacrifice_gbp": "5000.00",
    "student_loan_plan": 2
  }
}
```

### Risk

#### Get Risk Summary

```http
GET /api/risk/summary
```

**Response:**
```json
{
  "concentration": {
    "top_holding_pct": "45.5",
    "employer_exposure_pct": "40.0"
  },
  "liquidity": {
    "sellable_pct": "70.0",
    "locked_pct": "20.0",
    "forfeitable_pct": "10.0"
  },
  "optionality_index": "65.0",
  "guardrails": [
    {
      "type": "CONCENTRATION_TOP_HOLDING",
      "status": "BREACHED",
      "threshold_pct": "50.0",
      "current_pct": "45.5"
    }
  ]
}
```

### Scenario Lab

#### Run Scenario

```http
POST /api/scenarios/run
Content-Type: application/json

{
  "name": "Sell 50% of AAPL",
  "execution_mode": "INDEPENDENT",
  "legs": [
    {
      "security_id": "uuid",
      "quantity": "50.00",
      "price_per_share_gbp": "150.00",
      "disposal_date": "2024-06-01"
    }
  ]
}
```

**Response:**
```json
{
  "scenario_id": "uuid",
  "net_proceeds_gbp": "7340.00",
  "total_cgt_gbp": "150.00",
  "total_employment_tax_gbp": "0.00",
  "legs": [
    {
      "leg_index": 0,
      "proceeds_gbp": "7500.00",
      "cost_basis_gbp": "6000.00",
      "gain_gbp": "1500.00"
    }
  ]
}
```

#### List Scenarios

```http
GET /api/scenarios
```

**Response:**
```json
{
  "scenarios": [
    {
      "scenario_id": "uuid",
      "name": "Sell 50% of AAPL",
      "created_at": "2024-01-15T10:30:45Z",
      "execution_mode": "INDEPENDENT",
      "leg_count": 1
    }
  ]
}
```

### Capital Stack

#### Get Capital Stack

```http
GET /api/capital-stack
```

**Response:**
```json
{
  "gross_value_gbp": "150000.00",
  "locked_value_gbp": "30000.00",
  "forfeitable_value_gbp": "20000.00",
  "hypothetical_liquid_gbp": "100000.00",
  "employment_tax_estimate_gbp": "5000.00",
  "cgt_estimate_gbp": "10000.00",
  "broker_fees_estimate_gbp": "500.00",
  "net_deployable_gbp": "84500.00",
  "deployable_cash_gbp": "15000.00",
  "total_deployable_gbp": "99500.00"
}
```

### Analytics

#### Get Analytics Dashboard

```http
GET /api/analytics/dashboard
```

**Response:**
```json
{
  "widgets": [
    {
      "id": "liquidity",
      "title": "Liquidity Breakdown",
      "criticality": "HIGH",
      "data": { ... }
    },
    {
      "id": "concentration",
      "title": "Concentration Risk",
      "criticality": "HIGH",
      "data": { ... }
    }
  ]
}
```

### Calendar

#### Get Calendar Events

```http
GET /api/calendar/events?horizon_days=90
```

**Response:**
```json
{
  "events": [
    {
      "event_id": "uuid",
      "event_type": "VEST",
      "event_date": "2024-03-15",
      "security_id": "uuid",
      "ticker": "AAPL",
      "quantity": "100.00",
      "value_at_stake_gbp": "15000.00"
    }
  ]
}
```

### Sell Plan

#### Create Sell Plan

```http
POST /api/sell-plans
Content-Type: application/json

{
  "name": "AAPL Disposal Plan",
  "security_id": "uuid",
  "total_quantity": "100.00",
  "method": "CALENDAR",
  "start_date": "2024-02-01",
  "end_date": "2024-06-01",
  "tranches": [
    {
      "date": "2024-02-01",
      "quantity": "25.00"
    }
  ]
}
```

#### Get Sell Plan

```http
GET /api/sell-plans/{plan_id}
```

**Response:**
```json
{
  "plan_id": "uuid",
  "name": "AAPL Disposal Plan",
  "status": "ACTIVE",
  "tranches": [
    {
      "tranche_id": "uuid",
      "date": "2024-02-01",
      "quantity": "25.00",
      "status": "PENDING",
      "estimated_proceeds_gbp": "3750.00"
    }
  ]
}
```

## Admin Endpoints

### Database Unlock

```http
POST /admin/unlock
Content-Type: application/json

{
  "db_path": "/path/to/portfolio.db",
  "password": "your-passphrase"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Database unlocked"
}
```

### Health Check

```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "version": "0.1.0"
}
```

### Settings

#### Get Settings

```http
GET /api/settings
```

**Response:**
```json
{
  "default_gross_income": "80000.00",
  "default_pension_sacrifice": "5000.00",
  "default_student_loan_plan": 2,
  "concentration_top_holding_alert_pct": "50.00",
  "price_stale_after_days": 1,
  "fx_stale_after_minutes": 10
}
```

#### Update Settings

```http
PUT /api/settings
Content-Type: application/json

{
  "default_gross_income": "85000.00",
  "concentration_top_holding_alert_pct": "45.00"
}
```

### Audit Log

#### Get Audit Log

```http
GET /api/audit?table_name=lots&limit=100
```

**Query Parameters:**
- `table_name` (optional): Filter by table
- `record_id` (optional): Filter by record
- `action` (optional): Filter by action type
- `limit` (optional): Results per page (default: 100)
- `cursor` (optional): Pagination cursor

**Response:**
```json
{
  "entries": [
    {
      "id": "uuid",
      "table_name": "lots",
      "record_id": "uuid",
      "action": "INSERT",
      "new_values": { ... },
      "created_at": "2024-01-15T10:30:45Z"
    }
  ],
  "next_cursor": "abc123",
  "has_more": true
}
```

## Error Handling

### HTTP Status Codes

| Code | Meaning | Usage |
|------|---------|-------|
| `200` | OK | Successful GET/PUT request |
| `201` | Created | Successful POST creating resource |
| `400` | Bad Request | Invalid input data |
| `401` | Unauthorized | Missing or invalid authentication |
| `403` | Forbidden | Authenticated but not authorized |
| `404` | Not Found | Resource doesn't exist |
| `409` | Conflict | Duplicate or conflicting resource |
| `422` | Unprocessable Entity | Validation error |
| `429` | Too Many Requests | Rate limit exceeded |
| `500` | Internal Server Error | Server-side error |

### Error Response Format

```json
{
  "detail": "Human-readable error message",
  "type": "validation_error",
  "errors": [
    {
      "loc": ["body", "quantity"],
      "msg": "value is not a valid decimal",
      "type": "type_error.decimal"
    }
  ]
}
```

### Common Error Scenarios

#### Validation Error (422)

```json
{
  "detail": [
    {
      "loc": ["body", "acquisition_date"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

#### Not Found (404)

```json
{
  "detail": "Security not found: uuid"
}
```

#### Conflict (409)

```json
{
  "detail": "Duplicate security: AAPL already exists"
}
```

#### Rate Limit (429)

```json
{
  "detail": "Rate limit exceeded: 5 per minute"
}
```

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Service Layer](./04-SERVICE-LAYER.md) - Business logic behind endpoints
- [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md) - API testing guide