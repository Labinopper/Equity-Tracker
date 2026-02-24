"""
Fixtures shared across test_db tests.

All tests use an unencrypted in-memory SQLite database so they are:
  - Fast (no disk I/O)
  - Isolated (fresh DB per test function)
  - Portable (no SQLCipher installation required for the core test suite)

Encrypted DB tests (require sqlcipher3-binary) are in test_integration.py
and are individually marked with @pytest.mark.skipif(not SQLCIPHER_AVAILABLE, ...).
"""

from __future__ import annotations

import pytest

from src.db.engine import SQLCIPHER_AVAILABLE, DatabaseEngine
from src.db.repository import (
    AuditRepository,
    DisposalRepository,
    LotRepository,
    SecurityRepository,
)


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine for each test."""
    engine = DatabaseEngine.open_unencrypted()
    yield engine
    engine.dispose()


@pytest.fixture()
def session(db_engine):
    """Session context that auto-commits on yield exit."""
    with db_engine.session() as sess:
        yield sess


@pytest.fixture()
def repos(session):
    """All four repositories sharing the same session."""
    return {
        "securities": SecurityRepository(session),
        "lots":       LotRepository(session),
        "disposals":  DisposalRepository(session),
        "audit":      AuditRepository(session),
    }
