"""Separate DB session context for the paper-trading beta."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from .db.engine import BetaDatabaseEngine


class BetaContextError(RuntimeError):
    """Raised when the beta DB is accessed before initialization."""


class BetaContext:
    _engine: BetaDatabaseEngine | None = None

    @classmethod
    def initialize(cls, engine: BetaDatabaseEngine) -> None:
        cls._engine = engine

    @classmethod
    def is_initialized(cls) -> bool:
        return cls._engine is not None

    @classmethod
    def lock(cls) -> None:
        if cls._engine is not None:
            cls._engine.dispose()
            cls._engine = None

    @classmethod
    @contextmanager
    def write_session(cls) -> Generator[Session, None, None]:
        if cls._engine is None:
            raise BetaContextError("BetaContext is not initialized.")
        with cls._engine.session() as sess:
            yield sess

    @classmethod
    @contextmanager
    def read_session(cls) -> Generator[Session, None, None]:
        with cls.write_session() as sess:
            yield sess
