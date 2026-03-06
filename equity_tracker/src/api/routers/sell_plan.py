"""
Sell Plan routes (UI-only staged-disposal planning).
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from ...app_context import AppContext
from ...services.portfolio_service import PortfolioService
from ...services.sell_plan_service import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_DRAFT,
    BROKER_ALGO_TWAP,
    BROKER_ALGO_VWAP,
    PLAN_METHOD_BROKER_ALGO,
    PLAN_METHOD_CALENDAR_TRANCHES,
    PLAN_METHOD_LIMIT_LADDER,
    PLAN_METHOD_THRESHOLD_BANDS,
    PROFILE_CUSTOM,
    PROFILE_HYBRID_DE_RISK,
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
_METHOD_LABELS = {
    PLAN_METHOD_CALENDAR_TRANCHES: "Calendar Tranches",
    PLAN_METHOD_THRESHOLD_BANDS: "Threshold Bands",
    PLAN_METHOD_LIMIT_LADDER: "Limit Ladder",
    PLAN_METHOD_BROKER_ALGO: "Broker Algo (TWAP/VWAP)",
}
_MONEY_Q = Decimal("0.01")


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


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _build_plan_adherence_panel(
    *,
    total_quantity: str,
    tranches: list[dict],
    as_of: date_type,
) -> dict:
    total_target_qty = _safe_decimal(total_quantity)
    planned_qty = Decimal("0")
    executed_qty = Decimal("0")
    pending_qty = Decimal("0")
    planned_to_date_qty = Decimal("0")
    pending_tranche_count = 0
    due_tranche_count = 0
    executed_tranche_count = 0
    cancelled_tranche_count = 0
    remaining_tax_budget = Decimal("0")
    realised_tax_budget = Decimal("0")

    for tranche in tranches:
        qty = _safe_decimal(tranche.get("quantity"))
        effective_status = str(tranche.get("effective_status") or TRANCHE_STATUS_PLANNED).upper()
        raw_status = str(tranche.get("status") or TRANCHE_STATUS_PLANNED).upper()
        event_date = None
        try:
            event_date = date_type.fromisoformat(str(tranche.get("event_date") or ""))
        except ValueError:
            event_date = None

        if raw_status != TRANCHE_STATUS_CANCELLED:
            planned_qty += qty

        if effective_status == "EXECUTED":
            executed_qty += qty
            executed_tranche_count += 1
        elif effective_status == "CANCELLED":
            cancelled_tranche_count += 1
        elif effective_status in {"PLANNED", "DUE"}:
            pending_qty += qty
            pending_tranche_count += 1
            if effective_status == "DUE":
                due_tranche_count += 1

        if event_date is not None and event_date <= as_of and effective_status in {"DUE", "EXECUTED"}:
            planned_to_date_qty += qty

        if tranche.get("impact_available"):
            tranche_tax = _safe_decimal(tranche.get("impact_employment_tax_gbp")) + _safe_decimal(
                tranche.get("impact_cgt_gbp")
            )
            if effective_status == "EXECUTED":
                realised_tax_budget += tranche_tax
            elif effective_status in {"PLANNED", "DUE"}:
                remaining_tax_budget += tranche_tax

    if planned_qty <= Decimal("0"):
        planned_qty = total_target_qty

    drift_qty = executed_qty - planned_to_date_qty
    adherence_status = "ON_TRACK"
    if drift_qty < Decimal("0"):
        adherence_status = "BEHIND"
    elif drift_qty > Decimal("0"):
        adherence_status = "AHEAD"

    concentration_reduction_pct = Decimal("0")
    if total_target_qty > Decimal("0"):
        concentration_reduction_pct = (executed_qty / total_target_qty) * Decimal("100")

    return {
        "planned_quantity": str(planned_qty),
        "executed_quantity": str(executed_qty),
        "pending_quantity": str(pending_qty),
        "planned_to_date_quantity": str(planned_to_date_qty),
        "drift_quantity": str(drift_qty),
        "adherence_status": adherence_status,
        "concentration_reduction_achieved_pct": str(_q_money(concentration_reduction_pct)),
        "remaining_tax_budget_gbp": str(_q_money(remaining_tax_budget)),
        "realised_tax_budget_gbp": str(_q_money(realised_tax_budget)),
        "calendar_status_counts": {
            "planned": pending_tranche_count - due_tranche_count,
            "due": due_tranche_count,
            "executed": executed_tranche_count,
            "cancelled": cancelled_tranche_count,
        },
    }


def _default_form_values(
    *,
    today: date_type,
    security_id: str,
    total_quantity: str,
    reference_price_gbp: str,
) -> dict:
    return {
        "security_id": security_id,
        "method": PLAN_METHOD_CALENDAR_TRANCHES,
        "execution_profile": PROFILE_HYBRID_DE_RISK,
        "total_quantity": total_quantity,
        "tranche_count": "4",
        "start_date": today.isoformat(),
        "cadence_days": "14",
        "max_daily_quantity": "",
        "max_daily_notional_gbp": "",
        "min_spacing_days": "7",
        "reference_price_gbp": reference_price_gbp,
        "fee_per_tranche_gbp": "0.00",
        "threshold_upper_pct": "70.00",
        "threshold_target_pct": "40.00",
        "threshold_review_days": "7",
        "limit_start_gbp": reference_price_gbp,
        "limit_step_gbp": "0.50",
        "broker_algo_name": BROKER_ALGO_TWAP,
        "broker_algo_window_minutes": "60",
        "profile_concentration_trigger_pct": "40.00",
        "profile_limit_guardrail_discount_pct": "1.00",
    }


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
    method = str(enriched.get("method") or PLAN_METHOD_CALENDAR_TRANCHES).upper()
    approval = str(enriched.get("approval_status") or APPROVAL_STATUS_DRAFT).upper()
    adherence_panel = _build_plan_adherence_panel(
        total_quantity=str(enriched.get("total_quantity") or "0"),
        tranches=tranches,
        as_of=today,
    )
    return {
        **enriched,
        "tranches": tranches,
        "method_label": _METHOD_LABELS.get(method, method.replace("_", " ").title()),
        "is_approved": approval == APPROVAL_STATUS_APPROVED,
        "adherence_panel": adherence_panel,
    }


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

    form_defaults = previous_form or _default_form_values(
        today=today,
        security_id=default_security_id,
        total_quantity=default_total_qty,
        reference_price_gbp=default_ref_price,
    )

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
            "method_options": _METHOD_LABELS,
            "profile_options": {
                PROFILE_HYBRID_DE_RISK: "Hybrid De-Risk (Recommended)",
                PROFILE_CUSTOM: "Custom",
            },
            "broker_algo_options": [BROKER_ALGO_TWAP, BROKER_ALGO_VWAP],
            "approval_status_approved": APPROVAL_STATUS_APPROVED,
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
    method: str | None = None,
    execution_profile: str | None = None,
    msg: str | None = None,
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    prefill_form: dict | None = None
    if security_id or total_quantity or reference_price_gbp:
        prefill_form = _default_form_values(
            today=date_type.today(),
            security_id=(security_id or "").strip(),
            total_quantity=(total_quantity or "").strip(),
            reference_price_gbp=(reference_price_gbp or "").strip(),
        )
        if method:
            prefill_form["method"] = method.strip().upper()
        if execution_profile:
            prefill_form["execution_profile"] = execution_profile.strip().upper()
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
    method: str = Form(PLAN_METHOD_CALENDAR_TRANCHES),
    execution_profile: str = Form(PROFILE_HYBRID_DE_RISK),
    total_quantity: str = Form(...),
    tranche_count: str = Form(...),
    start_date: str = Form(...),
    cadence_days: str = Form(...),
    max_daily_quantity: str = Form(""),
    max_daily_notional_gbp: str = Form(""),
    min_spacing_days: str = Form(""),
    reference_price_gbp: str = Form(""),
    fee_per_tranche_gbp: str = Form("0"),
    threshold_upper_pct: str = Form(""),
    threshold_target_pct: str = Form(""),
    threshold_review_days: str = Form(""),
    limit_start_gbp: str = Form(""),
    limit_step_gbp: str = Form(""),
    broker_algo_name: str = Form(BROKER_ALGO_TWAP),
    broker_algo_window_minutes: str = Form(""),
    profile_concentration_trigger_pct: str = Form(""),
    profile_limit_guardrail_discount_pct: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()

    previous_form = {
        "security_id": security_id,
        "method": method,
        "execution_profile": execution_profile,
        "total_quantity": total_quantity,
        "tranche_count": tranche_count,
        "start_date": start_date,
        "cadence_days": cadence_days,
        "max_daily_quantity": max_daily_quantity,
        "max_daily_notional_gbp": max_daily_notional_gbp,
        "min_spacing_days": min_spacing_days,
        "reference_price_gbp": reference_price_gbp,
        "fee_per_tranche_gbp": fee_per_tranche_gbp,
        "threshold_upper_pct": threshold_upper_pct,
        "threshold_target_pct": threshold_target_pct,
        "threshold_review_days": threshold_review_days,
        "limit_start_gbp": limit_start_gbp,
        "limit_step_gbp": limit_step_gbp,
        "broker_algo_name": broker_algo_name,
        "broker_algo_window_minutes": broker_algo_window_minutes,
        "profile_concentration_trigger_pct": profile_concentration_trigger_pct,
        "profile_limit_guardrail_discount_pct": profile_limit_guardrail_discount_pct,
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

    parsed_threshold_upper, threshold_upper_err = _parse_optional_decimal(
        threshold_upper_pct,
        field_label="threshold upper percentage",
    )
    if threshold_upper_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=threshold_upper_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_threshold_target, threshold_target_err = _parse_optional_decimal(
        threshold_target_pct,
        field_label="threshold target percentage",
    )
    if threshold_target_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=threshold_target_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_threshold_review_days, threshold_review_err = _parse_optional_int(
        threshold_review_days,
        field_label="threshold review days",
    )
    if threshold_review_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=threshold_review_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_limit_start, limit_start_err = _parse_optional_decimal(
        limit_start_gbp,
        field_label="limit start",
    )
    if limit_start_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=limit_start_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_limit_step, limit_step_err = _parse_optional_decimal(
        limit_step_gbp,
        field_label="limit step",
    )
    if limit_step_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=limit_step_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_broker_algo_window, broker_algo_window_err = _parse_optional_int(
        broker_algo_window_minutes,
        field_label="broker algorithm window",
    )
    if broker_algo_window_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=broker_algo_window_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_profile_trigger, profile_trigger_err = _parse_optional_decimal(
        profile_concentration_trigger_pct,
        field_label="profile concentration trigger",
    )
    if profile_trigger_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=profile_trigger_err,
            status_code=422,
            previous_form=previous_form,
        )

    parsed_profile_guardrail, profile_guardrail_err = _parse_optional_decimal(
        profile_limit_guardrail_discount_pct,
        field_label="profile limit guardrail",
    )
    if profile_guardrail_err:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error=profile_guardrail_err,
            status_code=422,
            previous_form=previous_form,
        )

    method_code = (method or PLAN_METHOD_CALENDAR_TRANCHES).strip().upper()
    if method_code not in _METHOD_LABELS:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Unsupported execution method.",
            status_code=422,
            previous_form=previous_form,
        )
    profile_code = (execution_profile or PROFILE_HYBRID_DE_RISK).strip().upper()
    if profile_code not in {PROFILE_HYBRID_DE_RISK, PROFILE_CUSTOM}:
        return _render_sell_plan_page(
            request=request,
            settings=settings,
            error="Unsupported execution profile.",
            status_code=422,
            previous_form=previous_form,
        )

    db_path = _state.get_db_path()
    try:
        plan = SellPlanService.create_plan(
            db_path=db_path,
            security_id=selected_security["security_id"],
            ticker=selected_security["ticker"],
            method=method_code,
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
            threshold_upper_pct=parsed_threshold_upper,
            threshold_target_pct=parsed_threshold_target,
            threshold_review_days=parsed_threshold_review_days,
            limit_start_gbp=parsed_limit_start,
            limit_step_gbp=parsed_limit_step,
            broker_algo_name=(broker_algo_name or "").strip().upper() or None,
            broker_algo_window_minutes=parsed_broker_algo_window,
            execution_profile=profile_code,
            profile_concentration_trigger_pct=parsed_profile_trigger,
            profile_limit_guardrail_discount_pct=parsed_profile_guardrail,
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
        f"Sell plan {plan['plan_id'][:8]} created ({plan['method']})."
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


@router.post("/sell-plan/approval", response_class=HTMLResponse, include_in_schema=False)
async def sell_plan_set_approval_status(
    request: Request,
    plan_id: str = Form(...),
    approval_status: str = Form(...),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    try:
        plan = SellPlanService.set_plan_approval_status(
            db_path=db_path,
            plan_id=plan_id,
            approval_status=approval_status,
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/sell-plan?plan_id={plan_id}&msg={quote_plus(str(exc))}",
            status_code=303,
        )

    current = str(plan.get("approval_status") or APPROVAL_STATUS_DRAFT).upper()
    msg = "Plan approved for staging export." if current == APPROVAL_STATUS_APPROVED else "Plan returned to draft."
    return RedirectResponse(
        f"/sell-plan?plan_id={plan_id}&msg={quote_plus(msg)}",
        status_code=303,
    )


@router.get("/sell-plan/export", response_class=PlainTextResponse, include_in_schema=False)
async def sell_plan_export_csv(
    plan_id: str,
    include_closed: bool = False,
) -> PlainTextResponse:
    if not AppContext.is_initialized():
        return PlainTextResponse("Database is locked.", status_code=503)

    db_path = _state.get_db_path()
    try:
        csv_payload = SellPlanService.export_ibkr_order_staging_csv(
            db_path=db_path,
            plan_id=plan_id,
            include_closed=include_closed,
        )
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=422)

    filename = f"sell-plan-{plan_id[:8]}-ibkr-staging.csv"
    return PlainTextResponse(
        csv_payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
