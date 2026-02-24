"""
Analytics routes (UI + JSON API).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.analytics_service import AnalyticsService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required

router = APIRouter(tags=["analytics"])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"


def _load_settings() -> AppSettings | None:
    db_path = _state.get_db_path()
    return AppSettings.load(db_path) if db_path else None


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/api/analytics/portfolio-over-time")
async def api_portfolio_over_time(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    return AnalyticsService.get_portfolio_over_time(settings=settings)


@router.get("/api/analytics/summary")
async def api_analytics_summary(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    return AnalyticsService.get_summary(settings=settings)


@router.get("/analytics", response_class=HTMLResponse, include_in_schema=False)
async def analytics_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    summary = AnalyticsService.get_summary(settings=settings)
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "request": request,
            "analytics_summary": summary,
            "settings": settings,
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )
