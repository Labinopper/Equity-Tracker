"""
AuditRepository — append-only writes to audit_log.

Rules:
  - No UPDATE or DELETE is ever run against audit_log.
  - Every call to log_*() adds a new row; nothing is ever modified.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def _write(
        self,
        table_name: str,
        record_id: str,
        action: str,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            table_name=table_name,
            record_id=record_id,
            action=action,
            old_values_json=json.dumps(old_values, default=str) if old_values else None,
            new_values_json=json.dumps(new_values, default=str) if new_values else None,
            notes=notes,
        )
        self._s.add(entry)
        return entry

    def log_insert(
        self,
        table_name: str,
        record_id: str,
        new_values: dict[str, Any],
        notes: str | None = None,
    ) -> AuditLog:
        return self._write(
            table_name, record_id, "INSERT", new_values=new_values, notes=notes
        )

    def log_update(
        self,
        table_name: str,
        record_id: str,
        old_values: dict[str, Any],
        new_values: dict[str, Any],
        notes: str | None = None,
    ) -> AuditLog:
        return self._write(
            table_name, record_id, "UPDATE",
            old_values=old_values, new_values=new_values, notes=notes,
        )

    def log_correction(
        self,
        table_name: str,
        record_id: str,
        notes: str | None = None,
    ) -> AuditLog:
        return self._write(table_name, record_id, "CORRECTION", notes=notes)

    def log_reversal(
        self,
        table_name: str,
        record_id: str,
        old_values: dict[str, Any],
        notes: str | None = None,
    ) -> AuditLog:
        return self._write(
            table_name, record_id, "REVERSAL", old_values=old_values, notes=notes
        )

    # ── Read ────────────────────────────────────────────────────────────────

    def list_for_record(
        self,
        table_name: str,
        record_id: str,
    ) -> list[AuditLog]:
        """
        Return all audit entries for a specific record, oldest first.

        Used by the Lot Explorer "View Audit Trail" feature to show the
        complete change history for a single lot, transaction, etc.
        """
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.table_name == table_name,
                AuditLog.record_id == record_id,
            )
            .order_by(AuditLog.changed_at.asc())
        )
        return list(self._s.execute(stmt).scalars())

    def list_all(
        self,
        table_name: str | None = None,
        record_id: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditLog]:
        """
        Return audit log entries, newest first.

        Args:
            table_name : Optional filter by table (e.g. "lots", "transactions").
            since      : If provided, only return entries after this UTC datetime.

        Used by the Reports > Audit Log tab.
        """
        stmt = select(AuditLog).order_by(AuditLog.changed_at.desc())
        if table_name is not None:
            stmt = stmt.where(AuditLog.table_name == table_name)
        if record_id is not None:
            stmt = stmt.where(AuditLog.record_id == record_id)
        if since is not None:
            stmt = stmt.where(AuditLog.changed_at >= since)
        return list(self._s.execute(stmt).scalars())
