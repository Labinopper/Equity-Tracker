"""NotificationDigestService - deterministic digest of alerts, freshness, and upcoming events."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..settings import AppSettings
from .alert_service import AlertService
from .calendar_service import CalendarService
from .sell_plan_service import SellPlanService

_MONEY_Q = Decimal("0.01")
_CATEGORY_THRESHOLD = "THRESHOLD_BREACH"
_CATEGORY_FRESHNESS = "STALE_DATA"
_CATEGORY_UPCOMING = "UPCOMING_EVENT"

_EVENT_TYPES_INCLUDED = frozenset(
    {"FORFEITURE_END", "TAX_YEAR_END", "TAX_YEAR_START", "SELL_TRANCHE"}
)


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _safe_decimal(value: object, fallback: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _with_as_of(href: str, as_of_date: date | None) -> str:
    if as_of_date is None:
        return href
    parts = urlsplit(href)
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "as_of"
    ]
    query_pairs.append(("as_of", as_of_date.isoformat()))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_pairs),
            parts.fragment,
        )
    )


def _severity_rank(value: str) -> int:
    order = {"critical": 0, "warning": 1, "info": 2}
    return order.get(str(value or "info").lower(), 9)


class NotificationDigestService:
    @staticmethod
    def get_digest(
        *,
        settings: AppSettings | None,
        db_path,
        as_of: date | None = None,
        horizon_days: int = 30,
        max_items: int = 12,
    ) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        bounded_horizon = max(7, min(int(horizon_days), 120))
        bounded_max_items = max(3, min(int(max_items), 40))

        alert_center = AlertService.get_alert_center(
            settings=settings,
            db_path=db_path,
            as_of=as_of_date,
        )
        sell_plan_events = SellPlanService.calendar_events(
            db_path=db_path,
            as_of=as_of_date,
            horizon_days=bounded_horizon,
        )
        calendar_payload = CalendarService.get_events_payload(
            settings=settings,
            db_path=db_path,
            as_of=as_of_date,
            horizon_days=bounded_horizon,
            sell_plan_events=sell_plan_events,
        )

        entries: list[dict[str, Any]] = []

        for alert in alert_center.get("alerts", []):
            alert_id = str(alert.get("id") or "")
            category = None
            source_rule = None
            if alert_id.startswith("concentration_"):
                category = _CATEGORY_THRESHOLD
                source_rule = "alert_center.concentration_guardrail"
            elif alert_id in {"market_data_stale", "tax_inputs_stale"}:
                category = _CATEGORY_FRESHNESS
                source_rule = "alert_center.data_quality"
            if category is None:
                continue
            entries.append(
                {
                    "category": category,
                    "severity": str(alert.get("severity") or "info"),
                    "title": str(alert.get("title") or ""),
                    "message": str(alert.get("message") or ""),
                    "event_date": str(alert.get("event_date") or as_of_date.isoformat()),
                    "days_until": 0,
                    "source_surface": "Risk",
                    "source_rule": source_rule,
                    "href": _with_as_of(str(alert.get("href") or "/risk#alert-center"), as_of_date),
                }
            )

        for event in calendar_payload.get("events", []):
            event_type = str(event.get("event_type") or "")
            if event_type not in _EVENT_TYPES_INCLUDED:
                continue
            surface = "Calendar"
            source_rule = "calendar.constraint_timeline"
            if event_type == "SELL_TRANCHE":
                source_rule = "sell_plan.calendar_events"
            entries.append(
                {
                    "category": _CATEGORY_UPCOMING,
                    "severity": "warning" if event_type == "FORFEITURE_END" else "info",
                    "title": str(event.get("title") or ""),
                    "message": str(event.get("subtitle") or ""),
                    "event_date": str(event.get("event_date") or ""),
                    "days_until": int(event.get("days_until") or 0),
                    "source_surface": surface,
                    "source_rule": source_rule,
                    "href": _with_as_of(str(event.get("deep_link") or "/calendar"), as_of_date),
                    "value_at_stake_gbp": (
                        str(_q_money(_safe_decimal(event.get("value_at_stake_gbp"))))
                        if event.get("value_at_stake_gbp") is not None
                        else None
                    ),
                }
            )

        entries.sort(
            key=lambda row: (
                _severity_rank(str(row.get("severity") or "info")),
                str(row.get("event_date") or ""),
                str(row.get("title") or ""),
            )
        )

        threshold_count = sum(1 for row in entries if row["category"] == _CATEGORY_THRESHOLD)
        freshness_count = sum(1 for row in entries if row["category"] == _CATEGORY_FRESHNESS)
        upcoming_count = sum(1 for row in entries if row["category"] == _CATEGORY_UPCOMING)

        top_entry = entries[0] if entries else None

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "as_of_date": as_of_date.isoformat(),
            "horizon_days": bounded_horizon,
            "max_items": bounded_max_items,
            "summary": {
                "total_entries": len(entries),
                "threshold_breach_count": threshold_count,
                "stale_data_count": freshness_count,
                "upcoming_event_count": upcoming_count,
                "top_entry_title": top_entry.get("title") if top_entry else None,
            },
            "entries": entries[:bounded_max_items],
            "trace_links": {
                "alert_center": _with_as_of("/risk#alert-center", as_of_date),
                "calendar": _with_as_of("/calendar", as_of_date),
                "data_quality": _with_as_of("/data-quality", as_of_date),
            },
            "model_scope": {
                "inputs": [
                    "Active alert-center rules and visibility state",
                    "Calendar and sell-plan timeline events inside the chosen horizon",
                ],
                "assumptions": [
                    "Digest entries are generated only from deterministic current-state rules",
                    "Event ordering follows severity first, then event date",
                ],
                "exclusions": [
                    "No outbound notification delivery subsystem",
                    "No prioritization based on predicted market outcomes",
                ],
            },
            "notes": [
                "Digest entries are generated exclusively from existing deterministic rules and state.",
                "Threshold breaches reuse the active alert-center policy state, including persisted snooze/dismiss visibility.",
                "Upcoming events are limited to forfeiture, tax-year, and sell-plan timing items inside the selected horizon.",
            ],
        }
