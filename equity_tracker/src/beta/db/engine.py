"""Plain SQLite engine for the separate paper-trading beta database."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool


def _attach_sqlite_pragmas(engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA synchronous = NORMAL")


class BetaDatabaseEngine:
    """Small wrapper mirroring the core engine surface for the beta DB."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @classmethod
    def open(cls, db_path: Path) -> "BetaDatabaseEngine":
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        _attach_sqlite_pragmas(engine)
        return cls(engine)

    @classmethod
    def open_in_memory(cls) -> "BetaDatabaseEngine":
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        _attach_sqlite_pragmas(engine)
        return cls(engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        sess: Session = self._Session()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    @property
    def raw_engine(self):
        return self._engine

    def dispose(self) -> None:
        self._engine.dispose()
