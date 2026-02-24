"""
AppContext — application-wide database session management.

Single source of truth for the active DatabaseEngine during the application
lifetime.  All UI views obtain sessions from here; none interact with
DatabaseEngine directly after startup.

Design notes:
  - Class-level singleton: no instantiation required; methods are classmethods.
  - Single-threaded: designed for the Qt UI thread.  No locking is performed.
  - read_session() and write_session() are semantically distinct — read_session()
    communicates to the caller (and code reviewers) that no mutations are
    intended, but both delegate to the same DatabaseEngine.session() context
    manager for simplicity.

Typical lifecycle:
    # On unlock / create:
    engine = DatabaseEngine.open(path, password)
    AppContext.initialize(engine)

    # Reads (views, reports):
    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_active_lots_for_security(sid)

    # Writes (disposals, add lot):
    with AppContext.write_session() as sess:
        LotRepository(sess).add(lot, audit=AuditRepository(sess))

    # On lock (menu item or app close):
    AppContext.lock()
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from .db.engine import DatabaseEngine
from .db.models import Base


class AppContextError(RuntimeError):
    """Raised when AppContext is used before a database is initialized."""


class AppContext:
    """Application-level database session manager (class-level singleton)."""

    _engine: DatabaseEngine | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @classmethod
    def initialize(cls, engine: DatabaseEngine) -> None:
        """
        Set the active database engine.

        Must be called once after DatabaseEngine.create() or .open() succeeds.
        Calling again replaces the previous engine (the old one is NOT disposed
        automatically — call lock() first if you want explicit cleanup).
        """
        cls._engine = engine

    @classmethod
    def is_initialized(cls) -> bool:
        """Return True if an engine is currently active."""
        return cls._engine is not None

    @classmethod
    def lock(cls) -> None:
        """
        Dispose the engine and clear state (equivalent to locking the database).

        After this call, initialize() must be called again before any session
        access.  Safe to call even if no engine is active (no-op).
        """
        if cls._engine is not None:
            cls._engine.dispose()
            cls._engine = None

    @classmethod
    def recreate_schema(cls) -> None:
        """
        Drop and recreate all ORM-managed tables on the active database.

        This is intended for explicit destructive maintenance actions
        (for example a user-triggered DB reset in development).

        Raises:
            AppContextError: if the context has not been initialized.
        """
        if cls._engine is None:
            raise AppContextError(
                "AppContext is not initialized. "
                "Call AppContext.initialize(engine) after unlocking the database."
            )

        raw_engine = cls._engine.raw_engine
        Base.metadata.drop_all(raw_engine)
        Base.metadata.create_all(raw_engine)

    # ── Session access ──────────────────────────────────────────────────────

    @classmethod
    @contextmanager
    def write_session(cls) -> Generator[Session, None, None]:
        """
        Short-lived session for mutations.

        Auto-commits on clean exit; rolls back and re-raises on exception.
        Always closes the session on exit (success or failure).

        Raises:
            AppContextError: if the context has not been initialized.
        """
        if cls._engine is None:
            raise AppContextError(
                "AppContext is not initialized. "
                "Call AppContext.initialize(engine) after unlocking the database."
            )
        with cls._engine.session() as sess:
            yield sess

    @classmethod
    @contextmanager
    def read_session(cls) -> Generator[Session, None, None]:
        """
        Short-lived session for read-only queries.

        Semantically communicates read intent to callers.  Functionally
        identical to write_session() — both use DatabaseEngine.session() which
        auto-commits (a no-op for pure reads) and always closes.

        Raises:
            AppContextError: if the context has not been initialized.
        """
        with cls.write_session() as sess:
            yield sess
