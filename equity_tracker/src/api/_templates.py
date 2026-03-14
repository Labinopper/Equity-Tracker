"""
Shared Jinja2Templates instance for the UI layer.

Using a module-level singleton ensures that all UI route handlers share the
same template loader.  The path is resolved relative to this file so the
server works regardless of the working directory.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from . import _state
from ..app_context import AppContext
from ..settings import AppSettings
from ..services.alert_service import AlertService
from ..services.calendar_service import CalendarService

_BASE_DIR = Path(__file__).parent
_GLOBAL_CONTEXT_TTL_SECONDS = 5.0
_GLOBAL_CONTEXT_CACHE: dict[tuple[str | None, str | None], tuple[float, dict[str, object]]] = {}


def _is_hide_values_enabled() -> bool:
    db_path = _state.get_db_path()
    if db_path is None:
        return False
    try:
        return bool(AppSettings.load(db_path).hide_values)
    except OSError:
        return False


def _parse_as_of(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date_type.fromisoformat(raw[:10]).isoformat()
    except Exception:
        return None


def _with_as_of(href: str, as_of: str | None) -> str:
    value = _parse_as_of(as_of)
    if not value:
        return href
    parts = urlsplit(href)
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "as_of"]
    query_pairs.append(("as_of", value))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_pairs),
            parts.fragment,
        )
    )


def _global_template_context(_request) -> dict[str, bool | str]:
    selected_as_of = _parse_as_of(_request.query_params.get("as_of"))
    db_path = _state.get_db_path()
    cache_key = (str(db_path) if db_path is not None else None, selected_as_of)
    cached = _GLOBAL_CONTEXT_CACHE.get(cache_key)
    now = monotonic()
    if cached is not None and (now - cached[0]) <= _GLOBAL_CONTEXT_TTL_SECONDS:
        return dict(cached[1])

    alert_center = {
        "total": 0,
        "alerts": [],
        "suppressed_total": 0,
        "suppressed_alerts": [],
        "thresholds": {},
        "policies": {},
    }
    calendar_actionable_count = 0
    beta_nav_enabled = False
    hide_values = False
    if db_path is not None and AppContext.is_initialized():
        try:
            settings = AppSettings.load(db_path)
            hide_values = bool(settings.hide_values)
            alert_center = AlertService.get_alert_center(
                settings=settings,
                db_path=db_path,
                as_of=date_type.fromisoformat(selected_as_of) if selected_as_of else None,
            )
            calendar_payload = CalendarService.get_events_payload(
                as_of=date_type.fromisoformat(selected_as_of) if selected_as_of else None,
            )
            for event in calendar_payload.get("events") or []:
                if event.get("completed"):
                    continue
                event_date_raw = str(event.get("event_date") or "").strip()
                if not event_date_raw:
                    continue
                try:
                    event_date = date_type.fromisoformat(event_date_raw)
                except Exception:
                    continue
                comparison_date = date_type.fromisoformat(selected_as_of) if selected_as_of else date_type.today()
                if event_date <= comparison_date:
                    calendar_actionable_count += 1
            from ..beta.runtime_manager import beta_ui_is_enabled  # noqa: PLC0415
            from ..beta.state import get_beta_db_path  # noqa: PLC0415

            beta_nav_enabled = beta_ui_is_enabled(get_beta_db_path())
        except Exception:
            alert_center = {
                "total": 0,
                "alerts": [],
                "suppressed_total": 0,
                "suppressed_alerts": [],
                "thresholds": {},
                "policies": {},
            }
            calendar_actionable_count = 0
            beta_nav_enabled = False
            hide_values = False
    result: dict[str, object] = {
        "hide_values": hide_values,
        "logout_url": "/auth/logout",
        "alert_center": alert_center,
        "calendar_actionable_count": calendar_actionable_count,
        "beta_nav_enabled": beta_nav_enabled,
        "selected_as_of": selected_as_of,
        "with_as_of": _with_as_of,
    }
    _GLOBAL_CONTEXT_CACHE[cache_key] = (now, result)
    return dict(result)


templates = Jinja2Templates(
    directory=str(_BASE_DIR / "templates"),
    context_processors=[_global_template_context],
)


def _format_decimal(value: object) -> str:
    """Core formatting: 2 decimal places, thousands separators."""
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
        return f"{d:,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


@pass_context
def _money(context, value: object) -> str:
    """
    Format a private monetary value.

    Hides the value (shows ••••) when the hide_values setting is active.
    Use for personal financial data: holdings, costs, gains, net proceeds.
    Use `public_money` for publicly observable market prices.
    """
    if bool(context.get("hide_values")):
        return "••••"
    return _format_decimal(value)


def _public_money(value: object) -> str:
    """
    Format a publicly observable market price (never hidden).

    Use for per-share market prices, exchange rates, and other data that
    is publicly available from market feeds. Never suppressed by hide_values.
    """
    return _format_decimal(value)


templates.env.filters["money"] = _money
templates.env.filters["public_money"] = _public_money
templates.env.globals["with_as_of"] = _with_as_of
