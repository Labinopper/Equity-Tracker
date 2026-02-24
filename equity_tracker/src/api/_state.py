"""
API-level mutable state — augments AppContext with web-specific data.

AppContext only manages the DatabaseEngine.  The web API also needs to know
the database file path so that AppSettings (stored as {db_path}.settings.json)
can be loaded and saved by the settings endpoints.

This module holds that state as a simple module-level variable.  It is set
when the database is unlocked (lifespan hook or POST /admin/unlock) and
cleared when the database is locked (POST /admin/lock or shutdown).

Thread safety: module-level writes are GIL-protected in CPython.  Since all
route handlers are async def (event-loop single-threaded), concurrent writes
cannot occur.  If the project ever moves to threaded handlers, add a lock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

_db_path: Path | None = None
_refresh_last_success_at: str | None = None
_refresh_last_error: str | None = None
_refresh_next_due_at: str | None = None


def set_db_path(path: Path | None) -> None:
    """Store the current database file path.  Pass None to clear."""
    global _db_path
    _db_path = path


def get_db_path() -> Path | None:
    """Return the current database file path, or None if the DB is locked."""
    return _db_path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_utc(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def set_refresh_next_due(seconds_from_now: int = 60) -> None:
    """Set the next expected refresh timestamp."""
    global _refresh_next_due_at
    due = _utc_now() + timedelta(seconds=seconds_from_now)
    _refresh_next_due_at = _fmt_utc(due)


def record_refresh_result(result: dict, *, interval_seconds: int = 60) -> None:
    """
    Update process-level refresh diagnostics from a /prices/refresh result.

    Expected result shape: {"fetched": int, "failed": int, "errors": list[dict]}
    """
    global _refresh_last_success_at, _refresh_last_error
    failed = int(result.get("failed", 0))
    errors = result.get("errors", []) or []
    now = _fmt_utc(_utc_now())

    if failed == 0:
        _refresh_last_success_at = now
        _refresh_last_error = None
    else:
        _refresh_last_success_at = now
        first_error = errors[0]["error"] if errors and isinstance(errors[0], dict) else "refresh failed"
        _refresh_last_error = f"{failed} failed ({first_error})"

    set_refresh_next_due(interval_seconds)


def record_refresh_exception(message: str, *, interval_seconds: int = 60) -> None:
    """Record a refresh failure that happened before a structured result existed."""
    global _refresh_last_error
    _refresh_last_error = message
    set_refresh_next_due(interval_seconds)


def get_refresh_diagnostics() -> dict[str, str | None]:
    """Return current refresh diagnostics for UI rendering."""
    return {
        "last_success_at": _refresh_last_success_at,
        "last_error": _refresh_last_error,
        "next_due_at": _refresh_next_due_at,
    }


def reset_refresh_diagnostics() -> None:
    """Clear refresh diagnostics (used by tests and app reset paths)."""
    global _refresh_last_success_at, _refresh_last_error, _refresh_next_due_at
    _refresh_last_success_at = None
    _refresh_last_error = None
    _refresh_next_due_at = None
