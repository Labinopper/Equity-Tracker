from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import AppDiagnosticsLog


class AppDiagnosticsRepository:
    def __init__(self, sess: Session) -> None:
        self.sess = sess

    def add(self, entry: AppDiagnosticsLog) -> AppDiagnosticsLog:
        self.sess.add(entry)
        self.sess.flush()
        return entry

    def list_recent(
        self,
        *,
        severity: str | None = None,
        component: str | None = None,
        limit: int = 100,
    ) -> list[AppDiagnosticsLog]:
        stmt = select(AppDiagnosticsLog).order_by(desc(AppDiagnosticsLog.created_at)).limit(limit)
        if severity:
            stmt = stmt.where(AppDiagnosticsLog.severity == severity)
        if component:
            stmt = stmt.where(AppDiagnosticsLog.component == component)
        return list(self.sess.scalars(stmt).all())
