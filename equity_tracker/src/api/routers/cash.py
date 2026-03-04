"""
Cash ledger routes (UI-only deterministic multi-currency cash workflows).
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...app_context import AppContext
from ...services.cash_ledger_service import (
    CONTAINER_BANK,
    CONTAINER_BROKER,
    CashLedgerService,
    ENTRY_TYPE_MANUAL_ADJUSTMENT,
)
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import session_required

router = APIRouter(tags=["cash"], dependencies=[Depends(session_required)])
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


def _default_entry_form(today: date_type) -> dict:
    return {
        "entry_date": today.isoformat(),
        "container": CONTAINER_BROKER,
        "currency": "GBP",
        "amount": "",
        "entry_type": ENTRY_TYPE_MANUAL_ADJUSTMENT,
        "source": "manual",
        "notes": "",
    }


def _default_transfer_form(today: date_type) -> dict:
    return {
        "entry_date": today.isoformat(),
        "source_container": CONTAINER_BROKER,
        "source_currency": "GBP",
        "source_amount": "",
        "fx_rate": "",
        "fx_fee_gbp": "0.00",
        "fx_source": "",
        "notes": "",
    }


def _render_cash_page(
    *,
    request: Request,
    settings: AppSettings | None,
    error: str | None = None,
    msg: str | None = None,
    status_code: int = 200,
    entry_form: dict | None = None,
    transfer_form: dict | None = None,
) -> HTMLResponse:
    today = date_type.today()
    db_path = _state.get_db_path()
    dashboard = CashLedgerService.dashboard(db_path=db_path)
    context = {
        "request": request,
        "settings": settings,
        "error": error,
        "flash": msg,
        "dashboard": dashboard,
        "entry_form": entry_form or _default_entry_form(today),
        "transfer_form": transfer_form or _default_transfer_form(today),
        "containers": [CONTAINER_BROKER, "ISA", CONTAINER_BANK],
        "source_containers": [CONTAINER_BROKER, CONTAINER_BANK],
    }
    return templates.TemplateResponse(
        request,
        "cash.html",
        context,
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


@router.get("/cash", response_class=HTMLResponse, include_in_schema=False)
async def cash_page(
    request: Request,
    msg: str | None = None,
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    return _render_cash_page(
        request=request,
        settings=_load_settings(),
        msg=msg,
    )


@router.post("/cash/entry", response_class=HTMLResponse, include_in_schema=False)
async def cash_entry_create(
    request: Request,
    entry_date: str = Form(...),
    container: str = Form(...),
    currency: str = Form(...),
    amount: str = Form(...),
    entry_type: str = Form(ENTRY_TYPE_MANUAL_ADJUSTMENT),
    source: str = Form("manual"),
    notes: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    entry_form = {
        "entry_date": entry_date,
        "container": container,
        "currency": currency,
        "amount": amount,
        "entry_type": entry_type,
        "source": source,
        "notes": notes,
    }
    try:
        parsed_date = date_type.fromisoformat(entry_date)
        parsed_amount = Decimal(amount)
    except (ValueError, InvalidOperation):
        return _render_cash_page(
            request=request,
            settings=settings,
            error="Invalid cash entry date or amount.",
            status_code=422,
            entry_form=entry_form,
        )

    db_path = _state.get_db_path()
    try:
        CashLedgerService.record_entry(
            db_path=db_path,
            entry_date=parsed_date,
            container=container,
            currency=currency,
            amount=parsed_amount,
            entry_type=entry_type,
            source=source,
            notes=notes,
        )
    except ValueError as exc:
        return _render_cash_page(
            request=request,
            settings=settings,
            error=str(exc),
            status_code=422,
            entry_form=entry_form,
        )

    return RedirectResponse(
        f"/cash?msg={quote_plus('Cash entry recorded.')}",
        status_code=303,
    )


@router.post("/cash/isa-transfer", response_class=HTMLResponse, include_in_schema=False)
async def cash_isa_transfer(
    request: Request,
    entry_date: str = Form(...),
    source_container: str = Form(...),
    source_currency: str = Form(...),
    source_amount: str = Form(...),
    fx_rate: str = Form(""),
    fx_fee_gbp: str = Form("0"),
    fx_source: str = Form(""),
    notes: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    transfer_form = {
        "entry_date": entry_date,
        "source_container": source_container,
        "source_currency": source_currency,
        "source_amount": source_amount,
        "fx_rate": fx_rate,
        "fx_fee_gbp": fx_fee_gbp,
        "fx_source": fx_source,
        "notes": notes,
    }

    try:
        parsed_date = date_type.fromisoformat(entry_date)
        parsed_source_amount = Decimal(source_amount)
        parsed_fx_rate = Decimal(fx_rate) if fx_rate.strip() else None
        parsed_fx_fee = Decimal(fx_fee_gbp) if fx_fee_gbp.strip() else Decimal("0")
    except (ValueError, InvalidOperation):
        return _render_cash_page(
            request=request,
            settings=settings,
            error="Invalid ISA transfer input.",
            status_code=422,
            transfer_form=transfer_form,
        )

    db_path = _state.get_db_path()
    try:
        result = CashLedgerService.create_isa_transfer(
            db_path=db_path,
            entry_date=parsed_date,
            source_container=source_container,
            source_currency=source_currency,
            source_amount=parsed_source_amount,
            fx_rate=parsed_fx_rate,
            fx_fee_gbp=parsed_fx_fee,
            fx_source=fx_source,
            notes=notes,
        )
    except ValueError as exc:
        return _render_cash_page(
            request=request,
            settings=settings,
            error=str(exc),
            status_code=422,
            transfer_form=transfer_form,
        )

    msg = (
        "ISA transfer recorded "
        f"(group {result['group_id'][:8]}, net GBP {result['transferred_gbp']})."
    )
    return RedirectResponse(f"/cash?msg={quote_plus(msg)}", status_code=303)
