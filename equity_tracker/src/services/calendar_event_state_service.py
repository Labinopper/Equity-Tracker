from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".calendar_events.json")


def _load_json(path: Path) -> dict[str, Any]:
    fallback = {"version": 1, "events": {}}
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    events = data.get("events", {})
    if not isinstance(events, dict):
        events = {}
    return {"version": int(data.get("version", 1)), "events": events}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class CalendarEventStateService:
    @staticmethod
    def load_states(db_path: Path | None) -> dict[str, dict[str, Any]]:
        if db_path is None:
            return {}
        payload = _load_json(_storage_path(db_path))
        result: dict[str, dict[str, Any]] = {}
        for event_id, raw_value in payload.get("events", {}).items():
            if not isinstance(raw_value, dict):
                continue
            result[str(event_id)] = {
                "completed": bool(raw_value.get("completed")),
                "completed_at_utc": str(raw_value.get("completed_at_utc") or "").strip() or None,
            }
        return result

    @staticmethod
    def set_completed(*, db_path: Path | None, event_id: str, completed: bool) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("db_path is required.")
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            raise ValueError("event_id is required.")
        path = _storage_path(db_path)
        payload = _load_json(path)
        events = payload.setdefault("events", {})
        if not isinstance(events, dict):
            events = {}
            payload["events"] = events
        events[normalized_event_id] = {
            "completed": bool(completed),
            "completed_at_utc": _now_utc_iso() if completed else None,
        }
        _save_json(path, payload)
        return dict(events[normalized_event_id])
