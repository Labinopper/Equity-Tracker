"""
Tax planner routes (UI + JSON API).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from ...app_context import AppContext
from ...services.tax_plan_service import TaxPlanService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["tax-plan"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"
_DEFAULT_SELL_AMOUNT = Decimal("5000")
_DEFAULT_BONUS_AMOUNT = Decimal("0")
_DEFAULT_EXTRA_PENSION = Decimal("0")


def _load_settings() -> AppSettings | None:
    db_path = _state.get_db_path()
    return AppSettings.load(db_path) if db_path else None


def _tax_inputs_incomplete(settings: AppSettings | None) -> bool:
    if settings is None:
        return True
    return (
        settings.default_gross_income <= Decimal("0")
        and settings.default_other_income <= Decimal("0")
    )


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/api/tax-plan/summary")
async def api_tax_plan_summary(
    gross_income_gbp: Decimal | None = Query(None, ge=0),
    bonus_gbp: Decimal = Query(_DEFAULT_BONUS_AMOUNT, ge=0),
    sell_amount_gbp: Decimal = Query(_DEFAULT_SELL_AMOUNT, ge=0),
    additional_pension_sacrifice_gbp: Decimal = Query(_DEFAULT_EXTRA_PENSION, ge=0),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    return TaxPlanService.get_summary(
        settings=settings,
        compensation_gross_income_gbp=gross_income_gbp,
        compensation_bonus_gbp=bonus_gbp,
        compensation_sell_amount_gbp=sell_amount_gbp,
        compensation_additional_pension_sacrifice_gbp=additional_pension_sacrifice_gbp,
    )


@router.get("/tax-plan", response_class=HTMLResponse, include_in_schema=False)
async def tax_plan_page(
    request: Request,
    gross_income_gbp: Decimal | None = Query(None, ge=0),
    bonus_gbp: Decimal = Query(_DEFAULT_BONUS_AMOUNT, ge=0),
    sell_amount_gbp: Decimal = Query(_DEFAULT_SELL_AMOUNT, ge=0),
    additional_pension_sacrifice_gbp: Decimal = Query(_DEFAULT_EXTRA_PENSION, ge=0),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    payload = TaxPlanService.get_summary(
        settings=settings,
        compensation_gross_income_gbp=gross_income_gbp,
        compensation_bonus_gbp=bonus_gbp,
        compensation_sell_amount_gbp=sell_amount_gbp,
        compensation_additional_pension_sacrifice_gbp=additional_pension_sacrifice_gbp,
    )
    return templates.TemplateResponse(
        request,
        "tax_plan.html",
        {
            "request": request,
            "tax_plan": payload,
            "settings": settings,
            "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )
