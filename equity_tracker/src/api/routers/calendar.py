"""
Calendar routes (UI + JSON API).
"""

from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.calendar_service import CalendarService
from ...services.sell_plan_service import SellPlanService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["calendar"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"
_DEFAULT_DAYS = 400
_MAX_DAYS = 1460


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


@router.get("/api/calendar/events")
async def api_calendar_events(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    sell_plan_id: str | None = Query(None),
    sell_method: str | None = Query(None),
    sell_status: str | None = Query(None),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    sell_events = SellPlanService.calendar_events(
        db_path=db_path,
        as_of=date_type.today(),
        horizon_days=days,
        sell_plan_id=sell_plan_id,
        sell_method=sell_method,
        sell_status=sell_status,
    )
    return CalendarService.get_events_payload(
        settings=settings,
        horizon_days=days,
        sell_plan_events=sell_events,
    )


@router.get("/calendar", response_class=HTMLResponse, include_in_schema=False)
async def calendar_page(
    request: Request,
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    sell_plan_id: str | None = Query(None),
    sell_method: str | None = Query(None),
    sell_status: str | None = Query(None),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    db_path = _state.get_db_path()
    sell_events = SellPlanService.calendar_events(
        db_path=db_path,
        as_of=date_type.today(),
        horizon_days=days,
        sell_plan_id=sell_plan_id,
        sell_method=sell_method,
        sell_status=sell_status,
    )
    payload = CalendarService.get_events_payload(
        settings=settings,
        horizon_days=days,
        sell_plan_events=sell_events,
    )
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "request": request,
            "calendar": payload,
            "settings": settings,
            "calendar_filters": {
                "sell_plan_id": sell_plan_id or "",
                "sell_method": sell_method or "",
                "sell_status": sell_status or "",
            },
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )
