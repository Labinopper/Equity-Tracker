"""Write-side helpers for beta runtime status and notifications."""

from __future__ import annotations

import json
import os
import time
from datetime import date as date_type, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from ..context import BetaContext
from ..settings import BetaSettings
from ..state import get_supervisor_diagnostics
from ..db.models import (
    BetaBenchmarkBar,
    BetaHypothesis,
    BetaJobRun,
    BetaSignalCandidate,
    BetaStrategyVersion,
    BetaSystemStatus,
    BetaUiNotification,
    BetaUiSummarySnapshot,
    BetaValidationRun,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_SQLITE_LOCK_RETRY_DELAYS = (0.1, 0.25, 0.5, 1.0, 2.0)


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def _with_retry(op):
    last_exc: OperationalError | None = None
    attempts = len(_SQLITE_LOCK_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        try:
            return op()
        except OperationalError as exc:
            if not _is_sqlite_lock_error(exc):
                raise
            last_exc = exc
            if attempt >= len(_SQLITE_LOCK_RETRY_DELAYS):
                break
            time.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])
    if last_exc is not None:
        raise last_exc
    return None


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date_type)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return list(value)
    return str(value)


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, default=_json_default)


class BetaRuntimeService:
    """Persistence helpers used by the web process and supervisor."""

    @staticmethod
    def sync_system_status(
        *,
        core_db_path: Path | None,
        beta_db_path: Path,
        settings: BetaSettings,
        last_error: str | None = None,
        supervisor_status: str | None = None,
        supervisor_pid: int | None = None,
    ) -> None:
        diagnostics = get_supervisor_diagnostics()
        resolved_status = supervisor_status or str(diagnostics.get("supervisor_status") or "stopped")
        resolved_pid = supervisor_pid if supervisor_pid is not None else diagnostics.get("supervisor_pid")
        if os.environ.get("EQUITY_BETA_SUPERVISOR", "").strip() == "1":
            resolved_status = supervisor_status or "running"
            resolved_pid = supervisor_pid if supervisor_pid is not None else os.getpid()

        def _op() -> None:
            with BetaContext.write_session() as sess:
                row = sess.scalar(select(BetaSystemStatus).where(BetaSystemStatus.id == 1))
                if row is None:
                    row = BetaSystemStatus(id=1, beta_db_path=str(beta_db_path))
                    sess.add(row)
                row.core_db_path = str(core_db_path) if core_db_path is not None else None
                row.beta_db_path = str(beta_db_path)
                row.runtime_mode = settings.mode
                row.enabled = settings.enabled
                row.web_ui_enabled = settings.web_ui_enabled
                row.observation_enabled = settings.observation_enabled
                row.learning_enabled = settings.learning_enabled
                row.shadow_scoring_enabled = settings.shadow_scoring_enabled
                row.demo_execution_enabled = settings.demo_execution_enabled
                row.filings_enabled = settings.filings_enabled
                row.supervisor_status = resolved_status
                row.supervisor_pid = resolved_pid  # type: ignore[assignment]
                row.last_error = last_error or diagnostics.get("supervisor_last_error")  # type: ignore[assignment]
                row.last_heartbeat_at = _utcnow()

        _with_retry(_op)

    @staticmethod
    def touch_supervisor_status(
        *,
        supervisor_status: str | None = None,
        supervisor_pid: int | None = None,
    ) -> None:
        if not BetaContext.is_initialized():
            return

        def _op() -> None:
            with BetaContext.write_session() as sess:
                row = sess.scalar(select(BetaSystemStatus).where(BetaSystemStatus.id == 1))
                if row is None:
                    return
                resolved_status = supervisor_status
                resolved_pid = supervisor_pid
                if os.environ.get("EQUITY_BETA_SUPERVISOR", "").strip() == "1":
                    resolved_status = resolved_status or "running"
                    resolved_pid = resolved_pid if resolved_pid is not None else os.getpid()
                if resolved_status is not None:
                    row.supervisor_status = resolved_status
                if resolved_pid is not None:
                    row.supervisor_pid = resolved_pid  # type: ignore[assignment]
                row.last_heartbeat_at = _utcnow()

        _with_retry(_op)

    @staticmethod
    def record_job_run(
        *,
        job_name: str,
        job_type: str,
        status: str,
        details: dict | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        def _op() -> None:
            with BetaContext.write_session() as sess:
                sess.add(
                    BetaJobRun(
                        job_name=job_name,
                        job_type=job_type,
                        status=status,
                        details_json=_json_dumps(details or {}),
                        started_at=started_at or _utcnow(),
                        completed_at=completed_at or _utcnow(),
                    )
                )

        _with_retry(_op)

    @staticmethod
    def start_job_run(
        *,
        job_name: str,
        job_type: str,
        details: dict | None = None,
        started_at: datetime | None = None,
    ) -> str | None:
        if not BetaContext.is_initialized():
            return None

        def _op() -> str:
            with BetaContext.write_session() as sess:
                row = BetaJobRun(
                    job_name=job_name,
                    job_type=job_type,
                    status="RUNNING",
                    details_json=_json_dumps(details or {}),
                    started_at=started_at or _utcnow(),
                    completed_at=None,
                )
                sess.add(row)
                sess.flush()
                return row.id

        return _with_retry(_op)

    @staticmethod
    def finish_job_run(
        job_run_id: str | None,
        *,
        status: str,
        details: dict | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        if not BetaContext.is_initialized() or not job_run_id:
            return

        def _op() -> None:
            with BetaContext.write_session() as sess:
                row = sess.get(BetaJobRun, job_run_id)
                if row is None:
                    return
                row.status = status
                row.details_json = _json_dumps(details or {})
                row.completed_at = completed_at or _utcnow()

        _with_retry(_op)

    @staticmethod
    def finalize_running_jobs(
        *,
        status: str = "INTERRUPTED",
        message_text: str = "Supervisor stopped before the job completed.",
    ) -> int:
        if not BetaContext.is_initialized():
            return 0
        finished_at = _utcnow()

        def _op() -> int:
            finalized = 0
            with BetaContext.write_session() as sess:
                rows = list(sess.scalars(select(BetaJobRun).where(BetaJobRun.status == "RUNNING")).all())
                for row in rows:
                    details = {}
                    if row.details_json:
                        try:
                            payload = json.loads(row.details_json)
                            if isinstance(payload, dict):
                                details = payload
                        except (TypeError, ValueError, json.JSONDecodeError):
                            details = {}
                    details["interrupted"] = True
                    details["message"] = message_text
                    row.status = status
                    row.details_json = _json_dumps(details)
                    row.completed_at = finished_at
                    finalized += 1
            return finalized

        return int(_with_retry(_op) or 0)

    @staticmethod
    def record_notification(
        *,
        notification_type: str,
        severity: str,
        title: str,
        message_text: str,
        target_table: str | None = None,
        target_id: str | None = None,
    ) -> None:
        def _op() -> None:
            with BetaContext.write_session() as sess:
                sess.add(
                    BetaUiNotification(
                        notification_type=notification_type,
                        severity=severity,
                        title=title,
                        message_text=message_text,
                        target_table=target_table,
                        target_id=target_id,
                    )
                )

        _with_retry(_op)

    @staticmethod
    def ensure_daily_snapshot(settings: BetaSettings) -> None:
        snapshot_date = date_type.today()

        def _op() -> None:
            with BetaContext.write_session() as sess:
                existing = sess.scalar(
                    select(BetaUiSummarySnapshot).where(BetaUiSummarySnapshot.snapshot_date == snapshot_date)
                )
                if existing is not None:
                    return
                status = sess.scalar(select(BetaSystemStatus).where(BetaSystemStatus.id == 1))
                payload = {
                    "snapshot_date": snapshot_date.isoformat(),
                    "runtime_mode": status.runtime_mode if status is not None else settings.mode,
                    "observation_enabled": status.observation_enabled if status is not None else settings.observation_enabled,
                    "learning_enabled": status.learning_enabled if status is not None else settings.learning_enabled,
                    "shadow_scoring_enabled": status.shadow_scoring_enabled if status is not None else settings.shadow_scoring_enabled,
                    "demo_execution_enabled": status.demo_execution_enabled if status is not None else settings.demo_execution_enabled,
                    "filings_enabled": status.filings_enabled if status is not None else settings.filings_enabled,
                    "hypotheses_total": sess.scalar(select(func.count()).select_from(BetaHypothesis)) or 0,
                    "hypotheses_promoted": sess.scalar(
                        select(func.count()).select_from(BetaHypothesis).where(BetaHypothesis.status == "PROMOTED")
                    )
                    or 0,
                    "candidates_watching": sess.scalar(
                        select(func.count()).select_from(BetaSignalCandidate).where(BetaSignalCandidate.status == "WATCHING")
                    )
                    or 0,
                    "candidates_promoted": sess.scalar(
                        select(func.count()).select_from(BetaSignalCandidate).where(BetaSignalCandidate.status == "PROMOTED")
                    )
                    or 0,
                    "strategies_active": sess.scalar(
                        select(func.count()).select_from(BetaStrategyVersion).where(BetaStrategyVersion.is_active.is_(True))
                    )
                    or 0,
                    "validation_runs_total": sess.scalar(select(func.count()).select_from(BetaValidationRun)) or 0,
                    "benchmark_rows_total": sess.scalar(select(func.count()).select_from(BetaBenchmarkBar)) or 0,
                }
                sess.add(
                    BetaUiSummarySnapshot(
                        snapshot_date=snapshot_date,
                        summary_json=_json_dumps(payload),
                    )
                )
                if status is not None:
                    status.latest_snapshot_date = snapshot_date

        _with_retry(_op)
