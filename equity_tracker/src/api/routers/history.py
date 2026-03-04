"""
History routes — UI pages and JSON API for price history and portfolio value.

URL layout
──────────
  GET  /history                    History overview (portfolio chart + sparklines)
  GET  /history/{security_id}      Per-security detail (price + cost basis chart)

  GET  /api/history/portfolio      JSON: accurate portfolio value over time
  GET  /api/history/{security_id}  JSON: per-security price history

Note: /api/history/portfolio must be registered before /api/history/{security_id}
so that the literal path "portfolio" is matched before the path parameter.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.history_service import HistoryService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["history"], dependencies=[Depends(session_required)])
_HTML_UTF8 = "text/html; charset=utf-8"

_RANGE_DAYS: dict[str, int | None] = {
    "30d": 30,
    "90d": 90,
    "365d": 365,
    "all": None,
}


def _load_settings() -> AppSettings | None:
    db_path = _state.get_db_path()
    return AppSettings.load(db_path) if db_path else None


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8,
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@router.get("/api/history/portfolio")
async def api_portfolio_history(
    range: str = "all",
    _: None = Depends(db_required),
) -> dict:
    """Accurate portfolio value over time."""
    days = _RANGE_DAYS.get(range)
    from_date = date.today() - timedelta(days=days) if days else None
    settings = _load_settings()
    return HistoryService.get_portfolio_history(from_date=from_date, settings=settings)


@router.get("/api/history/{security_id}")
async def api_security_history(
    security_id: str,
    range: str = "all",
    _: None = Depends(db_required),
) -> dict:
    """Per-security price history, cost basis overlays, and summary stats."""
    days = _RANGE_DAYS.get(range)
    from_date = date.today() - timedelta(days=days) if days else None
    settings = _load_settings()
    return HistoryService.get_security_history(
        security_id,
        from_date=from_date,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# UI pages
# ---------------------------------------------------------------------------

@router.get("/history", response_class=HTMLResponse, include_in_schema=False)
async def history_overview(request: Request) -> HTMLResponse:
    """History overview — portfolio value chart + per-security sparkline cards."""
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    history_data = HistoryService.get_portfolio_history(settings=settings)
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "request": request,
            "history_data": history_data,
            "settings": settings,
        },
        media_type=_HTML_UTF8,
    )


@router.get("/history/{security_id}", response_class=HTMLResponse, include_in_schema=False)
async def history_security(request: Request, security_id: str) -> HTMLResponse:
    """Per-security history — price chart, cost basis overlay, and lot table."""
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    history_data = HistoryService.get_security_history(security_id, settings=settings)

    if history_data.get("error") == "security_not_found":
        return templates.TemplateResponse(
            request,
            "locked.html",
            {"request": request},
            status_code=404,
            media_type=_HTML_UTF8,
        )

    return templates.TemplateResponse(
        request,
        "history_security.html",
        {
            "request": request,
            "history_data": history_data,
            "settings": settings,
        },
        media_type=_HTML_UTF8,
    )
