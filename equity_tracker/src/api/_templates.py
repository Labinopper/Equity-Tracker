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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from . import _state
from ..app_context import AppContext
from ..settings import AppSettings
from ..services.alert_service import AlertService

_BASE_DIR = Path(__file__).parent


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
    alert_center = {
        "total": 0,
        "alerts": [],
        "suppressed_total": 0,
        "suppressed_alerts": [],
        "thresholds": {},
        "policies": {},
    }
    db_path = _state.get_db_path()
    if db_path is not None and AppContext.is_initialized():
        try:
            settings = AppSettings.load(db_path)
            alert_center = AlertService.get_alert_center(
                settings=settings,
                db_path=db_path,
                as_of=date_type.fromisoformat(selected_as_of) if selected_as_of else None,
            )
        except Exception:
            alert_center = {
                "total": 0,
                "alerts": [],
                "suppressed_total": 0,
                "suppressed_alerts": [],
                "thresholds": {},
                "policies": {},
            }
    return {
        "hide_values": _is_hide_values_enabled(),
        "logout_url": "/auth/logout",
        "alert_center": alert_center,
        "selected_as_of": selected_as_of,
        "with_as_of": _with_as_of,
    }


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
