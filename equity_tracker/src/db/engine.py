"""
Database engine factory — SQLCipher encryption + argon2id key derivation.

Encryption design:
  - Key derivation : argon2id (time_cost=3, memory_cost=65536 KiB, parallelism=1)
  - Output         : 32 raw bytes → 64 hex chars
  - Salt           : 16 random bytes, stored in {db_path}.salt (plain file)
  - SQLCipher key  : PRAGMA key = "x'<64 hex chars>'"
                     This bypasses SQLCipher's own PBKDF2 since we already derived
                     a strong key ourselves.

Usage:
    # New database
    engine = DatabaseEngine.create(Path("portfolio.db"), "my-password")

    # Existing database
    engine = DatabaseEngine.open(Path("portfolio.db"), "my-password")

    # Session context manager (auto-commit on success, rollback on exception)
    with engine.session() as sess:
        sess.add(Security(...))

    # Testing / development (no encryption, in-memory by default)
    engine = DatabaseEngine.open_unencrypted()

SQLCipher availability:
    Requires:  pip install sqlcipher3-binary
    If absent: .create() and .open() raise RuntimeError. open_unencrypted() always works.
"""

from __future__ import annotations

import os
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from .models import Base

# ---------------------------------------------------------------------------
# SQLCipher detection
# ---------------------------------------------------------------------------

try:
    import sqlcipher3.dbapi2 as _sqlcipher_dbapi  # type: ignore[import-untyped]

    SQLCIPHER_AVAILABLE = True
except ImportError:
    _sqlcipher_dbapi = None  # type: ignore[assignment]
    SQLCIPHER_AVAILABLE = False

# ---------------------------------------------------------------------------
# argon2 key derivation
# ---------------------------------------------------------------------------

try:
    from argon2.low_level import Type, hash_secret_raw  # type: ignore[import-untyped]

    _ARGON2_AVAILABLE = True
except ImportError:
    _ARGON2_AVAILABLE = False


_SALT_BYTES = 16
_KEY_BYTES = 32  # → 64 hex chars for SQLCipher raw key

# argon2id parameters (OWASP 2024 minimum for interactive logins)
_A2_TIME_COST = 3
_A2_MEMORY_COST = 65536  # 64 MiB
_A2_PARALLELISM = 1


def _derive_key(password: str, salt: bytes) -> str:
    """Derive a 32-byte key from password+salt using argon2id. Return 64 hex chars."""
    if not _ARGON2_AVAILABLE:
        raise RuntimeError(
            "argon2-cffi is required for key derivation. "
            "Install it with: pip install argon2-cffi"
        )
    raw: bytes = hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=_A2_TIME_COST,
        memory_cost=_A2_MEMORY_COST,
        parallelism=_A2_PARALLELISM,
        hash_len=_KEY_BYTES,
        type=Type.ID,
    )
    return raw.hex()


# ---------------------------------------------------------------------------
# SQLAlchemy engine factories
# ---------------------------------------------------------------------------

def _attach_sqlite_pragmas(engine, hex_key: str | None = None) -> None:
    """Register a connect event that configures the connection on open."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record) -> None:
        if hex_key:
            # Unlock SQLCipher with raw hex key (skips SQLCipher's internal KDF)
            dbapi_conn.execute(f"PRAGMA key = \"x'{hex_key}'\"")
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA synchronous = NORMAL")


def _make_encrypted_engine(db_path: Path, hex_key: str):
    """Create a SQLAlchemy engine backed by a SQLCipher file database."""
    if not SQLCIPHER_AVAILABLE:
        raise RuntimeError(
            "sqlcipher3 is required for encrypted databases. "
            "Install with: pip install sqlcipher3-binary"
        )

    path_str = str(db_path)

    def _creator():
        conn = _sqlcipher_dbapi.connect(path_str, check_same_thread=False)
        return conn

    # Use StaticPool for single-user desktop app (one connection, always unlocked).
    engine = create_engine(
        "sqlite://",
        creator=_creator,
        poolclass=StaticPool,
    )
    _attach_sqlite_pragmas(engine, hex_key=hex_key)
    return engine


def _make_plain_engine(db_url: str):
    """Create an unencrypted SQLAlchemy engine (plain sqlite3)."""
    is_memory = ":memory:" in db_url
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool if is_memory else NullPool,
    )
    _attach_sqlite_pragmas(engine, hex_key=None)
    return engine


# ---------------------------------------------------------------------------
# DatabaseEngine
# ---------------------------------------------------------------------------

class DatabaseEngine:
    """
    Thin wrapper around a SQLAlchemy engine that provides:
      - Encrypted or plain-SQLite creation/opening
      - A session() context manager with auto-commit/rollback
      - Schema initialisation (Base.metadata.create_all)
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    # ── Factory methods ────────────────────────────────────────────────────

    @classmethod
    def create(cls, db_path: Path, password: str) -> "DatabaseEngine":
        """
        Create a new encrypted SQLite database at db_path.

        A {db_path}.salt file is written alongside the database.
        If the .salt file already exists, it is OVERWRITTEN — meaning any
        existing encrypted data at db_path will be permanently inaccessible.
        Call .open() instead if the database already exists.
        """
        salt = os.urandom(_SALT_BYTES)
        salt_path = Path(str(db_path) + ".salt")
        salt_path.write_bytes(salt)

        hex_key = _derive_key(password, salt)
        engine = _make_encrypted_engine(db_path, hex_key)
        Base.metadata.create_all(engine)
        return cls(engine)

    @classmethod
    def open(cls, db_path: Path, password: str) -> "DatabaseEngine":
        """
        Open an existing encrypted database.

        Reads the salt from {db_path}.salt and derives the key.
        Raises FileNotFoundError if db_path or .salt file is missing.
        SQLCipher will raise an error on first query if the password is wrong.
        """
        salt_path = Path(str(db_path) + ".salt")
        if not salt_path.exists():
            raise FileNotFoundError(
                f"Salt file not found: {salt_path}. "
                "Was this database created with DatabaseEngine.create()?"
            )
        salt = salt_path.read_bytes()
        hex_key = _derive_key(password, salt)
        engine = _make_encrypted_engine(db_path, hex_key)
        return cls(engine)

    @classmethod
    def open_unencrypted(cls, db_url: str = "sqlite:///:memory:") -> "DatabaseEngine":
        """
        Open a plain (unencrypted) SQLite database.

        Intended for:
          - Unit / integration tests (default: in-memory)
          - Development without SQLCipher installed
          - Alembic migration execution against a plain DB for inspection

        WARNING: Do NOT use for production data. No encryption is applied.

        Schema note: ``create_all`` is called only for in-memory databases
        (tests).  File-based databases must be initialised exclusively via
        Alembic migrations — see ``migration_manager.ensure_migrated()``.
        """
        if "memory" not in db_url and "sqlcipher" not in db_url:
            warnings.warn(
                "DatabaseEngine.open_unencrypted() used with a file path. "
                "Data will NOT be encrypted.",
                stacklevel=2,
            )
        engine = _make_plain_engine(db_url)
        if ":memory:" in db_url:
            Base.metadata.create_all(engine)
        return cls(engine)

    # ── Session access ─────────────────────────────────────────────────────

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager that yields a SQLAlchemy Session.

        Commits on clean exit, rolls back on exception, always closes.

        Usage:
            with engine.session() as sess:
                sess.add(my_object)
                # auto-committed
        """
        sess: Session = self._Session()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def dispose(self) -> None:
        """Release all pooled connections. Call before process exit or DB re-key."""
        self._engine.dispose()

    @property
    def raw_engine(self):
        """Expose the underlying SQLAlchemy Engine (e.g. for Alembic env.py)."""
        return self._engine
