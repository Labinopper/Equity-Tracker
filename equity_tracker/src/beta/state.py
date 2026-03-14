"""Web-process state for the beta runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_beta_db_path: Path | None = None
_supervisor_pid: int | None = None
_supervisor_status: str = "stopped"
_supervisor_started_at: str | None = None
_supervisor_last_error: str | None = None


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def set_beta_db_path(path: Path | None) -> None:
    global _beta_db_path
    _beta_db_path = path


def get_beta_db_path() -> Path | None:
    return _beta_db_path


def record_supervisor_started(pid: int) -> None:
    global _supervisor_pid, _supervisor_status, _supervisor_started_at, _supervisor_last_error
    _supervisor_pid = pid
    _supervisor_status = "running"
    _supervisor_started_at = _utc_now_text()
    _supervisor_last_error = None


def record_supervisor_stopped() -> None:
    global _supervisor_pid, _supervisor_status
    _supervisor_pid = None
    _supervisor_status = "stopped"


def record_supervisor_error(message: str) -> None:
    global _supervisor_status, _supervisor_last_error
    _supervisor_status = "error"
    _supervisor_last_error = message


def get_supervisor_diagnostics() -> dict[str, str | int | None]:
    return {
        "supervisor_pid": _supervisor_pid,
        "supervisor_status": _supervisor_status,
        "supervisor_started_at": _supervisor_started_at,
        "supervisor_last_error": _supervisor_last_error,
    }

