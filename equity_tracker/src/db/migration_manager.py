"""
Migration safety guard — ensures the database is always at Alembic head.

Called once during application startup (FastAPI lifespan) before AppContext
is initialised.  Raises RuntimeError on failure so the app remains locked
rather than starting against a mismatched schema.

Logic
─────
  1. Probe the target DB to read its current state.
  2. If ``alembic_version`` is absent:
       a. App tables exist  → legacy DB created via ``create_all()``
          Stamp to revision 001 so Alembic knows the base schema is in place,
          then upgrade to head (applies any revisions after 001).
       b. No app tables     → fresh / empty DB
          Do NOT stamp; let ``upgrade head`` run all migrations from scratch.
  3. Run ``alembic upgrade head`` (always).  No-op when already at head.
  4. Raise RuntimeError on any Alembic failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Resolve paths relative to this file:
#   src/db/migration_manager.py  →  up 3 levels  →  equity_tracker/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"
_ALEMBIC_DIR = _PROJECT_ROOT / "alembic"


def ensure_migrated(db_url: str) -> None:
    """
    Guarantee the database at *db_url* is at Alembic head before startup.

    Parameters
    ----------
    db_url:
        Plain-SQLite URL, e.g. ``"sqlite:///C:/Users/labin/portfolio.db"``.
        Must be accessible without encryption (plain SQLite only).

    Raises
    ------
    RuntimeError
        If Alembic fails to stamp or upgrade.  The caller must NOT initialise
        ``AppContext`` after this error — the app starts in locked state.
    """
    # ── 1. Inspect current state ─────────────────────────────────────────────
    probe = create_engine(
        db_url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    try:
        with probe.connect() as conn:
            table_names = set(sa_inspect(conn).get_table_names())
    finally:
        probe.dispose()

    has_alembic_version = "alembic_version" in table_names
    has_app_tables = "securities" in table_names

    # ── 2. Build Alembic config (absolute paths — CWD-independent) ──────────
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))

    # ── 3. Stamp if DB was created outside Alembic ───────────────────────────
    if not has_alembic_version:
        if has_app_tables:
            # Legacy DB: base schema exists (from create_all) but was never
            # tracked by Alembic.  Stamp 001 so upgrade head applies only the
            # delta migrations (002 onward).
            logger.warning(
                "Database not stamped — existing schema detected. "
                "Registering initial revision (001)."
            )
            try:
                command.stamp(cfg, "001")
            except Exception as exc:
                raise RuntimeError(f"Alembic stamp failed: {exc}") from exc
        else:
            # Fresh DB: no tables at all.  upgrade head will run all migrations
            # from scratch (001 creates base tables, 002+ adds later schema).
            logger.info(
                "Fresh database detected — Alembic will create all tables."
            )

    # ── 4. Apply pending migrations ──────────────────────────────────────────
    logger.info("Applying migrations...")
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(f"Alembic upgrade failed: {exc}") from exc

    logger.info("Database schema up to date.")
