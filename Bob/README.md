# Equity Tracker - Comprehensive Technical Documentation

**Last Updated:** 2026-04-03
**Reviewed By:** Bob (Technical Leader)

---

## 🎯 Start Here

**[📊 Executive Summary](./00-EXECUTIVE-SUMMARY.md)** - **READ THIS FIRST**
- Project status overview (Core: Stable, Beta: Critical Issues)
- Current Beta performance: 48.9% accuracy, 0 activated models
- 8 critical gaps preventing production deployment
- Recommended 24-week action plan with decision gates
- Go/no-go decision framework

---

## 📋 Documentation Index

This directory contains comprehensive technical documentation for the Equity Tracker project - a privacy-first, UK-focused personal equity compensation and investment tracking system.

### Core Documentation

1. **[Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md)**
   - System design and architectural patterns
   - Technology stack and dependencies
   - Component relationships and data flow

2. **[Database Schema](./02-DATABASE-SCHEMA.md)**
   - Complete database model documentation
   - Entity relationships and constraints
   - Migration strategy and versioning

3. **[API Reference](./03-API-REFERENCE.md)**
   - REST API endpoints and contracts
   - Authentication and authorization
   - Request/response formats

4. **[Service Layer](./04-SERVICE-LAYER.md)**
   - Business logic services
   - Service responsibilities and patterns
   - Inter-service dependencies

5. **[Beta Features](./05-BETA-FEATURES.md)**
   - Experimental paper-trading system
   - Hypothesis engine and research framework
   - Isolation and governance

6. **[Deployment Guide](./06-DEPLOYMENT-GUIDE.md)**
   - Installation and configuration
   - Docker deployment
   - Environment variables and security

7. **[Developer Onboarding](./07-DEVELOPER-ONBOARDING.md)**
   - Getting started guide
   - Development workflow
   - Testing and contribution guidelines

8. **[Tax Engine](./08-TAX-ENGINE.md)**
   - UK tax calculation logic
   - FIFO and UK matching rules
   - Employment tax and CGT

9. **[Future Roadmap](./09-FUTURE-ROADMAP.md)**
   - Bug fixes and technical debt
   - Feature enhancements
   - Performance optimizations
   - Strategic priorities and recommendation engine

10. **[Beta Production Readiness](./10-BETA-PRODUCTION-READINESS.md)**
    - Beta system architecture deep-dive
    - Critical gaps analysis (8 major gaps identified)
    - 6-month production readiness roadmap
    - Success metrics and risk management

11. **[Beta Realistic Assessment](./11-BETA-REALISTIC-ASSESSMENT.md)**
    - Honest evaluation of profit identification capability
    - Best/realistic/worst case scenarios
    - Key success factors and red flags
    - Go/no-go decision framework

12. **[Beta Implementation Plan](./12-BETA-IMPLEMENTATION-PLAN.md)** ⚠️ **ACTION REQUIRED**
    - Detailed 24-week implementation roadmap
    - Phase 1: Foundation & Validation (Weeks 1-8)
    - Phase 2: Live Market Integration (Weeks 9-16)
    - Phase 3: Buy Signals & Production (Weeks 17-24)
    - Concrete code changes and acceptance criteria

13. **[Implementation Next Steps](./13-IMPLEMENTATION-NEXT-STEPS.md)** 🚀 **START HERE FOR IMPLEMENTATION**
    - Concrete step-by-step implementation guide
    - Database migration code examples
    - Service implementation templates
    - Integration points and testing strategy
    - Week-by-week deliverables and checklists

## 🎯 Project Overview

**Equity Tracker** is a deterministic, privacy-first financial tracking system designed for UK-based individuals managing equity compensation (RSUs, ESPP, SIP) and investment portfolios.

### Key Characteristics

- **Privacy-First**: Local SQLite/SQLCipher database, no cloud dependencies
- **UK Tax Focus**: HMRC-compliant CGT, income tax, NI, student loan calculations
- **Deterministic**: No market predictions, only fact-based analysis
- **Comprehensive**: Tracks lots, disposals, dividends, cash, tax events
- **Segregated Beta**: Experimental predictive features isolated from core

### Technology Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy
- **Database**: SQLite with SQLCipher encryption
- **Frontend**: Jinja2 templates, vanilla JavaScript
- **Testing**: pytest with comprehensive coverage
- **Deployment**: Docker, uvicorn ASGI server

## 📊 Project Statistics

- **Total Services**: 40+ business logic services
- **Database Tables**: 20+ core tables + beta research tables
- **API Endpoints**: 100+ REST endpoints
- **Test Coverage**: Comprehensive unit and integration tests
- **Lines of Code**: ~50,000+ (excluding tests and docs)

## 🔒 Security Model

- **Encrypted Database**: SQLCipher for data at rest
- **TOTP Authentication**: Time-based one-time passwords
- **Session Management**: Secure cookie-based sessions
- **Rate Limiting**: Protection against brute force
- **CORS Configuration**: Controlled cross-origin access

## 🏗️ Architecture Principles

1. **Separation of Concerns**: Clear boundaries between API, services, and data layers
2. **Immutability**: Append-only audit logs, immutable lot records
3. **Determinism**: Reproducible calculations, no hidden state
4. **Testability**: Dependency injection, isolated test fixtures
5. **Privacy**: Local-first, no external data transmission

## 📁 Repository Structure

```
equity_tracker/
├── src/                    # Application source code
│   ├── api/               # FastAPI routes and middleware
│   ├── services/          # Business logic layer
│   ├── db/                # Database models and repositories
│   ├── core/              # Core domain logic (tax, lot matching)
│   └── beta/              # Experimental features (isolated)
├── tests/                 # Test suite
├── alembic/               # Database migrations
├── docs/                  # Strategic documentation
└── Bob/                   # Technical documentation (this folder)
```

## 🚀 Quick Start

For developers new to the project:

1. Read [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) for system understanding
2. Follow [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md) for setup
3. Review [Service Layer](./04-SERVICE-LAYER.md) for business logic patterns
4. Consult [API Reference](./03-API-REFERENCE.md) when working with endpoints

## 📞 Documentation Maintenance

This documentation should be updated when:

- New services or major features are added
- Database schema changes occur
- API contracts are modified
- Architectural patterns evolve
- Beta features graduate to core or are removed

## 🔗 Related Documentation

- **Strategic Docs**: [`../docs/STRATEGIC_DOCUMENTATION.md`](../docs/STRATEGIC_DOCUMENTATION.md)
- **TODO Tracking**: [`../docs/todo.md`](../docs/todo.md)
- **Beta Planning**: [`../docs/paper_trading_beta/`](../docs/paper_trading_beta/)

---

**Note**: This documentation focuses on technical implementation details. For product strategy, user workflows, and decision-making frameworks, refer to the strategic documentation in the [`docs/`](../docs/) directory.