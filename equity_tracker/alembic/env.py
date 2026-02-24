"""
Alembic environment — configures the migration engine.

Supports two modes:
  1. Encrypted (SQLCipher): set EQUITY_DB_PATH + EQUITY_DB_PASSWORD
  2. Unencrypted (plain SQLite): set EQUITY_DB_PATH + EQUITY_DB_ENCRYPTED=false
                                 (useful for schema inspection / CI / local dev)

Environment variables (primary names match the application):
  EQUITY_DB_PATH       — absolute path to the .db file
  EQUITY_DB_PASSWORD   — database password (encrypted mode only)
  EQUITY_DB_ENCRYPTED  — "true" (default) | "false" for plain-SQLite

Legacy aliases (checked as fallbacks for backward compatibility):
  EQUITY_TRACKER_DB_PATH, EQUITY_TRACKER_DB_PASSWORD, EQUITY_TRACKER_NO_ENCRYPT

The src package is added to sys.path so models can be imported without installation.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# Ensure 'src' is importable when running alembic from project root.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.db.models import Base  # noqa: E402  (must come after sys.path fix)

# ---------------------------------------------------------------------------
# Alembic Config
# ---------------------------------------------------------------------------

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------

def _build_engine():
    """Build a SQLAlchemy engine from environment variables.

    Primary variable names (EQUITY_DB_*) match the application so that the
    same environment used to run the server can also run Alembic without any
    extra configuration.  The legacy EQUITY_TRACKER_* names are checked as
    fallbacks so existing scripts are not broken.
    """
    # Resolve DB path — primary name first, legacy fallback second.
    db_path_str = (
        os.environ.get("EQUITY_DB_PATH")
        or os.environ.get("EQUITY_TRACKER_DB_PATH")
    )

    # Determine whether to use plain SQLite.
    #   EQUITY_DB_ENCRYPTED=false  → plain SQLite  (primary)
    #   EQUITY_TRACKER_NO_ENCRYPT=1 → plain SQLite (legacy)
    db_encrypted = os.environ.get("EQUITY_DB_ENCRYPTED", "true").strip().lower()
    no_encrypt_legacy = os.environ.get("EQUITY_TRACKER_NO_ENCRYPT", "").strip()
    is_plain = (db_encrypted == "false") or bool(no_encrypt_legacy)

    if db_path_str is None or is_plain:
        # Plain SQLite for Alembic — create engine directly so create_all is
        # NOT called; Alembic owns schema creation here.
        url = f"sqlite:///{db_path_str}" if db_path_str else "sqlite:///dev_migration.db"
        from sqlalchemy import create_engine, event
        from sqlalchemy.pool import NullPool

        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=NullPool)

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _cr) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys = ON")
            dbapi_conn.execute("PRAGMA journal_mode = WAL")
            dbapi_conn.execute("PRAGMA synchronous = NORMAL")

        return engine

    # Encrypted path — DatabaseEngine handles key derivation + SQLCipher setup.
    from src.db.engine import DatabaseEngine

    password = (
        os.environ.get("EQUITY_DB_PASSWORD")
        or os.environ.get("EQUITY_TRACKER_DB_PASSWORD")
    )
    if not password:
        raise RuntimeError(
            "EQUITY_DB_PASSWORD must be set for encrypted migrations."
        )
    db_path = Path(db_path_str)
    if db_path.exists():
        engine = DatabaseEngine.open(db_path, password)
    else:
        engine = DatabaseEngine.create(db_path, password)

    return engine.raw_engine


# ---------------------------------------------------------------------------
# Offline / online migration runners
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite ALTER TABLE emulation
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    engine = _build_engine()

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # Required for SQLite column changes
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
