"""
Calendar routes (UI + JSON API).
"""

from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...app_context import AppContext
from ...services.calendar_event_state_service import CalendarEventStateService
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
    as_of: date_type | None = Query(None),
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
        as_of=as_of or date_type.today(),
        horizon_days=days,
        sell_plan_id=sell_plan_id,
        sell_method=sell_method,
        sell_status=sell_status,
    )
    return CalendarService.get_events_payload(
        settings=settings,
        db_path=db_path,
        horizon_days=days,
        as_of=as_of,
        sell_plan_events=sell_events,
    )


@router.get("/calendar", response_class=HTMLResponse, include_in_schema=False)
async def calendar_page(
    request: Request,
    as_of: date_type | None = Query(None),
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
        as_of=as_of or date_type.today(),
        horizon_days=days,
        sell_plan_id=sell_plan_id,
        sell_method=sell_method,
        sell_status=sell_status,
    )
    payload = CalendarService.get_events_payload(
        settings=settings,
        db_path=db_path,
        horizon_days=days,
        as_of=as_of,
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
                "as_of": (as_of or payload.get("as_of_date") or ""),
                "sell_plan_id": sell_plan_id or "",
                "sell_method": sell_method or "",
                "sell_status": sell_status or "",
            },
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.post("/calendar/events/state", response_class=HTMLResponse, include_in_schema=False)
async def calendar_event_state_submit(
    request: Request,
    event_id: str = Form(...),
    completed: str = Form("false"),
    as_of: str = Form(""),
    days: int = Form(_DEFAULT_DAYS),
    sell_plan_id: str = Form(""),
    sell_method: str = Form(""),
    sell_status: str = Form(""),
) -> RedirectResponse:
    if not AppContext.is_initialized():
        return RedirectResponse("/calendar", status_code=303)

    db_path = _state.get_db_path()
    CalendarEventStateService.set_completed(
        db_path=db_path,
        event_id=(event_id or "").strip(),
        completed=str(completed or "").strip().lower() == "true",
    )

    query: list[str] = []
    if as_of:
        query.append(f"as_of={as_of}")
    if days:
        query.append(f"days={int(days)}")
    if sell_plan_id:
        query.append(f"sell_plan_id={sell_plan_id}")
    if sell_method:
        query.append(f"sell_method={sell_method}")
    if sell_status:
        query.append(f"sell_status={sell_status}")
    suffix = f"?{'&'.join(query)}" if query else ""
    return RedirectResponse(f"/calendar{suffix}", status_code=303)
