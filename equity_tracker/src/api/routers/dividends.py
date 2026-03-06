"""
Dividend routes (UI + JSON API).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ...app_context import AppContext
from ...services.dividend_service import DividendService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["dividends"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"
_VALID_TREATMENTS = ("TAXABLE", "ISA_EXEMPT")


class DividendCreatePayload(BaseModel):
    security_id: str
    dividend_date: date
    amount_gbp: str | None = None
    amount_original_ccy: str | None = None
    original_currency: str = "GBP"
    fx_rate_to_gbp: str | None = None
    fx_rate_source: str | None = None
    tax_treatment: Literal["TAXABLE", "ISA_EXEMPT"] = "TAXABLE"
    source: str | None = "manual"
    notes: str | None = None


def _exc_message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _load_settings() -> AppSettings | None:
    db_path = _state.get_db_path()
    return AppSettings.load(db_path) if db_path else None


def _parse_decimal_optional(raw_value: str | None, field_name: str) -> Decimal | None:
    if raw_value is None:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid decimal.") from exc


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _render_dividends_page(
    request: Request,
    *,
    settings: AppSettings | None,
    error: str | None = None,
    prev: dict | None = None,
    msg: str | None = None,
) -> HTMLResponse:
    payload = DividendService.get_summary(settings=settings)
    context = {
        "request": request,
        "dividends": payload,
        "tax_treatments": _VALID_TREATMENTS,
        "flash": msg,
    }
    if error:
        context["error"] = error
    if prev:
        context["prev"] = prev
    return templates.TemplateResponse(
        request,
        "dividends.html",
        context,
        media_type=_HTML_UTF8_MEDIA_TYPE,
        status_code=422 if error else 200,
    )


@router.get("/api/dividends/summary")
async def api_dividends_summary(_: None = Depends(db_required)) -> dict:
    settings = _load_settings()
    return DividendService.get_summary(settings=settings)


@router.post("/api/dividends/entries", status_code=201)
async def api_dividend_entry_create(
    payload: DividendCreatePayload,
    _: None = Depends(db_required),
) -> dict:
    try:
        amount_gbp = _parse_decimal_optional(payload.amount_gbp, "amount_gbp")
        amount_original_ccy = _parse_decimal_optional(
            payload.amount_original_ccy, "amount_original_ccy"
        )
        fx_rate_to_gbp = _parse_decimal_optional(payload.fx_rate_to_gbp, "fx_rate_to_gbp")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return DividendService.add_dividend_entry(
            security_id=payload.security_id,
            dividend_date=payload.dividend_date,
            amount_gbp=amount_gbp,
            amount_original_ccy=amount_original_ccy,
            original_currency=payload.original_currency,
            fx_rate_to_gbp=fx_rate_to_gbp,
            fx_rate_source=payload.fx_rate_source,
            tax_treatment=payload.tax_treatment,
            source=payload.source,
            notes=payload.notes,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=_exc_message(exc)) from exc


@router.get("/dividends", response_class=HTMLResponse, include_in_schema=False)
async def dividends_page(request: Request, msg: str | None = None) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    return _render_dividends_page(request, settings=settings, msg=msg)


@router.post("/dividends/add", response_class=HTMLResponse, include_in_schema=False)
async def dividends_add_submit(
    request: Request,
    security_id: str = Form(...),
    dividend_date: str = Form(...),
    amount_gbp: str = Form(""),
    amount_original_ccy: str = Form(""),
    original_currency: str = Form("GBP"),
    fx_rate_to_gbp: str = Form(""),
    fx_rate_source: str = Form("manual"),
    tax_treatment: str = Form("TAXABLE"),
    source: str = Form("manual"),
    notes: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    prev = {
        "security_id": security_id,
        "dividend_date": dividend_date,
        "amount_gbp": amount_gbp,
        "amount_original_ccy": amount_original_ccy,
        "original_currency": original_currency,
        "fx_rate_to_gbp": fx_rate_to_gbp,
        "fx_rate_source": fx_rate_source,
        "tax_treatment": tax_treatment,
        "source": source,
        "notes": notes,
    }

    try:
        parsed_date = date.fromisoformat(dividend_date)
    except ValueError as exc:
        return _render_dividends_page(
            request,
            settings=settings,
            error=f"Invalid dividend date: {exc}",
            prev=prev,
        )

    try:
        parsed_amount_gbp = _parse_decimal_optional(amount_gbp, "amount_gbp")
        parsed_amount_original_ccy = _parse_decimal_optional(
            amount_original_ccy, "amount_original_ccy"
        )
        parsed_fx_rate_to_gbp = _parse_decimal_optional(
            fx_rate_to_gbp, "fx_rate_to_gbp"
        )
    except ValueError as exc:
        return _render_dividends_page(request, settings=settings, error=str(exc), prev=prev)

    try:
        DividendService.add_dividend_entry(
            security_id=security_id,
            dividend_date=parsed_date,
            amount_gbp=parsed_amount_gbp,
            amount_original_ccy=parsed_amount_original_ccy,
            original_currency=original_currency,
            fx_rate_to_gbp=parsed_fx_rate_to_gbp,
            fx_rate_source=fx_rate_source.strip() or None,
            tax_treatment=tax_treatment,
            source=source.strip() or "manual",
            notes=notes.strip() or None,
        )
    except (ValueError, KeyError) as exc:
        return _render_dividends_page(
            request,
            settings=settings,
            error=_exc_message(exc),
            prev=prev,
        )

    return RedirectResponse(
        "/dividends?msg=Dividend+entry+added.",
        status_code=303,
    )
