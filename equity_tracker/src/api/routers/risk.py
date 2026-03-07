"""
Risk routes (UI + JSON API).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...app_context import AppContext
from ...services.alert_lifecycle_service import AlertLifecycleService
from ...services.risk_service import RiskService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required
from ..schemas.risk import RiskSummarySchema

router = APIRouter(tags=["risk"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"


def _flash(msg: str | None) -> dict[str, str]:
    return {"flash": msg} if msg else {}


def _parse_optionality_weights(
    *,
    sellability: float | None,
    forfeiture: float | None,
    concentration: float | None,
    isa_ratio: float | None,
    config: float | None,
) -> dict[str, Decimal] | None:
    candidates = {
        "sellability": sellability,
        "forfeiture": forfeiture,
        "concentration": concentration,
        "isa_ratio": isa_ratio,
        "config": config,
    }
    parsed: dict[str, Decimal] = {}
    for key, value in candidates.items():
        if value is None:
            continue
        try:
            parsed[key] = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
    return parsed or None


def _query_float(request: Request, key: str) -> float | None:
    raw_value = request.query_params.get(key)
    if raw_value is None:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/api/risk/summary", response_model=RiskSummarySchema)
async def api_risk_summary(
    weight_sellability: float | None = Query(default=None),
    weight_forfeiture: float | None = Query(default=None),
    weight_concentration: float | None = Query(default=None),
    weight_isa_ratio: float | None = Query(default=None),
    weight_config: float | None = Query(default=None),
    _: None = Depends(db_required),
) -> RiskSummarySchema:
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    optionality_weights = _parse_optionality_weights(
        sellability=weight_sellability,
        forfeiture=weight_forfeiture,
        concentration=weight_concentration,
        isa_ratio=weight_isa_ratio,
        config=weight_config,
    )
    summary = RiskService.get_risk_summary(
        settings=settings,
        db_path=db_path,
        optionality_weights=optionality_weights,
    )
    return RiskSummarySchema.from_service(summary)


@router.get("/risk", response_class=HTMLResponse, include_in_schema=False)
async def risk_page(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    optionality_weights = _parse_optionality_weights(
        sellability=_query_float(request, "weight_sellability"),
        forfeiture=_query_float(request, "weight_forfeiture"),
        concentration=_query_float(request, "weight_concentration"),
        isa_ratio=_query_float(request, "weight_isa_ratio"),
        config=_query_float(request, "weight_config"),
    )
    summary = RiskService.get_risk_summary(
        settings=settings,
        db_path=db_path,
        optionality_weights=optionality_weights,
    )
    return templates.TemplateResponse(
        request,
        "risk.html",
        {
            "request": request,
            "risk_summary": summary,
            "settings": settings,
            "optionality_weights": (
                {k: str(v) for k, v in summary.optionality_index.weights_pct.items()}
                if summary.optionality_index is not None
                else {}
            ),
            **_flash(request.query_params.get("msg")),
        },
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.post("/risk/alerts/lifecycle", include_in_schema=False)
async def update_risk_alert_lifecycle(
    request: Request,
    lifecycle_id: str = Form(...),
    condition_hash: str = Form(default=""),
    action: str = Form(...),
) -> RedirectResponse:
    if not AppContext.is_initialized():
        return RedirectResponse(
            url=f"/risk?msg={quote_plus('Database is locked.')}#alert-center",
            status_code=303,
        )

    try:
        payload = AlertLifecycleService.record_state_transition(
            lifecycle_id=lifecycle_id,
            condition_hash=condition_hash,
            action=action,
            source="risk_alert_center",
            notes="Updated from Risk alert center.",
        )
        msg = f"{payload['state_label']} alert state saved."
    except ValueError as exc:
        msg = str(exc)

    return RedirectResponse(
        url=f"/risk?msg={quote_plus(msg)}#alert-center",
        status_code=303,
    )
