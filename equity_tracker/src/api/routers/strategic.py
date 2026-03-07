"""Stage-10 strategic pages and APIs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...app_context import AppContext
from ...services.allocation_planner_service import AllocationPlannerService
from ...services.notification_digest_service import NotificationDigestService
from ...services.pension_service import PensionService
from ...services.strategic_service import StrategicService
from ...services.weekly_review_service import WeeklyReviewService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["strategic"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"


def _load_settings() -> AppSettings | None:
    db_path = _state.get_db_path()
    return AppSettings.load(db_path) if db_path else None


def _parse_optional_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _redirect_with_params(
    path: str,
    *,
    msg: str | None = None,
    as_of: date | None = None,
) -> RedirectResponse:
    params: list[tuple[str, str]] = []
    if as_of is not None:
        params.append(("as_of", as_of.isoformat()))
    if msg:
        params.append(("msg", msg))
    target = path
    if params:
        target = f"{path}?{urlencode(params)}"
    return RedirectResponse(target, status_code=303)


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
async def api_reconcile(
    lookback_days: int = Query(30, ge=7, le=365),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return StrategicService.get_cross_page_reconcile(
        settings=settings,
        db_path=db_path,
        lookback_days=lookback_days,
    )


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


@router.get("/api/strategic/pension")
async def api_pension(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return PensionService.get_dashboard(settings=settings, db_path=db_path)


@router.get("/api/strategic/weekly-review")
async def api_weekly_review(
    as_of: date | None = Query(None),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return WeeklyReviewService.get_dashboard(
        settings=settings,
        db_path=db_path,
        as_of=as_of,
    )


@router.get("/api/strategic/notification-digest")
async def api_notification_digest(
    as_of: date | None = Query(None),
    horizon_days: int = Query(30, ge=7, le=120),
    max_items: int = Query(12, ge=3, le=40),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return NotificationDigestService.get_digest(
        settings=settings,
        db_path=db_path,
        as_of=as_of,
        horizon_days=horizon_days,
        max_items=max_items,
    )


@router.get("/api/strategic/allocation-planner")
async def api_allocation_planner(
    as_of: date | None = Query(None),
    _: None = Depends(db_required),
) -> dict:
    settings = _load_settings()
    db_path = _state.get_db_path()
    return AllocationPlannerService.get_dashboard(
        settings=settings,
        db_path=db_path,
        as_of=as_of,
    )


@router.get("/insights", response_class=HTMLResponse, include_in_schema=False)
async def insights_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    links = [
        {
            "href": "/capital-efficiency",
            "label": "Capital Efficiency",
            "desc": "Structural drag decomposition and annualized drag rate.",
            "trend_context": "Compare annualized drag against current-period drag.",
            "action_href": "/fee-drag",
            "action_label": "Open Fee Drag",
        },
        {
            "href": "/employment-exit",
            "label": "Employment Exit",
            "desc": "Deterministic leave-employment scenario.",
            "trend_context": "Compare retained vs forfeited value under fixed shock.",
            "action_href": "/scenario-lab",
            "action_label": "Run Scenario Lab",
        },
        {
            "href": "/isa-efficiency",
            "label": "ISA Efficiency",
            "desc": "Tax-year shelter headroom and wrapper split.",
            "trend_context": "Track ISA ratio and contribution headroom in current tax year.",
            "action_href": "/cash",
            "action_label": "Open Cash Workflow",
        },
        {
            "href": "/fee-drag",
            "label": "Fee Drag",
            "desc": "Broker fee ledger and fee impact by tax year.",
            "trend_context": "Compare latest-tax-year fee % against all-time fee %.",
            "action_href": "/sell-plan",
            "action_label": "Open Sell Plan",
        },
        {
            "href": "/data-quality",
            "label": "Data Quality",
            "desc": "Stale/missing input impact map by surface.",
            "trend_context": "Compare stale vs missing-input pressure on major surfaces.",
            "action_href": "/settings",
            "action_label": "Open Settings",
        },
        {
            "href": "/employment-tax-events",
            "label": "Employment Tax Events",
            "desc": "Persisted and derived employment-tax event trail.",
            "trend_context": "Compare latest tax-year event totals versus prior year.",
            "action_href": "/tax-plan",
            "action_label": "Open Tax Plan",
        },
        {
            "href": "/reconcile",
            "label": "Reconcile",
            "desc": "Cross-page reconciliation path and delta explanation.",
            "trend_context": "Decompose recent drift into price/FX/quantity/settings/transactions.",
            "action_href": "/reconcile?lookback_days=30#trace-drift-decomposition",
            "action_label": "Open Drift Panel",
        },
        {
            "href": "/basis-timeline",
            "label": "Price/FX Basis Timeline",
            "desc": "Native vs FX contribution timeline by basis updates.",
            "trend_context": "Compare cumulative native-move contribution vs FX contribution.",
            "action_href": "/history",
            "action_label": "Open History",
        },
        {
            "href": "/pension",
            "label": "Pension",
            "desc": "Contribution ledger, retirement projection, and tracked-wealth context.",
            "trend_context": "Compare conservative, base, and aggressive outcomes across timeline checkpoints.",
            "action_href": "/pension#pension-ledger",
            "action_label": "Open Pension Ledger",
        },
        {
            "href": "/weekly-review",
            "label": "Weekly Review",
            "desc": "Persisted checklist across Portfolio, Risk, Calendar, and Reconcile.",
            "trend_context": "Resume the same as-of review without rebuilding context or notes.",
            "action_href": "/weekly-review#review-steps",
            "action_label": "Open Review Steps",
        },
        {
            "href": "/notification-digest",
            "label": "Notification Digest",
            "desc": "Deterministic digest of threshold breaches, stale data, and upcoming timing items.",
            "trend_context": "Compare what is urgent now versus what is merely upcoming in the chosen horizon.",
            "action_href": "/notification-digest#digest-entries",
            "action_label": "Open Digest",
        },
        {
            "href": "/allocation-planner",
            "label": "Allocation Planner",
            "desc": "Trim overweight exposures and compare user-defined redeployment candidates.",
            "trend_context": "Compare before/after concentration, FX, wrapper, and friction deltas.",
            "action_href": "/allocation-planner#candidate-universe",
            "action_label": "Open Candidate Universe",
        },
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
async def reconcile_page(
    request: Request,
    lookback_days: int = Query(30, ge=7, le=365),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = StrategicService.get_cross_page_reconcile(
        settings=settings,
        db_path=db_path,
        lookback_days=lookback_days,
    )
    return templates.TemplateResponse(
        request,
        "reconcile.html",
        {
            "request": request,
            "payload": payload,
            "lookback_days": lookback_days,
        },
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


def _render_pension_page(
    request: Request,
    *,
    msg: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = PensionService.get_dashboard(settings=settings, db_path=db_path)
    return templates.TemplateResponse(
        request,
        "pension.html",
        {
            "request": request,
            "payload": payload,
            "flash": msg,
            "error": error,
            "model_scope": payload.get("model_scope"),
        },
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _render_weekly_review_page(
    request: Request,
    *,
    as_of: date | None = None,
    msg: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = WeeklyReviewService.get_dashboard(
        settings=settings,
        db_path=db_path,
        as_of=as_of,
    )
    return templates.TemplateResponse(
        request,
        "weekly_review.html",
        {
            "request": request,
            "payload": payload,
            "flash": msg,
            "error": error,
            "model_scope": payload.get("model_scope"),
        },
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _render_notification_digest_page(
    request: Request,
    *,
    as_of: date | None = None,
    horizon_days: int = 30,
    max_items: int = 12,
) -> HTMLResponse:
    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = NotificationDigestService.get_digest(
        settings=settings,
        db_path=db_path,
        as_of=as_of,
        horizon_days=horizon_days,
        max_items=max_items,
    )
    return templates.TemplateResponse(
        request,
        "notification_digest.html",
        {
            "request": request,
            "payload": payload,
            "horizon_days": horizon_days,
            "max_items": max_items,
            "model_scope": payload.get("model_scope"),
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _render_allocation_planner_page(
    request: Request,
    *,
    as_of: date | None = None,
    msg: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = _load_settings()
    db_path = _state.get_db_path()
    payload = AllocationPlannerService.get_dashboard(
        settings=settings,
        db_path=db_path,
        as_of=as_of,
    )
    return templates.TemplateResponse(
        request,
        "allocation_planner.html",
        {
            "request": request,
            "payload": payload,
            "flash": msg,
            "error": error,
            "model_scope": payload.get("model_scope"),
        },
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/pension", response_class=HTMLResponse, include_in_schema=False)
async def pension_page(request: Request, msg: str | None = None) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    return _render_pension_page(request, msg=msg)


@router.post("/pension/contributions", response_class=HTMLResponse, include_in_schema=False)
async def pension_add_contribution(
    request: Request,
    entry_date: str = Form(...),
    entry_type: str = Form(...),
    amount_gbp: str = Form(...),
    source: str = Form("manual"),
    notes: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    try:
        parsed_date = date.fromisoformat(entry_date)
        amount = Decimal(amount_gbp)
        PensionService.record_entry(
            db_path=db_path,
            entry_date=parsed_date,
            entry_type=entry_type,
            amount_gbp=amount,
            source=source,
            notes=notes,
        )
    except (InvalidOperation, ValueError) as exc:
        return _render_pension_page(
            request,
            error=f"Pension contribution not saved: {exc}",
            status_code=422,
        )

    return RedirectResponse("/pension?msg=Pension+contribution+saved.", status_code=303)


@router.post("/pension/assumptions", response_class=HTMLResponse, include_in_schema=False)
async def pension_save_assumptions(
    request: Request,
    current_pension_value_gbp: str = Form("0"),
    monthly_employee_contribution_gbp: str = Form("0"),
    monthly_employer_contribution_gbp: str = Form("0"),
    retirement_date: str = Form(...),
    target_annual_income_gbp: str = Form("0"),
    target_withdrawal_rate_pct: str = Form("4"),
    conservative_annual_return_pct: str = Form("3"),
    base_annual_return_pct: str = Form("5"),
    aggressive_annual_return_pct: str = Form("7"),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    try:
        PensionService.save_assumptions(
            db_path=db_path,
            current_pension_value_gbp=current_pension_value_gbp,
            monthly_employee_contribution_gbp=monthly_employee_contribution_gbp,
            monthly_employer_contribution_gbp=monthly_employer_contribution_gbp,
            retirement_date=retirement_date,
            target_annual_income_gbp=target_annual_income_gbp,
            target_withdrawal_rate_pct=target_withdrawal_rate_pct,
            conservative_annual_return_pct=conservative_annual_return_pct,
            base_annual_return_pct=base_annual_return_pct,
            aggressive_annual_return_pct=aggressive_annual_return_pct,
        )
    except ValueError as exc:
        return _render_pension_page(
            request,
            error=f"Pension assumptions not saved: {exc}",
            status_code=422,
        )

    return RedirectResponse("/pension?msg=Pension+assumptions+saved.", status_code=303)


@router.get("/weekly-review", response_class=HTMLResponse, include_in_schema=False)
async def weekly_review_page(
    request: Request,
    as_of: date | None = Query(None),
    msg: str | None = None,
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    return _render_weekly_review_page(request, as_of=as_of, msg=msg)


@router.post("/weekly-review/start", response_class=HTMLResponse, include_in_schema=False)
async def weekly_review_start(
    request: Request,
    as_of: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    review = WeeklyReviewService.start_new_review(
        db_path=db_path,
        as_of=_parse_optional_date(as_of),
    )
    return _redirect_with_params(
        "/weekly-review",
        msg="Weekly review restarted.",
        as_of=_parse_optional_date(review.get("as_of_date")),
    )


@router.post(
    "/weekly-review/steps/{step_key}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def weekly_review_update_step(
    request: Request,
    step_key: str,
    notes: str = Form(""),
    completed: str = Form("false"),
    as_of: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    try:
        review = WeeklyReviewService.update_step(
            db_path=db_path,
            step_key=step_key,
            notes=notes,
            completed=str(completed or "").strip().lower() == "true",
            as_of=_parse_optional_date(as_of),
        )
    except ValueError as exc:
        return _render_weekly_review_page(
            request,
            as_of=_parse_optional_date(as_of),
            error=f"Weekly review step not saved: {exc}",
            status_code=422,
        )

    return _redirect_with_params(
        "/weekly-review",
        msg="Weekly review step saved.",
        as_of=_parse_optional_date(review.get("as_of_date")),
    )


@router.get("/notification-digest", response_class=HTMLResponse, include_in_schema=False)
async def notification_digest_page(
    request: Request,
    as_of: date | None = Query(None),
    horizon_days: int = Query(30, ge=7, le=120),
    max_items: int = Query(12, ge=3, le=40),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    return _render_notification_digest_page(
        request,
        as_of=as_of,
        horizon_days=horizon_days,
        max_items=max_items,
    )


@router.get("/allocation-planner", response_class=HTMLResponse, include_in_schema=False)
async def allocation_planner_page(
    request: Request,
    as_of: date | None = Query(None),
    msg: str | None = None,
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    return _render_allocation_planner_page(request, as_of=as_of, msg=msg)


@router.post(
    "/allocation-planner/settings",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def allocation_planner_save_settings(
    request: Request,
    source_selection_mode: str = Form(...),
    source_ticker: str = Form(""),
    target_max_pct: str = Form("25"),
    as_of: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    parsed_as_of = _parse_optional_date(as_of)
    try:
        AllocationPlannerService.save_settings(
            db_path=db_path,
            source_selection_mode=source_selection_mode,
            source_ticker=source_ticker,
            target_max_pct=target_max_pct,
        )
    except ValueError as exc:
        return _render_allocation_planner_page(
            request,
            as_of=parsed_as_of,
            error=f"Planner settings not saved: {exc}",
            status_code=422,
        )

    return _redirect_with_params(
        "/allocation-planner",
        msg="Planner settings saved.",
        as_of=parsed_as_of,
    )


@router.post(
    "/allocation-planner/candidates",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def allocation_planner_add_candidate(
    request: Request,
    label: str = Form(...),
    ticker: str = Form(""),
    currency: str = Form("GBP"),
    target_wrapper: str = Form("TAXABLE"),
    bucket: str = Form("UNSPECIFIED"),
    allocation_weight: str = Form("1"),
    notes: str = Form(""),
    as_of: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    parsed_as_of = _parse_optional_date(as_of)
    try:
        AllocationPlannerService.add_candidate(
            db_path=db_path,
            label=label,
            ticker=ticker,
            currency=currency,
            target_wrapper=target_wrapper,
            bucket=bucket,
            allocation_weight=allocation_weight,
            notes=notes,
        )
    except ValueError as exc:
        return _render_allocation_planner_page(
            request,
            as_of=parsed_as_of,
            error=f"Candidate not saved: {exc}",
            status_code=422,
        )

    return _redirect_with_params(
        "/allocation-planner",
        msg="Candidate added.",
        as_of=parsed_as_of,
    )


@router.post(
    "/allocation-planner/candidates/{candidate_id}/delete",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def allocation_planner_delete_candidate(
    request: Request,
    candidate_id: str,
    as_of: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    parsed_as_of = _parse_optional_date(as_of)
    removed = AllocationPlannerService.remove_candidate(
        db_path=db_path,
        candidate_id=candidate_id,
    )
    msg = "Candidate removed." if removed else "Candidate not found."
    return _redirect_with_params(
        "/allocation-planner",
        msg=msg,
        as_of=parsed_as_of,
    )
