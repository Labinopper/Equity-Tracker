"""
Risk routes (UI + JSON API).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.risk_service import RiskService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required
from ..schemas.risk import RiskSummarySchema

router = APIRouter(tags=["risk"])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/api/risk/summary", response_model=RiskSummarySchema)
async def api_risk_summary(_: None = Depends(db_required)) -> RiskSummarySchema:
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    summary = RiskService.get_risk_summary(settings=settings)
    return RiskSummarySchema.from_service(summary)


@router.get("/risk", response_class=HTMLResponse, include_in_schema=False)
async def risk_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    summary = RiskService.get_risk_summary(settings=settings)
    return templates.TemplateResponse(
        "risk.html",
        {
            "request": request,
            "risk_summary": summary,
            "settings": settings,
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )
