"""Shared persisted lifecycle rules for alerts and behavioral guardrails."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..app_context import AppContext
from ..db.models import PortfolioGuardrailStateEvent
from ..db.repository import AuditRepository

_ACTIVE = "ACTIVE"
_DISMISSED = "DISMISSED"
_SNOOZED = "SNOOZED"
_DEFAULT_DISMISS_DAYS = 30
_DEFAULT_SNOOZE_DAYS = 7


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash_payload(item: dict[str, Any]) -> str:
    payload = "|".join(
        [
            str(item.get("lifecycle_id") or ""),
            str(item.get("severity") or ""),
            str(item.get("title") or ""),
            str(item.get("message") or ""),
            str(item.get("href") or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_label(state: str) -> str:
    mapping = {
        _ACTIVE: "Active",
        _DISMISSED: "Dismissed",
        _SNOOZED: "Snoozed",
    }
    return mapping.get((state or "").upper(), state.title())


class AlertLifecycleService:
    DISMISS_MAX_DAYS = _DEFAULT_DISMISS_DAYS
    SNOOZE_MAX_DAYS = _DEFAULT_SNOOZE_DAYS

    @staticmethod
    def annotate_items(
        items: list[dict[str, Any]],
        *,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        annotated: list[dict[str, Any]] = []
        prefix = f"{namespace.strip()}:" if namespace and namespace.strip() else ""
        for item in items:
            enriched = dict(item)
            raw_id = str(
                enriched.get("lifecycle_id") or enriched.get("id") or ""
            ).strip()
            lifecycle_id = f"{prefix}{raw_id}" if prefix and raw_id else raw_id
            enriched["lifecycle_id"] = lifecycle_id
            enriched["condition_hash"] = _hash_payload(enriched)
            annotated.append(enriched)
        return annotated

    @staticmethod
    def latest_state_by_lifecycle_id(
        lifecycle_ids: set[str],
    ) -> dict[str, PortfolioGuardrailStateEvent]:
        if not lifecycle_ids:
            return {}
        with AppContext.read_session() as sess:
            rows = list(
                sess.scalars(
                    select(PortfolioGuardrailStateEvent)
                    .where(PortfolioGuardrailStateEvent.guardrail_id.in_(lifecycle_ids))
                    .order_by(
                        PortfolioGuardrailStateEvent.guardrail_id.asc(),
                        PortfolioGuardrailStateEvent.changed_at.desc(),
                    )
                ).all()
            )
        latest: dict[str, PortfolioGuardrailStateEvent] = {}
        for row in rows:
            if row.guardrail_id in latest:
                continue
            latest[row.guardrail_id] = row
        return latest

    @staticmethod
    def is_suppressed(
        item: dict[str, Any],
        state_row: PortfolioGuardrailStateEvent | None,
        *,
        now_utc: datetime | None = None,
    ) -> bool:
        if state_row is None:
            return False
        state = str(state_row.state or "").upper()
        if state not in {_DISMISSED, _SNOOZED}:
            return False
        if state_row.condition_hash and state_row.condition_hash != item.get("condition_hash"):
            return False
        if state_row.dismiss_until is None:
            return True
        current_time = now_utc or _utc_now()
        return _to_utc_aware(state_row.dismiss_until) > current_time

    @staticmethod
    def apply_visibility(
        items: list[dict[str, Any]],
        *,
        namespace: str | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        annotated = AlertLifecycleService.annotate_items(items, namespace=namespace)
        latest_rows = AlertLifecycleService.latest_state_by_lifecycle_id(
            {
                str(item.get("lifecycle_id") or "").strip()
                for item in annotated
                if str(item.get("lifecycle_id") or "").strip()
            }
        )
        visible: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        current_time = now_utc or _utc_now()
        for item in annotated:
            lifecycle_id = str(item.get("lifecycle_id") or "").strip()
            state_row = latest_rows.get(lifecycle_id)
            if AlertLifecycleService.is_suppressed(
                item, state_row, now_utc=current_time
            ):
                suppressed_item = dict(item)
                suppressed_item["suppression_state"] = str(state_row.state or "").upper()
                suppressed_item["suppression_state_label"] = _state_label(
                    str(state_row.state or "")
                )
                suppressed_item["suppressed_until_iso"] = (
                    _to_utc_aware(state_row.dismiss_until).isoformat()
                    if state_row.dismiss_until is not None
                    else None
                )
                suppressed.append(suppressed_item)
                continue
            visible.append(item)
        return {
            "active": visible,
            "suppressed": suppressed,
            "suppressed_total": len(suppressed),
        }

    @staticmethod
    def record_state_transition(
        *,
        lifecycle_id: str,
        condition_hash: str | None,
        action: str,
        source: str,
        notes: str | None = None,
        dismiss_days: int | None = None,
        snooze_days: int | None = None,
    ) -> dict[str, Any]:
        lifecycle_key = str(lifecycle_id or "").strip()
        action_key = str(action or "").strip().lower()
        condition_value = str(condition_hash or "").strip() or None
        if not lifecycle_key:
            raise ValueError("lifecycle_id is required.")

        now_utc = _utc_now()
        if action_key == "dismiss":
            state = _DISMISSED
            expires_at = now_utc + timedelta(
                days=dismiss_days or AlertLifecycleService.DISMISS_MAX_DAYS
            )
        elif action_key == "snooze":
            state = _SNOOZED
            expires_at = now_utc + timedelta(
                days=snooze_days or AlertLifecycleService.SNOOZE_MAX_DAYS
            )
        elif action_key in {"activate", "reset"}:
            state = _ACTIVE
            expires_at = None
        else:
            raise ValueError("Unsupported lifecycle action.")

        with AppContext.write_session() as sess:
            previous = sess.scalar(
                select(PortfolioGuardrailStateEvent)
                .where(PortfolioGuardrailStateEvent.guardrail_id == lifecycle_key)
                .order_by(PortfolioGuardrailStateEvent.changed_at.desc())
                .limit(1)
            )
            event = PortfolioGuardrailStateEvent(
                guardrail_id=lifecycle_key,
                state=state,
                condition_hash=condition_value,
                dismiss_until=expires_at,
                source=source,
                notes=notes,
            )
            sess.add(event)
            sess.flush()
            AuditRepository(sess).log_insert(
                table_name="portfolio_guardrail_state_events",
                record_id=event.id,
                new_values={
                    "guardrail_id": event.guardrail_id,
                    "state": event.state,
                    "condition_hash": event.condition_hash,
                    "dismiss_until": event.dismiss_until.isoformat()
                    if event.dismiss_until
                    else None,
                    "source": event.source,
                    "previous_state": str(previous.state or "").upper()
                    if previous is not None
                    else None,
                },
                notes=notes or "Alert lifecycle update.",
            )

        return {
            "ok": True,
            "lifecycle_id": lifecycle_key,
            "state": state,
            "state_label": _state_label(state),
            "condition_hash": condition_value,
            "until": expires_at.isoformat() if expires_at is not None else None,
            "policy": "until_condition_change_or_expiry"
            if state in {_DISMISSED, _SNOOZED}
            else "active",
            "dismiss_max_days": dismiss_days or AlertLifecycleService.DISMISS_MAX_DAYS,
            "snooze_max_days": snooze_days or AlertLifecycleService.SNOOZE_MAX_DAYS,
        }
