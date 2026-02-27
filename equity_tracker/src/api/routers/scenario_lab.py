"""
Scenario Lab routes (UI + JSON API).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.scenario_service import ScenarioService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required
from ..schemas.scenario import ScenarioRunRequest

router = APIRouter(tags=["scenario-lab"], dependencies=[Depends(session_required)])
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


@router.post("/api/scenarios/run")
async def api_run_scenario(
    req: ScenarioRunRequest,
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    try:
        return ScenarioService.run_scenario(
            name=req.name,
            as_of=req.as_of_date,
            price_shock_pct=Decimal(req.price_shock_pct),
            legs=[leg.model_dump(mode="python") for leg in req.legs],
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc


@router.get("/api/scenarios/{scenario_id}")
async def api_get_scenario(
    scenario_id: str,
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    payload = ScenarioService.get_scenario(scenario_id, settings=settings)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Scenario not found."},
        )
    return payload


@router.get("/scenario-lab", response_class=HTMLResponse, include_in_schema=False)
async def scenario_lab_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    scenario_context = ScenarioService.get_builder_context(settings=settings)
    return templates.TemplateResponse(
        request,
        "scenario_lab.html",
        {
            "request": request,
            "scenario_context": scenario_context,
            "settings": settings,
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )
