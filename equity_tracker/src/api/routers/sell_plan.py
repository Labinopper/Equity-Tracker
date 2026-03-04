"""
Sell Plan routes (UI-only staged-disposal planning).
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...app_context import AppContext
from ...services.portfolio_service import PortfolioService
from ...services.sell_plan_service import (
    PLAN_METHOD_CALENDAR_TRANCHES,
    SellPlanService,
    TRANCHE_STATUS_CANCELLED,
    TRANCHE_STATUS_EXECUTED,
    TRANCHE_STATUS_PLANNED,
)
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import session_required

router = APIRouter(tags=["sell-plan"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"


def _floor_whole(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_FLOOR)


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


def _sellable_securities(summary) -> list[dict]:
    rows: list[dict] = []
    today = date_type.today()
    for security_summary in summary.securities:
        sellable_qty = Decimal("0")
        for lot_summary in security_summary.active_lots:
            # Align with Simulate:
            # - include ESPP+ paid shares (even if at-risk),
            # - include matched shares once forfeiture window has passed,
            # - exclude currently locked matched lots and pre-vest RSUs.
            if (
                lot_summary.forfeiture_risk is not None
                and lot_summary.forfeiture_risk.in_window
                and lot_summary.lot.matching_lot_id is not None
            ):
                continue
            if (
                lot_summary.lot.scheme_type == "RSU"
                and lot_summary.lot.acquisition_date > today
            ):
                continue
            sellable_qty += lot_summary.quantity_remaining
        sellable_whole = int(_floor_whole(sellable_qty))
        if sellable_whole <= 0:
            continue
        rows.append(
            {
                "security_id": security_summary.security.id,
                "ticker": security_summary.security.ticker,
                "name": security_summary.security.name,
                "sellable_quantity": sellable_whole,
                "reference_price_gbp": security_summary.current_price_gbp,
            }
        )
    rows.sort(key=lambda row: row["ticker"])
    return rows


def _parse_optional_decimal(raw_value: str, *, field_label: str) -> tuple[Decimal | None, str | None]:
    raw = (raw_value or "").strip()
    if not raw:
        return None, None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, f"Invalid {field_label}."
    return value, None


def _parse_optional_int(raw_value: str, *, field_label: str) -> tuple[int | None, str | None]:
    raw = (raw_value or "").strip()
    if not raw:
        return None, None
    try:
        value = int(raw)
    except ValueError:
        return None, f"Invalid {field_label}."
    return value, None


def _decorate_plan_for_ui(
    plan: dict,
    *,
    settings: AppSettings | None,
    today: date_type,
) -> dict:
    enriched = SellPlanService.plan_with_impact_preview(plan=plan, settings=settings)
    tranches = []
    for tranche in enriched.get("tranches", []):
        event_date_raw = tranche.get("event_date")
        status = str(tranche.get("status") or TRANCHE_STATUS_PLANNED).upper()
        is_due = status == TRANCHE_STATUS_PLANNED and event_date_raw <= today.isoformat()
        effective_status = "DUE" if is_due else status
        tranches.append(
            {
                **tranche,
                "effective_status": effective_status,
                "is_actionable": effective_status in {"PLANNED", "DUE"},
            }
        )
    return {**enriched, "tranches": tranches}


def _render_sell_plan_page(
    *,
    request: Request,
    settings: AppSettings | None,
    error: str | None = None,
    msg: str | None = None,
    status_code: int = 200,
    selected_plan_id: str | None = None,
    previous_form: dict | None = None,
) -> HTMLResponse:
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    securities = _sellable_securities(summary)
    db_path = _state.get_db_path()
    today = date_type.today()

    plans = [
        _decorate_plan_for_ui(plan, settings=settings, today=today)
        for plan in SellPlanService.list_plans(db_path)
    ]

    selected_security = None
    if previous_form:
        selected_security = next(
            (
                sec
                for sec in securities
                if sec["security_id"] == previous_form.get("security_id")
            ),
            None,
        )
    elif securities:
        selected_security = securities[0]

    default_security_id = selected_security["security_id"] if selected_security else ""
    default_total_qty = str(selected_security["sellable_quantity"]) if selected_security else ""
    default_ref_price = (
        f"{selected_security['reference_price_gbp']:.2f}"
        if selected_security and selected_security.get("reference_price_gbp") is not None
        else ""
    )

    form_defaults = previous_form or {
        "security_id": default_security_id,
        "total_quantity": default_total_qty,
        "tranche_count": "4",
        "start_date": today.isoformat(),
        "cadence_days": "14",
        "max_daily_quantity": "",
        "max_daily_notional_gbp": "",
        "min_spacing_days": "7",
        "reference_price_gbp": default_ref_price,
        "fee_per_tranche_gbp": "0.00",
    }

    return templates.TemplateResponse(
        request,
        "sell_plan.html",
        {
            "request": request,
            "settings": settings,
            "error": error,
            "flash": msg,
            "securities": securities,
            "plans": plans,
            "selected_plan_id": selected_plan_id or "",
            "today_iso": today.isoformat(),
            "form_defaults": form_defaults,
            "method_label": "Calendar Tranches",
        },
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/sell-plan", response_class=HTMLResponse, include_in_schema=False)
async def sell_plan_page(
    request: Request,
    plan_id: str | None = None,
    security_id: str | None = None,
    total_quantity: str | None = None,
    reference_price_gbp: str | None = None,
    msg: str | None = None,
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    prefill_form: dict | None = None
    if security_id or total_quantity or reference_price_gbp:
        prefill_form = {
            "security_id": (security_id or "").strip(),
            "total_quantity": (total_quantity or "").strip(),
            "tranche_count": "4",
            "start_date": date_type.today().isoformat(),
            "cadence_days": "14",
            "max_daily_quantity": "",
            "max_daily_notional_gbp": "",
            "min_spacing_days": "7",
            "reference_price_gbp": (reference_price_gbp or "").strip(),
            "fee_per_tranche_gbp": "0.00",
        }
    return _render_sell_plan_page(
        request=request,
        settings=settings,
        msg=msg,
        selected_plan_id=plan_id,
        previous_form=prefill_form,
    )


@router.post("/sell-plan", response_class=HTMLResponse, include_in_schema=False)
async def sell_plan_create(
    request: Request,
    security_id: str = Form(...),
    total_quantity: str = Form(...),
    tranche_count: str = Form(...),
    start_date: str = Form(...),
    cadence_days: str = Form(...),
    max_daily_quantity: str = Form(""),
    max_daily_notional_gbp: str = Form(""),
    min_spacing_days: str = Form(""),
    reference_price_gbp: str = Form(""),
    fee_per_tranche_gbp: str = Form("0"),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()

    previous_form = {
        "security_id": security_id,
        "total_quantity": total_quantity,
        "tranche_count": tranche_count,
        "start_date": start_date,
        "cadence_days": cadence_days,
        "max_daily_quantity": max_daily_quantity,
        "max_daily_notional_gbp": max_daily_notional_gbp,
        "min_spacing_days": min_spacing_days,
        "reference_price_gbp": reference_price_gbp,
        "fee_per_tranche_gbp": fee_per_tranche_gbp,
    }

    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    security_rows = _sellable_securities(summary)
    security_map = {row["security_id"]: row for row in security_rows}
    selected_security = security_map.get(security_id)
    if selected_security is None:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Unknown or non-sellable security selected.",
            status_code=422,
            previous_form=previous_form,
        )

    try:
        quantity = Decimal(total_quantity)
    except InvalidOperation:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Invalid total quantity.",
            status_code=422,
            previous_form=previous_form,
        )

    try:
        count = int(tranche_count)
    except ValueError:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Invalid tranche count.",
            status_code=422,
            previous_form=previous_form,
        )

    try:
        cadence = int(cadence_days)
    except ValueError:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Invalid cadence days.",
            status_code=422,
            previous_form=previous_form,
        )

    try:
        start = date_type.fromisoformat(start_date)
    except ValueError:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Invalid start date (expected YYYY-MM-DD).",
            status_code=422,
            previous_form=previous_form,
        )

    parsed_max_daily_qty, max_daily_qty_err = _parse_optional_decimal(
        max_daily_quantity,
        field_label="max daily quantity",
    )
    if max_daily_qty_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=max_daily_qty_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_max_daily_notional, max_daily_notional_err = _parse_optional_decimal(
        max_daily_notional_gbp,
        field_label="max daily notional",
    )
    if max_daily_notional_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=max_daily_notional_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_min_spacing, min_spacing_err = _parse_optional_int(
        min_spacing_days,
        field_label="minimum spacing days",
    )
    if min_spacing_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=min_spacing_err,
            status_code=422,
            previous_form=previous_form,
        )
    if parsed_min_spacing is None:
        parsed_min_spacing = 7

    parsed_ref_price, ref_price_err = _parse_optional_decimal(
        reference_price_gbp,
        field_label="reference price",
    )
    if ref_price_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=ref_price_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_fee, fee_err = _parse_optional_decimal(
        fee_per_tranche_gbp,
        field_label="fee per tranche",
    )
    if fee_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=fee_err,
            status_code=422,
            previous_form=previous_form,
        )

    db_path = _state.get_db_path()
    try:
        plan = SellPlanService.create_calendar_tranche_plan(
            db_path=db_path,
            security_id=selected_security["security_id"],
            ticker=selected_security["ticker"],
            total_quantity=quantity,
            tranche_count=count,
            start_date=start,
            cadence_days=cadence,
            max_sellable_quantity=selected_security["sellable_quantity"],
            max_daily_quantity=parsed_max_daily_qty,
            max_daily_notional_gbp=parsed_max_daily_notional,
            min_spacing_days=parsed_min_spacing,
            reference_price_gbp=parsed_ref_price,
            fee_per_tranche_gbp=parsed_fee,
        )
    except ValueError as exc:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=str(exc),
            status_code=422,
            previous_form=previous_form,
        )

    msg = quote_plus(
        f"Sell plan {plan['plan_id'][:8]} created ({PLAN_METHOD_CALENDAR_TRANCHES})."
    )
    return RedirectResponse(
        f"/sell-plan?plan_id={plan['plan_id']}&msg={msg}",
        status_code=303,
    )


@router.post("/sell-plan/tranche-status", response_class=HTMLResponse, include_in_schema=False)
async def sell_plan_set_tranche_status(
    request: Request,
    plan_id: str = Form(...),
    tranche_id: str = Form(...),
    status: str = Form(...),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    try:
        SellPlanService.update_tranche_status(
            db_path=db_path,
            plan_id=plan_id,
            tranche_id=tranche_id,
            new_status=status,
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/sell-plan?plan_id={plan_id}&msg={quote_plus(str(exc))}",
            status_code=303,
        )

    state_label = (status or "").strip().upper()
    if state_label == TRANCHE_STATUS_EXECUTED:
        msg = "Tranche marked executed."
    elif state_label == TRANCHE_STATUS_CANCELLED:
        msg = "Tranche cancelled."
    else:
        msg = "Tranche status updated."
    return RedirectResponse(
        f"/sell-plan?plan_id={plan_id}&msg={quote_plus(msg)}",
        status_code=303,
    )


@router.post("/sell-plan/delete", response_class=HTMLResponse, include_in_schema=False)
async def sell_plan_delete(
    request: Request,
    plan_id: str = Form(...),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    db_path = _state.get_db_path()
    try:
        removed = SellPlanService.delete_plan(
            db_path=db_path,
            plan_id=plan_id,
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/sell-plan?msg={quote_plus(str(exc))}",
            status_code=303,
        )

    msg = "Sell plan deleted." if removed else "Sell plan not found."
    return RedirectResponse(
        f"/sell-plan?msg={quote_plus(msg)}",
        status_code=303,
    )
