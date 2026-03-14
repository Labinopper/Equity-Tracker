"""Write-side helpers for beta runtime status and notifications."""

from __future__ import annotations

import json
import os
from datetime import date as date_type, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

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

    @staticmethod
    def record_job_run(
        *,
        job_name: str,
        job_type: str,
        status: str,
        details: dict | None = None,
    ) -> None:
        with BetaContext.write_session() as sess:
            sess.add(
                BetaJobRun(
                    job_name=job_name,
                    job_type=job_type,
                    status=status,
                    details_json=json.dumps(details or {}, sort_keys=True),
                    completed_at=_utcnow(),
                )
            )

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

    @staticmethod
    def ensure_daily_snapshot(settings: BetaSettings) -> None:
        snapshot_date = date_type.today()
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
                    summary_json=json.dumps(payload, sort_keys=True),
                )
            )
            if status is not None:
                status.latest_snapshot_date = snapshot_date
