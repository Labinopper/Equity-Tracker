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


def _global_template_context(_request) -> dict[str, bool]:
    return {"hide_values": _is_hide_values_enabled()}


templates = Jinja2Templates(
    directory=str(_BASE_DIR / "templates"),
    context_processors=[_global_template_context],
)


@pass_context
def _money(context, value: object) -> str:
    """
    Format a monetary value to 2 decimal places with thousands separators.

    Accepts Decimal, str, int, or float.  Falls back to str(value) on error.
    """
    if bool(context.get("hide_values")):
        return "••••"
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
        return f"{d:,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


templates.env.filters["money"] = _money
