"""
Shared Jinja2Templates instance for the UI layer.

Using a module-level singleton ensures that all UI route handlers share the
same template loader.  The path is resolved relative to this file so the
server works regardless of the working directory.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from . import _state
from ..settings import AppSettings

_BASE_DIR = Path(__file__).parent


def _is_hide_values_enabled() -> bool:
    db_path = _state.get_db_path()
    if db_path is None:
        return False
    try:
        return bool(AppSettings.load(db_path).hide_values)
    except OSError:
        return False


def _global_template_context(_request) -> dict[str, bool | str]:
    return {
        "hide_values": _is_hide_values_enabled(),
        "logout_url": "/auth/logout",
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
