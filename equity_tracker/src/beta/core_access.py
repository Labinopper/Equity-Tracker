"""Read access to the core Equity Tracker database from beta code."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy.orm import Session

from ..app_context import AppContext
from ..db.engine import DatabaseEngine


@contextmanager
def core_read_session() -> Generator[Session, None, None]:
    """
    Yield a read-capable core DB session.

    Uses the in-process AppContext when available. Otherwise opens the core DB
    from the same environment variables used by the main app and supervisor.
    """
    if AppContext.is_initialized():
        with AppContext.read_session() as sess:
            yield sess
        return

    db_path_str = os.environ.get("EQUITY_DB_PATH", "").strip()
    db_password = os.environ.get("EQUITY_DB_PASSWORD", "").strip()
    db_encrypted = os.environ.get("EQUITY_DB_ENCRYPTED", "true").lower() != "false"
    if not db_path_str:
        raise RuntimeError("Core database path is not configured for beta access.")

    path = Path(db_path_str)
    if db_encrypted:
        engine = DatabaseEngine.open(path, db_password)
    else:
        engine = DatabaseEngine.open_unencrypted(f"sqlite:///{path}")

    try:
        with engine.session() as sess:
            yield sess
    finally:
        engine.dispose()
