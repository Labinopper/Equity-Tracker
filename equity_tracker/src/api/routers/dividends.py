"""
Dividend routes (UI + JSON API).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ...app_context import AppContext
from ...db.repository import LotRepository, SecurityRepository
from ...services.cash_ledger_service import (
    CONTAINER_BANK,
    CONTAINER_BROKER,
    CONTAINER_ISA,
    CashLedgerService,
)
from ...services.dividend_service import DividendService
from ...services.portfolio_service import PortfolioService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import db_required, session_required

router = APIRouter(tags=["dividends"], dependencies=[Depends(session_required)])
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"
_VALID_TREATMENTS = ("TAXABLE", "ISA_EXEMPT")
_VALID_CASH_CONTAINERS = (CONTAINER_BROKER, CONTAINER_ISA, CONTAINER_BANK, "NONE")
_ENTRY_TYPE_DIVIDEND_PAYOUT = "DIVIDEND_PAYOUT"


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
    cash_container: Literal["BROKER", "ISA", "BANK", "NONE"] = "BROKER"
    cash_amount_original_ccy: str | None = None


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


def _parse_date_optional(raw_value: str | None, field_name: str) -> date | None:
    if raw_value is None:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD).") from exc


def _default_treatment_for_group(lot_group: str | None) -> str:
    normalized = (lot_group or "").strip().upper()
    if normalized in {"ISA_ONLY", "SCHEME:ISA"}:
        return "ISA_EXEMPT"
    return "TAXABLE"


def _normalize_cash_container(raw_value: str | None) -> str:
    cleaned = (raw_value or "").strip().upper() or CONTAINER_BROKER
    if cleaned not in _VALID_CASH_CONTAINERS:
        raise ValueError("cash_container must be one of BROKER, ISA, BANK, NONE.")
    return cleaned


def _resolve_cash_backfill_amount(entry: dict) -> Decimal | None:
    parsed_net = _parse_decimal_optional(
        str(entry.get("net_amount_original_ccy") or ""),
        "net_amount_original_ccy",
    )
    if parsed_net is not None and parsed_net > Decimal("0"):
        return parsed_net

    parsed_original = _parse_decimal_optional(
        str(entry.get("amount_original_ccy") or ""),
        "amount_original_ccy",
    )
    if parsed_original is not None and parsed_original > Decimal("0"):
        return parsed_original

    if str(entry.get("original_currency") or "").strip().upper() == "GBP":
        parsed_gbp = _parse_decimal_optional(
            str(entry.get("amount_gbp") or ""),
            "amount_gbp",
        )
        if parsed_gbp is not None and parsed_gbp > Decimal("0"):
            return parsed_gbp
    return None


def _existing_cash_dividend_ids(db_path) -> set[str]:
    dividend_ids: set[str] = set()
    for entry in CashLedgerService.load_entries(db_path):
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        dividend_id = str(metadata.get("dividend_entry_id") or "").strip()
        if dividend_id:
            dividend_ids.add(dividend_id)
    return dividend_ids


def _normalize_lot_ids(raw_lot_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_lot_id in raw_lot_ids or []:
        lot_id = (raw_lot_id or "").strip()
        if not lot_id or lot_id in seen:
            continue
        normalized.append(lot_id)
        seen.add(lot_id)
    return normalized


def _lot_group_for_schemes(scheme_types: set[str]) -> str:
    if not scheme_types:
        return "ALL"
    if len(scheme_types) == 1:
        return f"SCHEME:{next(iter(scheme_types))}"
    if all(scheme == "ISA" for scheme in scheme_types):
        return "ISA_ONLY"
    if all(scheme != "ISA" for scheme in scheme_types):
        return "TAXABLE_ONLY"
    return "ALL"


def _resolve_selected_lot_context(lot_ids: list[str]) -> dict[str, object] | None:
    normalized_lot_ids = _normalize_lot_ids(lot_ids)
    if not normalized_lot_ids:
        return None

    selected_rows: list[tuple[object, object, Decimal]] = []
    with AppContext.read_session() as sess:
        lot_repo = LotRepository(sess)
        sec_repo = SecurityRepository(sess)
        for lot_id in normalized_lot_ids:
            lot = lot_repo.require_by_id(lot_id)
            qty_remaining = _parse_decimal_optional(
                str(getattr(lot, "quantity_remaining", "")),
                "quantity_remaining",
            )
            if qty_remaining is None or qty_remaining <= Decimal("0"):
                raise ValueError(f"Lot has no remaining quantity: {lot_id}")
            sec = sec_repo.require_by_id(lot.security_id)
            selected_rows.append((lot, sec, qty_remaining))

    security_ids = {sec.id for _, sec, _ in selected_rows}
    if len(security_ids) != 1:
        raise ValueError("Selected lots must belong to a single security.")

    currencies = {
        str(getattr(sec, "currency", "") or "").strip().upper() for _, sec, _ in selected_rows
    }
    currencies.discard("")
    if len(currencies) > 1:
        raise ValueError("Selected lots must share one security currency.")

    scheme_types = {
        str(getattr(lot, "scheme_type", "") or "").strip().upper() for lot, _, _ in selected_rows
    }
    scheme_types.discard("")
    lot_group = _lot_group_for_schemes(scheme_types)
    security = selected_rows[0][1]
    total_quantity = sum((qty for _, _, qty in selected_rows), Decimal("0"))
    return {
        "lot_ids": normalized_lot_ids,
        "security_id": security.id,
        "ticker": security.ticker,
        "currency": str(getattr(security, "currency", "") or "").strip().upper() or "GBP",
        "lot_group": lot_group,
        "default_tax_treatment": _default_treatment_for_group(lot_group),
        "total_quantity": str(total_quantity),
    }


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
        "cash_containers": _VALID_CASH_CONTAINERS,
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
        cash_amount_original_ccy = _parse_decimal_optional(
            payload.cash_amount_original_ccy, "cash_amount_original_ccy"
        )
        fx_rate_to_gbp = _parse_decimal_optional(payload.fx_rate_to_gbp, "fx_rate_to_gbp")
        cash_container = _normalize_cash_container(payload.cash_container)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        entry = DividendService.add_dividend_entry(
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

    if cash_container != "NONE":
        post_amount = cash_amount_original_ccy
        if post_amount is None:
            if amount_original_ccy is not None:
                post_amount = amount_original_ccy
            elif amount_gbp is not None and entry.get("original_currency") == "GBP":
                post_amount = amount_gbp
            else:
                post_amount = _parse_decimal_optional(
                    entry.get("amount_original_ccy"),
                    "amount_original_ccy",
                )
        if post_amount is not None and post_amount > Decimal("0"):
            db_path = _state.get_db_path()
            try:
                cash_entry = CashLedgerService.record_entry(
                    db_path=db_path,
                    entry_date=payload.dividend_date,
                    container=cash_container,
                    currency=entry["original_currency"],
                    amount=post_amount,
                    entry_type=_ENTRY_TYPE_DIVIDEND_PAYOUT,
                    source=(payload.source or "manual").strip() or "manual",
                    notes=(
                        f"Auto-posted from dividend entry {entry['id']} "
                        f"({entry['ticker']})."
                    ),
                    metadata={
                        "dividend_entry_id": entry["id"],
                        "security_id": entry["security_id"],
                        "ticker": entry["ticker"],
                        "fx_rate": entry.get("fx_rate_to_gbp") or None,
                        "fx_source": entry.get("fx_rate_source") or None,
                    },
                )
                entry["cash_entry_id"] = cash_entry.get("entry_id")
                entry["cash_container"] = cash_container
            except ValueError as exc:
                entry["cash_post_error"] = str(exc)

    return entry


@router.get("/dividends", response_class=HTMLResponse, include_in_schema=False)
async def dividends_page(request: Request, msg: str | None = None) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    return _render_dividends_page(request, settings=settings, msg=msg)


@router.post("/dividends/reminder", response_class=HTMLResponse, include_in_schema=False)
async def dividends_reminder_submit(
    request: Request,
    security_id: str = Form(...),
    dividend_reminder_date: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    try:
        parsed_reminder_date = _parse_date_optional(
            dividend_reminder_date,
            "dividend_reminder_date",
        )
        PortfolioService.set_security_dividend_reminder_date(
            security_id=security_id,
            dividend_reminder_date=parsed_reminder_date,
        )
    except (ValueError, KeyError) as exc:
        return _render_dividends_page(
            request,
            settings=settings,
            error=_exc_message(exc),
            prev={
                "security_id": security_id,
                "dividend_reminder_date": dividend_reminder_date,
            },
        )

    msg = (
        "Dividend+reminder+saved."
        if parsed_reminder_date is not None
        else "Dividend+reminder+cleared."
    )
    return RedirectResponse(f"/dividends?msg={msg}", status_code=303)


@router.post("/dividends/relink", response_class=HTMLResponse, include_in_schema=False)
async def dividends_relink_submit(
    request: Request,
    entry_id: str = Form(...),
    lot_ids: list[str] = Form([]),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    normalized_entry_id = (entry_id or "").strip()
    normalized_lot_ids = _normalize_lot_ids(lot_ids)
    prev = {
        "relink_entry_id": normalized_entry_id,
        "relink_lot_ids": normalized_lot_ids,
    }

    if not normalized_entry_id:
        return _render_dividends_page(
            request,
            settings=settings,
            error="Select a dividend entry to relink.",
            prev=prev,
        )
    if not normalized_lot_ids:
        return _render_dividends_page(
            request,
            settings=settings,
            error="Select at least one lot to relink this dividend entry.",
            prev=prev,
        )

    try:
        lot_context = _resolve_selected_lot_context(normalized_lot_ids)
        if lot_context is None:
            raise ValueError("Select at least one valid lot.")
        updated = DividendService.relink_dividend_entry_lots(
            entry_id=normalized_entry_id,
            security_id=str(lot_context["security_id"]),
            lot_ids=normalized_lot_ids,
            lot_group=str(lot_context["lot_group"]),
            linked_lot_quantity=str(lot_context["total_quantity"]),
        )
    except (KeyError, ValueError) as exc:
        return _render_dividends_page(
            request,
            settings=settings,
            error=_exc_message(exc),
            prev=prev,
        )

    msg_text = (
        f"Dividend entry {updated['id']} ({updated['ticker']}) relinked to "
        f"{len(normalized_lot_ids)} lot(s)."
    )
    return RedirectResponse(
        f"/dividends?msg={quote_plus(msg_text)}",
        status_code=303,
    )


@router.post("/dividends/backfill-cash", response_class=HTMLResponse, include_in_schema=False)
async def dividends_backfill_cash_submit(
    request: Request,
    cash_container: str = Form(CONTAINER_BROKER),
    include_forecast: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)

    settings = _load_settings()
    try:
        normalized_cash_container = _normalize_cash_container(cash_container)
    except ValueError as exc:
        return _render_dividends_page(request, settings=settings, error=str(exc))

    if normalized_cash_container == "NONE":
        return _render_dividends_page(
            request,
            settings=settings,
            error="Select BROKER, ISA, or BANK for cash backfill.",
        )

    include_forecast_entries = (include_forecast or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    summary_settings = settings
    if summary_settings is not None and summary_settings.hide_values:
        summary_settings = None
    entries = DividendService.get_summary(settings=summary_settings).get("entries", [])

    db_path = _state.get_db_path()
    existing_dividend_ids = _existing_cash_dividend_ids(db_path)
    posted = 0
    skipped = 0
    failed = 0

    for entry in entries:
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            failed += 1
            continue
        if entry_id in existing_dividend_ids:
            skipped += 1
            continue
        if entry.get("is_forecast") and not include_forecast_entries:
            skipped += 1
            continue

        try:
            amount = _resolve_cash_backfill_amount(entry)
            if amount is None or amount <= Decimal("0"):
                skipped += 1
                continue
            entry_date = date.fromisoformat(str(entry.get("dividend_date") or ""))
            currency = str(entry.get("original_currency") or "").strip().upper()
            if len(currency) != 3 or not currency.isalpha():
                failed += 1
                continue
            source = str(entry.get("source") or "dividend_backfill").strip()
            cash_entry = CashLedgerService.record_entry(
                db_path=db_path,
                entry_date=entry_date,
                container=normalized_cash_container,
                currency=currency,
                amount=amount,
                entry_type=_ENTRY_TYPE_DIVIDEND_PAYOUT,
                source=source or "dividend_backfill",
                notes=(
                    f"Backfilled from dividend entry {entry_id} "
                    f"({entry.get('ticker') or 'UNKNOWN'})."
                ),
                metadata={
                    "dividend_entry_id": entry_id,
                    "security_id": entry.get("security_id"),
                    "ticker": entry.get("ticker"),
                    "lot_group": entry.get("lot_group"),
                    "fx_rate": entry.get("fx_rate_to_gbp"),
                    "fx_source": entry.get("fx_rate_source"),
                    "backfill": True,
                },
            )
            if cash_entry.get("entry_id"):
                posted += 1
                existing_dividend_ids.add(entry_id)
            else:
                failed += 1
        except (TypeError, ValueError):
            failed += 1

    msg_text = (
        "Dividend cash backfill complete: "
        f"{posted} posted, {skipped} skipped, {failed} failed."
    )
    return RedirectResponse(
        f"/dividends?msg={quote_plus(msg_text)}",
        status_code=303,
    )


@router.post("/dividends/add", response_class=HTMLResponse, include_in_schema=False)
async def dividends_add_submit(
    request: Request,
    security_id: str = Form(""),
    lot_ids: list[str] = Form([]),
    dividend_date: str = Form(...),
    lot_group: str = Form("ALL"),
    ib_row: str = Form(""),
    net_amount_original_ccy: str = Form(""),
    gross_amount_original_ccy: str = Form(""),
    tax_withheld_original_ccy: str = Form(""),
    fee_original_ccy: str = Form(""),
    quantity: str = Form(""),
    gross_rate_original_ccy: str = Form(""),
    ex_date: str = Form(""),
    ib_code: str = Form(""),
    amount_gbp: str = Form(""),
    amount_original_ccy: str = Form(""),
    original_currency: str = Form("GBP"),
    fx_rate_to_gbp: str = Form(""),
    fx_rate_source: str = Form("manual"),
    tax_treatment: str = Form("TAXABLE"),
    source: str = Form("manual"),
    cash_container: str = Form(CONTAINER_BROKER),
    notes: str = Form(""),
) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    settings = _load_settings()
    normalized_lot_ids = _normalize_lot_ids(lot_ids)
    prev = {
        "security_id": security_id,
        "lot_ids": normalized_lot_ids,
        "dividend_date": dividend_date,
        "lot_group": lot_group,
        "ib_row": ib_row,
        "net_amount_original_ccy": net_amount_original_ccy,
        "gross_amount_original_ccy": gross_amount_original_ccy,
        "tax_withheld_original_ccy": tax_withheld_original_ccy,
        "fee_original_ccy": fee_original_ccy,
        "quantity": quantity,
        "gross_rate_original_ccy": gross_rate_original_ccy,
        "ex_date": ex_date,
        "ib_code": ib_code,
        "amount_gbp": amount_gbp,
        "amount_original_ccy": amount_original_ccy,
        "original_currency": original_currency,
        "fx_rate_to_gbp": fx_rate_to_gbp,
        "fx_rate_source": fx_rate_source,
        "tax_treatment": tax_treatment,
        "source": source,
        "cash_container": cash_container,
        "notes": notes,
    }

    selected_lot_context: dict[str, object] | None = None
    if normalized_lot_ids:
        try:
            selected_lot_context = _resolve_selected_lot_context(normalized_lot_ids)
        except (KeyError, ValueError) as exc:
            return _render_dividends_page(
                request,
                settings=settings,
                error=_exc_message(exc),
                prev=prev,
            )
        if selected_lot_context is not None:
            security_id = str(selected_lot_context["security_id"])
            original_currency = str(selected_lot_context["currency"])
            lot_group = str(selected_lot_context["lot_group"])
            if not (quantity or "").strip():
                quantity = str(selected_lot_context["total_quantity"])
            prev["security_id"] = security_id
            prev["original_currency"] = original_currency
            prev["lot_group"] = lot_group
            prev["quantity"] = quantity

    resolved_security_id = (security_id or "").strip()
    if not resolved_security_id:
        return _render_dividends_page(
            request,
            settings=settings,
            error="Select one or more lots, or choose a security.",
            prev=prev,
        )
    prev["security_id"] = resolved_security_id

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
        parsed_net_amount_original_ccy = _parse_decimal_optional(
            net_amount_original_ccy, "net_amount_original_ccy"
        )
        parsed_gross_amount_original_ccy = _parse_decimal_optional(
            gross_amount_original_ccy, "gross_amount_original_ccy"
        )
        parsed_tax_withheld_original_ccy = _parse_decimal_optional(
            tax_withheld_original_ccy, "tax_withheld_original_ccy"
        )
        parsed_fee_original_ccy = _parse_decimal_optional(
            fee_original_ccy, "fee_original_ccy"
        )
        parsed_gross_rate_original_ccy = _parse_decimal_optional(
            gross_rate_original_ccy, "gross_rate_original_ccy"
        )
        parsed_fx_rate_to_gbp = _parse_decimal_optional(
            fx_rate_to_gbp, "fx_rate_to_gbp"
        )
        parsed_ex_date = _parse_date_optional(ex_date, "ex_date")
        normalized_cash_container = _normalize_cash_container(cash_container)
    except ValueError as exc:
        return _render_dividends_page(request, settings=settings, error=str(exc), prev=prev)

    non_negative_fields = (
        ("net_amount_original_ccy", parsed_net_amount_original_ccy),
        ("gross_amount_original_ccy", parsed_gross_amount_original_ccy),
        ("tax_withheld_original_ccy", parsed_tax_withheld_original_ccy),
        ("fee_original_ccy", parsed_fee_original_ccy),
        ("gross_rate_original_ccy", parsed_gross_rate_original_ccy),
    )
    for field_name, value in non_negative_fields:
        if value is not None and value < Decimal("0"):
            return _render_dividends_page(
                request,
                settings=settings,
                error=f"{field_name} cannot be negative.",
                prev=prev,
            )

    resolved_amount_original_ccy = parsed_gross_amount_original_ccy
    if resolved_amount_original_ccy is None:
        resolved_amount_original_ccy = parsed_amount_original_ccy
    if resolved_amount_original_ccy is None and parsed_net_amount_original_ccy is not None:
        withheld = parsed_tax_withheld_original_ccy or Decimal("0")
        fee = parsed_fee_original_ccy or Decimal("0")
        resolved_amount_original_ccy = parsed_net_amount_original_ccy + withheld + fee
    if (
        resolved_amount_original_ccy is None
        and parsed_amount_gbp is not None
        and (original_currency or "").strip().upper() == "GBP"
    ):
        resolved_amount_original_ccy = parsed_amount_gbp

    if resolved_amount_original_ccy is None and parsed_amount_gbp is None:
        return _render_dividends_page(
            request,
            settings=settings,
            error=(
                "Provide Net, Gross, amount_original_ccy, or amount_gbp so the dividend "
                "can be recorded."
            ),
            prev=prev,
        )

    resolved_treatment = (tax_treatment or "").strip().upper()
    if resolved_treatment not in _VALID_TREATMENTS:
        resolved_treatment = _default_treatment_for_group(lot_group)

    normalized_group = (lot_group or "").strip()
    normalized_group_value = (
        normalized_group if normalized_group.upper() not in {"", "ALL"} else None
    )
    normalized_quantity = (quantity or "").strip() or None
    normalized_ib_code = (ib_code or "").strip() or None
    has_extended_ib_fields = any(
        (
            normalized_group_value is not None,
            parsed_net_amount_original_ccy is not None,
            parsed_gross_amount_original_ccy is not None,
            parsed_tax_withheld_original_ccy is not None,
            parsed_fee_original_ccy is not None,
            parsed_gross_rate_original_ccy is not None,
            parsed_ex_date is not None,
            normalized_quantity is not None,
            normalized_ib_code is not None,
        )
    )

    ib_meta = {
        "lot_group": normalized_group_value,
        "net_amount_original_ccy": parsed_net_amount_original_ccy,
        "gross_amount_original_ccy": (
            parsed_gross_amount_original_ccy
            if parsed_gross_amount_original_ccy is not None
            else (resolved_amount_original_ccy if has_extended_ib_fields else None)
        ),
        "tax_withheld_original_ccy": parsed_tax_withheld_original_ccy,
        "fee_original_ccy": parsed_fee_original_ccy,
        "quantity": normalized_quantity,
        "gross_rate_original_ccy": parsed_gross_rate_original_ccy,
        "ex_date": parsed_ex_date.isoformat() if parsed_ex_date else None,
        "ib_code": normalized_ib_code,
    }
    if normalized_lot_ids:
        ib_meta["lot_ids"] = ",".join(normalized_lot_ids)
    notes_with_meta = DividendService.append_ibkr_metadata_to_notes(
        notes=notes.strip() or None,
        metadata=ib_meta,
    )

    resolved_cash_amount_original = parsed_net_amount_original_ccy
    if resolved_cash_amount_original is None and resolved_amount_original_ccy is not None:
        withheld = parsed_tax_withheld_original_ccy or Decimal("0")
        fee = parsed_fee_original_ccy or Decimal("0")
        candidate = resolved_amount_original_ccy - withheld - fee
        resolved_cash_amount_original = (
            candidate if candidate > Decimal("0") else resolved_amount_original_ccy
        )
    if (
        resolved_cash_amount_original is None
        and parsed_amount_gbp is not None
        and (original_currency or "").strip().upper() == "GBP"
    ):
        resolved_cash_amount_original = parsed_amount_gbp

    try:
        entry = DividendService.add_dividend_entry(
            security_id=resolved_security_id,
            dividend_date=parsed_date,
            amount_gbp=parsed_amount_gbp,
            amount_original_ccy=resolved_amount_original_ccy,
            original_currency=original_currency,
            fx_rate_to_gbp=parsed_fx_rate_to_gbp,
            fx_rate_source=fx_rate_source.strip() or None,
            tax_treatment=resolved_treatment,
            source=source.strip() or "manual",
            notes=notes_with_meta,
        )
    except (ValueError, KeyError) as exc:
        return _render_dividends_page(
            request,
            settings=settings,
            error=_exc_message(exc),
            prev=prev,
        )

    msg_text = "Dividend entry added."
    if (
        normalized_cash_container != "NONE"
        and resolved_cash_amount_original is not None
        and resolved_cash_amount_original > Decimal("0")
    ):
        db_path = _state.get_db_path()
        try:
            CashLedgerService.record_entry(
                db_path=db_path,
                entry_date=parsed_date,
                container=normalized_cash_container,
                currency=entry["original_currency"],
                amount=resolved_cash_amount_original,
                entry_type=_ENTRY_TYPE_DIVIDEND_PAYOUT,
                source=source.strip() or "manual",
                notes=(
                    f"Auto-posted from dividend entry {entry['id']} "
                    f"({entry['ticker']})."
                ),
                metadata={
                    "dividend_entry_id": entry["id"],
                    "security_id": entry["security_id"],
                    "ticker": entry["ticker"],
                    "lot_group": normalized_group_value,
                    "fx_rate": entry.get("fx_rate_to_gbp") or None,
                    "fx_source": entry.get("fx_rate_source") or None,
                },
            )
            msg_text = (
                f"Dividend entry added; cash posted to {normalized_cash_container} "
                f"{entry['original_currency']} {resolved_cash_amount_original}."
            )
        except ValueError as exc:
            msg_text = f"Dividend entry added; cash post failed: {exc}"

    return RedirectResponse(
        f"/dividends?msg={quote_plus(msg_text)}",
        status_code=303,
    )
