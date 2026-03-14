from __future__ import annotations

import json
from typing import Any

from ..app_context import AppContext
from ..db.models import AppDiagnosticsLog
from ..db.repository import AppDiagnosticsRepository


class AppDiagnosticsService:
    @staticmethod
    def record(
        *,
        severity: str,
        component: str,
        title: str,
        message_text: str,
        event_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        if not AppContext.is_initialized():
            return
        payload = json.dumps(context, sort_keys=True, default=str) if context else None
        with AppContext.write_session() as sess:
            repo = AppDiagnosticsRepository(sess)
            repo.add(
                AppDiagnosticsLog(
                    severity=severity,
                    component=component,
                    event_type=event_type,
                    title=title,
                    message_text=message_text,
                    context_json=payload,
                )
            )

    @staticmethod
    def recent(
        *,
        severity: str | None = None,
        component: str | None = None,
        limit: int = 100,
    ) -> list[AppDiagnosticsLog]:
        if not AppContext.is_initialized():
            return []
        with AppContext.read_session() as sess:
            repo = AppDiagnosticsRepository(sess)
            return repo.list_recent(severity=severity, component=component, limit=limit)
