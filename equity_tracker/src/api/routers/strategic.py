"""Stage-10 strategic pages and APIs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.strategic_service import StrategicService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["strategic"], dependencies=[Depends(session_required)])
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


@router.get("/api/strategic/capital-efficiency")
async def api_capital_efficiency(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return StrategicService.get_capital_efficiency(settings=settings, db_path=db_path)


@router.get("/api/strategic/employment-exit")
async def api_employment_exit(
    exit_date: date = Query(default_factory=date.today),
    price_shock_pct: str = Query("0"),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    try:
        shock = Decimal(price_shock_pct)
    except (InvalidOperation, ValueError):
        shock = Decimal("0")
    return StrategicService.get_employment_exit(
        settings=settings,
        exit_date=exit_date,
        price_shock_pct=shock,
    )


@router.get("/api/strategic/isa-efficiency")
async def api_isa_efficiency(
    tax_year: str | None = Query(None),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    return StrategicService.get_isa_efficiency(settings=settings, tax_year=tax_year)


@router.get("/api/strategic/fee-drag")
async def api_fee_drag(_: None = Depends(db_required)) -> dict:
    return StrategicService.get_fee_drag_ledger()


@router.get("/api/strategic/data-quality")
async def api_data_quality(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    return StrategicService.get_data_quality(settings=settings)


@router.get("/api/strategic/employment-tax-events")
async def api_employment_tax_events(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    return StrategicService.get_employment_tax_events(settings=settings)


@router.get("/api/strategic/reconcile")
async def api_reconcile(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return StrategicService.get_cross_page_reconcile(settings=settings, db_path=db_path)


@router.get("/api/strategic/basis-timeline")
async def api_basis_timeline(
    lookback_days: int = Query(365, ge=30, le=1825),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    return StrategicService.get_price_fx_basis_timeline(
        settings=settings,
        lookback_days=lookback_days,
    )


@router.get("/insights", response_class=HTMLResponse, include_in_schema=False)
async def insights_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    links = [
        {"href": "/capital-efficiency", "label": "Capital Efficiency", "desc": "Structural drag decomposition and annualized drag rate."},
        {"href": "/employment-exit", "label": "Employment Exit", "desc": "Deterministic leave-employment scenario."},
        {"href": "/isa-efficiency", "label": "ISA Efficiency", "desc": "Tax-year shelter headroom and wrapper split."},
        {"href": "/fee-drag", "label": "Fee Drag", "desc": "Broker fee ledger and fee impact by tax year."},
        {"href": "/data-quality", "label": "Data Quality", "desc": "Stale/missing input impact map by surface."},
        {"href": "/employment-tax-events", "label": "Employment Tax Events", "desc": "Persisted and derived employment-tax event trail."},
        {"href": "/reconcile", "label": "Reconcile", "desc": "Cross-page reconciliation path and delta explanation."},
        {"href": "/basis-timeline", "label": "Price/FX Basis Timeline", "desc": "Native vs FX contribution timeline by basis updates."},
    ]

    return templates.TemplateResponse(
        request,
        "insights.html",
        {
            "request": request,
            "links": links,
            "model_scope": {
                "inputs": ["Read-only strategic pages derived from core portfolio/tax services"],
                "assumptions": ["Deterministic formulas only"],
                "exclusions": ["No prediction or advisory outputs"],
            },
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/capital-efficiency", response_class=HTMLResponse, include_in_schema=False)
async def capital_efficiency_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = StrategicService.get_capital_efficiency(settings=settings, db_path=db_path)
    return templates.TemplateResponse(
        request,
        "capital_efficiency.html",
        {"request": request, "payload": payload, "model_scope": payload.get("model_scope")},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/employment-exit", response_class=HTMLResponse, include_in_schema=False)
async def employment_exit_page(
    request: Request,
    exit_date: date = Query(default_factory=date.today),
    price_shock_pct: str = Query("0"),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    try:
        shock = Decimal(price_shock_pct)
    except (InvalidOperation, ValueError):
        shock = Decimal("0")

    payload = StrategicService.get_employment_exit(
        settings=settings,
        exit_date=exit_date,
        price_shock_pct=shock,
    )
    return templates.TemplateResponse(
        request,
        "employment_exit.html",
        {
            "request": request,
            "payload": payload,
            "input_exit_date": exit_date.isoformat(),
            "input_price_shock_pct": str(shock),
            "model_scope": payload.get("model_scope"),
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/isa-efficiency", response_class=HTMLResponse, include_in_schema=False)
async def isa_efficiency_page(
    request: Request,
    tax_year: str | None = Query(None),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    payload = StrategicService.get_isa_efficiency(settings=settings, tax_year=tax_year)
    return templates.TemplateResponse(
        request,
        "isa_efficiency.html",
        {"request": request, "payload": payload},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/fee-drag", response_class=HTMLResponse, include_in_schema=False)
async def fee_drag_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    payload = StrategicService.get_fee_drag_ledger()
    return templates.TemplateResponse(
        request,
        "fee_drag.html",
        {"request": request, "payload": payload},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/data-quality", response_class=HTMLResponse, include_in_schema=False)
async def data_quality_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    payload = StrategicService.get_data_quality(settings=settings)
    return templates.TemplateResponse(
        request,
        "data_quality.html",
        {"request": request, "payload": payload},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/employment-tax-events", response_class=HTMLResponse, include_in_schema=False)
async def employment_tax_events_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    payload = StrategicService.get_employment_tax_events(settings=settings)
    return templates.TemplateResponse(
        request,
        "employment_tax_events.html",
        {"request": request, "payload": payload},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/reconcile", response_class=HTMLResponse, include_in_schema=False)
async def reconcile_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = StrategicService.get_cross_page_reconcile(settings=settings, db_path=db_path)
    return templates.TemplateResponse(
        request,
        "reconcile.html",
        {"request": request, "payload": payload},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/basis-timeline", response_class=HTMLResponse, include_in_schema=False)
async def basis_timeline_page(
    request: Request,
    lookback_days: int = Query(365, ge=30, le=1825),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    payload = StrategicService.get_price_fx_basis_timeline(
        settings=settings,
        lookback_days=lookback_days,
    )
    return templates.TemplateResponse(
        request,
        "basis_timeline.html",
        {"request": request, "payload": payload, "lookback_days": lookback_days},
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )
