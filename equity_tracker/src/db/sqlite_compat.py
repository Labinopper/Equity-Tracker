"""SQLite compatibility helpers for Python runtime changes."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

_ADAPTERS_REGISTERED = False


def register_sqlite_adapters() -> None:
    """Register explicit sqlite adapters to avoid deprecated implicit bindings."""
    global _ADAPTERS_REGISTERED
    if _ADAPTERS_REGISTERED:
        return

    sqlite3.register_adapter(date, lambda value: value.isoformat())
    sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
    _ADAPTERS_REGISTERED = True
