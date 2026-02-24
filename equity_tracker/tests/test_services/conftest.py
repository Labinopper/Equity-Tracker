"""
Fixtures shared across test_services tests.

All tests use an unencrypted in-memory SQLite database (fresh per test) wired
into AppContext so that service methods work without modification.

AppContext is a class-level singleton, so each fixture:
  1. Initialises AppContext with the test engine (setup).
  2. Calls AppContext.lock() after the test (teardown) to restore a clean state.
"""

from __future__ import annotations

import pytest

from src.app_context import AppContext
from src.db.engine import DatabaseEngine


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine for each test."""
    engine = DatabaseEngine.open_unencrypted()
    yield engine
    engine.dispose()


@pytest.fixture()
def app_context(db_engine):
    """
    Initialise AppContext with the test engine.

    Ensures AppContext.lock() is always called on teardown so the class-level
    singleton is reset between tests.
    """
    AppContext.initialize(db_engine)
    yield
    AppContext.lock()
