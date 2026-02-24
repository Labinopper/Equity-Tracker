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

_BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


def _money(value: object) -> str:
    """
    Format a monetary value to 2 decimal places with thousands separators.

    Accepts Decimal, str, int, or float.  Falls back to str(value) on error.
    """
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
        return f"{d:,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


templates.env.filters["money"] = _money
