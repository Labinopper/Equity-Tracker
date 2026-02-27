"""
Fixtures shared across test_api tests.

Uses a file-based unencrypted SQLite database (via tmp_path) so that
AppSettings can be loaded from / saved to the filesystem alongside it.

TestClient is created WITHOUT using it as a context manager so that the
app lifespan hook (which reads env vars) is skipped.  AppContext is
initialised manually in the fixture instead.

Teardown always calls _state.set_db_path(None) and AppContext.lock() to
restore a clean singleton state between tests.

Session auth bypass
───────────────────
All test clients receive a pre-signed session cookie so that session_required
does not redirect tests to /auth/login.  The EQUITY_SECRET_KEY env var is
set via the ``auth_env`` autouse fixture.  Tests do NOT exercise the TOTP
login flow (that is tested separately in test_auth.py).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import _state
from src.api.app import app
from src.api.auth import SESSION_COOKIE_NAME, make_session_token
from src.app_context import AppContext
from src.db.engine import DatabaseEngine
from src.db.models import Base

# Fixed test secret — never used in production.
_TEST_SECRET_KEY = "test-secret-key-equity-tracker-testing-only-xx"
_TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    """
    Set auth env vars for every test in test_api/.

    ``autouse=True`` ensures this runs before any fixture that creates a
    session token (make_session_token reads EQUITY_SECRET_KEY from env).
    """
    monkeypatch.setenv("EQUITY_SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setenv("EQUITY_TOTP_SECRET", _TEST_TOTP_SECRET)
    monkeypatch.setenv("EQUITY_DEV_MODE", "true")  # allow HTTP cookies in tests


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
    TestClient with AppContext pre-initialised and a valid session cookie.

    The lifespan hook is NOT triggered (no context manager), so env-var
    auto-unlock does not interfere with tests.

    A pre-signed session cookie is injected so that session_required passes
    without going through the TOTP login flow.
    """
    engine, db_path = db_engine
    AppContext.initialize(engine)
    _state.set_db_path(db_path)
    _state.reset_refresh_diagnostics()
    token = make_session_token()
    yield TestClient(
        app,
        raise_server_exceptions=True,
        cookies={SESSION_COOKIE_NAME: token},
    )
    _state.reset_refresh_diagnostics()
    _state.set_db_path(None)
    AppContext.lock()
