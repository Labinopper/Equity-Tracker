"""
Fixtures shared across test_api tests.

Uses a file-based unencrypted SQLite database (via tmp_path) so that
AppSettings can be loaded from / saved to the filesystem alongside it.

TestClient is created WITHOUT using it as a context manager so that the
app lifespan hook (which reads env vars) is skipped.  AppContext is
initialised manually in the fixture instead.

Teardown always calls _state.set_db_path(None) and AppContext.lock() to
restore a clean singleton state between tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import _state
from src.api.app import app
from src.app_context import AppContext
from src.db.engine import DatabaseEngine
from src.db.models import Base


@pytest.fixture()
def db_engine(tmp_path: Path):
    """File-based unencrypted SQLite engine in a temp directory."""
    db_file = tmp_path / "test.db"
    engine = DatabaseEngine.open_unencrypted(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine.raw_engine)
    yield engine, db_file
    engine.dispose()


@pytest.fixture()
def client(db_engine):
    """
    TestClient with AppContext pre-initialised.

    The lifespan hook is NOT triggered (no context manager), so env-var
    auto-unlock does not interfere with tests.
    """
    engine, db_path = db_engine
    AppContext.initialize(engine)
    _state.set_db_path(db_path)
    _state.reset_refresh_diagnostics()
    yield TestClient(app, raise_server_exceptions=True)
    _state.reset_refresh_diagnostics()
    _state.set_db_path(None)
    AppContext.lock()
