"""
UI router â€” server-rendered Jinja2 pages.

All pages call service methods directly; they do NOT make HTTP requests to the
JSON API.  This keeps latency minimal and avoids circular HTTP calls.

URL layout (no conflict with JSON API paths)
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  GET  /                        Portfolio overview (homepage)
  GET  /per-scheme              Per-scheme current vs historic P&L
  GET  /portfolio/add-security  Add-security form
  POST /portfolio/add-security  Create security â†’ redirect to /
  GET  /portfolio/add-lot       Add-lot form
  POST /portfolio/add-lot       Create lot â†’ redirect to /
  GET  /simulate                Simulate disposal (form + result on same page)
  POST /simulate                Run simulation â†’ re-render with result
  GET  /cgt                     CGT report
  GET  /economic-gain           Economic gain report
  GET  /audit                   Audit log
  GET  /settings                Settings form
  POST /settings                Save settings â†’ redirect to /settings

PRG (Post-Redirect-Get)
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Write operations redirect on success so that a browser refresh does not
re-submit the form.  Flash messages are passed via a single-use ``msg``
query parameter.

Locked state
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Every page helper calls ``_check_locked(request)`` first.  If the database
is not open, the locked.html page is returned instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...app_context import AppContext
from ...core.tax_engine import (
    available_tax_years,
    brokerage_true_cost,
    espp_true_cost,
    get_marginal_rates,
    tax_year_for_date,
)
from ...core.tax_engine.context import TaxContext
from ...db.models import (
    Lot,
    LotDisposal,
    PriceTickerSnapshot,
    Transaction,
)
from ...db.repository import (
    AuditRepository,
    LotRepository,
    PriceRepository,
    SecurityRepository,
)
from ...services.fx_service import FxService
from ...services.ibkr_price_service import IbkrPriceService
from ...services.capital_stack_service import CapitalStackService
from ...services.exposure_service import ExposureService
from ...services.alert_lifecycle_service import AlertLifecycleService
from ...services.calendar_service import CalendarService
from ...services.portfolio_service import (
    LotSummary,
    PortfolioService,
    SecuritySummary,
    _estimate_sell_all_employment_tax,
)
from ...services.price_service import CreditBudgetExceededError, PriceService
from ...services.report_service import ReportService
from ...services.dividend_service import DividendService
from ...settings import AppSettings
from .. import _state
from .._templates import templates
from ..dependencies import session_required

router = APIRouter(tags=["ui"], dependencies=[Depends(session_required)])

ADD_LOT_SCHEME_TYPES = ("RSU", "ESPP", "ESPP_PLUS", "BROKERAGE", "ISA")
SIMULATE_SCHEME_TYPES = ["", *ADD_LOT_SCHEME_TYPES]
DEFAULT_PRICE_INPUT_CURRENCIES = ("GBP", "USD")
_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"
_DAILY_STALE_WHILE_OPEN_MINUTES = 20
_US_MARKET_EXCHANGES = {
    "NYSE",
    "NASDAQ",
    "AMEX",
    "ARCA",
    "IEX",
    "XNYS",
    "XNAS",
}
_UK_MARKET_EXCHANGES = {"LSE", "XLON", "LON"}

SELLABILITY_RANK = {
    "SELLABLE": 0,
    "AT_RISK": 1,
    "LOCKED": 2,
}

SCHEME_DISPLAY_NAMES: dict[str, str] = {
    "RSU": "RSU",
    "ESPP": "ESPP",
    "ESPP_PLUS": "ESPP+",
    "SIP_PARTNERSHIP": "SIP Partnership",
    "SIP_MATCHING": "SIP Matching",
    "SIP_DIVIDEND": "SIP Dividend",
    "BROKERAGE": "Brokerage",
    "ISA": "ISA",
}

SCHEME_DISPLAY_ORDER: tuple[str, ...] = (
    "RSU",
    "ESPP",
    "ESPP_PLUS",
    "SIP_PARTNERSHIP",
    "SIP_MATCHING",
    "SIP_DIVIDEND",
    "BROKERAGE",
    "ISA",
)

_EMPLOYMENT_TAX_ESTIMATE_SCHEMES = frozenset(
    {
        "ESPP",
        "ESPP_PLUS",
        "SIP_PARTNERSHIP",
        "SIP_MATCHING",
        "SIP_DIVIDEND",
    }
)

_GUARDRAIL_LIQUIDITY_ILLUSION_RATIO = Decimal("2")
_GUARDRAIL_ISA_UNDERUTILISATION_MAX_RATIO_PCT = Decimal("25")
_GUARDRAIL_ISA_UNDERUTILISATION_MIN_TAXABLE_GBP = Decimal("5000")
_GUARDRAIL_DRAG_ESCALATION_PCT_OF_INCOME = Decimal("10")
_GUARDRAIL_FORFEITURE_IMMINENCE_DAYS = 183
_GUARDRAIL_CONCENTRATION_TOP_PCT_DEFAULT = Decimal("50")
_GUARDRAIL_CONCENTRATION_EMPLOYER_PCT_DEFAULT = Decimal("40")
_GUARDRAIL_DISMISS_MAX_DAYS = AlertLifecycleService.DISMISS_MAX_DAYS
_GUARDRAIL_SNOOZE_MAX_DAYS = AlertLifecycleService.SNOOZE_MAX_DAYS


@dataclass
class PositionGroupRow:
    """
    Portfolio UI row model.

    View-layer only structure used by the portfolio table/card renderer.
    """
    group_id: str
    acquisition_date: date_type
    scheme_display: str
    paid_qty: Decimal
    match_qty: Decimal
    total_qty: Decimal
    paid_mv: Decimal | None
    match_mv: Decimal | None
    total_mv: Decimal | None
    paid_true_cost: Decimal
    paid_cost_basis: Decimal
    sellability_status: str
    sellability_unlock_date: date_type | None
    forfeiture_risk_days_remaining: int | None
    sell_now_cash_paid: Decimal | None
    sell_now_match_effect: str
    sell_now_forfeited_match_value: Decimal
    sell_now_employment_tax_est: Decimal | None
    sell_now_economic_result: Decimal | None
    # Additional view-only fields used by the template.
    row_kind: str
    has_tax_window: bool
    pnl_tax_basis: Decimal | None
    pnl_economic: Decimal | None
    net_cash_if_sold: Decimal | None
    reason_unavailable: str | None
    decision_signal: str
    decision_title: str
    action_lot_id: str
    detail_lots: list[LotSummary]
    next_milestone_date: date_type | None = None
    next_milestone_net: Decimal | None = None
    next_milestone_gain: Decimal | None = None
    next_milestone_reason: str | None = None
    long_term_date: date_type | None = None
    long_term_net: Decimal | None = None
    long_term_gain: Decimal | None = None
    long_term_reason: str | None = None
    notes: str = ""


@dataclass
class SchemeCurrentMetrics:
    """Current (open-lot) per-scheme metrics shown on /per-scheme."""
    lot_count: int
    position_count: int
    quantity: Decimal
    cost_basis_gbp: Decimal
    true_cost_gbp: Decimal
    market_value_gbp: Decimal | None
    unrealised_tax_pnl_gbp: Decimal | None
    unrealised_economic_pnl_gbp: Decimal | None
    est_employment_tax_gbp: Decimal | None
    est_net_liquidation_gbp: Decimal | None
    post_tax_economic_pnl_gbp: Decimal | None
    allocated_net_dividends_gbp: Decimal
    economic_plus_net_dividends_gbp: Decimal | None
    capital_at_risk_after_dividends_gbp: Decimal
    potential_forfeiture_value_gbp: Decimal


@dataclass
class SchemeHistoricMetrics:
    """Historic (disposed) per-scheme metrics shown on /per-scheme."""
    disposal_count: int
    disposed_lot_count: int
    quantity_disposed: Decimal
    proceeds_gbp: Decimal
    cost_basis_gbp: Decimal
    true_cost_gbp: Decimal
    realised_tax_pnl_gbp: Decimal
    realised_economic_pnl_gbp: Decimal


@dataclass
class SchemeReport:
    """Combined current + historic metrics for one scheme."""
    scheme_type: str
    display_name: str
    current: SchemeCurrentMetrics
    historic: SchemeHistoricMetrics
    lifetime_economic_pnl_gbp: Decimal | None


@dataclass
class MarketWindowStatus:
    """Market open/closed state for a security exchange."""
    is_open: bool | None
    status_text: str | None


@dataclass
class SecurityDailyChange:
    """Per-security daily move summary shown on the portfolio dashboard."""
    security_id: str
    direction: str
    arrow: str
    percent_change: Decimal | None
    value_change_gbp: Decimal | None
    current_as_of: date_type | None
    previous_as_of: date_type | None
    official_close_as_of: date_type | None = None
    unavailable_reason: str | None = None
    price_last_changed_at: datetime | None = None
    freshness_text: str | None = None
    freshness_level: str = "muted"
    freshness_title: str | None = None
    # Native-currency daily value change (shown when security is not GBP-denominated)
    native_currency: str | None = None
    value_change_native: Decimal | None = None
    # Market status: "Open" or "Closed"
    market_status: str | None = None
    # Time until market opens (e.g., "2d 3h"), only if status is "Closed"
    market_opens_in: str | None = None
    # Current/previous prices in GBP and native currency for tooltip display
    current_price_gbp: Decimal | None = None
    previous_price_gbp: Decimal | None = None
    current_price_native: Decimal | None = None
    previous_price_native: Decimal | None = None
    stock_percent_change: Decimal | None = None
    fx_percent_change: Decimal | None = None
    current_fx_rate: Decimal | None = None
    previous_fx_rate: Decimal | None = None
    stock_value_change_gbp: Decimal | None = None
    fx_value_change_gbp: Decimal | None = None
    component_value_change_gbp: Decimal | None = None
    component_percent_change: Decimal | None = None
    component_basis_note: str | None = None
    sparkline_path: str | None = None
    sparkline_direction: str = "flat"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _html_template_response(
    name: str,
    context: dict,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    """Render HTML templates with explicit UTF-8 content type."""
    req = context.get("request")
    if req is None:
        raise KeyError("Template context must include 'request'.")
    return templates.TemplateResponse(
        req,
        name,
        context,
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _is_locked() -> bool:
    return not AppContext.is_initialized()


def _flash(msg: str | None) -> dict:
    """Return a context dict with an optional flash message."""
    return {"flash": msg} if msg else {}


def _tax_inputs_incomplete(settings: AppSettings | None) -> bool:
    """Return True when income inputs are missing/zero for tax estimates."""
    if settings is None:
        return True
    return (
        settings.default_gross_income <= Decimal("0")
        and settings.default_other_income <= Decimal("0")
    )


def _compute_exit_summary(
    *,
    proceeds_cash_gbp: Decimal,
    true_cost_gbp: Decimal,
    employment_tax_due_gbp: Decimal,
    broker_fees_gbp: Decimal,
) -> dict[str, Decimal]:
    """
    Compute cash and economic outcomes for disposal summary presentation.

    Invariant: Gain = Net – True Economic Cost.
    Forfeiture is handled via quantity (forfeited shares excluded from
    proceeds), never as an additional deduction.
    """
    net_cash_received = proceeds_cash_gbp - employment_tax_due_gbp - broker_fees_gbp
    net_economic_result = net_cash_received - true_cost_gbp
    return {
        "proceeds_cash_gbp": proceeds_cash_gbp,
        "out_of_pocket_cost_gbp": true_cost_gbp,
        "employment_tax_due_gbp": employment_tax_due_gbp,
        "broker_fees_gbp": broker_fees_gbp,
        "net_cash_received_gbp": net_cash_received,
        "net_economic_result_gbp": net_economic_result,
    }


def _simulate_security_context(summary) -> list[dict]:
    """
    Build lightweight security rows for /simulate UI.

    Each row includes:
    - id / ticker / name
    - sellable available quantity (excludes currently locked lots)
    - latest market price (if available)
    - sellable available quantity by scheme type
    """
    rows: list[dict] = []
    today = date_type.today()
    for ss in summary.securities:
        sellable_total = Decimal("0")
        by_scheme: dict[str, str] = {}
        for ls in ss.active_lots:
            # MAX button and quantity guard should only consider sellable lots.
            # ESPP+ matched lots inside forfeiture window and pre-vest RSUs are excluded.
            if ls.forfeiture_risk is not None and ls.forfeiture_risk.in_window:
                continue
            if ls.lot.scheme_type == "RSU" and ls.lot.acquisition_date > today:
                continue
            qty = ls.quantity_remaining
            sellable_total += qty
            current = Decimal(by_scheme.get(ls.lot.scheme_type, "0"))
            by_scheme[ls.lot.scheme_type] = str(current + qty)

        sellable_whole = sellable_total.to_integral_value(rounding=ROUND_FLOOR)
        by_scheme_whole: dict[str, str] = {}
        for scheme, qty_raw in by_scheme.items():
            qty_dec = Decimal(qty_raw)
            whole = qty_dec.to_integral_value(rounding=ROUND_FLOOR)
            if whole > Decimal("0"):
                by_scheme_whole[scheme] = str(int(whole))

        rows.append(
            {
                "id": ss.security.id,
                "ticker": ss.security.ticker,
                "name": ss.security.name,
                "available_quantity": str(int(sellable_whole)),
                "latest_price_gbp": (
                    f"{ss.current_price_gbp:.2f}"
                    if ss.current_price_gbp is not None
                    else ""
                ),
                "price_as_of": (
                    ss.price_as_of.isoformat() if ss.price_as_of is not None else ""
                ),
                "price_is_stale": bool(ss.price_is_stale),
                "price_refreshed_at": ss.price_refreshed_at or "",
                "fx_as_of": ss.fx_as_of or "",
                "fx_is_stale": bool(ss.fx_is_stale),
                "available_by_scheme": by_scheme_whole,
            }
        )
    return rows


def _q2(value: Decimal) -> Decimal:
    """Quantize to currency precision."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sum_optional(values: list[Decimal | None]) -> Decimal | None:
    """Sum values when all are present; otherwise return None."""
    if not values:
        return Decimal("0.00")
    if any(v is None for v in values):
        return None
    return _q2(sum((v for v in values if v is not None), Decimal("0")))


def _is_whole_quantity(value: Decimal) -> bool:
    return value == value.to_integral_value(rounding=ROUND_FLOOR)


def _quantity_text(value: Decimal) -> str:
    if _is_whole_quantity(value):
        return str(int(value))
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _allocation_scheme_types(allocations: tuple) -> set[str]:
    lot_ids = {
        str(allocation.lot_id)
        for allocation in allocations
        if getattr(allocation, "lot_id", None)
    }
    if not lot_ids:
        return set()

    with AppContext.read_session() as sess:
        lot_repo = LotRepository(sess)
        scheme_types: set[str] = set()
        for lot_id in lot_ids:
            lot = lot_repo.get_by_id(lot_id)
            if lot is not None and lot.scheme_type:
                scheme_types.add(str(lot.scheme_type).upper())
    return scheme_types


def _simulate_employment_tax_status(result) -> dict[str, object]:
    if result is None:
        return {
            "applicable": False,
            "estimate_available": True,
            "ack_required": False,
            "scheme_types": (),
        }

    scheme_types = tuple(sorted(_allocation_scheme_types(result.allocations)))
    applicable = bool(
        set(scheme_types).intersection(_EMPLOYMENT_TAX_ESTIMATE_SCHEMES)
    ) or bool(result.sip_tax_estimates)
    estimate_available = bool(result.sip_tax_estimates) or not applicable
    return {
        "applicable": applicable,
        "estimate_available": estimate_available,
        "ack_required": applicable and not estimate_available,
        "scheme_types": scheme_types,
    }


def _build_transfer_impact_preview(
    *,
    lot_id: str | None,
    quantity_raw: str,
    candidates: list[dict],
    settings: AppSettings | None,
) -> dict[str, object]:
    preview = {
        "has_selection": False,
        "source_scheme": None,
        "transfer_quantity": None,
        "quantity_error": None,
        "forfeited_match_lot_count": 0,
        "forfeited_match_quantity": "0",
        "forfeited_match_value_gbp": Decimal("0.00"),
        "estimated_transfer_tax_gbp": None,
        "tax_estimate_available": True,
        "transfer_price_gbp": None,
        "price_basis_note": None,
        "notes": [],
    }
    target_lot_id = (lot_id or "").strip()
    if not target_lot_id:
        return preview

    selected_candidate = next(
        (candidate for candidate in candidates if candidate.get("lot_id") == target_lot_id),
        None,
    )
    if selected_candidate is None:
        preview["notes"] = ["Selected transfer source is no longer eligible."]
        return preview

    preview["has_selection"] = True
    preview["source_scheme"] = selected_candidate.get("scheme_type")

    default_qty_text = str(
        selected_candidate.get("default_transfer_quantity")
        or selected_candidate.get("whole_quantity_available")
        or selected_candidate.get("quantity_remaining")
        or ""
    ).strip()
    raw_qty = (quantity_raw or "").strip() or default_qty_text
    transfer_qty: Decimal | None = None
    if raw_qty:
        try:
            transfer_qty = Decimal(raw_qty)
        except InvalidOperation:
            preview["quantity_error"] = "Invalid transfer quantity."
    else:
        preview["quantity_error"] = "Transfer quantity is required."

    if transfer_qty is not None:
        preview["transfer_quantity"] = _quantity_text(transfer_qty)
        if transfer_qty <= Decimal("0"):
            preview["quantity_error"] = "Transfer quantity must be greater than zero."

    with AppContext.read_session() as sess:
        lot_repo = LotRepository(sess)
        price_repo = PriceRepository(sess)
        lot = lot_repo.get_by_id(target_lot_id)
        if lot is None:
            preview["notes"] = ["Selected lot was not found."]
            return preview

        scheme = str(lot.scheme_type or "").upper()
        preview["source_scheme"] = scheme
        lot_qty_remaining = Decimal(lot.quantity_remaining)
        whole_qty_available = Decimal(str(selected_candidate.get("whole_quantity_available") or "0"))

        if transfer_qty is not None and preview["quantity_error"] is None:
            if scheme == "ESPP":
                if not _is_whole_quantity(transfer_qty):
                    preview["quantity_error"] = (
                        "ESPP transfers require a whole number of shares."
                    )
                elif transfer_qty > whole_qty_available:
                    preview["quantity_error"] = (
                        "Requested quantity exceeds whole-share FIFO availability."
                    )
            elif transfer_qty != lot_qty_remaining:
                preview["quantity_error"] = (
                    f"{scheme} transfers must use full remaining quantity "
                    f"({_quantity_text(lot_qty_remaining)})."
                )

        if scheme != "ESPP_PLUS":
            preview["notes"] = [
                "No matched-share forfeiture applies to this source scheme."
            ]
            return preview

        if lot.matching_lot_id is not None:
            preview["notes"] = [
                "Matched ESPP+ lots cannot be selected directly; choose the linked paid lot."
            ]
            return preview

        today = date_type.today()
        latest_price_row = price_repo.get_latest(lot.security_id)
        transfer_price = (
            Decimal(latest_price_row.close_price_gbp)
            if latest_price_row is not None and latest_price_row.close_price_gbp is not None
            else Decimal(lot.acquisition_price_gbp)
        )
        preview["transfer_price_gbp"] = _q2(transfer_price)
        preview["price_basis_note"] = (
            "Latest tracked price basis."
            if latest_price_row is not None and latest_price_row.close_price_gbp is not None
            else "No live price available; acquisition price basis used."
        )

        linked_matched_lots = [
            linked
            for linked in lot_repo.get_active_lots_for_security(
                lot.security_id,
                scheme_type="ESPP_PLUS",
            )
            if linked.matching_lot_id == lot.id
            and Decimal(linked.quantity_remaining) > Decimal("0")
        ]

        forfeited_qty = Decimal("0")
        forfeited_count = 0
        for linked_lot in linked_matched_lots:
            forfeiture_end = linked_lot.forfeiture_period_end or (
                linked_lot.acquisition_date + timedelta(days=183)
            )
            if today >= forfeiture_end:
                continue
            forfeited_count += 1
            forfeited_qty += Decimal(linked_lot.quantity_remaining)

        forfeited_value = _q2(forfeited_qty * transfer_price)
        preview["forfeited_match_lot_count"] = forfeited_count
        preview["forfeited_match_quantity"] = _quantity_text(forfeited_qty)
        preview["forfeited_match_value_gbp"] = forfeited_value

        est_transfer_tax = _estimate_sell_all_employment_tax(
            [lot],
            transfer_price,
            today,
            settings,
        )
        preview["estimated_transfer_tax_gbp"] = (
            _q2(est_transfer_tax) if est_transfer_tax is not None else None
        )
        preview["tax_estimate_available"] = est_transfer_tax is not None
        preview["notes"] = []
        if forfeited_count <= 0:
            preview["notes"].append(
                "No matched shares are currently within the forfeiture window."
            )
        if est_transfer_tax is None:
            preview["notes"].append(
                "Employment-tax estimate is unavailable; configure income settings for transfer-time estimate."
            )

    return preview


def _build_behavioral_guardrails(
    *,
    summary,
    settings: AppSettings | None,
    position_rows_by_security: dict[str, list[PositionGroupRow]],
    deployable_capital_gbp: Decimal | None,
    sellable_employment_tax_gbp: Decimal | None,
    forfeitable_capital_gbp: Decimal | None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []

    security_gross_by_ticker: dict[str, Decimal] = {}
    total_gross_market = Decimal("0")
    for security_summary in summary.securities:
        if security_summary.market_value_gbp is None:
            continue
        ticker = (security_summary.security.ticker or "").strip().upper()
        value = security_summary.market_value_gbp
        total_gross_market += value
        security_gross_by_ticker[ticker] = (
            security_gross_by_ticker.get(ticker, Decimal("0")) + value
        )

    if total_gross_market > Decimal("0") and security_gross_by_ticker:
        top_ticker, top_value = max(
            security_gross_by_ticker.items(),
            key=lambda item: item[1],
        )
        top_pct = (
            top_value / total_gross_market * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        top_threshold = _GUARDRAIL_CONCENTRATION_TOP_PCT_DEFAULT
        if settings is not None:
            try:
                top_threshold = Decimal(
                    getattr(
                        settings,
                        "concentration_top_holding_alert_pct",
                        _GUARDRAIL_CONCENTRATION_TOP_PCT_DEFAULT,
                    )
                )
            except (TypeError, ValueError, InvalidOperation):
                top_threshold = _GUARDRAIL_CONCENTRATION_TOP_PCT_DEFAULT
            top_threshold = max(Decimal("0"), min(Decimal("100"), top_threshold))
        if top_pct > top_threshold:
            warnings.append(
                {
                    "id": "concentration_top_holding",
                    "severity": "warning",
                    "title": "Top-holding concentration breach",
                    "condition_key": f"concentration_top_holding|ticker:{top_ticker}|threshold:{top_threshold}",
                    "message": (
                        f"{top_ticker} is {top_pct}% of gross market value "
                        f"(threshold {top_threshold}%)."
                    ),
                }
            )

    employer_ticker = (
        (settings.employer_ticker if settings is not None else "") or ""
    ).strip().upper()
    if employer_ticker and total_gross_market > Decimal("0"):
        employer_value = security_gross_by_ticker.get(employer_ticker, Decimal("0"))
        employer_pct = (
            employer_value / total_gross_market * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        employer_threshold = _GUARDRAIL_CONCENTRATION_EMPLOYER_PCT_DEFAULT
        if settings is not None:
            try:
                employer_threshold = Decimal(
                    getattr(
                        settings,
                        "concentration_employer_alert_pct",
                        _GUARDRAIL_CONCENTRATION_EMPLOYER_PCT_DEFAULT,
                    )
                )
            except (TypeError, ValueError, InvalidOperation):
                employer_threshold = _GUARDRAIL_CONCENTRATION_EMPLOYER_PCT_DEFAULT
            employer_threshold = max(Decimal("0"), min(Decimal("100"), employer_threshold))
        if employer_pct > employer_threshold:
            warnings.append(
                {
                    "id": "concentration_employer",
                    "severity": "warning",
                    "title": "Employer concentration breach",
                    "condition_key": f"concentration_employer|ticker:{employer_ticker}|threshold:{employer_threshold}",
                    "message": (
                        f"{employer_ticker} exposure is {employer_pct}% of gross market value "
                        f"(threshold {employer_threshold}%)."
                    ),
                }
            )

    gross_market_value = summary.total_market_value_gbp
    deployable = deployable_capital_gbp or Decimal("0")
    if (
        gross_market_value is not None
        and gross_market_value > Decimal("0")
        and deployable > Decimal("0")
    ):
        gross_to_deployable = gross_market_value / deployable
        if gross_to_deployable >= _GUARDRAIL_LIQUIDITY_ILLUSION_RATIO:
            non_deployable_pct = (
                (gross_market_value - deployable) / gross_market_value * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            warnings.append(
                {
                    "id": "liquidity_illusion",
                    "severity": "warning",
                    "title": "Liquidity illusion risk",
                    "condition_key": "liquidity_illusion",
                    "message": (
                        f"{non_deployable_pct}% of gross capital is currently non-deployable "
                        "(locked, forfeitable, or tax-constrained)."
                    ),
                }
            )

    isa_market_value = Decimal("0")
    taxable_market_value = Decimal("0")
    for security_summary in summary.securities:
        for lot_summary in security_summary.active_lots:
            if lot_summary.market_value_gbp is None:
                continue
            if lot_summary.lot.scheme_type == "ISA":
                isa_market_value += lot_summary.market_value_gbp
            else:
                taxable_market_value += lot_summary.market_value_gbp

    total_visible_market = isa_market_value + taxable_market_value
    if (
        total_visible_market > Decimal("0")
        and taxable_market_value >= _GUARDRAIL_ISA_UNDERUTILISATION_MIN_TAXABLE_GBP
    ):
        isa_ratio_pct = (
            isa_market_value / total_visible_market * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if isa_ratio_pct < _GUARDRAIL_ISA_UNDERUTILISATION_MAX_RATIO_PCT:
            warnings.append(
                {
                    "id": "isa_underutilisation",
                    "severity": "info",
                    "title": "ISA shelter under-utilisation",
                    "condition_key": "isa_underutilisation",
                    "message": (
                        f"ISA market-value share is {isa_ratio_pct}% while taxable holdings remain "
                        f"£{_q2(taxable_market_value)}."
                    ),
                }
            )

    if (
        sellable_employment_tax_gbp is not None
        and settings is not None
        and settings.default_gross_income > Decimal("0")
    ):
        drag_pct = (
            sellable_employment_tax_gbp / settings.default_gross_income * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if drag_pct >= _GUARDRAIL_DRAG_ESCALATION_PCT_OF_INCOME:
            warnings.append(
                {
                    "id": "drag_escalation",
                    "severity": "warning",
                    "title": "Employment-tax drag escalation",
                    "condition_key": "drag_escalation",
                    "message": (
                        f"Sellable employment-tax estimate is {drag_pct}% of configured gross income."
                    ),
                }
            )

    imminent_count = 0
    min_days_remaining: int | None = None
    for rows in position_rows_by_security.values():
        for row in rows:
            days = row.forfeiture_risk_days_remaining
            if days is None or days > _GUARDRAIL_FORFEITURE_IMMINENCE_DAYS:
                continue
            imminent_count += 1
            if min_days_remaining is None or days < min_days_remaining:
                min_days_remaining = days
    if imminent_count > 0:
        at_risk_value = forfeitable_capital_gbp or Decimal("0")
        warnings.append(
            {
                "id": "forfeiture_imminence",
                "severity": "warning",
                "title": "Forfeiture timing exposure",
                "condition_key": f"forfeiture_imminence|count:{imminent_count}",
                "message": (
                    f"{imminent_count} position(s) have forfeiture windows inside "
                    f"{_GUARDRAIL_FORFEITURE_IMMINENCE_DAYS} days "
                    f"(nearest in {min_days_remaining} days; value at risk £{_q2(at_risk_value)})."
                ),
            }
        )

    return warnings


def _apply_guardrail_visibility_persistence(
    guardrails: list[dict[str, str]],
    *,
    now_utc: datetime,
) -> tuple[list[dict[str, str]], int]:
    lifecycle = AlertLifecycleService.apply_visibility(
        guardrails,
        now_utc=now_utc,
    )
    return lifecycle["active"], lifecycle["suppressed_total"]


def _price_row_gbp_value(price_row) -> Decimal | None:
    """Return a GBP Decimal value from a PriceHistory row."""
    raw = price_row.close_price_gbp or price_row.close_price_original_ccy
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _price_row_native_value(price_row) -> Decimal | None:
    """Return the original-currency Decimal price from a PriceHistory row.

    In some older records the ``close_price_original_ccy`` column is
    NULL.  Previous implementations fell back to the GBP price when this
    happened, which made it look as though the security was trading in GBP
    and caused the native‑currency delta to evaluate to zero (hence
    ``$0.00`` in the tooltip even when there was a non‑zero GBP movement).

    We now return ``None`` if the native price is missing so that callers
    can suppress any native‑currency output rather than showing misleading
    values.  Future migration work should attempt to backfill these rows
    from historical FX rates once such data is available.
    """
    raw = price_row.close_price_original_ccy
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _snapshot_native_value(snapshot_row) -> Decimal | None:
    raw = getattr(snapshot_row, "price_native", None)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _utc_now() -> datetime:
    """Current UTC timestamp (helper for deterministic tests)."""
    return datetime.now(timezone.utc)


def _build_sparkline_path(values: list[Decimal]) -> tuple[str | None, str]:
    if len(values) < 2:
        return None, "flat"
    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum
    width = 111.0
    height = 10.0
    step_x = width / max(1, len(values) - 1)
    points: list[str] = []
    for index, value in enumerate(values):
        x = index * step_x
        if span == 0:
            y = height / 2
        else:
            normalized = (value - minimum) / span
            y = height - (float(normalized) * height)
        points.append(f"{x:.2f},{y:.2f}")
    direction = "flat"
    if values[-1] > values[0]:
        direction = "up"
    elif values[-1] < values[0]:
        direction = "down"
    return " ".join(points), direction


def _to_utc_aware(ts: datetime) -> datetime:
    """Normalize naive/aware datetime into UTC-aware datetime."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _format_compact_duration(delta: timedelta) -> str:
    """Human-readable compact duration string."""
    total_seconds = max(0, int(delta.total_seconds()))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if total_seconds < 120:
        return f"{total_seconds}s"
    return f"{minutes}m"


def _next_weekday_open(local_now: datetime, open_time: time) -> datetime:
    """
    Return the next market-open local datetime from local_now.
    """
    tz = local_now.tzinfo
    candidate = local_now.date()
    if local_now.weekday() < 5 and local_now.time() < open_time:
        return datetime.combine(candidate, open_time, tzinfo=tz)

    candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return datetime.combine(candidate, open_time, tzinfo=tz)


def _market_window_status(exchange: str | None, now_utc: datetime) -> MarketWindowStatus:
    """
    Return market-open state for known exchanges plus closed-countdown text.
    """
    ex = (exchange or "").strip().upper()
    if ex in _US_MARKET_EXCHANGES:
        tz = ZoneInfo("America/New_York")
        open_time = time(9, 30)
        close_time = time(16, 0)
        label = "US market"
    elif ex in _UK_MARKET_EXCHANGES:
        tz = ZoneInfo("Europe/London")
        open_time = time(8, 0)
        close_time = time(16, 30)
        label = "LSE market"
    else:
        return MarketWindowStatus(is_open=None, status_text=None)

    local_now = now_utc.astimezone(tz)
    is_weekday = local_now.weekday() < 5
    is_open = is_weekday and open_time <= local_now.time() < close_time
    if is_open:
        return MarketWindowStatus(is_open=True, status_text=f"{label} open")

    next_open = _next_weekday_open(local_now, open_time)
    opens_in = _format_compact_duration(next_open - local_now)
    return MarketWindowStatus(
        is_open=False,
        status_text=f"{label} closed (opening in {opens_in})",
    )


def _market_status_label(exchange: str | None, now_utc: datetime) -> tuple[str | None, str | None]:
    """
    Extract market status label and opens-in duration.

    Returns (status_label, opens_in_duration)
    - status_label: "Open" or "Closed", or None if exchange unknown
    - opens_in_duration: e.g., "2d 3h", only if status is "Closed"
    """
    market = _market_window_status(exchange, now_utc)
    if market.is_open is None:
        return None, None

    if market.is_open:
        return "Open", None

    # Market is closed; calculate opens_in
    ex = (exchange or "").strip().upper()
    if ex in _US_MARKET_EXCHANGES:
        tz = ZoneInfo("America/New_York")
        open_time = time(9, 30)
    elif ex in _UK_MARKET_EXCHANGES:
        tz = ZoneInfo("Europe/London")
        open_time = time(8, 0)
    else:
        return "Closed", None

    local_now = now_utc.astimezone(tz)
    next_open = _next_weekday_open(local_now, open_time)
    opens_in = _format_compact_duration(next_open - local_now)

    return "Closed", opens_in


def _daily_freshness_note(
    *,
    exchange: str | None,
    price_last_changed_at: datetime | None,
    now_utc: datetime,
) -> tuple[str | None, str, str | None]:
    """
    Build market-aware freshness text for the daily ticker.
    """
    market = _market_window_status(exchange, now_utc)
    if price_last_changed_at is None:
        if market.status_text:
            return market.status_text, "muted", "No snapshot history yet."
        return None, "muted", None

    changed_utc = _to_utc_aware(price_last_changed_at)
    if changed_utc > now_utc:
        changed_utc = now_utc
    age = now_utc - changed_utc
    age_txt = _format_compact_duration(age)
    title = f"Price last changed at {changed_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"

    # always show a simple updated timestamp, regardless of market state
    if age >= timedelta(minutes=_DAILY_STALE_WHILE_OPEN_MINUTES):
        level = "warning"
    else:
        level = "ok"
    return f"Updated {age_txt} ago", level, title


def _security_daily_change_unavailable(
    security_id: str,
    reason: str,
) -> SecurityDailyChange:
    return SecurityDailyChange(
        security_id=security_id,
        direction="unavailable",
        arrow="-",
        percent_change=None,
        value_change_gbp=None,
        current_as_of=None,
        previous_as_of=None,
        official_close_as_of=None,
        unavailable_reason=reason,
    )


def _build_security_daily_changes(
    summary,
    *,
    as_of: date_type | None = None,
) -> dict[str, SecurityDailyChange]:
    """
    Build latest-vs-previous-close daily move cards for each security.

    Value change is quantity-aware in GBP:
      (latest_price_gbp - previous_price_gbp) x current_quantity
    """
    changes: dict[str, SecurityDailyChange] = {}
    now_utc = _utc_now()
    selected_as_of = as_of or now_utc.date()
    historical_mode = selected_as_of != now_utc.date()
    for ss in summary.securities:
        with AppContext.read_session() as sess:
            price_repo = PriceRepository(sess)
            security_id = ss.security.id
            last_changed_at = price_repo.get_current_price_run_started_at(security_id)
            if historical_mode:
                freshness_text = f"Historical as of {selected_as_of.isoformat()}"
                freshness_level = "muted"
                freshness_title = (
                    "Using the latest stored price on or before the selected as-of date."
                )
            else:
                freshness_text, freshness_level, freshness_title = _daily_freshness_note(
                    exchange=ss.security.exchange,
                    price_last_changed_at=last_changed_at,
                    now_utc=now_utc,
                )

            market_status, market_opens_in = _market_status_label(
                exchange=ss.security.exchange,
                now_utc=now_utc,
            )

            latest_row = price_repo.get_latest_on_or_before(security_id, selected_as_of)
            if latest_row is None:
                daily = _security_daily_change_unavailable(
                    security_id,
                    "No stored price yet.",
                )
                daily.price_last_changed_at = last_changed_at
                daily.freshness_text = freshness_text
                daily.freshness_level = freshness_level
                daily.freshness_title = freshness_title
                daily.market_status = market_status
                daily.market_opens_in = market_opens_in
                changes[security_id] = daily
                continue

            latest_price_gbp = _price_row_gbp_value(latest_row)
            if latest_price_gbp is None:
                daily = _security_daily_change_unavailable(
                    security_id,
                    "Current price unavailable.",
                )
                daily.price_last_changed_at = last_changed_at
                daily.freshness_text = freshness_text
                daily.freshness_level = freshness_level
                daily.freshness_title = freshness_title
                daily.market_status = market_status
                daily.market_opens_in = market_opens_in
                changes[security_id] = daily
                continue

            latest_snapshot = (
                price_repo.get_latest_ticker_snapshot(security_id)
                if not historical_mode
                else None
            )
            recent_snapshots = (
                price_repo.list_recent_ticker_snapshots(
                    security_id,
                    limit=None,
                    price_date=selected_as_of,
                )
                if not historical_mode
                else []
            )
            recent_history_rows = price_repo.get_history_range(
                security_id,
                from_date=selected_as_of - timedelta(days=40),
                to_date=selected_as_of,
            )
            live_price_gbp = latest_price_gbp
            current_as_of = latest_row.price_date
            latest_native = _price_row_native_value(latest_row)
            component_basis_note: str | None = None
            uses_live_snapshot = False
            if (
                latest_snapshot is not None
                and latest_snapshot.price_date == latest_row.price_date
            ):
                try:
                    snapshot_price = Decimal(latest_snapshot.price_gbp)
                except (InvalidOperation, TypeError, ValueError):
                    snapshot_price = None
                if snapshot_price is not None and snapshot_price > Decimal("0"):
                    live_price_gbp = snapshot_price
                    current_as_of = latest_snapshot.price_date
                    uses_live_snapshot = True
                    snapshot_native = _snapshot_native_value(latest_snapshot)
                    if snapshot_native is not None and snapshot_native > Decimal("0"):
                        latest_native = snapshot_native
                    if live_price_gbp != latest_price_gbp:
                        component_basis_note = (
                            "Live GBP value is intraday. Official close values are shown separately."
                        )

        if historical_mode:
            with AppContext.read_session() as sess:
                previous_row = PriceRepository(sess).get_latest_before(security_id, latest_row.price_date)
        else:
            previous_row = PriceService.ensure_previous_official_close(
                security_id=security_id,
                ticker=ss.security.ticker,
                currency=ss.security.currency or "GBP",
                exchange=ss.security.exchange,
                reference_date=current_as_of,
            )

        if previous_row is None:
            daily = _security_daily_change_unavailable(
                security_id,
                "Need previous official close.",
            )
            daily.price_last_changed_at = last_changed_at
            daily.freshness_text = freshness_text
            daily.freshness_level = freshness_level
            daily.freshness_title = freshness_title
            daily.market_status = market_status
            daily.market_opens_in = market_opens_in
            changes[security_id] = daily
            continue

        previous_price_gbp = _price_row_gbp_value(previous_row)
        official_close_as_of = previous_row.price_date
        previous_as_of: date_type | None = previous_row.price_date
        previous_native: Decimal | None = _price_row_native_value(previous_row)
        component_current_price_gbp = live_price_gbp
        component_previous_price_gbp: Decimal | None = previous_price_gbp

        if previous_price_gbp is None or previous_price_gbp <= Decimal("0"):
                daily = _security_daily_change_unavailable(
                    security_id,
                    "Previous official close unavailable.",
                )
                daily.price_last_changed_at = last_changed_at
                daily.freshness_text = freshness_text
                daily.freshness_level = freshness_level
                daily.freshness_title = freshness_title
                daily.market_status = market_status
                daily.market_opens_in = market_opens_in
                changes[security_id] = daily
                continue

        delta_price = live_price_gbp - previous_price_gbp
        pct_change = (
            (delta_price / previous_price_gbp) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        value_change = _q2(delta_price * ss.total_quantity)

        native_currency = ss.market_value_native_currency or None
        if (
            latest_native is not None
            and previous_native is not None
            and previous_native > Decimal("0")
        ):
            native_current_value = latest_native * ss.total_quantity
            native_previous_value = previous_native * ss.total_quantity
            value_change_native = _q2(native_current_value - native_previous_value)
        else:
            value_change_native = None

        stock_percent_change: Decimal | None = None
        fx_percent_change: Decimal | None = None
        current_fx_rate: Decimal | None = None
        previous_fx_rate: Decimal | None = None
        stock_value_change_gbp: Decimal | None = None
        fx_value_change_gbp: Decimal | None = None
        component_value_change_gbp: Decimal | None = None
        component_percent_change: Decimal | None = None
        can_split_components = (
            latest_native is not None
            and previous_native is not None
            and latest_native > Decimal("0")
            and previous_native > Decimal("0")
            and component_current_price_gbp is not None
            and component_previous_price_gbp is not None
            and component_previous_price_gbp > Decimal("0")
        )
        if can_split_components:
            stock_percent_change = (
                ((latest_native - previous_native) / previous_native) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            previous_fx_rate = (
                component_previous_price_gbp / previous_native
            ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            current_fx_rate = (
                component_current_price_gbp / latest_native
            ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            if previous_fx_rate > Decimal("0"):
                fx_percent_change = (
                    ((current_fx_rate - previous_fx_rate) / previous_fx_rate) * Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                stock_only_current_gbp = (
                    latest_native * previous_fx_rate
                ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                stock_delta_per_share_gbp = stock_only_current_gbp - component_previous_price_gbp
                stock_value_change_gbp = _q2(stock_delta_per_share_gbp * ss.total_quantity)
                fx_value_change_gbp = _q2(value_change - stock_value_change_gbp)
                component_value_change_gbp = _q2(
                    stock_value_change_gbp + fx_value_change_gbp
                )
                previous_component_value_gbp = (
                    component_previous_price_gbp * ss.total_quantity
                )
                if previous_component_value_gbp > Decimal("0"):
                    component_percent_change = (
                        (component_value_change_gbp / previous_component_value_gbp)
                        * Decimal("100")
                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if delta_price > Decimal("0"):
            direction = "up"
            arrow = "▲"
        elif delta_price < Decimal("0"):
            direction = "down"
            arrow = "▼"
        else:
            direction = "flat"
            arrow = "→"

        sparkline_values: list[Decimal] = []
        if market_status and market_status.lower() == "open":
            for snapshot in reversed(recent_snapshots):
                snapshot_native = _snapshot_native_value(snapshot)
                if snapshot_native is None or snapshot_native <= Decimal("0"):
                    continue
                sparkline_values.append(snapshot_native)
        else:
            native_by_date: dict[date_type, Decimal] = {}
            for row in recent_history_rows:
                native_value = _price_row_native_value(row)
                if native_value is None or native_value <= Decimal("0"):
                    continue
                native_by_date[row.price_date] = native_value
            for day in sorted(native_by_date.keys())[-16:]:
                sparkline_values.append(native_by_date[day])
        sparkline_path, _sparkline_window_direction = _build_sparkline_path(sparkline_values)

        changes[security_id] = SecurityDailyChange(
            security_id=security_id,
            direction=direction,
            arrow=arrow,
            percent_change=pct_change,
            value_change_gbp=value_change,
            current_as_of=current_as_of,
            previous_as_of=previous_as_of,
            official_close_as_of=official_close_as_of,
            price_last_changed_at=last_changed_at,
            freshness_text=freshness_text,
            freshness_level=freshness_level,
            freshness_title=freshness_title,
            native_currency=native_currency,
            value_change_native=value_change_native,
            market_status=market_status,
            market_opens_in=market_opens_in,
            current_price_gbp=live_price_gbp,
            previous_price_gbp=previous_price_gbp,
            current_price_native=latest_native,
            previous_price_native=previous_native,
            stock_percent_change=stock_percent_change,
            fx_percent_change=fx_percent_change,
            current_fx_rate=current_fx_rate,
            previous_fx_rate=previous_fx_rate,
            stock_value_change_gbp=stock_value_change_gbp,
            fx_value_change_gbp=fx_value_change_gbp,
            component_value_change_gbp=component_value_change_gbp,
            component_percent_change=component_percent_change,
            component_basis_note=component_basis_note,
            sparkline_path=sparkline_path,
            sparkline_direction=direction,
        )
    return changes


def _portfolio_valuation_basis(summary) -> dict[str, object]:
    """Build compact valuation-basis metadata for portfolio headline cards."""
    securities = list(summary.securities or [])
    total_security_count = len(securities)

    price_dates = [
        ss.price_as_of for ss in securities if ss.price_as_of is not None
    ]
    price_as_of_latest = max(price_dates).isoformat() if price_dates else None
    price_as_of_earliest = min(price_dates).isoformat() if price_dates else None
    stale_price_count = sum(
        1 for ss in securities if ss.price_as_of is not None and ss.price_is_stale
    )
    missing_price_count = total_security_count - len(price_dates)

    fx_required = [
        ss
        for ss in securities
        if str(getattr(ss.security, "currency", "") or "").upper() != "GBP"
    ]
    fx_required_count = len(fx_required)
    fx_as_of_count = sum(1 for ss in fx_required if ss.fx_as_of)
    stale_fx_count = sum(1 for ss in fx_required if ss.fx_as_of and ss.fx_is_stale)
    missing_fx_count = fx_required_count - fx_as_of_count

    return {
        "total_security_count": total_security_count,
        "price_tracked_count": len(price_dates),
        "price_as_of_latest": price_as_of_latest,
        "price_as_of_earliest": price_as_of_earliest,
        "price_dates_mixed": len(set(price_dates)) > 1,
        "stale_price_count": stale_price_count,
        "missing_price_count": missing_price_count,
        "fx_required_count": fx_required_count,
        "fx_as_of_count": fx_as_of_count,
        "fx_as_of": summary.fx_as_of,
        "fx_is_stale": summary.fx_is_stale,
        "stale_fx_count": stale_fx_count,
        "missing_fx_count": missing_fx_count,
    }


def _has_tax_window(ls: LotSummary) -> bool:
    if ls.sip_qualifying_status is None:
        return False
    return ls.sip_qualifying_status.category.value != "FIVE_PLUS_YEARS"


def _decision_signal(
    sellability_status: str,
    economic_result: Decimal | None,
) -> tuple[str, str]:
    """
    Return (emoji, tooltip) decision indicator.

    Mapping:
      - sellable + non-negative economic result -> green
      - at-risk -> yellow
      - negative economic result -> red
      - fallback (locked / unavailable) -> yellow
    """
    if sellability_status == "AT_RISK":
        return "ðŸŸ¡", "At risk"
    if economic_result is not None and economic_result < Decimal("0"):
        return "ðŸ”´", "Economic loss if sold"
    if sellability_status == "SELLABLE" and economic_result is not None:
        return "ðŸŸ¢", "Profitable and sellable"
    return "ðŸŸ¡", "Review required"


def _is_matched_espp_plus_lot(lot: Lot) -> bool:
    return lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None


def _forfeiture_end_date(lot: Lot) -> date_type:
    return lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))


def _lock_reason_for_lot_on(lot: Lot, as_of: date_type) -> str | None:
    if lot.scheme_type == "RSU" and as_of < lot.acquisition_date:
        return f"Locked until {lot.acquisition_date.isoformat()}"
    if _is_matched_espp_plus_lot(lot):
        end = _forfeiture_end_date(lot)
        if as_of < end:
            return f"Locked until {end.isoformat()}"
    return None


def _row_constant_price_per_share(row: PositionGroupRow) -> Decimal | None:
    for ls in row.detail_lots:
        if ls.market_value_gbp is None or ls.quantity_remaining <= Decimal("0"):
            continue
        return (ls.market_value_gbp / ls.quantity_remaining).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
    return None


def _estimate_lot_net_cash_on(
    ls: LotSummary,
    *,
    as_of: date_type,
    price_per_share_gbp: Decimal,
    settings: AppSettings | None,
) -> tuple[Decimal | None, str | None]:
    lock_reason = _lock_reason_for_lot_on(ls.lot, as_of)
    if lock_reason is not None:
        return None, lock_reason

    lot_mv = _q2(ls.quantity_remaining * price_per_share_gbp)
    lot_tax = _estimate_sell_all_employment_tax(
        [ls.lot],
        price_per_share_gbp,
        as_of,
        settings,
    )
    if lot_tax is None:
        return None, "Employment-tax estimate unavailable."
    return _q2(lot_mv - lot_tax), None


def _evaluate_single_row_outcome_on(
    row: PositionGroupRow,
    *,
    as_of: date_type,
    price_per_share_gbp: Decimal,
    settings: AppSettings | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    if not row.detail_lots:
        return None, None, "Unavailable"
    lot_net, reason = _estimate_lot_net_cash_on(
        row.detail_lots[0],
        as_of=as_of,
        price_per_share_gbp=price_per_share_gbp,
        settings=settings,
    )
    if lot_net is None:
        return None, None, reason
    gain = _q2(lot_net - row.paid_true_cost)
    return lot_net, gain, None


def _evaluate_espp_plus_group_outcome_on(
    row: PositionGroupRow,
    *,
    as_of: date_type,
    price_per_share_gbp: Decimal,
    settings: AppSettings | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    paid_lots = [ls for ls in row.detail_lots if ls.lot.matching_lot_id is None]
    match_lots = [ls for ls in row.detail_lots if ls.lot.matching_lot_id is not None]

    paid_cash = Decimal("0.00")
    for ls in paid_lots:
        lot_net, reason = _estimate_lot_net_cash_on(
            ls,
            as_of=as_of,
            price_per_share_gbp=price_per_share_gbp,
            settings=settings,
        )
        if lot_net is None:
            return None, None, reason
        paid_cash = _q2(paid_cash + lot_net)

    match_qty = sum((ls.quantity_remaining for ls in match_lots), Decimal("0"))
    match_mv = Decimal("0.00")
    match_cash = Decimal("0.00")
    in_forfeiture_window = False
    any_match_locked = False

    for ls in match_lots:
        match_mv = _q2(match_mv + _q2(ls.quantity_remaining * price_per_share_gbp))

        lock_reason = _lock_reason_for_lot_on(ls.lot, as_of)
        if lock_reason is not None:
            any_match_locked = True
            if _is_matched_espp_plus_lot(ls.lot) and as_of < _forfeiture_end_date(ls.lot):
                in_forfeiture_window = True
            continue

        lot_net, reason = _estimate_lot_net_cash_on(
            ls,
            as_of=as_of,
            price_per_share_gbp=price_per_share_gbp,
            settings=settings,
        )
        if lot_net is None:
            return None, None, reason
        match_cash = _q2(match_cash + lot_net)

    if match_qty <= Decimal("0"):
        match_effect = "NONE"
    elif in_forfeiture_window and row.paid_qty > Decimal("0"):
        match_effect = "FORFEITED"
    elif any_match_locked:
        match_effect = "LOCKED"
    else:
        match_effect = "INCLUDED"

    if match_effect == "INCLUDED":
        net = _q2(paid_cash + match_cash)
    elif match_effect == "LOCKED" and row.paid_qty <= Decimal("0"):
        return None, None, "Locked"
    else:
        net = paid_cash

    # Invariant: Gain = Net – True Economic Cost.
    # Forfeiture is handled via quantity (match shares excluded from net),
    # never as an additional deduction.
    gain = _q2(net - row.paid_true_cost)
    return net, gain, None


def _evaluate_row_outcome_on(
    row: PositionGroupRow,
    *,
    as_of: date_type,
    settings: AppSettings | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    price_per_share_gbp = _row_constant_price_per_share(row)
    if price_per_share_gbp is None:
        return None, None, "No live price available."
    if row.row_kind == "GROUPED_ESPP_PLUS":
        return _evaluate_espp_plus_group_outcome_on(
            row,
            as_of=as_of,
            price_per_share_gbp=price_per_share_gbp,
            settings=settings,
        )
    return _evaluate_single_row_outcome_on(
        row,
        as_of=as_of,
        price_per_share_gbp=price_per_share_gbp,
        settings=settings,
    )


def _row_next_milestone(
    row: PositionGroupRow,
    *,
    as_of: date_type,
) -> tuple[date_type | None, str | None]:
    candidates: list[tuple[date_type, str]] = []
    for ls in row.detail_lots:
        lot = ls.lot
        if lot.scheme_type == "RSU" and as_of < lot.acquisition_date:
            candidates.append((lot.acquisition_date, "unlock"))
        if _is_matched_espp_plus_lot(lot):
            end = _forfeiture_end_date(lot)
            if as_of < end:
                candidates.append((end, "forfeiture"))

        sip_status = ls.sip_qualifying_status
        if sip_status is None:
            continue
        if as_of < sip_status.three_year_date:
            candidates.append((sip_status.three_year_date, "tax_window"))
        elif as_of < sip_status.five_year_date:
            candidates.append((sip_status.five_year_date, "tax_window"))

    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def _row_long_term_date(row: PositionGroupRow, *, as_of: date_type) -> date_type:
    maturity_points: list[date_type] = [as_of]
    for ls in row.detail_lots:
        lot = ls.lot
        if lot.scheme_type == "RSU":
            maturity_points.append(max(as_of, lot.acquisition_date))
        if _is_matched_espp_plus_lot(lot):
            maturity_points.append(max(as_of, _forfeiture_end_date(lot)))
        sip_status = ls.sip_qualifying_status
        if sip_status is not None:
            maturity_points.append(max(as_of, sip_status.five_year_date))
    return max(maturity_points)


def _format_month_window(days: int) -> int:
    return max(1, int((days + 15) / 30))


def _row_structural_note(
    row: PositionGroupRow,
    *,
    as_of: date_type,
    next_milestone: date_type | None,
    next_kind: str | None,
) -> str:
    if row.forfeiture_risk_days_remaining is not None:
        return f"Match preserved in {row.forfeiture_risk_days_remaining}d"
    if row.sellability_status == "LOCKED" and row.sellability_unlock_date is not None:
        return f"Locked until {row.sellability_unlock_date.isoformat()}"
    if next_kind == "tax_window" and next_milestone is not None and next_milestone > as_of:
        months = _format_month_window((next_milestone - as_of).days)
        return f"Next tax window in {months}m"
    return "Fully matured"


def _attach_row_decision_scenarios(
    row: PositionGroupRow,
    *,
    settings: AppSettings | None,
    as_of: date_type,
) -> PositionGroupRow:
    next_milestone, next_kind = _row_next_milestone(row, as_of=as_of)
    long_term_date_calc = _row_long_term_date(row, as_of=as_of)

    # Store dates for template use (time-until calculations)
    row.next_milestone_date = next_milestone if next_milestone and next_milestone > as_of else None
    row.long_term_date = long_term_date_calc if long_term_date_calc > as_of else None

    if next_milestone is None or next_milestone <= as_of:
        row.next_milestone_net = row.net_cash_if_sold
        row.next_milestone_gain = row.sell_now_economic_result
        row.next_milestone_reason = row.reason_unavailable
    else:
        next_net, next_gain, next_reason = _evaluate_row_outcome_on(
            row,
            as_of=next_milestone,
            settings=settings,
        )
        row.next_milestone_net = next_net
        row.next_milestone_gain = next_gain
        row.next_milestone_reason = next_reason

    if long_term_date_calc <= as_of:
        row.long_term_net = row.net_cash_if_sold
        row.long_term_gain = row.sell_now_economic_result
        row.long_term_reason = row.reason_unavailable
    else:
        lt_net, lt_gain, lt_reason = _evaluate_row_outcome_on(
            row,
            as_of=long_term_date_calc,
            settings=settings,
        )
        row.long_term_net = lt_net
        row.long_term_gain = lt_gain
        row.long_term_reason = lt_reason

    row.notes = _row_structural_note(
        row,
        as_of=as_of,
        next_milestone=next_milestone,
        next_kind=next_kind,
    )
    return row


def _scheme_display_single(ls: LotSummary) -> str:
    st = ls.lot.scheme_type
    if st == "ESPP_PLUS":
        return "ESPP+"
    if st == "BROKERAGE":
        return "Brokerage"
    if st == "ISA":
        return "ISA"
    return st.replace("_", " ")


def _build_single_position_row(
    security_id: str,
    ls: LotSummary,
    row_index: int,
) -> PositionGroupRow:
    forfeiture_days = (
        ls.forfeiture_risk.days_remaining
        if ls.forfeiture_risk is not None and ls.forfeiture_risk.in_window
        else None
    )
    net_cash = ls.est_net_proceeds_gbp
    economic = ls.sell_now_economic_gbp
    reason = ls.est_net_proceeds_reason
    if ls.sellability_status == "LOCKED":
        net_cash = None
        economic = None
        if ls.sellability_unlock_date is not None and forfeiture_days is None:
            reason = f"Locked until {ls.sellability_unlock_date.isoformat()}."
    signal, signal_title = _decision_signal(ls.sellability_status, economic)

    # Employment tax estimate for single lots: only applies to sellable positions
    sell_now_employment_tax_est: Decimal | None = None
    if net_cash is not None and ls.market_value_gbp is not None:
        sell_now_employment_tax_est = _q2(ls.market_value_gbp - ls.est_net_proceeds_gbp)

    return PositionGroupRow(
        group_id=f"{security_id}:{ls.lot.acquisition_date.isoformat()}:{row_index}",
        acquisition_date=ls.lot.acquisition_date,
        scheme_display=_scheme_display_single(ls),
        paid_qty=ls.quantity_remaining,
        match_qty=Decimal("0"),
        total_qty=ls.quantity_remaining,
        paid_mv=ls.market_value_gbp,
        match_mv=Decimal("0.00") if ls.market_value_gbp is not None else None,
        total_mv=ls.market_value_gbp,
        paid_true_cost=ls.true_cost_total_gbp,
        paid_cost_basis=ls.cost_basis_total_gbp,
        sellability_status=ls.sellability_status,
        sellability_unlock_date=ls.sellability_unlock_date,
        forfeiture_risk_days_remaining=forfeiture_days,
        sell_now_cash_paid=ls.est_net_proceeds_gbp,
        sell_now_match_effect="NONE",
        sell_now_forfeited_match_value=Decimal("0.00"),
        sell_now_employment_tax_est=sell_now_employment_tax_est,
        sell_now_economic_result=economic,
        row_kind="SINGLE_LOT",
        has_tax_window=_has_tax_window(ls),
        pnl_tax_basis=ls.unrealised_gain_cgt_gbp,
        pnl_economic=ls.unrealised_gain_economic_gbp,
        net_cash_if_sold=net_cash,
        reason_unavailable=reason,
        decision_signal=signal,
        decision_title=signal_title,
        action_lot_id=ls.lot.id,
        detail_lots=[ls],
    )


def _build_espp_plus_group_row(
    security_id: str,
    acquisition_date: date_type,
    group_lots: list[LotSummary],
    row_index: int,
) -> PositionGroupRow:
    paid_lots = [ls for ls in group_lots if ls.lot.matching_lot_id is None]
    match_lots = [ls for ls in group_lots if ls.lot.matching_lot_id is not None]

    paid_qty = sum((ls.quantity_remaining for ls in paid_lots), Decimal("0"))
    match_qty = sum((ls.quantity_remaining for ls in match_lots), Decimal("0"))
    total_qty = paid_qty + match_qty

    paid_cost_basis = _q2(sum((ls.cost_basis_total_gbp for ls in paid_lots), Decimal("0")))
    paid_true_cost = _q2(sum((ls.true_cost_total_gbp for ls in paid_lots), Decimal("0")))

    paid_mv = _sum_optional([ls.market_value_gbp for ls in paid_lots]) if paid_lots else Decimal("0.00")
    match_mv = _sum_optional([ls.market_value_gbp for ls in match_lots]) if match_lots else Decimal("0.00")
    total_mv = (
        _q2((paid_mv or Decimal("0")) + (match_mv or Decimal("0")))
        if paid_mv is not None and match_mv is not None
        else None
    )

    statuses = [ls.sellability_status for ls in group_lots]
    sellability_status = max(statuses, key=lambda s: SELLABILITY_RANK.get(s, 0))

    unlock_dates = [ls.sellability_unlock_date for ls in group_lots if ls.sellability_unlock_date is not None]
    sellability_unlock_date = max(unlock_dates) if unlock_dates else None

    forfeiture_days = [
        ls.forfeiture_risk.days_remaining
        for ls in match_lots
        if ls.forfeiture_risk is not None and ls.forfeiture_risk.in_window
    ]
    forfeiture_risk_days_remaining = min(forfeiture_days) if forfeiture_days else None

    paid_cash = _sum_optional([ls.est_net_proceeds_gbp for ls in paid_lots]) if paid_lots else Decimal("0.00")
    match_cash = _sum_optional([ls.est_net_proceeds_gbp for ls in match_lots]) if match_lots else Decimal("0.00")

    if match_qty <= Decimal("0"):
        match_effect = "NONE"
    elif forfeiture_risk_days_remaining is not None and paid_qty > Decimal("0"):
        match_effect = "FORFEITED"
    elif any(ls.sellability_status == "LOCKED" for ls in match_lots):
        match_effect = "LOCKED"
    elif match_cash is not None:
        match_effect = "INCLUDED"
    else:
        match_effect = "NONE"

    forfeited_match_value = (
        match_mv if match_effect == "FORFEITED" and match_mv is not None else Decimal("0.00")
    )

    # Mixed ESPP+ rows with sellable employee-paid shares should remain actionable.
    # A locked or forfeitable matched portion changes the economics, but it should
    # not make the whole grouped row look fully locked to portfolio-level totals.
    if paid_qty > Decimal("0") and match_effect in {"FORFEITED", "LOCKED"}:
        sellability_status = "AT_RISK"

    if paid_cash is None:
        net_cash_if_sold = None
    elif match_effect == "INCLUDED":
        net_cash_if_sold = _q2(paid_cash + (match_cash or Decimal("0")))
    elif match_effect == "LOCKED" and sellability_status == "LOCKED":
        net_cash_if_sold = None
    else:
        net_cash_if_sold = paid_cash

    # Invariant: Gain = Net – True Economic Cost.
    # If matched shares are forfeited on early sale, that loss is already
    # reflected by excluding them from net_cash_if_sold. Do not deduct the
    # forfeited match value a second time here.
    sell_now_economic = (
        _q2(net_cash_if_sold - paid_true_cost)
        if net_cash_if_sold is not None
        else None
    )

    # Employment tax is due only on shares actually being sold.
    # If matches are forfeited, no tax on that portion.
    sell_now_employment_tax_est: Decimal | None = None
    if net_cash_if_sold is not None:
        if match_effect == "INCLUDED":
            # Tax on: (paid_mv + match_mv) - (paid_cash + match_cash)
            sold_mv = _q2((paid_mv or Decimal("0")) + (match_mv or Decimal("0")))
            sold_cash = _q2(paid_cash + (match_cash or Decimal("0")))
            sell_now_employment_tax_est = _q2(sold_mv - sold_cash)
        else:
            # Tax on: paid_mv - paid_cash only (no match, or match forfeited/locked)
            if paid_mv is not None:
                sell_now_employment_tax_est = _q2(paid_mv - paid_cash)

    pnl_tax_basis = _q2(total_mv - paid_cost_basis) if total_mv is not None else None
    pnl_economic = _q2(total_mv - paid_true_cost) if total_mv is not None else None

    reason = None
    if total_mv is None:
        reason = "No live price available."
    elif (
        net_cash_if_sold is None
        and sellability_unlock_date is not None
        and forfeiture_risk_days_remaining is None
    ):
        reason = f"Locked until {sellability_unlock_date.isoformat()}."

    signal, signal_title = _decision_signal(sellability_status, sell_now_economic)
    has_tax_window = any(_has_tax_window(ls) for ls in group_lots)
    detail_lots = sorted(
        group_lots,
        key=lambda ls: (0 if ls.lot.matching_lot_id is None else 1, ls.lot.id),
    )
    action_lot_id = (
        paid_lots[0].lot.id
        if paid_lots
        else detail_lots[0].lot.id
    )

    return PositionGroupRow(
        group_id=f"{security_id}:{acquisition_date.isoformat()}:{row_index}",
        acquisition_date=acquisition_date,
        scheme_display="ESPP+",
        paid_qty=paid_qty,
        match_qty=match_qty,
        total_qty=total_qty,
        paid_mv=paid_mv,
        match_mv=match_mv,
        total_mv=total_mv,
        paid_true_cost=paid_true_cost,
        paid_cost_basis=paid_cost_basis,
        sellability_status=sellability_status,
        sellability_unlock_date=sellability_unlock_date,
        forfeiture_risk_days_remaining=forfeiture_risk_days_remaining,
        sell_now_cash_paid=paid_cash,
        sell_now_match_effect=match_effect,
        sell_now_forfeited_match_value=_q2(forfeited_match_value),
        sell_now_employment_tax_est=sell_now_employment_tax_est,
        sell_now_economic_result=sell_now_economic,
        row_kind="GROUPED_ESPP_PLUS",
        has_tax_window=has_tax_window,
        pnl_tax_basis=pnl_tax_basis,
        pnl_economic=pnl_economic,
        net_cash_if_sold=net_cash_if_sold,
        reason_unavailable=reason,
        decision_signal=signal,
        decision_title=signal_title,
        action_lot_id=action_lot_id,
        detail_lots=detail_lots,
    )


def _build_portfolio_position_rows(
    summary,
    *,
    settings: AppSettings | None = None,
    as_of: date_type | None = None,
) -> dict[str, list[PositionGroupRow]]:
    """
    Build portfolio rows for the decision-first table.

    ESPP+ lots are grouped by security + acquisition date into one position row.
    Non-ESPP+ lots remain one row per lot.
    """
    rows_by_security: dict[str, list[PositionGroupRow]] = {}
    scenario_as_of = as_of or date_type.today()
    for ss in summary.securities:
        lots_sorted = sorted(
            ss.active_lots,
            key=lambda ls: (ls.lot.acquisition_date, ls.lot.id),
        )

        espp_groups: dict[date_type, list[LotSummary]] = {}
        for ls in lots_sorted:
            if ls.lot.scheme_type != "ESPP_PLUS":
                continue
            espp_groups.setdefault(ls.lot.acquisition_date, []).append(ls)

        emitted_espp_dates: set[date_type] = set()
        rows: list[PositionGroupRow] = []
        row_index = 0
        for ls in lots_sorted:
            if ls.lot.scheme_type == "ESPP_PLUS":
                acq = ls.lot.acquisition_date
                if acq in emitted_espp_dates:
                    continue
                emitted_espp_dates.add(acq)
                row = (
                    _build_espp_plus_group_row(
                        ss.security.id,
                        acq,
                        espp_groups[acq],
                        row_index,
                    )
                )
                rows.append(
                    _attach_row_decision_scenarios(
                        row,
                        settings=settings,
                        as_of=scenario_as_of,
                    )
                )
            else:
                row = _build_single_position_row(ss.security.id, ls, row_index)
                rows.append(
                    _attach_row_decision_scenarios(
                        row,
                        settings=settings,
                        as_of=scenario_as_of,
                    )
                )
            row_index += 1
        rows_by_security[ss.security.id] = rows

    return rows_by_security


def _portfolio_net_gain_if_sold(
    rows_by_security: dict[str, list[PositionGroupRow]],
) -> Decimal | None:
    """
    Aggregate "Gain If Sold Today" across all rows with a calculable sell-now value.

    Truly locked rows (no sell_now_economic_result) are skipped silently.
    ESPP+ forfeiture-window rows are included: the paid shares can be sold today
    even though the matched shares are locked, so they carry a non-None result.
    Returns None when any non-locked row lacks a sell-now economic value.
    """
    values: list[Decimal] = []
    for rows in rows_by_security.values():
        for row in rows:
            if row.sell_now_economic_result is None:
                if row.sellability_status == "LOCKED":
                    continue  # Truly locked (e.g. unvested RSU) — skip silently
                return None  # Non-locked row missing value — can't compute total
            values.append(row.sell_now_economic_result)
    return _q2(sum(values, Decimal("0")))


def _portfolio_est_net_liquidity(
    rows_by_security: dict[str, list[PositionGroupRow]],
) -> Decimal | None:
    """
    Sum of Net If Sold Today for all rows with available proceeds.

    Definition:
      Est. Net Liquidity = sum(Net If Sold Today) for all rows where
      net_cash_if_sold is calculable (not None).
      This includes SELLABLE and AT_RISK rows that can be sold today.
      For ESPP+ with mixed lock periods, only includes the sellable portion.
    """
    values: list[Decimal] = []
    for rows in rows_by_security.values():
        for row in rows:
            # Include any row with a calculable net_cash_if_sold value
            # This properly handles ESPP+ bundles where some shares are sellable
            # even though other shares in the bundle are locked
            if row.net_cash_if_sold is not None:
                values.append(row.net_cash_if_sold)

    if not values:
        return None
    return _q2(sum(values, Decimal("0")))


def _portfolio_blocked_restricted_value(
    rows_by_security: dict[str, list[PositionGroupRow]],
) -> Decimal | None:
    """
    Aggregate currently non-realizable value for portfolio context.

    Includes:
      - market value of locked rows
      - ESPP+ matched-share value forfeited on immediate sale
    Returns None when a locked row lacks market value.
    """
    total = Decimal("0")
    for rows in rows_by_security.values():
        for row in rows:
            if row.sellability_status == "LOCKED":
                if row.total_mv is None:
                    return None
                total += row.total_mv
            total += row.sell_now_forfeited_match_value
    return _q2(total)


def _portfolio_sellable_employment_tax(
    rows_by_security: dict[str, list[PositionGroupRow]],
) -> Decimal | None:
    """
    Aggregate estimated employment tax for positions sellable today.

    LOCKED rows are skipped silently. Returns None when any non-locked row
    lacks a tax estimate (e.g. no income settings configured).
    """
    values: list[Decimal] = []
    for rows in rows_by_security.values():
        for row in rows:
            if row.sellability_status == "LOCKED":
                continue
            if row.sell_now_employment_tax_est is None:
                return None
            values.append(row.sell_now_employment_tax_est)
    if not values:
        return None
    return _q2(sum(values, Decimal("0")))


def _portfolio_sellable_true_cost(
    rows_by_security: dict[str, list[PositionGroupRow]],
) -> Decimal:
    """Sum of true economic cost for positions sellable today (excludes LOCKED rows)."""
    total = Decimal("0")
    for rows in rows_by_security.values():
        for row in rows:
            if row.sellability_status != "LOCKED":
                total += row.paid_true_cost
    return _q2(total)


def _portfolio_actionable_metrics(summary) -> dict[str, Decimal | str | None]:
    """
    Canonical sellable/actionable metrics derived from lot summaries.

    Uses lot-level data instead of grouped UI rows so the top band does not
    depend on view-layer grouping semantics.
    """
    sellable_market_value = Decimal("0")
    sellable_true_cost = Decimal("0")
    sellable_employment_tax = Decimal("0")
    sellable_net_liquidity = Decimal("0")
    sellable_quantity = Decimal("0")
    sellable_isa_market_value = Decimal("0")
    sellable_taxable_market_value = Decimal("0")

    has_sellable = False
    tax_incomplete = False
    net_incomplete = False

    for security_summary in summary.securities:
        for lot_summary in security_summary.active_lots:
            if (lot_summary.sellability_status or "").upper() == "LOCKED":
                continue
            if lot_summary.market_value_gbp is None:
                continue
            has_sellable = True
            lot_mv = _q2(lot_summary.market_value_gbp)
            sellable_market_value += lot_mv
            sellable_true_cost += _q2(lot_summary.true_cost_total_gbp)
            sellable_quantity += Decimal(lot_summary.quantity_remaining)
            if (lot_summary.lot.scheme_type or "").upper() == "ISA":
                sellable_isa_market_value += lot_mv
            else:
                sellable_taxable_market_value += lot_mv

            lot_tax = getattr(lot_summary, "est_employment_tax_on_lot_gbp", None)
            if lot_tax is None:
                tax_incomplete = True
            else:
                sellable_employment_tax += _q2(lot_tax)

            lot_net = getattr(lot_summary, "est_net_proceeds_gbp", None)
            if lot_net is None:
                net_incomplete = True
            else:
                sellable_net_liquidity += _q2(lot_net)

    sellable_market_value = _q2(sellable_market_value)
    sellable_true_cost = _q2(sellable_true_cost)
    sellable_isa_market_value = _q2(sellable_isa_market_value)
    sellable_taxable_market_value = _q2(sellable_taxable_market_value)
    sellable_whole_quantity = sellable_quantity.to_integral_value(rounding=ROUND_FLOOR)

    employment_tax_value: Decimal | None
    net_liquidity_value: Decimal | None
    net_gain_value: Decimal | None
    if not has_sellable:
        employment_tax_value = None
        net_liquidity_value = None
        net_gain_value = None
    else:
        employment_tax_value = None if tax_incomplete else _q2(sellable_employment_tax)
        net_liquidity_value = None if net_incomplete else _q2(sellable_net_liquidity)
        net_gain_value = (
            _q2(net_liquidity_value - sellable_true_cost)
            if net_liquidity_value is not None
            else None
        )

    tax_drag_pct: Decimal | None = None
    if (
        employment_tax_value is not None
        and sellable_market_value > Decimal("0")
    ):
        tax_drag_pct = (
            (employment_tax_value / sellable_market_value) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    isa_sellable_pct = Decimal("0.00")
    taxable_sellable_pct = Decimal("0.00")
    if sellable_market_value > Decimal("0"):
        isa_sellable_pct = (
            (sellable_isa_market_value / sellable_market_value) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        taxable_sellable_pct = (
            (sellable_taxable_market_value / sellable_market_value) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "sellable_market_value_gbp": sellable_market_value,
        "sellable_true_cost_gbp": sellable_true_cost,
        "sellable_employment_tax_gbp": employment_tax_value,
        "sellable_net_liquidity_gbp": net_liquidity_value,
        "sellable_net_gain_gbp": net_gain_value,
        "sellable_quantity": sellable_quantity,
        "sellable_quantity_text": _quantity_text(sellable_quantity),
        "sellable_whole_quantity": sellable_whole_quantity,
        "sellable_whole_quantity_text": _quantity_text(sellable_whole_quantity),
        "sellable_isa_market_value_gbp": sellable_isa_market_value,
        "sellable_taxable_market_value_gbp": sellable_taxable_market_value,
        "sellable_isa_pct": isa_sellable_pct,
        "sellable_taxable_pct": taxable_sellable_pct,
        "sellable_tax_drag_pct": tax_drag_pct,
    }


def _calendar_actionable_today_summary(*, as_of: date_type) -> dict[str, object]:
    try:
        payload = CalendarService.get_events_payload(as_of=as_of)
    except Exception:
        return {
            "count": 0,
            "label": "0 things to do",
            "preview": [],
        }

    actionable_events: list[dict[str, str]] = []
    for event in payload.get("events") or []:
        if event.get("completed"):
            continue
        event_date_raw = str(event.get("event_date") or "").strip()
        if not event_date_raw:
            continue
        try:
            event_date = date_type.fromisoformat(event_date_raw)
        except ValueError:
            continue
        if event_date <= as_of:
            title = str(event.get("title") or "").strip()
            scheme_type = str(event.get("scheme_type") or "").strip()
            preview_title = title
            if scheme_type and event.get("event_type") == "DIVIDEND_CONFIRMATION":
                preview_title = f"{title} ({scheme_type})"
            actionable_events.append(
                {
                    "event_date": event_date.isoformat(),
                    "title": preview_title,
                }
            )

    actionable_events.sort(key=lambda item: (item["event_date"], item["title"]))
    actionable_count = len(actionable_events)
    label = "1 thing to do" if actionable_count == 1 else f"{actionable_count} things to do"
    return {
        "count": actionable_count,
        "label": label,
        "preview": [event["title"] for event in actionable_events[:2] if event["title"]],
    }


def _portfolio_upcoming_timing_summary(*, as_of: date_type) -> list[dict[str, str]]:
    try:
        payload = CalendarService.get_events_payload(as_of=as_of)
    except Exception:
        return []

    relevant_types = {"FORFEITURE_END", "VEST_DATE", "DIVIDEND_CONFIRMATION"}
    upcoming: list[dict[str, str]] = []
    for event in payload.get("events") or []:
        if event.get("completed"):
            continue
        event_type = str(event.get("event_type") or "").strip().upper()
        if event_type not in relevant_types:
            continue
        event_date_raw = str(event.get("event_date") or "").strip()
        if not event_date_raw:
            continue
        try:
            event_date = date_type.fromisoformat(event_date_raw)
        except ValueError:
            continue
        if event_date < as_of:
            continue
        days_until = int(event.get("days_until", 0))
        title = str(event.get("title") or "").strip()
        subtitle = str(event.get("subtitle") or "").strip()
        upcoming.append(
            {
                "event_date": event_date.isoformat(),
                "title": title,
                "subtitle": subtitle,
                "days_label": "today" if days_until == 0 else f"in {days_until}d",
            }
        )

    upcoming.sort(key=lambda item: (item["event_date"], item["title"]))
    return upcoming[:2]


def _scheme_display_name(scheme_type: str) -> str:
    """Human-friendly scheme label for per-scheme reporting."""
    return SCHEME_DISPLAY_NAMES.get(scheme_type, scheme_type.replace("_", " ").title())


def _default_current_metrics() -> SchemeCurrentMetrics:
    """Zero/empty current metrics scaffold for schemes without open positions."""
    return SchemeCurrentMetrics(
        lot_count=0,
        position_count=0,
        quantity=Decimal("0"),
        cost_basis_gbp=Decimal("0"),
        true_cost_gbp=Decimal("0"),
        market_value_gbp=None,
        unrealised_tax_pnl_gbp=None,
        unrealised_economic_pnl_gbp=None,
        est_employment_tax_gbp=None,
        est_net_liquidation_gbp=None,
        post_tax_economic_pnl_gbp=None,
        allocated_net_dividends_gbp=Decimal("0"),
        economic_plus_net_dividends_gbp=None,
        capital_at_risk_after_dividends_gbp=Decimal("0"),
        potential_forfeiture_value_gbp=Decimal("0"),
    )


def _default_historic_metrics() -> SchemeHistoricMetrics:
    """Zero historic metrics scaffold for schemes with no disposal history."""
    return SchemeHistoricMetrics(
        disposal_count=0,
        disposed_lot_count=0,
        quantity_disposed=Decimal("0"),
        proceeds_gbp=Decimal("0"),
        cost_basis_gbp=Decimal("0"),
        true_cost_gbp=Decimal("0"),
        realised_tax_pnl_gbp=Decimal("0"),
        realised_economic_pnl_gbp=Decimal("0"),
    )


def _row_estimated_employment_tax_gbp(row: PositionGroupRow) -> Decimal | None:
    """
    Derive row-level employment-tax estimate from portfolio decision fields.

    For grouped ESPP+ rows, matched-share forfeiture is a non-tax loss and must
    not be treated as employment tax.
    """
    if row.row_kind == "GROUPED_ESPP_PLUS":
        if row.paid_mv is None or row.sell_now_cash_paid is None:
            return None
        return _q2(row.paid_mv - row.sell_now_cash_paid)

    if row.total_mv is None or row.net_cash_if_sold is None:
        return None
    return _q2(row.total_mv - row.net_cash_if_sold)


def _allocate_scheme_net_dividends(
    rows_by_security: dict[str, list[PositionGroupRow]],
    *,
    settings: AppSettings | None = None,
) -> dict[str, Decimal]:
    """
    Allocate security-level net dividends to schemes using active true-cost weights.

    Reconciliation invariant:
      sum(per-scheme allocated net dividends) == sum(security allocated net dividends)
    """
    dividend_summary = DividendService.get_summary(settings=settings)
    allocation_rows = (
        (dividend_summary.get("allocation") or {}).get("rows") or []
    )
    if not allocation_rows:
        return {}

    scheme_true_cost_by_security: dict[str, dict[str, Decimal]] = {}
    for security_id, rows in rows_by_security.items():
        sec_bucket: dict[str, Decimal] = {}
        for row in rows:
            if not row.detail_lots:
                continue
            scheme_type = str(row.detail_lots[0].lot.scheme_type or "").upper()
            if not scheme_type:
                continue
            sec_bucket[scheme_type] = sec_bucket.get(scheme_type, Decimal("0")) + row.paid_true_cost
        if sec_bucket:
            scheme_true_cost_by_security[security_id] = sec_bucket

    allocated_by_scheme: dict[str, Decimal] = {}
    for row in allocation_rows:
        security_id = str(row.get("security_id") or "").strip()
        if not security_id:
            continue
        try:
            allocated_net = Decimal(str(row.get("allocated_net_dividends_gbp") or "0"))
        except (InvalidOperation, ValueError, TypeError):
            continue
        allocated_net = _q2(allocated_net)
        if allocated_net == Decimal("0"):
            continue

        scheme_weights = scheme_true_cost_by_security.get(security_id)
        if not scheme_weights:
            continue

        positive_weights = {
            scheme: weight
            for scheme, weight in scheme_weights.items()
            if weight > Decimal("0")
        }
        if positive_weights:
            ranked_schemes = sorted(
                positive_weights.items(),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )
            denominator = sum((weight for _, weight in ranked_schemes), Decimal("0"))
        else:
            ranked_schemes = sorted(
                scheme_weights.items(),
                key=lambda item: item[0],
            )
            denominator = Decimal(len(ranked_schemes))

        if not ranked_schemes or denominator <= Decimal("0"):
            continue

        allocated_sum = Decimal("0")
        for scheme_type, weight in ranked_schemes:
            if positive_weights:
                portion = _q2((allocated_net * weight) / denominator)
            else:
                portion = _q2(allocated_net / denominator)
            allocated_sum += portion
            allocated_by_scheme[scheme_type] = (
                allocated_by_scheme.get(scheme_type, Decimal("0")) + portion
            )

        remainder = _q2(allocated_net - allocated_sum)
        if remainder != Decimal("0"):
            top_scheme = ranked_schemes[0][0]
            allocated_by_scheme[top_scheme] = (
                allocated_by_scheme.get(top_scheme, Decimal("0")) + remainder
            )

    return {scheme: _q2(value) for scheme, value in allocated_by_scheme.items()}


def _aggregate_current_scheme_metrics(
    rows_by_security: dict[str, list[PositionGroupRow]],
    *,
    scheme_net_dividends_gbp: dict[str, Decimal] | None = None,
) -> dict[str, SchemeCurrentMetrics]:
    """
    Aggregate current (open position) metrics by scheme from portfolio rows.

    Uses PositionGroupRow to preserve grouped ESPP+ early-sale semantics.
    """
    current_raw: dict[str, dict] = {}
    for rows in rows_by_security.values():
        for row in rows:
            if not row.detail_lots:
                continue
            scheme_type = row.detail_lots[0].lot.scheme_type
            bucket = current_raw.setdefault(
                scheme_type,
                {
                    "lot_count": 0,
                    "position_count": 0,
                    "quantity": Decimal("0"),
                    "cost_basis_gbp": Decimal("0"),
                    "true_cost_gbp": Decimal("0"),
                    "market_values": [],
                    "unrealised_tax_pnls": [],
                    "unrealised_economic_pnls": [],
                    "employment_taxes": [],
                    "post_tax_economic_pnls": [],
                    "potential_forfeiture_value_gbp": Decimal("0"),
                },
            )

            bucket["lot_count"] += len(row.detail_lots)
            bucket["position_count"] += 1
            bucket["quantity"] += row.total_qty
            bucket["cost_basis_gbp"] += row.paid_cost_basis
            bucket["true_cost_gbp"] += row.paid_true_cost
            bucket["market_values"].append(row.total_mv)
            bucket["unrealised_tax_pnls"].append(row.pnl_tax_basis)
            bucket["unrealised_economic_pnls"].append(row.pnl_economic)
            bucket["employment_taxes"].append(_row_estimated_employment_tax_gbp(row))
            bucket["post_tax_economic_pnls"].append(row.sell_now_economic_result)
            bucket["potential_forfeiture_value_gbp"] += row.sell_now_forfeited_match_value

    current: dict[str, SchemeCurrentMetrics] = {}
    for scheme_type, bucket in current_raw.items():
        allocated_net_dividends = _q2(
            (scheme_net_dividends_gbp or {}).get(scheme_type, Decimal("0"))
        )
        post_tax_economic_pnl = _sum_optional(bucket["post_tax_economic_pnls"])
        economic_plus_net_dividends = (
            _q2(post_tax_economic_pnl + allocated_net_dividends)
            if post_tax_economic_pnl is not None
            else None
        )
        true_cost_total = _q2(bucket["true_cost_gbp"])
        current[scheme_type] = SchemeCurrentMetrics(
            lot_count=bucket["lot_count"],
            position_count=bucket["position_count"],
            quantity=bucket["quantity"],
            cost_basis_gbp=_q2(bucket["cost_basis_gbp"]),
            true_cost_gbp=true_cost_total,
            market_value_gbp=_sum_optional(bucket["market_values"]),
            unrealised_tax_pnl_gbp=_sum_optional(bucket["unrealised_tax_pnls"]),
            unrealised_economic_pnl_gbp=_sum_optional(
                bucket["unrealised_economic_pnls"]
            ),
            est_employment_tax_gbp=_sum_optional(bucket["employment_taxes"]),
            # "Est. Net Liquidation" is intended as economic net outcome
            # (post-tax P&L), not gross cash / market-value style totals.
            est_net_liquidation_gbp=post_tax_economic_pnl,
            post_tax_economic_pnl_gbp=post_tax_economic_pnl,
            allocated_net_dividends_gbp=allocated_net_dividends,
            economic_plus_net_dividends_gbp=economic_plus_net_dividends,
            capital_at_risk_after_dividends_gbp=_q2(
                max(Decimal("0"), true_cost_total - allocated_net_dividends)
            ),
            potential_forfeiture_value_gbp=_q2(
                bucket["potential_forfeiture_value_gbp"]
            ),
        )

    return current


def _aggregate_historic_scheme_metrics() -> dict[str, SchemeHistoricMetrics]:
    """
    Aggregate disposal history by originating lot scheme.

    Historic metrics are sourced from persisted LotDisposal records, so they
    reflect actual committed disposals rather than hypothetical sell-now states.
    """
    with AppContext.read_session() as sess:
        rows = sess.execute(
            select(
                Lot.scheme_type,
                LotDisposal.lot_id,
                LotDisposal.transaction_id,
                LotDisposal.quantity_allocated,
                LotDisposal.proceeds_gbp,
                LotDisposal.realised_gain_gbp,
                LotDisposal.realised_gain_economic_gbp,
            )
            .join(Lot, Lot.id == LotDisposal.lot_id)
            .join(Transaction, Transaction.id == LotDisposal.transaction_id)
            .where(
                Transaction.transaction_type == "DISPOSAL",
                Transaction.is_reversal.is_(False),
            )
        ).all()

    historic_raw: dict[str, dict] = {}
    for (
        scheme_type,
        lot_id,
        transaction_id,
        quantity_allocated,
        proceeds_gbp,
        realised_gain_gbp,
        realised_gain_economic_gbp,
    ) in rows:
        bucket = historic_raw.setdefault(
            scheme_type,
            {
                "transaction_ids": set(),
                "lot_ids": set(),
                "quantity_disposed": Decimal("0"),
                "proceeds_gbp": Decimal("0"),
                "realised_tax_pnl_gbp": Decimal("0"),
                "realised_economic_pnl_gbp": Decimal("0"),
            },
        )
        bucket["transaction_ids"].add(transaction_id)
        bucket["lot_ids"].add(lot_id)
        bucket["quantity_disposed"] += Decimal(quantity_allocated)
        bucket["proceeds_gbp"] += Decimal(proceeds_gbp)
        bucket["realised_tax_pnl_gbp"] += Decimal(realised_gain_gbp)
        bucket["realised_economic_pnl_gbp"] += Decimal(realised_gain_economic_gbp)

    historic: dict[str, SchemeHistoricMetrics] = {}
    for scheme_type, bucket in historic_raw.items():
        proceeds = _q2(bucket["proceeds_gbp"])
        realised_tax = _q2(bucket["realised_tax_pnl_gbp"])
        realised_economic = _q2(bucket["realised_economic_pnl_gbp"])
        historic[scheme_type] = SchemeHistoricMetrics(
            disposal_count=len(bucket["transaction_ids"]),
            disposed_lot_count=len(bucket["lot_ids"]),
            quantity_disposed=bucket["quantity_disposed"],
            proceeds_gbp=proceeds,
            cost_basis_gbp=_q2(proceeds - realised_tax),
            true_cost_gbp=_q2(proceeds - realised_economic),
            realised_tax_pnl_gbp=realised_tax,
            realised_economic_pnl_gbp=realised_economic,
        )

    return historic


def _build_per_scheme_reports(
    rows_by_security: dict[str, list[PositionGroupRow]],
    *,
    settings: AppSettings | None = None,
) -> list[SchemeReport]:
    """Build per-scheme report payload for /per-scheme page."""
    scheme_net_dividends = _allocate_scheme_net_dividends(
        rows_by_security,
        settings=settings,
    )
    current = _aggregate_current_scheme_metrics(
        rows_by_security,
        scheme_net_dividends_gbp=scheme_net_dividends,
    )
    historic = _aggregate_historic_scheme_metrics()

    scheme_types: list[str] = []
    for scheme_type in SCHEME_DISPLAY_ORDER:
        if scheme_type in current or scheme_type in historic:
            scheme_types.append(scheme_type)
    extras = sorted((set(current) | set(historic)) - set(scheme_types))
    scheme_types.extend(extras)

    reports: list[SchemeReport] = []
    for scheme_type in scheme_types:
        current_metrics = current.get(scheme_type, _default_current_metrics())
        historic_metrics = historic.get(scheme_type, _default_historic_metrics())
        if current_metrics.post_tax_economic_pnl_gbp is None:
            lifetime_economic = (
                historic_metrics.realised_economic_pnl_gbp
                if current_metrics.lot_count == 0
                else None
            )
        else:
            lifetime_economic = _q2(
                current_metrics.post_tax_economic_pnl_gbp
                + historic_metrics.realised_economic_pnl_gbp
            )
        reports.append(
            SchemeReport(
                scheme_type=scheme_type,
                display_name=_scheme_display_name(scheme_type),
                current=current_metrics,
                historic=historic_metrics,
                lifetime_economic_pnl_gbp=lifetime_economic,
            )
        )
    return reports


def _tax_context_from_settings(
    settings: AppSettings | None,
    on_date: date_type,
) -> TaxContext | None:
    """Build TaxContext from app settings for the date's tax year."""
    if settings is None:
        return None
    return TaxContext(
        tax_year=tax_year_for_date(on_date),
        gross_employment_income=settings.default_gross_income,
        pension_sacrifice=settings.default_pension_sacrifice,
        other_income=settings.default_other_income,
        student_loan_plan=settings.default_student_loan_plan,
    )


def _normalize_iso_currency(raw_value: str, *, field_name: str) -> str:
    """Normalize and validate a 3-letter ISO currency code."""
    cleaned = (raw_value or "").strip().upper()
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise ValueError(f"{field_name} must be a 3-letter ISO currency code.")
    return cleaned


def _normalize_price_input_currency(raw_value: str) -> str:
    """Normalize Add Lot input currency (Phase B: generalized 3-letter ISO)."""
    raw = raw_value.strip() if raw_value else "GBP"
    return _normalize_iso_currency(raw, field_name="price_input_currency")


def _phase_a_broker_currency_or_none(raw_value: str | None) -> str | None:
    """Return normalized currency when valid, otherwise None."""
    if raw_value is None:
        return None
    try:
        return _normalize_iso_currency(raw_value, field_name="broker_currency")
    except ValueError:
        return None


def _ordered_currency_codes(codes: set[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    remaining = set(codes)
    for preferred in DEFAULT_PRICE_INPUT_CURRENCIES:
        if preferred in remaining:
            ordered.append(preferred)
            remaining.remove(preferred)
    ordered.extend(sorted(remaining))
    return tuple(ordered)


def _price_input_currency_options(securities: list) -> tuple[str, ...]:
    """
    Currency choices shown on Add Lot.

    Always include GBP/USD for continuity, then add security-native currencies
    discovered in the current portfolio.
    """
    codes = set(DEFAULT_PRICE_INPUT_CURRENCIES)
    for security in securities:
        if security is None:
            continue
        code = _phase_a_broker_currency_or_none(getattr(security, "currency", None))
        if code is not None:
            codes.add(code)
    return _ordered_currency_codes(codes)


def _broker_currency_options(*candidates: str | None) -> tuple[str, ...]:
    """Broker-currency selector options for edit/transfer workflows."""
    codes = set(DEFAULT_PRICE_INPUT_CURRENCIES)
    for candidate in candidates:
        normalized = _phase_a_broker_currency_or_none(candidate)
        if normalized is not None:
            codes.add(normalized)
    return _ordered_currency_codes(codes)


def _build_add_lot_currency_workflow(
    *,
    securities: list,
    currency_options: tuple[str, ...],
) -> dict:
    """
    Build Add Lot currency-workflow context.

    Includes security currency map plus best-effort quote-to-GBP metadata.
    """
    security_currency_by_id: dict[str, str] = {}
    for security in securities:
        if security is None:
            continue
        currency = _phase_a_broker_currency_or_none(getattr(security, "currency", None))
        security_currency_by_id[getattr(security, "id")] = currency or "GBP"

    fx_to_gbp: dict[str, dict[str, str]] = {}
    fx_error: str | None = None
    for currency in currency_options:
        try:
            quote = FxService.get_rate(currency, "GBP")
        except Exception as exc:
            if currency != "GBP" and fx_error is None:
                fx_error = str(exc)
            continue
        fx_to_gbp[currency] = {
            "rate": str(quote.rate),
            "as_of": quote.as_of or "",
            "path": " -> ".join(quote.path),
            "source": quote.source,
        }

    return {
        "security_currency_by_id": security_currency_by_id,
        "fx_to_gbp": fx_to_gbp,
        "fx_error": fx_error,
    }


def _suggest_broker_currency(
    *,
    source_broker_currency: str | None,
    source_original_currency: str | None,
    security_currency: str | None,
) -> str:
    """
    Resolve a default broker holding currency for UI transfer flows.

    Preference:
      1) existing lot broker_currency
      2) original acquisition currency
      3) security currency
      4) GBP fallback
    """
    for candidate in (
        source_broker_currency,
        source_original_currency,
        security_currency,
    ):
        resolved = _phase_a_broker_currency_or_none(candidate)
        if resolved is not None:
            return resolved
    return "GBP"


def _resolve_input_fx_to_gbp(price_input_currency: str) -> tuple[Decimal, str]:
    """
    Resolve FX rate used to convert Add Lot price inputs to GBP.

    Returns:
        (fx_rate_to_gbp, fx_rate_source)
    """
    if price_input_currency == "GBP":
        return Decimal("1"), "identity_gbp"

    try:
        quote = FxService.get_rate(price_input_currency, "GBP")
    except RuntimeError as exc:
        currency = price_input_currency.upper()
        if currency == "USD":
            if "USD2GBP" in str(exc):
                raise ValueError(
                    "USD->GBP conversion rate (USD2GBP) was not found in the FX source."
                ) from exc
            raise ValueError(
                "USD->GBP conversion is unavailable. Refresh FX data and try again."
            ) from exc
        raise ValueError(
            f"{currency}->GBP conversion is unavailable. Add FX pair data and try again."
        ) from exc

    if quote.rate <= Decimal("0"):
        raise ValueError(f"{price_input_currency.upper()}->GBP conversion rate must be greater than zero.")
    return quote.rate, quote.source


def _convert_input_price_to_gbp(amount: Decimal, fx_rate_to_gbp: Decimal) -> Decimal:
    """Convert an Add Lot per-share price to GBP with consistent precision."""
    return (amount * fx_rate_to_gbp).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )


def _derive_true_cost_per_share(
    scheme_type: str,
    *,
    quantity: Decimal,
    acquisition_date: date_type,
    purchase_price_per_share_gbp: Decimal | None = None,
    rsu_fmv_at_vest_gbp: Decimal | None = None,
    espp_fmv_at_purchase_gbp: Decimal | None = None,
    settings: AppSettings | None = None,
) -> Decimal | None:
    """
    Derive true economic cost/share using existing tax engine helpers.

    Returns None when required inputs/settings are missing for a scheme.
    """
    if quantity <= 0:
        return None

    if scheme_type in ("BROKERAGE", "ISA") and purchase_price_per_share_gbp is not None:
        return brokerage_true_cost(
            purchase_price_gbp=purchase_price_per_share_gbp,
            quantity=quantity,
        ).true_cost_per_share_gbp

    if scheme_type == "RSU" and rsu_fmv_at_vest_gbp is not None:
        ctx = _tax_context_from_settings(settings, acquisition_date)
        if ctx is None:
            return None
        rates = get_marginal_rates(ctx)
        # RSU has Â£0 upfront purchase outlay; employee out-of-pocket cost at vest
        # is the payroll tax cash impact (IT + NI).
        rsu_tax_rate = rates.income_tax + rates.national_insurance
        return (rsu_fmv_at_vest_gbp * rsu_tax_rate).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )

    if scheme_type == "ESPP":
        # Net-pay ESPP: contributions are post-PAYE; no income-tax liability on
        # the discount at acquisition. CGT applies only at disposal.
        # True economic cost = the purchase price paid from net salary.
        # FMV at purchase is stored for records only and is not used here.
        if purchase_price_per_share_gbp is None:
            return None
        return purchase_price_per_share_gbp

    if scheme_type == "ESPP_PLUS":
        # ESPP_PLUS contributions are funded from gross salary (pre-tax).
        # True economic cost = gross_price Ã— (1 âˆ’ combined_marginal_rate),
        # i.e. what the employee actually gives up in after-tax terms.
        # FMV at purchase is NOT used; no discount-benefit tax is applied.
        if purchase_price_per_share_gbp is None:
            return None
        ctx = _tax_context_from_settings(settings, acquisition_date)
        if ctx is None:
            return None
        rates = get_marginal_rates(ctx)
        return (purchase_price_per_share_gbp * rates.pence_kept_per_pound).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    return None


# ---------------------------------------------------------------------------
# Portfolio overview â€” GET /
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(
    request: Request,
    msg: str | None = None,
    as_of: date_type | None = Query(None),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    context = _build_portfolio_page_context(
        request,
        msg=msg,
        as_of=as_of,
    )
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        context,
    )


def _build_portfolio_page_context(
    request: Request,
    *,
    msg: str | None = None,
    as_of: date_type | None = None,
) -> dict:
    if _is_locked():
        raise RuntimeError("portfolio page context requested while locked")
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    refresh_diag = _state.get_refresh_diagnostics()
    if refresh_diag["next_due_at"] is None:
        _state.set_refresh_next_due(60)
        refresh_diag = _state.get_refresh_diagnostics()
    if as_of is None or as_of >= date_type.today():
        IbkrPriceService.ingest_all()
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
        as_of=as_of,
    )
    security_daily_changes = _build_security_daily_changes(summary, as_of=as_of)
    position_rows_by_security = _build_portfolio_position_rows(
        summary,
        settings=settings,
    )
    actionable_metrics = _portfolio_actionable_metrics(summary)
    portfolio_est_net_liquidity = actionable_metrics["sellable_net_liquidity_gbp"]
    portfolio_blocked_restricted_value = _portfolio_blocked_restricted_value(
        position_rows_by_security
    )
    portfolio_net_gain_if_sold = actionable_metrics["sellable_net_gain_gbp"]
    portfolio_sellable_employment_tax = actionable_metrics["sellable_employment_tax_gbp"]
    portfolio_sellable_true_cost = actionable_metrics["sellable_true_cost_gbp"]
    exposure_snapshot = ExposureService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
    )
    capital_stack_snapshot = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
        as_of=as_of,
    )
    estimated_net_dividends_gbp = capital_stack_snapshot.get("estimated_net_dividends_gbp")
    portfolio_net_gain_plus_net_dividends: Decimal | None = None
    if portfolio_net_gain_if_sold is not None and estimated_net_dividends_gbp is not None:
        try:
            portfolio_net_gain_plus_net_dividends = _q2(
                portfolio_net_gain_if_sold + Decimal(str(estimated_net_dividends_gbp))
            )
        except Exception:
            portfolio_net_gain_plus_net_dividends = None
    behavioral_guardrails_raw = _build_behavioral_guardrails(
        summary=summary,
        settings=settings,
        position_rows_by_security=position_rows_by_security,
        deployable_capital_gbp=exposure_snapshot.get("deployable_capital_gbp"),
        sellable_employment_tax_gbp=portfolio_sellable_employment_tax,
        forfeitable_capital_gbp=exposure_snapshot.get("forfeitable_capital_gbp"),
    )
    now_utc = _utc_now()
    behavioral_guardrails, behavioral_guardrails_hidden_count = (
        _apply_guardrail_visibility_persistence(
            behavioral_guardrails_raw,
            now_utc=now_utc,
        )
    )
    behavioral_guardrails_total_count = len(behavioral_guardrails_raw)
    behavioral_guardrails_active_count = len(behavioral_guardrails)
    today = now_utc.date()
    calendar_actionable_today = _calendar_actionable_today_summary(as_of=today)
    portfolio_upcoming_timing = _portfolio_upcoming_timing_summary(as_of=today)
    return {
        "request": request,
        "summary": summary,
        "settings": settings,
        "price_stale_after_days": settings.price_stale_after_days if settings else 1,
        "fx_stale_after_minutes": settings.fx_stale_after_minutes if settings else 10,
        "security_daily_changes": security_daily_changes,
        "portfolio_valuation_basis": _portfolio_valuation_basis(summary),
        "position_rows_by_security": position_rows_by_security,
        "portfolio_est_net_liquidity": portfolio_est_net_liquidity,
        "portfolio_blocked_restricted_value": portfolio_blocked_restricted_value,
        "portfolio_net_gain_if_sold": portfolio_net_gain_if_sold,
        "portfolio_sellable_employment_tax": portfolio_sellable_employment_tax,
        "portfolio_sellable_true_cost": portfolio_sellable_true_cost,
        "portfolio_locked_value": exposure_snapshot.get("locked_capital_gbp"),
        "portfolio_forfeitable_value": exposure_snapshot.get("forfeitable_capital_gbp"),
        "portfolio_isa_wrapper_market_value_gbp": exposure_snapshot.get("isa_wrapper_market_value_gbp"),
        "portfolio_taxable_wrapper_market_value_gbp": exposure_snapshot.get("taxable_wrapper_market_value_gbp"),
        "portfolio_isa_wrapper_pct_of_total": exposure_snapshot.get("isa_wrapper_pct_of_total"),
        "portfolio_taxable_wrapper_pct_of_total": exposure_snapshot.get("taxable_wrapper_pct_of_total"),
        "portfolio_top_holding_ticker_gross": exposure_snapshot.get("top_holding_ticker_gross"),
        "portfolio_top_holding_pct_gross": exposure_snapshot.get("top_holding_pct_gross"),
        "portfolio_top_holding_ticker_sellable": exposure_snapshot.get("top_holding_ticker_sellable"),
        "portfolio_top_holding_pct_sellable": exposure_snapshot.get("top_holding_pct_sellable"),
        "portfolio_total_gross_market_value_gbp": exposure_snapshot.get("total_gross_market_value_gbp"),
        "portfolio_employer_ticker": exposure_snapshot.get("employer_ticker"),
        "portfolio_employer_pct_of_gross": exposure_snapshot.get("employer_pct_of_gross"),
        "portfolio_employer_pct_of_sellable": exposure_snapshot.get("employer_pct_of_sellable"),
        "portfolio_total_sellable_market_value_gbp": actionable_metrics["sellable_market_value_gbp"],
        "portfolio_deployable_cash_gbp": exposure_snapshot.get("deployable_cash_gbp"),
        "portfolio_deployable_capital_gbp": exposure_snapshot.get("deployable_capital_gbp"),
        "portfolio_employer_share_of_deployable_pct": exposure_snapshot.get("employer_share_of_deployable_pct"),
        "portfolio_sellable_quantity_text": actionable_metrics["sellable_quantity_text"],
        "portfolio_sellable_whole_quantity_text": actionable_metrics["sellable_whole_quantity_text"],
        "portfolio_sellable_tax_drag_pct": actionable_metrics["sellable_tax_drag_pct"],
        "portfolio_sellable_isa_market_value_gbp": actionable_metrics["sellable_isa_market_value_gbp"],
        "portfolio_sellable_taxable_market_value_gbp": actionable_metrics["sellable_taxable_market_value_gbp"],
        "portfolio_sellable_isa_pct": actionable_metrics["sellable_isa_pct"],
        "portfolio_sellable_taxable_pct": actionable_metrics["sellable_taxable_pct"],
        "portfolio_employer_dependence_ratio_pct": exposure_snapshot.get("employer_dependence_ratio_pct"),
        "portfolio_employer_income_dependency_proxy_gbp": exposure_snapshot.get("employer_income_dependency_proxy_gbp"),
        "portfolio_employer_dependence_denominator_gbp": exposure_snapshot.get("employer_dependence_denominator_gbp"),
        "portfolio_exposure_notes": exposure_snapshot.get("notes", []),
        "portfolio_net_gain_plus_net_dividends": portfolio_net_gain_plus_net_dividends,
        "dividend_adjusted_capital_at_risk_gbp": (
            capital_stack_snapshot.get("dividend_adjusted_capital_at_risk_gbp")
        ),
        "estimated_net_dividends_gbp": estimated_net_dividends_gbp,
        "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
        "refresh_diag": refresh_diag,
        "behavioral_guardrails": behavioral_guardrails,
        "behavioral_guardrails_active_count": behavioral_guardrails_active_count,
        "behavioral_guardrails_hidden_count": behavioral_guardrails_hidden_count,
        "behavioral_guardrails_total_count": behavioral_guardrails_total_count,
        "calendar_actionable_today_count": calendar_actionable_today["count"],
        "calendar_actionable_today_label": calendar_actionable_today["label"],
        "calendar_actionable_today_preview": calendar_actionable_today["preview"],
        "portfolio_upcoming_timing": portfolio_upcoming_timing,
        "income_profile_configured": not _tax_inputs_incomplete(settings),
        "employer_ticker_configured": bool(getattr(settings, "employer_ticker", "").strip()) if settings else False,
        "guardrail_dismiss_max_days": _GUARDRAIL_DISMISS_MAX_DAYS,
        "guardrail_snooze_max_days": _GUARDRAIL_SNOOZE_MAX_DAYS,
        "page_as_of_date": (as_of or today).isoformat(),
        "page_as_of_active": as_of is not None,
        "selected_as_of": (as_of.isoformat() if as_of else ""),
        "today": today,
        **_flash(msg),
    }


@router.get("/portfolio/live-data", include_in_schema=False)
async def portfolio_live_data(
    request: Request,
    as_of: date_type | None = Query(None),
) -> JSONResponse:
    if _is_locked():
        return JSONResponse(
            {"ok": False, "message": "Database is locked."},
            status_code=423,
        )
    context = _build_portfolio_page_context(
        request,
        as_of=as_of,
    )
    root_html = templates.get_template("partials/portfolio_live_root.html").render(
        context
    )
    return JSONResponse(
        {
            "ok": True,
            "root_html": root_html,
            "guardrails": {
                "active_count": context["behavioral_guardrails_active_count"],
                "hidden_count": context["behavioral_guardrails_hidden_count"],
                "total_count": context["behavioral_guardrails_total_count"],
            },
            "calendar_actionables": {
                "count": context["calendar_actionable_today_count"],
                "label": context["calendar_actionable_today_label"],
            },
        }
    )


@router.post("/portfolio/guardrails/dismiss", include_in_schema=False)
async def dismiss_portfolio_guardrail(request: Request) -> JSONResponse:
    if _is_locked():
        return JSONResponse(
            {"ok": False, "message": "Database is locked."},
            status_code=423,
        )
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "message": "Invalid JSON payload."},
            status_code=400,
        )

    guardrail_id = str(payload.get("guardrail_id") or "").strip()
    condition_hash = str(payload.get("condition_hash") or "").strip()
    action = str(payload.get("action") or "dismiss").strip().lower()
    if not guardrail_id or not condition_hash:
        return JSONResponse(
            {
                "ok": False,
                "message": "guardrail_id and condition_hash are required.",
            },
            status_code=400,
        )
    try:
        result = AlertLifecycleService.record_state_transition(
            lifecycle_id=guardrail_id,
            condition_hash=condition_hash,
            action=action,
            source="portfolio_ui",
            notes=(
                "Updated from portfolio behavioral guardrails; "
                "auto-reactivates on condition change or expiry."
            ),
            dismiss_days=_GUARDRAIL_DISMISS_MAX_DAYS,
            snooze_days=_GUARDRAIL_SNOOZE_MAX_DAYS,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    return JSONResponse(
        {
            "ok": True,
            "guardrail_id": guardrail_id,
            "state": result["state"],
            "until": result["until"],
            "policy": result["policy"],
            "dismiss_max_days": _GUARDRAIL_DISMISS_MAX_DAYS,
            "snooze_max_days": _GUARDRAIL_SNOOZE_MAX_DAYS,
        }
    )


# ---------------------------------------------------------------------------
# Per-scheme view â€” GET /per-scheme
# ---------------------------------------------------------------------------

@router.get("/per-scheme", response_class=HTMLResponse, include_in_schema=False)
async def per_scheme(request: Request) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    position_rows_by_security = _build_portfolio_position_rows(
        summary,
        settings=settings,
    )
    scheme_reports = _build_per_scheme_reports(
        position_rows_by_security,
        settings=settings,
    )
    return _html_template_response(
        "per_scheme.html",
        {
            "request": request,
            "scheme_reports": scheme_reports,
        },
    )


# ---------------------------------------------------------------------------
# Net Value page - GET /net-value
# ---------------------------------------------------------------------------

def _uk_tax_year(d: date_type) -> str:
    # Return YYYY-YY UK tax year string for a given date.
    year = d.year
    if d < date_type(year, 4, 6):
        return str(year - 1) + "-" + str(year)[-2:]
    return str(year) + "-" + str(year + 1)[-2:]


def _build_nv_row(row: PositionGroupRow) -> dict:
    # Flatten a PositionGroupRow into a plain dict with all per-lot aggregations
    # precomputed. This removes all accumulation logic from the template.
    cost_basis = Decimal("0")
    acct_pnl: Decimal | None = Decimal("0")
    mv_native: Decimal | None = None
    mv_native_ccy: str | None = None

    for dls in row.detail_lots:
        cost_basis += dls.cost_basis_total_gbp
        if acct_pnl is not None:
            if dls.unrealised_gain_cgt_gbp is not None:
                acct_pnl += dls.unrealised_gain_cgt_gbp
            else:
                acct_pnl = None
        if (
            dls.market_value_native is not None
            and dls.market_value_native_currency
            and dls.market_value_native_currency != "GBP"
        ):
            mv_native = (mv_native or Decimal("0")) + dls.market_value_native
            mv_native_ccy = dls.market_value_native_currency

    return {
        "group_id": row.group_id,
        "acquisition_date": row.acquisition_date,
        "tax_year": _uk_tax_year(row.acquisition_date),
        "scheme_display": row.scheme_display,
        "total_qty": row.total_qty,
        "total_mv": row.total_mv,
        "sellability_status": row.sellability_status,
        "sellability_unlock_date": row.sellability_unlock_date,
        "forfeiture_risk_days_remaining": row.forfeiture_risk_days_remaining,
        "has_tax_window": row.has_tax_window,
        "sell_now_employment_tax_est": row.sell_now_employment_tax_est,
        "net_cash_if_sold": row.net_cash_if_sold,
        "reason_unavailable": row.reason_unavailable,
        # Precomputed aggregations - no template iteration needed
        "cost_basis_gbp": _q2(cost_basis),
        # Locked pre-vest RSUs have no real economic P&L (notional cost basis only).
        "accounting_pnl_gbp": (
            None
            if row.sellability_status == "LOCKED" and row.scheme_display == "RSU"
            else (_q2(acct_pnl) if acct_pnl is not None else None)
        ),
        "market_value_native": mv_native,
        "market_value_native_currency": mv_native_ccy,
        "paid_true_cost": row.paid_true_cost,
    }


def _build_sell_all_metrics(summary) -> dict:
    # Build the sell_all_metrics view object for the Net Value page top cards.
    # All values sourced exclusively from PortfolioSummary - no template math.
    return {
        "gross_market_value_gbp": summary.total_market_value_gbp,
        "est_employment_tax_gbp": summary.est_total_employment_tax_gbp,
        "net_value_gbp": summary.est_total_net_liquidation_gbp,
        "cost_basis_gbp": summary.total_cost_basis_gbp,
        "valuation_currency": summary.valuation_currency,
        "fx_conversion_basis": summary.fx_conversion_basis,
        "fx_as_of": summary.fx_as_of,
        "fx_is_stale": summary.fx_is_stale,
    }


def _build_nv_securities(summary) -> list[dict]:
    # Wrap SecuritySummary objects into view dicts with renamed fields.
    # Renames unrealised_gain_cgt_gbp -> accounting_pnl_cost_basis_gbp
    # so the template contains no _cgt_ variable references.
    #
    # accounting_pnl_cost_basis_gbp is summed across ALL active lots
    # (locked + sellable) to reconcile with the per-lot table rows.
    # SecuritySummary.unrealised_gain_cgt_gbp is SELLABLE-only and must
    # NOT be used here.
    result = []
    for ss in summary.securities:
        if not ss.active_lots:
            continue
        # Sum accounting P&L across every active lot regardless of sellability,
        # but skip locked (pre-vest) RSU lots -- their cost basis is notional.
        all_lots_pnl: Decimal | None = Decimal("0")
        for ls in ss.active_lots:
            if all_lots_pnl is not None:
                if ls.sellability_status == "LOCKED" and ls.lot.scheme_type == "RSU":
                    continue  # no meaningful P&L pre-vest
                if ls.unrealised_gain_cgt_gbp is not None:
                    all_lots_pnl += ls.unrealised_gain_cgt_gbp
                else:
                    all_lots_pnl = None  # any lot missing a price voids the total
        result.append({
            "security": ss.security,
            "active_lots": ss.active_lots,
            "has_forfeiture_risk": ss.has_forfeiture_risk,
            "has_sip_qualifying_risk": ss.has_sip_qualifying_risk,
            "total_quantity": ss.total_quantity,
            "market_value_gbp": ss.market_value_gbp,
            "market_value_native": ss.market_value_native,
            "market_value_native_currency": ss.market_value_native_currency,
            "total_cost_basis_gbp": ss.total_cost_basis_gbp,
            # All-lots accounting P&L (not a CGT figure; not sellable-only)
            "accounting_pnl_cost_basis_gbp": (
                _q2(all_lots_pnl) if all_lots_pnl is not None else None
            ),
            "est_employment_tax_gbp": ss.est_employment_tax_gbp,
            "est_net_proceeds_gbp": ss.est_net_proceeds_gbp,
            "current_price_gbp": ss.current_price_gbp,
            "price_as_of": ss.price_as_of,
            "sellable_pure_market_value_gbp": ss.sellable_pure_market_value_gbp,
            "espp_plus_pending_market_value_gbp": ss.espp_plus_pending_market_value_gbp,
            "rsu_vesting_market_value_gbp": ss.rsu_vesting_market_value_gbp,
        })
    return result


@router.get("/net-value", response_class=HTMLResponse, include_in_schema=False)
async def net_value(
    request: Request,
    as_of: date_type | None = Query(None),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    if as_of is None or as_of >= date_type.today():
        IbkrPriceService.ingest_all()
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
        as_of=as_of,
    )
    security_daily_changes = _build_security_daily_changes(summary, as_of=as_of)
    position_rows_by_security = _build_portfolio_position_rows(
        summary,
        settings=settings,
    )
    sell_all_metrics = _build_sell_all_metrics(summary)
    capital_stack_snapshot = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
        as_of=as_of,
    )
    deployable_today_gbp = capital_stack_snapshot.get("net_deployable_today_gbp")
    net_vs_deployable_delta_gbp: Decimal | None = None
    if (
        sell_all_metrics.get("net_value_gbp") is not None
        and deployable_today_gbp is not None
    ):
        net_vs_deployable_delta_gbp = _q2(
            Decimal(str(sell_all_metrics["net_value_gbp"]))
            - Decimal(str(deployable_today_gbp))
        )
    nv_securities = _build_nv_securities(summary)
    nv_rows_by_security = {
        sec_id: [_build_nv_row(row) for row in rows]
        for sec_id, rows in position_rows_by_security.items()
    }
    return templates.TemplateResponse(
        request,
        "net_value.html",
        {
            "request": request,
            "sell_all_metrics": sell_all_metrics,
            "deployable_today_gbp": deployable_today_gbp,
            "net_vs_deployable_delta_gbp": net_vs_deployable_delta_gbp,
            "nv_securities": nv_securities,
            "nv_rows_by_security": nv_rows_by_security,
            "settings": settings,
            "fx_stale_after_minutes": settings.fx_stale_after_minutes if settings else 10,
            "security_daily_changes": security_daily_changes,
            "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
            "page_as_of_date": (as_of or date_type.today()).isoformat(),
            "page_as_of_active": as_of is not None,
        },
    )


@router.get("/capital-stack", response_class=HTMLResponse, include_in_schema=False)
async def capital_stack(
    request: Request,
    as_of: date_type | None = Query(None),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    if as_of is None or as_of >= date_type.today():
        IbkrPriceService.ingest_all()
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
        as_of=as_of,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
        as_of=as_of,
    )
    return _html_template_response(
        "capital_stack.html",
        {
            "request": request,
            "stack": stack,
            "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
            "page_as_of_active": as_of is not None,
        },
    )




# ---------------------------------------------------------------------------
# Add security â€” GET + POST /portfolio/add-security
# ---------------------------------------------------------------------------

def _security_conflict_index_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with AppContext.read_session() as sess:
        securities = SecurityRepository(sess).list_all()
    for security in securities:
        rows.append(
            {
                "id": security.id,
                "ticker": (security.ticker or "").strip().upper(),
                "currency": (security.currency or "").strip().upper(),
                "exchange": (security.exchange or "").strip().upper(),
                "isin": (security.isin or "").strip().upper(),
                "name": security.name or "",
            }
        )
    return rows


def _build_add_security_conflict_helper(
    *,
    ticker: str,
    currency: str,
) -> dict[str, object] | None:
    ticker_clean = (ticker or "").strip().upper()
    if not ticker_clean:
        return None
    currency_clean = (currency or "").strip().upper()
    matches = [
        row
        for row in _security_conflict_index_rows()
        if row["ticker"] == ticker_clean
    ]
    if not matches:
        return None
    exact = [row for row in matches if row["currency"] == currency_clean]
    alternate = [row for row in matches if row["currency"] != currency_clean]
    return {
        "ticker": ticker_clean,
        "currency": currency_clean,
        "exact_matches": exact,
        "alternate_currency_matches": alternate,
    }

@router.get(
    "/portfolio/add-security",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def add_security_form(
    request: Request, error: str | None = None
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    return templates.TemplateResponse(
        request,
        "add_security.html",
        {
            "request": request,
            "error": error,
            "existing_security_index": _security_conflict_index_rows(),
        },
    )


@router.post(
    "/portfolio/add-security",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def add_security_submit(
    request: Request,
    ticker: str = Form(...),
    name: str = Form(...),
    currency: str = Form(...),
    isin: str = Form(""),
    exchange: str = Form(""),
    units_precision: int = Form(0),
    dividend_reminder_date: str = Form(""),
    catalog_id: str = Form(""),
    is_manual_override: str = Form("false"),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    _catalog_id = catalog_id.strip() or None
    _is_manual  = is_manual_override.strip().lower() == "true"

    try:
        reminder_date_raw = (dividend_reminder_date or "").strip()
        reminder_date = (
            date_type.fromisoformat(reminder_date_raw)
            if reminder_date_raw
            else None
        )
        PortfolioService.add_security(
            ticker=ticker.strip().upper(),
            name=name.strip(),
            currency=currency.strip().upper(),
            isin=isin.strip() or None,
            exchange=exchange.strip() or None,
            units_precision=units_precision,
            dividend_reminder_date=reminder_date,
            catalog_id=_catalog_id,
            is_manual_override=_is_manual,
        )
    except (ValueError, IntegrityError) as exc:
        conflict_helper = _build_add_security_conflict_helper(
            ticker=ticker,
            currency=currency,
        )
        return templates.TemplateResponse(
            request,
            "add_security.html",
            {
                "request": request,
                "error": str(exc),
                "existing_security_index": _security_conflict_index_rows(),
                "conflict_helper": conflict_helper,
                "prev": {
                    "ticker": ticker,
                    "name": name,
                    "currency": currency,
                    "isin": isin,
                    "exchange": exchange,
                    "units_precision": units_precision,
                    "dividend_reminder_date": dividend_reminder_date,
                    "catalog_id": catalog_id,
                    "is_manual_override": _is_manual,
                },
            },
            status_code=422,
        )

    _clean_ticker = ticker.strip().upper()

    return RedirectResponse(
        f"/?msg=Security+%27{_clean_ticker}%27+added.", status_code=303
    )


# ---------------------------------------------------------------------------
# Add lot â€” GET + POST /portfolio/add-lot
# ---------------------------------------------------------------------------

@router.get(
    "/portfolio/add-lot",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def add_lot_form(
    request: Request,
    security_id: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    today = date_type.today()
    tax_ctx = _tax_context_from_settings(settings, date_type.today())
    rates = get_marginal_rates(tax_ctx) if tax_ctx is not None else None
    espp_combined_rate = rates.combined if rates is not None else None
    rsu_tax_rate = (
        (rates.income_tax + rates.national_insurance)
        if rates is not None
        else None
    )
    summary = PortfolioService.get_portfolio_summary()
    securities = [ss.security for ss in summary.securities]
    currency_options = _price_input_currency_options(securities)
    currency_workflow = _build_add_lot_currency_workflow(
        securities=securities,
        currency_options=currency_options,
    )
    rsu_live_prices = {
        ss.security.id: f"{ss.current_price_gbp:.2f}"
        for ss in summary.securities
        if ss.current_price_gbp is not None
    }
    return templates.TemplateResponse(
        request,
        "add_lot.html",
        {
            "request": request,
            "securities": securities,
            "scheme_types": ADD_LOT_SCHEME_TYPES,
            "price_input_currencies": currency_options,
            "currency_workflow": currency_workflow,
            "preselect": security_id,
            "error": error,
            "default_acquisition_date": today.isoformat(),
            "default_rsu_vesting_date": today.isoformat(),
            "default_price_input_currency": "GBP",
            "settings_available": settings is not None,
            "espp_combined_rate": str(espp_combined_rate) if espp_combined_rate is not None else "",
            "rsu_tax_rate": str(rsu_tax_rate) if rsu_tax_rate is not None else "",
            "rsu_live_prices": rsu_live_prices,
        },
    )


@router.post(
    "/portfolio/add-lot",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def add_lot_submit(
    request: Request,
    security_id: str = Form(...),
    scheme_type: str = Form(...),
    acquisition_date: str = Form(""),
    rsu_vesting_date: str = Form(""),
    quantity: str = Form(...),
    matched_shares_quantity: str = Form(""),
    matched_shares_overridden: str = Form("false"),
    price_input_currency: str = Form("GBP"),
    purchase_price_per_share_gbp: str = Form(""),
    espp_plus_net_price_per_share_gbp: str = Form(""),
    espp_plus_net_price_overridden: str = Form("false"),
    rsu_fmv_at_vest_gbp: str = Form(""),
    espp_fmv_at_purchase_gbp: str = Form(""),
    notes: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None

    def _re_render(err: str) -> HTMLResponse:
        today = date_type.today()
        summary = PortfolioService.get_portfolio_summary()
        securities = [ss.security for ss in summary.securities]
        currency_options = _price_input_currency_options(securities)
        currency_workflow = _build_add_lot_currency_workflow(
            securities=securities,
            currency_options=currency_options,
        )
        rsu_live_prices = {
            ss.security.id: f"{ss.current_price_gbp:.2f}"
            for ss in summary.securities
            if ss.current_price_gbp is not None
        }
        tax_ctx = _tax_context_from_settings(settings, today)
        rates = get_marginal_rates(tax_ctx) if tax_ctx is not None else None
        espp_combined_rate = rates.combined if rates is not None else None
        rsu_tax_rate = (
            (rates.income_tax + rates.national_insurance)
            if rates is not None
            else None
        )
        return templates.TemplateResponse(
            request,
            "add_lot.html",
            {
                "request": request,
                "securities": securities,
                "scheme_types": ADD_LOT_SCHEME_TYPES,
                "price_input_currencies": currency_options,
                "currency_workflow": currency_workflow,
                "preselect": security_id,
                "error": err,
                "prev": {
                    "security_id": security_id,
                    "scheme_type": scheme_type,
                    "acquisition_date": acquisition_date,
                    "rsu_vesting_date": rsu_vesting_date,
                    "quantity": quantity,
                    "matched_shares_quantity": matched_shares_quantity,
                    "matched_shares_overridden": matched_shares_overridden,
                    "price_input_currency": price_input_currency,
                    "purchase_price_per_share_gbp": purchase_price_per_share_gbp,
                    "espp_plus_net_price_per_share_gbp": espp_plus_net_price_per_share_gbp,
                    "espp_plus_net_price_overridden": espp_plus_net_price_overridden,
                    "rsu_fmv_at_vest_gbp": rsu_fmv_at_vest_gbp,
                    "espp_fmv_at_purchase_gbp": espp_fmv_at_purchase_gbp,
                    "notes": notes,
                },
                "default_acquisition_date": today.isoformat(),
                "default_rsu_vesting_date": today.isoformat(),
                "default_price_input_currency": "GBP",
                "settings_available": settings is not None,
                "espp_combined_rate": str(espp_combined_rate) if espp_combined_rate is not None else "",
                "rsu_tax_rate": str(rsu_tax_rate) if rsu_tax_rate is not None else "",
                "rsu_live_prices": rsu_live_prices,
            },
            status_code=422,
        )

    try:
        qty = Decimal(quantity)
    except (ValueError, InvalidOperation) as exc:
        return _re_render(f"Invalid value: {exc}")

    if qty <= 0:
        return _re_render("Quantity must be greater than zero.")

    if scheme_type not in ADD_LOT_SCHEME_TYPES:
        return _re_render(
            f"scheme_type must be one of: {list(ADD_LOT_SCHEME_TYPES)}"
        )

    try:
        normalized_price_input_currency = _normalize_price_input_currency(
            price_input_currency
        )
    except ValueError as exc:
        return _re_render(str(exc))

    try:
        fx_rate_to_gbp, fx_rate_source = _resolve_input_fx_to_gbp(
            normalized_price_input_currency
        )
    except ValueError as exc:
        return _re_render(str(exc))

    def _to_gbp(amount: Decimal) -> Decimal:
        return _convert_input_price_to_gbp(amount, fx_rate_to_gbp)

    if scheme_type == "RSU":
        raw_rsu_vesting_date = rsu_vesting_date.strip()
        if not raw_rsu_vesting_date:
            return _re_render("RSU vesting date must be provided.")
        try:
            acq_date = date_type.fromisoformat(raw_rsu_vesting_date)
        except ValueError as exc:
            return _re_render(f"Invalid RSU vesting date value: {exc}")
    else:
        try:
            acq_date = date_type.fromisoformat(acquisition_date)
        except ValueError as exc:
            return _re_render(f"Invalid acquisition date value: {exc}")

    try:
        if scheme_type == "RSU":
            with AppContext.read_session() as sess:
                latest_price = PriceRepository(sess).get_latest(security_id)

            rsu_fmv: Decimal | None = None
            if latest_price is not None and latest_price.close_price_gbp is not None:
                try:
                    rsu_fmv = Decimal(latest_price.close_price_gbp)
                except (ValueError, InvalidOperation):
                    rsu_fmv = None

            if rsu_fmv is None or rsu_fmv <= 0:
                return _re_render(
                    "RSU taxable value is pending latest price (currently GBP 0.00). "
                    "Refresh prices and try again."
                )

            true_cost = _derive_true_cost_per_share(
                "RSU",
                quantity=qty,
                acquisition_date=acq_date,
                rsu_fmv_at_vest_gbp=rsu_fmv,
                settings=settings,
            )
            if true_cost is None:
                return _re_render(
                    "RSU tax cost requires tax settings. Configure income/tax settings first."
                )

            PortfolioService.add_lot(
                security_id=security_id,
                scheme_type="RSU",
                acquisition_date=acq_date,
                quantity=qty,
                acquisition_price_gbp=rsu_fmv,
                true_cost_per_share_gbp=true_cost,
                fmv_at_acquisition_gbp=rsu_fmv,
                import_source="ui_rsu_auto_estimated_live_price",
                notes=notes.strip() or None,
            )

        elif scheme_type in ("ESPP", "BROKERAGE", "ISA"):
            try:
                purchase_price_input = Decimal(purchase_price_per_share_gbp)
            except InvalidOperation as exc:
                return _re_render(f"Invalid purchase price value: {exc}")
            if purchase_price_input <= 0:
                return _re_render("Purchase price per share must be greater than zero.")
            purchase_price = _to_gbp(purchase_price_input)

            espp_fmv_input: Decimal | None = None
            if espp_fmv_at_purchase_gbp.strip():
                try:
                    espp_fmv_input = Decimal(espp_fmv_at_purchase_gbp)
                except InvalidOperation as exc:
                    return _re_render(f"Invalid ESPP FMV at purchase value: {exc}")
                if espp_fmv_input <= 0:
                    return _re_render("ESPP FMV at purchase must be greater than zero.")
            espp_fmv = _to_gbp(espp_fmv_input) if espp_fmv_input is not None else None

            derived_true_cost = _derive_true_cost_per_share(
                scheme_type,
                quantity=qty,
                acquisition_date=acq_date,
                purchase_price_per_share_gbp=purchase_price,
                espp_fmv_at_purchase_gbp=espp_fmv,
                settings=settings,
            )
            true_cost = derived_true_cost or purchase_price
            broker_currency = (
                normalized_price_input_currency
                if scheme_type in {"BROKERAGE", "ISA"}
                else None
            )

            PortfolioService.add_lot(
                security_id=security_id,
                scheme_type=scheme_type,
                acquisition_date=acq_date,
                quantity=qty,
                acquisition_price_gbp=purchase_price,
                true_cost_per_share_gbp=true_cost,
                fmv_at_acquisition_gbp=espp_fmv,
                acquisition_price_original_ccy=purchase_price_input,
                original_currency=normalized_price_input_currency,
                broker_currency=broker_currency,
                fx_rate_at_acquisition=fx_rate_to_gbp,
                fx_rate_source=fx_rate_source,
                notes=notes.strip() or None,
            )

        elif scheme_type == "ESPP_PLUS":
            try:
                purchase_price_input = Decimal(purchase_price_per_share_gbp)
            except InvalidOperation as exc:
                return _re_render(f"Invalid purchase price value: {exc}")
            if purchase_price_input <= 0:
                return _re_render("Purchase price per share must be greater than zero.")
            purchase_price = _to_gbp(purchase_price_input)

            try:
                matched_qty = Decimal(matched_shares_quantity or "0")
            except InvalidOperation as exc:
                return _re_render(f"Invalid matched shares quantity: {exc}")
            if matched_qty < 0:
                return _re_render("Matched shares quantity cannot be negative.")

            net_overridden = (
                espp_plus_net_price_overridden.strip().lower()
                in ("true", "1", "yes", "on")
            )
            net_override: Decimal | None = None
            if net_overridden:
                if not espp_plus_net_price_per_share_gbp.strip():
                    return _re_render("Net price override is enabled but no net price was provided.")
                try:
                    net_override = Decimal(espp_plus_net_price_per_share_gbp)
                except InvalidOperation as exc:
                    return _re_render(f"Invalid ESPP+ net price value: {exc}")
                if net_override <= 0:
                    return _re_render("ESPP+ net price must be greater than zero.")
                net_override = _to_gbp(net_override)

            espp_fmv_input: Decimal | None = None
            if espp_fmv_at_purchase_gbp.strip():
                try:
                    espp_fmv_input = Decimal(espp_fmv_at_purchase_gbp)
                except InvalidOperation as exc:
                    return _re_render(f"Invalid ESPP FMV at purchase value: {exc}")
                if espp_fmv_input <= 0:
                    return _re_render("ESPP FMV at purchase must be greater than zero.")
            espp_fmv = _to_gbp(espp_fmv_input) if espp_fmv_input is not None else None

            if net_override is not None:
                employee_true_cost = net_override
                employee_import_source = "ui_espp_plus_employee_override"
            else:
                # ESPP+ employee true cost is locked at acquisition time using
                # the then-current tax settings.
                derived_true_cost = _derive_true_cost_per_share(
                    "ESPP_PLUS",
                    quantity=qty,
                    acquisition_date=acq_date,
                    purchase_price_per_share_gbp=purchase_price,
                    espp_fmv_at_purchase_gbp=espp_fmv,
                    settings=settings,
                )
                employee_true_cost = derived_true_cost or purchase_price
                employee_import_source = "ui_espp_plus_employee"
            employee_award_fmv = espp_fmv if espp_fmv is not None else purchase_price

            PortfolioService.add_espp_plus_lot_pair(
                security_id=security_id,
                acquisition_date=acq_date,
                employee_quantity=qty,
                employee_acquisition_price_gbp=purchase_price,
                employee_true_cost_per_share_gbp=employee_true_cost,
                employee_fmv_at_acquisition_gbp=employee_award_fmv,
                matched_quantity=matched_qty,
                acquisition_price_original_ccy=purchase_price_input,
                original_currency=normalized_price_input_currency,
                broker_currency=normalized_price_input_currency,
                fx_rate_at_acquisition=fx_rate_to_gbp,
                fx_rate_source=fx_rate_source,
                employee_import_source=employee_import_source,
                notes=notes.strip() or None,
                forfeiture_period_end=acq_date + timedelta(days=183),
            )
    except IntegrityError:
        return _re_render("Could not save lot due to a data integrity constraint.")
    except ValueError as exc:
        return _re_render(str(exc))

    return RedirectResponse("/?msg=Lot+added+successfully.", status_code=303)
# ---------------------------------------------------------------------------
# Edit lot - GET + POST /portfolio/edit-lot
# ---------------------------------------------------------------------------

def _render_edit_lot_page(
    request: Request,
    *,
    lot_id: str,
    error: str | None = None,
    prev: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    with AppContext.read_session() as sess:
        lot = LotRepository(sess).get_by_id(lot_id)
        if lot is None:
            return _html_template_response(
                "locked.html",
                {"request": request},
                status_code=404,
            )
        security = SecurityRepository(sess).get_by_id(lot.security_id)

    disposed_qty = Decimal(lot.quantity) - Decimal(lot.quantity_remaining)
    default_prev = {
        "acquisition_date": lot.acquisition_date.isoformat(),
        "quantity": lot.quantity,
        "acquisition_price_gbp": lot.acquisition_price_gbp,
        "true_cost_per_share_gbp": lot.true_cost_per_share_gbp,
        "tax_year": lot.tax_year,
        "fmv_at_acquisition_gbp": lot.fmv_at_acquisition_gbp or "",
        "broker_currency": (
            lot.broker_currency
            or _suggest_broker_currency(
                source_broker_currency=lot.broker_currency,
                source_original_currency=lot.original_currency,
                security_currency=security.currency if security is not None else None,
            )
        ),
        "notes": lot.notes or "",
    }
    resolved_prev = prev or default_prev
    broker_currency_options = _broker_currency_options(
        resolved_prev.get("broker_currency"),
        lot.broker_currency,
        lot.original_currency,
        security.currency if security is not None else None,
    )
    context = {
        "request": request,
        "lot": lot,
        "security": security,
        "disposed_qty": disposed_qty,
        "error": error,
        "prev": resolved_prev,
        "broker_currency_options": broker_currency_options,
    }
    return _html_template_response("edit_lot.html", context, status_code=status_code)


@router.get(
    "/portfolio/edit-lot",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def edit_lot_form(
    request: Request,
    lot_id: str,
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    return _render_edit_lot_page(request, lot_id=lot_id)


@router.post(
    "/portfolio/edit-lot",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def edit_lot_submit(
    request: Request,
    lot_id: str = Form(...),
    acquisition_date: str = Form(...),
    quantity: str = Form(...),
    acquisition_price_gbp: str = Form(...),
    true_cost_per_share_gbp: str = Form(...),
    tax_year: str = Form(...),
    fmv_at_acquisition_gbp: str = Form(""),
    broker_currency: str = Form(""),
    notes: str = Form(""),
    confirm_changes: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    prev = {
        "acquisition_date": acquisition_date,
        "quantity": quantity,
        "acquisition_price_gbp": acquisition_price_gbp,
        "true_cost_per_share_gbp": true_cost_per_share_gbp,
        "tax_year": tax_year,
        "fmv_at_acquisition_gbp": fmv_at_acquisition_gbp,
        "broker_currency": broker_currency,
        "notes": notes,
    }

    if confirm_changes.lower() not in ("on", "true", "1", "yes"):
        return _render_edit_lot_page(
            request,
            lot_id=lot_id,
            error="Confirm the change summary before saving.",
            prev=prev,
            status_code=422,
        )

    try:
        acq_date = date_type.fromisoformat(acquisition_date)
        qty = Decimal(quantity)
        acq_price = Decimal(acquisition_price_gbp)
        true_cost = Decimal(true_cost_per_share_gbp)
        fmv = (
            Decimal(fmv_at_acquisition_gbp.strip())
            if fmv_at_acquisition_gbp.strip()
            else None
        )
    except (ValueError, InvalidOperation) as exc:
        return _render_edit_lot_page(
            request,
            lot_id=lot_id,
            error=f"Invalid value: {exc}",
            prev=prev,
            status_code=422,
        )

    normalized_broker_currency: str | None = None
    if broker_currency.strip():
        try:
            normalized_broker_currency = _normalize_iso_currency(
                broker_currency,
                field_name="broker_currency",
            )
        except ValueError as exc:
            return _render_edit_lot_page(
                request,
                lot_id=lot_id,
                error=str(exc),
                prev=prev,
                status_code=422,
            )

    try:
        _, audit_id = PortfolioService.edit_lot(
            lot_id=lot_id,
            acquisition_date=acq_date,
            quantity=qty,
            acquisition_price_gbp=acq_price,
            true_cost_per_share_gbp=true_cost,
            tax_year=tax_year.strip(),
            fmv_at_acquisition_gbp=fmv,
            broker_currency=normalized_broker_currency,
            notes=notes,
        )
    except (KeyError, ValueError) as exc:
        return _render_edit_lot_page(
            request,
            lot_id=lot_id,
            error=str(exc),
            prev=prev,
            status_code=422,
        )

    if audit_id:
        msg = f"Lot+updated+(audit+{audit_id})."
    else:
        msg = "Lot+unchanged+(no+edits+applied)."
    return RedirectResponse(f"/?msg={msg}", status_code=303)


# ---------------------------------------------------------------------------
# Transfer lot - GET + POST /portfolio/transfer-lot
# ---------------------------------------------------------------------------

def _load_transfer_candidates() -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    today = date_type.today()
    with AppContext.read_session() as sess:
        sec_repo = SecurityRepository(sess)
        lot_repo = LotRepository(sess)
        for sec in sec_repo.list_all():
            lots = lot_repo.get_all_lots_for_security(sec.id)

            # ESPP is a scheme-level FIFO transfer source (not per-lot in UI).
            espp_lots = [
                lot
                for lot in lots
                if lot.scheme_type == "ESPP"
                and Decimal(lot.quantity_remaining) > Decimal("0")
            ]
            if espp_lots:
                fifo_espp_lots = sorted(
                    espp_lots,
                    key=lambda lot: (lot.acquisition_date, lot.id),
                )
                fifo_head = fifo_espp_lots[0]
                total_qty = sum(
                    (Decimal(lot.quantity_remaining) for lot in fifo_espp_lots),
                    Decimal("0"),
                )
                whole_qty = total_qty.to_integral_value(rounding=ROUND_FLOOR)
                if whole_qty > Decimal("0"):
                    suggested_broker_currency = _suggest_broker_currency(
                        source_broker_currency=fifo_head.broker_currency,
                        source_original_currency=fifo_head.original_currency,
                        security_currency=sec.currency,
                    )
                    candidates.append(
                        {
                            "lot_id": fifo_head.id,
                            "security_id": sec.id,
                            "label": (
                                f"{sec.ticker} | ESPP (FIFO pool) | "
                                f"oldest acq {fifo_head.acquisition_date} | "
                                f"whole qty {whole_qty} | raw qty {total_qty} | "
                                f"broker ccy {suggested_broker_currency}"
                            ),
                            "scheme_type": "ESPP",
                            "quantity_remaining": str(total_qty),
                            "default_transfer_quantity": str(whole_qty),
                            "whole_quantity_available": str(whole_qty),
                            "acquisition_date": fifo_head.acquisition_date.isoformat(),
                            "broker_currency": suggested_broker_currency,
                        }
                    )

            for lot in lots:
                if lot.scheme_type not in ("RSU", "ESPP_PLUS"):
                    continue
                if Decimal(lot.quantity_remaining) <= Decimal("0"):
                    continue
                # RSU transfers are allowed only after vest date.
                if lot.scheme_type == "RSU" and lot.acquisition_date > today:
                    continue
                # ESPP+ matched lots are forfeiture-linked and not direct transfer inputs.
                if lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None:
                    continue
                suggested_broker_currency = _suggest_broker_currency(
                    source_broker_currency=lot.broker_currency,
                    source_original_currency=lot.original_currency,
                    security_currency=sec.currency,
                )
                candidates.append(
                    {
                        "lot_id": lot.id,
                        "security_id": sec.id,
                        "label": (
                            f"{sec.ticker} | {lot.scheme_type} | "
                            f"acq {lot.acquisition_date} | "
                            f"qty {lot.quantity_remaining} | "
                            f"broker ccy {suggested_broker_currency}"
                        ),
                        "scheme_type": lot.scheme_type,
                        "quantity_remaining": lot.quantity_remaining,
                        "default_transfer_quantity": lot.quantity_remaining,
                        "whole_quantity_available": lot.quantity_remaining,
                        "acquisition_date": lot.acquisition_date.isoformat(),
                        "broker_currency": suggested_broker_currency,
                    }
                )
    return candidates


def _render_transfer_lot_page(
    request: Request,
    *,
    settings: AppSettings | None = None,
    error: str | None = None,
    preselect_lot_id: str | None = None,
    quantity: str = "",
    broker_currency: str = "",
    notes: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    candidates = _load_transfer_candidates()
    resolved_preselect_lot_id = preselect_lot_id or ""
    if resolved_preselect_lot_id:
        candidate_ids = {c["lot_id"] for c in candidates}
        if resolved_preselect_lot_id not in candidate_ids:
            with AppContext.read_session() as sess:
                lot = LotRepository(sess).get_by_id(resolved_preselect_lot_id)
                if lot is not None and lot.scheme_type == "ESPP":
                    for candidate in candidates:
                        if (
                            candidate.get("scheme_type") == "ESPP"
                            and candidate.get("security_id") == lot.security_id
                        ):
                            resolved_preselect_lot_id = candidate["lot_id"]
                            break
    resolved_broker_currency = broker_currency.strip().upper()
    if not resolved_broker_currency:
        selected_candidate = None
        if resolved_preselect_lot_id:
            selected_candidate = next(
                (
                    c for c in candidates
                    if c.get("lot_id") == resolved_preselect_lot_id
                ),
                None,
            )
        if selected_candidate is None and candidates:
            selected_candidate = candidates[0]
        resolved_broker_currency = (
            selected_candidate.get("broker_currency", "GBP")
            if selected_candidate is not None
            else "GBP"
        )
    broker_currency_options = _broker_currency_options(
        resolved_broker_currency,
        *[c.get("broker_currency") for c in candidates],
    )
    transfer_impact = _build_transfer_impact_preview(
        lot_id=resolved_preselect_lot_id,
        quantity_raw=quantity,
        candidates=candidates,
        settings=settings,
    )
    return _html_template_response(
        "transfer_lot.html",
        {
            "request": request,
            "candidates": candidates,
            "error": error,
            "preselect_lot_id": resolved_preselect_lot_id,
            "quantity": quantity,
            "broker_currency": resolved_broker_currency,
            "broker_currency_options": broker_currency_options,
            "notes": notes,
            "transfer_impact": transfer_impact,
        },
        status_code=status_code,
    )


@router.get(
    "/portfolio/transfer-lot",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def transfer_lot_form(
    request: Request,
    lot_id: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    return _render_transfer_lot_page(
        request,
        settings=settings,
        error=error,
        preselect_lot_id=lot_id,
    )


@router.post(
    "/portfolio/transfer-lot",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def transfer_lot_submit(
    request: Request,
    lot_id: str = Form(...),
    quantity: str = Form(""),
    broker_currency: str = Form(""),
    notes: str = Form(""),
    confirm_transfer: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None

    if confirm_transfer.lower() not in ("on", "true", "1", "yes"):
        return _render_transfer_lot_page(
            request,
            settings=settings,
            error="Confirm the transfer summary before continuing.",
            preselect_lot_id=lot_id,
            quantity=quantity,
            broker_currency=broker_currency,
            notes=notes,
            status_code=422,
        )

    transfer_qty: Decimal | None = None
    if quantity.strip():
        try:
            transfer_qty = Decimal(quantity)
        except InvalidOperation as exc:
            return _render_transfer_lot_page(
                request,
                settings=settings,
                error=f"Invalid transfer quantity: {exc}",
                preselect_lot_id=lot_id,
                quantity=quantity,
                broker_currency=broker_currency,
                notes=notes,
                status_code=422,
            )

    normalized_broker_currency: str | None = None
    if broker_currency.strip():
        try:
            normalized_broker_currency = _normalize_iso_currency(
                broker_currency,
                field_name="broker_currency",
            )
        except ValueError as exc:
            return _render_transfer_lot_page(
                request,
                settings=settings,
                error=str(exc),
                preselect_lot_id=lot_id,
                quantity=quantity,
                broker_currency=broker_currency,
                notes=notes,
                status_code=422,
            )

    try:
        _, audit_id = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot_id,
            notes=notes,
            settings=settings,
            quantity=transfer_qty,
            destination_broker_currency=normalized_broker_currency,
        )
    except (KeyError, ValueError) as exc:
        return _render_transfer_lot_page(
            request,
            settings=settings,
            error=str(exc),
            preselect_lot_id=lot_id,
            quantity=quantity,
            broker_currency=broker_currency,
            notes=notes,
            status_code=422,
        )

    return RedirectResponse(
        f"/?msg=Lot+transferred+(audit+{audit_id}).",
        status_code=303,
    )

# ---------------------------------------------------------------------------
# Simulate disposal â€” GET + POST /simulate
# ---------------------------------------------------------------------------

@router.get("/simulate", response_class=HTMLResponse, include_in_schema=False)
async def simulate_form(
    request: Request,
    security_id: str | None = None,
    quantity: str | None = None,
    price_per_share_gbp: str | None = None,
    sell_plan_id: str | None = None,
    tranche_id: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    summary = PortfolioService.get_portfolio_summary()
    securities = _simulate_security_context(summary)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    prefill_price = ""
    prefill_qty = ""
    raw_prefill_qty = (quantity or "").strip()
    if raw_prefill_qty:
        try:
            prefill_dec = Decimal(raw_prefill_qty)
            if prefill_dec > Decimal("0"):
                prefill_qty = str(
                    int(prefill_dec.to_integral_value(rounding=ROUND_FLOOR))
                )
        except InvalidOperation:
            prefill_qty = ""
    if security_id:
        selected = next((s for s in securities if s["id"] == security_id), None)
        if selected:
            prefill_price = (price_per_share_gbp or "").strip() or selected["latest_price_gbp"]
    linked_sell_plan = None
    if sell_plan_id or tranche_id:
        linked_sell_plan = {
            "plan_id": (sell_plan_id or "").strip(),
            "tranche_id": (tranche_id or "").strip(),
        }

    return _html_template_response(
        "simulate.html",
        {
            "request": request,
            "securities": securities,
            "scheme_types": SIMULATE_SCHEME_TYPES,
            "result": None,
            "error": error,
            "prev": {
                "security_id": security_id or "",
                "price_per_share_gbp": prefill_price,
                "broker_fees_gbp": "",
                "quantity": prefill_qty,
                "scheme_type": "",
            },
            "simulate_meta": {s["id"]: s for s in securities},
            "exit_summary": None,
            "employment_tax_status": {
                "applicable": False,
                "estimate_available": True,
                "ack_required": False,
                "scheme_types": (),
            },
            "settings": settings,
            "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
            "linked_sell_plan": linked_sell_plan,
        },
    )


@router.post("/simulate", response_class=HTMLResponse, include_in_schema=False)
async def simulate_submit(
    request: Request,
    security_id: str = Form(...),
    quantity: str = Form(...),
    price_per_share_gbp: str = Form(""),
    broker_fees_gbp: str = Form(""),
    scheme_type: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    summary = PortfolioService.get_portfolio_summary()
    securities = _simulate_security_context(summary)
    simulate_meta = {s["id"]: s for s in securities}
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    tax_inputs_incomplete = _tax_inputs_incomplete(settings)

    def _render_simulate(context: dict, *, status_code: int = 200) -> HTMLResponse:
        base_context = {
            "request": request,
            "securities": securities,
            "scheme_types": SIMULATE_SCHEME_TYPES,
            "simulate_meta": simulate_meta,
            "settings": settings,
            "tax_inputs_incomplete": tax_inputs_incomplete,
            "exit_summary": None,
            "employment_tax_status": {
                "applicable": False,
                "estimate_available": True,
                "ack_required": False,
                "scheme_types": (),
            },
        }
        base_context.update(context)
        return _html_template_response(
            "simulate.html",
            base_context,
            status_code=status_code,
        )

    selected = simulate_meta.get(security_id)
    if selected is None:
        return _render_simulate(
            {
                "result": None,
                "error": "Unknown security selected.",
            },
            status_code=422,
        )

    try:
        qty = Decimal(quantity)
    except InvalidOperation as exc:
        return _render_simulate(
            {
                "result": None,
                "error": f"Invalid number: {exc}",
            },
            status_code=422,
        )

    if qty <= 0:
        return _render_simulate(
            {
                "result": None,
                "error": "Quantity must be greater than zero.",
            },
            status_code=422,
        )
    if qty != qty.to_integral_value(rounding=ROUND_FLOOR):
        return _render_simulate(
            {
                "result": None,
                "error": "Quantity must be a whole number of shares.",
                "prev": {
                    "security_id": security_id,
                    "quantity": quantity,
                    "price_per_share_gbp": price_per_share_gbp,
                    "broker_fees_gbp": broker_fees_gbp,
                    "scheme_type": scheme_type,
                },
            },
            status_code=422,
        )
    qty = qty.to_integral_value(rounding=ROUND_FLOOR)

    by_scheme = selected.get("available_by_scheme", {})
    available_qty = (
        Decimal(by_scheme.get(scheme_type, "0"))
        if scheme_type
        else Decimal(selected.get("available_quantity", "0"))
    )
    if qty > available_qty:
        return _render_simulate(
            {
                "result": None,
                "error": (
                    f"Requested quantity ({qty}) cannot exceed available quantity "
                    f"({available_qty}) for this selection."
                ),
                "prev": {
                    "security_id": security_id,
                    "quantity": quantity,
                    "price_per_share_gbp": price_per_share_gbp,
                    "broker_fees_gbp": broker_fees_gbp,
                    "scheme_type": scheme_type,
                },
            },
            status_code=422,
        )

    resolved_price = price_per_share_gbp.strip() or selected.get("latest_price_gbp", "")
    try:
        price = Decimal(resolved_price)
    except InvalidOperation as exc:
        return _render_simulate(
            {
                "result": None,
                "error": f"Invalid disposal price: {exc}",
                "prev": {
                    "security_id": security_id,
                    "quantity": quantity,
                    "price_per_share_gbp": resolved_price,
                    "broker_fees_gbp": broker_fees_gbp,
                    "scheme_type": scheme_type,
                },
            },
            status_code=422,
        )
    try:
        fees = Decimal(broker_fees_gbp.strip() or "0")
    except InvalidOperation as exc:
        return _render_simulate(
            {
                "result": None,
                "error": f"Invalid broker fees value: {exc}",
                "prev": {
                    "security_id": security_id,
                    "quantity": quantity,
                    "price_per_share_gbp": resolved_price,
                    "broker_fees_gbp": broker_fees_gbp,
                    "scheme_type": scheme_type,
                },
            },
            status_code=422,
        )
    if fees < 0:
        return _render_simulate(
            {
                "result": None,
                "error": "Broker fees cannot be negative.",
                "prev": {
                    "security_id": security_id,
                    "quantity": quantity,
                    "price_per_share_gbp": resolved_price,
                    "broker_fees_gbp": broker_fees_gbp,
                    "scheme_type": scheme_type,
                },
            },
            status_code=422,
        )
    fees_str = f"{fees:.2f}" if fees != 0 else ""

    try:
        result = PortfolioService.simulate_disposal(
            security_id=security_id,
            quantity=qty,
            price_per_share_gbp=price,
            scheme_type=scheme_type or None,
            settings=settings,
            broker_fees_gbp=fees,
            use_live_true_cost=False,
        )
    except ValueError as exc:
        return _render_simulate(
            {
                "result": None,
                "error": str(exc),
            },
            status_code=422,
        )
    employment_tax_status = _simulate_employment_tax_status(result)

    return _render_simulate(
        {
            "result": result,
            "exit_summary": _compute_exit_summary(
                proceeds_cash_gbp=result.total_proceeds_gbp,
                true_cost_gbp=result.total_true_cost_gbp,
                employment_tax_due_gbp=result.total_sip_employment_tax_gbp,
                broker_fees_gbp=fees,
            ),
            "employment_tax_status": employment_tax_status,
            "error": None,
            "prev": {
                "security_id": security_id,
                "quantity": quantity,
                "price_per_share_gbp": resolved_price,
                "broker_fees_gbp": fees_str,
                "scheme_type": scheme_type,
            },
        },
    )


# ---------------------------------------------------------------------------
# Commit disposal â€” POST /simulate/commit
# ---------------------------------------------------------------------------

@router.post(
    "/simulate/commit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def commit_disposal(
    request: Request,
    security_id: str = Form(...),
    quantity: str = Form(...),
    price_per_share_gbp: str = Form(...),
    transaction_date: str = Form(...),
    scheme_type: str = Form(""),
    broker_fees_gbp: str = Form(""),
    broker_reference: str = Form(""),
    external_id: str = Form(""),
    notes: str = Form(""),
    employment_tax_acknowledged: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    from datetime import date as date_type

    try:
        qty = Decimal(quantity)
        price = Decimal(price_per_share_gbp)
        tx_date = date_type.fromisoformat(transaction_date)
        fees: Decimal | None = Decimal(broker_fees_gbp) if broker_fees_gbp.strip() else None
    except (ValueError, InvalidOperation) as exc:
        return RedirectResponse(
            f"/simulate?error=Invalid+value%3A+{exc}", status_code=303
        )

    try:
        db_path = _state.get_db_path()
        settings = AppSettings.load(db_path) if db_path else None
        preflight = PortfolioService.simulate_disposal(
            security_id=security_id,
            quantity=qty,
            price_per_share_gbp=price,
            scheme_type=scheme_type or None,
            as_of_date=tx_date,
            settings=settings,
            broker_fees_gbp=fees,
            use_live_true_cost=False,
        )
        employment_tax_status = _simulate_employment_tax_status(preflight)
        ack_confirmed = employment_tax_acknowledged.lower() in (
            "on",
            "true",
            "1",
            "yes",
        )
        if employment_tax_status["ack_required"] and not ack_confirmed:
            return RedirectResponse(
                (
                    "/simulate?error=Employment-tax+estimate+is+unavailable.+"
                    "Confirm+acknowledgement+before+commit."
                ),
                status_code=303,
            )
        PortfolioService.commit_disposal(
            security_id=security_id,
            quantity=qty,
            price_per_share_gbp=price,
            transaction_date=tx_date,
            scheme_type=scheme_type or None,
            settings=settings,
            broker_fees_gbp=fees,
            broker_reference=broker_reference.strip() or None,
            external_id=external_id.strip() or None,
            notes=notes.strip() or None,
            use_live_true_cost=False,
        )
    except IntegrityError:
        return RedirectResponse(
            "/simulate?error=A+disposal+with+this+external+ID+already+exists.",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/simulate?error={str(exc).replace(' ', '+')}", status_code=303
        )

    return RedirectResponse("/?msg=Disposal+committed+successfully.", status_code=303)


# ---------------------------------------------------------------------------
# CGT report â€” GET /cgt
# ---------------------------------------------------------------------------

def _tax_year_nav_context(
    tax_years: list[str],
    active_year: str,
) -> dict[str, str | None | int]:
    idx = tax_years.index(active_year)
    prev_year = tax_years[idx - 1] if idx > 0 else None
    next_year = tax_years[idx + 1] if idx < len(tax_years) - 1 else None
    return {
        "active_year_index": idx,
        "previous_tax_year": prev_year,
        "next_tax_year": next_year,
    }


def _cgt_assumption_badges(
    *,
    include_tax_due: bool,
    settings: AppSettings | None,
) -> list[dict[str, str]]:
    badges: list[dict[str, str]] = []
    if not include_tax_due:
        badges.append(
            {
                "label": "Realised-only mode",
                "style": "neutral",
                "detail": "CGT due estimate is disabled; table shows realised disposal outcomes only.",
            }
        )
        return badges

    if settings is None:
        badges.append(
            {
                "label": "Settings missing",
                "style": "warning",
                "detail": "CGT due cannot be estimated until income settings are saved in Settings.",
            }
        )
        return badges

    if _tax_inputs_incomplete(settings):
        badges.append(
            {
                "label": "Estimate constrained",
                "style": "warning",
                "detail": "Income inputs are incomplete/zero; CGT due may materially understate reality.",
            }
        )
        return badges

    badges.append(
        {
            "label": "Configured estimate",
            "style": "sellable",
            "detail": "CGT due uses saved income, pension, and student-loan settings.",
        }
    )
    return badges


@router.get("/cgt", response_class=HTMLResponse, include_in_schema=False)
async def cgt_report(
    request: Request,
    tax_year: str | None = None,
    include_tax_due: bool = False,
    prior_year_losses: str = "0",
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    tax_years = available_tax_years()
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    active_year = tax_year or (settings.default_tax_year if settings else tax_years[-1])

    if active_year not in tax_years:
        active_year = tax_years[-1]
    nav = _tax_year_nav_context(tax_years, active_year)

    try:
        losses = Decimal(prior_year_losses)
    except InvalidOperation:
        losses = Decimal("0")

    tax_context: TaxContext | None = None
    if include_tax_due and settings:
        tax_context = TaxContext(
            tax_year=active_year,
            gross_employment_income=settings.default_gross_income,
            pension_sacrifice=settings.default_pension_sacrifice,
            other_income=settings.default_other_income,
            student_loan_plan=settings.default_student_loan_plan,
        )

    report = ReportService.cgt_summary(
        active_year,
        tax_context=tax_context,
        prior_year_losses=losses,
    )
    assumption_badges = _cgt_assumption_badges(
        include_tax_due=include_tax_due,
        settings=settings,
    )

    return templates.TemplateResponse(
        request,
        "cgt_report.html",
        {
            "request": request,
            "report": report,
            "tax_years": tax_years,
            "active_year": active_year,
            "include_tax_due": include_tax_due,
            "prior_year_losses": str(losses),
            "settings": settings,
            "assumption_badges": assumption_badges,
            "active_year_index": nav["active_year_index"],
            "previous_tax_year": nav["previous_tax_year"],
            "next_tax_year": nav["next_tax_year"],
        },
    )


# ---------------------------------------------------------------------------
# Economic gain report â€” GET /economic-gain
# ---------------------------------------------------------------------------

@router.get(
    "/economic-gain", response_class=HTMLResponse, include_in_schema=False
)
async def economic_gain_report(
    request: Request, tax_year: str | None = None
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    tax_years = available_tax_years()
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    active_year = tax_year or (settings.default_tax_year if settings else tax_years[-1])
    if active_year not in tax_years:
        active_year = tax_years[-1]
    nav = _tax_year_nav_context(tax_years, active_year)

    report = ReportService.economic_gain_summary(active_year)
    cgt_report = ReportService.cgt_summary(active_year)
    net_delta_vs_cgt = report.net_economic_gain_gbp - cgt_report.net_gain_gbp

    return templates.TemplateResponse(
        request,
        "economic_gain.html",
        {
            "request": request,
            "report": report,
            "tax_years": tax_years,
            "active_year": active_year,
            "cgt_report": cgt_report,
            "net_delta_vs_cgt_gbp": net_delta_vs_cgt,
            "active_year_index": nav["active_year_index"],
            "previous_tax_year": nav["previous_tax_year"],
            "next_tax_year": nav["next_tax_year"],
        },
    )


# ---------------------------------------------------------------------------
# Audit log â€” GET /audit
# ---------------------------------------------------------------------------

def _parse_filter_date(raw: str | None) -> date_type | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date_type.fromisoformat(text)
    except ValueError:
        return None


def _audit_json_to_dict(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _audit_structured_diff_rows(
    *,
    old_values_json: str | None,
    new_values_json: str | None,
) -> list[dict[str, str]]:
    old_map = _audit_json_to_dict(old_values_json)
    new_map = _audit_json_to_dict(new_values_json)
    keys = sorted(set(old_map.keys()) | set(new_map.keys()))
    rows: list[dict[str, str]] = []
    for key in keys:
        old_val = old_map.get(key)
        new_val = new_map.get(key)
        if old_val == new_val:
            continue
        if key not in old_map:
            change_type = "added"
        elif key not in new_map:
            change_type = "removed"
        else:
            change_type = "changed"
        rows.append(
            {
                "field": str(key),
                "before": json.dumps(old_val, default=str) if key in old_map else "",
                "after": json.dumps(new_val, default=str) if key in new_map else "",
                "change_type": change_type,
            }
        )
    return rows


def _audit_entry_view(entry) -> dict[str, object]:
    return {
        "id": entry.id,
        "changed_at": entry.changed_at,
        "table_name": entry.table_name,
        "action": entry.action,
        "record_id": entry.record_id,
        "old_values_json": entry.old_values_json,
        "new_values_json": entry.new_values_json,
        "notes": entry.notes,
        "diff_rows": _audit_structured_diff_rows(
            old_values_json=entry.old_values_json,
            new_values_json=entry.new_values_json,
        ),
    }


@router.get("/audit", response_class=HTMLResponse, include_in_schema=False)
async def audit_log(
    request: Request,
    table_name: str | None = None,
    record_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    active_table = (table_name or "").strip()
    active_record_id = (record_id or "").strip()
    active_date_from = (date_from or "").strip()
    active_date_to = (date_to or "").strip()

    parsed_from = _parse_filter_date(active_date_from)
    parsed_to = _parse_filter_date(active_date_to)
    filter_error: str | None = None
    if active_date_from and parsed_from is None:
        filter_error = "Invalid start date filter; expected YYYY-MM-DD."
    elif active_date_to and parsed_to is None:
        filter_error = "Invalid end date filter; expected YYYY-MM-DD."
    elif parsed_from and parsed_to and parsed_from > parsed_to:
        filter_error = "Start date cannot be after end date."

    since = datetime.combine(parsed_from, time.min) if parsed_from else None
    until = datetime.combine(parsed_to, time.max) if parsed_to else None
    if filter_error:
        since = None
        until = None

    raw_entries = ReportService.audit_log(
        table_name=active_table or None,
        record_id=active_record_id or None,
        since=since,
        until=until,
    )
    entries = [_audit_entry_view(entry) for entry in raw_entries]
    tables = [
        "lots",
        "securities",
        "transactions",
        "lot_disposals",
        "employment_tax_events",
        "dividend_entries",
        "scenario_snapshots",
    ]
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "request": request,
            "entries": entries,
            "tables": tables,
            "active_table": active_table,
            "active_record_id": active_record_id,
            "active_date_from": active_date_from,
            "active_date_to": active_date_to,
            "filter_error": filter_error,
        },
    )


# ---------------------------------------------------------------------------
# Settings â€” GET + POST /settings
# ---------------------------------------------------------------------------

def _settings_completeness_payload(
    settings: AppSettings | None,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    checks.append(
        {
            "label": "Income Inputs Saved",
            "ok": bool(
                settings is not None
                and settings.default_gross_income > Decimal("0")
            ),
            "detail": "Gross employment income is required for deterministic tax estimates.",
        }
    )
    checks.append(
        {
            "label": "Tax Year Selected",
            "ok": bool(settings is not None and settings.default_tax_year),
            "detail": "CGT/Economic Gain reports default to this year.",
        }
    )
    checks.append(
        {
            "label": "Staleness Thresholds",
            "ok": bool(
                settings is not None
                and settings.price_stale_after_days >= 0
                and settings.fx_stale_after_minutes >= 0
            ),
            "detail": "Controls stale-price and stale-FX warning behavior.",
        }
    )
    checks.append(
        {
            "label": "ESPP Monthly Reminder Day",
            "ok": bool(
                settings is not None
                and settings.monthly_espp_input_reminder_day >= 1
                and settings.monthly_espp_input_reminder_day <= 28
            ),
            "detail": "Calendar monthly reminder day is valid when enabled.",
        }
    )
    checks.append(
        {
            "label": "Concentration Guardrails",
            "ok": bool(
                settings is not None
                and settings.concentration_top_holding_alert_pct > Decimal("0")
                and settings.concentration_employer_alert_pct > Decimal("0")
            ),
            "detail": "Risk pages use these thresholds for deterministic alerts.",
        }
    )
    checks.append(
        {
            "label": "Employer Identity",
            "ok": bool(
                settings is not None
                and bool((settings.employer_ticker or "").strip())
            ),
            "detail": "Needed for employer concentration and dependence metrics.",
        }
    )

    constrained_surfaces: list[dict[str, str]] = []
    if settings is None or _tax_inputs_incomplete(settings):
        constrained_surfaces.extend(
            [
                {
                    "surface": "Portfolio / Net Value",
                    "reason": "Employment-tax overlays may understate drag.",
                    "href": "/",
                },
                {
                    "surface": "Simulate / Scenario Lab",
                    "reason": "Employment-tax estimates can be unavailable for sellable lots.",
                    "href": "/simulate",
                },
                {
                    "surface": "CGT / Tax Plan",
                    "reason": "Tax-due projections remain estimate-constrained.",
                    "href": "/cgt",
                },
            ]
        )
    if settings is None or not bool((settings.employer_ticker or "").strip()):
        constrained_surfaces.append(
            {
                "surface": "Risk / Analytics",
                "reason": "Employer exposure and dependence metrics are incomplete.",
                "href": "/risk",
            }
        )

    ok_count = sum(1 for row in checks if bool(row["ok"]))
    score_pct = int(round((ok_count / len(checks)) * 100)) if checks else 0
    return {
        "score_pct": score_pct,
        "checks": checks,
        "constrained_surfaces": constrained_surfaces,
    }


def _render_settings_page(
    request: Request,
    db_path,
    *,
    msg: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = AppSettings.load(db_path) if db_path else None
    started_at_utc = getattr(request.app.state, "server_started_at_utc", None)
    started_at_display = None
    if isinstance(started_at_utc, datetime):
        local_started = started_at_utc.astimezone()
        started_at_display = local_started.strftime("%Y-%m-%d %H:%M:%S %Z")

    try:
        from ...services.twelve_data_catalog_service import TwelveDataCatalogService
        from ...services.twelve_data_stream_service import TwelveDataStreamService

        stream_health = TwelveDataStreamService.health_snapshot()
        catalog_last_synced_at = TwelveDataCatalogService.last_synced_at()
        catalog_last_synced_display = (
            catalog_last_synced_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            if isinstance(catalog_last_synced_at, datetime)
            else None
        )
    except Exception:
        stream_health = {
            "enabled": False,
            "connected": False,
            "symbols": [],
            "last_error": "Diagnostics unavailable.",
            "last_message_at": None,
        }
        catalog_last_synced_display = None

    context = {
        "request": request,
        "settings": settings,
        "tax_years": available_tax_years(),
        "settings_completeness": _settings_completeness_payload(settings),
        "server_started_at_display": started_at_display,
        "stream_health": stream_health,
        "catalog_last_synced_display": catalog_last_synced_display,
        **_flash(msg),
    }
    if error:
        context["error"] = error
    return _html_template_response("settings.html", context, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings_form(request: Request, msg: str | None = None) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    return _render_settings_page(request, db_path, msg=msg)


@router.post("/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings_submit(
    request: Request,
    default_gross_income: str = Form("0"),
    default_pension_sacrifice: str = Form("0"),
    default_student_loan_plan: str = Form(""),
    default_other_income: str = Form("0"),
    employer_income_dependency_pct: str = Form("0"),
    employer_ticker: str = Form(""),
    concentration_top_holding_alert_pct: str = Form("50"),
    concentration_employer_alert_pct: str = Form("40"),
    default_tax_year: str = Form(...),
    price_stale_after_days: str = Form("1"),
    fx_stale_after_minutes: str = Form("10"),
    monthly_espp_input_reminder_enabled: str = Form(""),
    monthly_espp_input_reminder_day: str = Form("1"),
    show_exhausted_lots: str = Form(""),
    hide_values: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    try:
        gross = Decimal(default_gross_income or "0")
        pension = Decimal(default_pension_sacrifice or "0")
        other = Decimal(default_other_income or "0")
        income_dependency_pct = Decimal(employer_income_dependency_pct or "0")
        top_holding_alert_pct = Decimal(concentration_top_holding_alert_pct or "50")
        employer_alert_pct = Decimal(concentration_employer_alert_pct or "40")
        employer_ticker_clean = (employer_ticker or "").strip().upper()
        plan: int | None = int(default_student_loan_plan) if default_student_loan_plan else None
        price_stale_days = int(price_stale_after_days or "1")
        fx_stale_minutes = int(fx_stale_after_minutes or "10")
        monthly_reminder_day = int(monthly_espp_input_reminder_day or "1")
    except (ValueError, InvalidOperation) as exc:
        return _render_settings_page(
            request,
            db_path,
            error=f"Invalid value: {exc}",
            status_code=422,
        )
    if price_stale_days < 0 or fx_stale_minutes < 0:
        return _render_settings_page(
            request,
            db_path,
            error="Staleness thresholds must be zero or greater.",
            status_code=422,
        )
    if monthly_reminder_day < 1 or monthly_reminder_day > 28:
        return _render_settings_page(
            request,
            db_path,
            error="Monthly ESPP reminder day must be between 1 and 28.",
            status_code=422,
        )
    if income_dependency_pct < Decimal("0") or income_dependency_pct > Decimal("100"):
        return _render_settings_page(
            request,
            db_path,
            error="Employer income dependency must be between 0 and 100.",
            status_code=422,
        )
    if top_holding_alert_pct < Decimal("0") or top_holding_alert_pct > Decimal("100"):
        return _render_settings_page(
            request,
            db_path,
            error="Top-holding concentration alert threshold must be between 0 and 100.",
            status_code=422,
        )
    if employer_alert_pct < Decimal("0") or employer_alert_pct > Decimal("100"):
        return _render_settings_page(
            request,
            db_path,
            error="Employer concentration alert threshold must be between 0 and 100.",
            status_code=422,
        )

    settings = AppSettings.load(db_path)
    settings.default_gross_income = gross
    settings.default_pension_sacrifice = pension
    settings.default_student_loan_plan = plan
    settings.default_other_income = other
    settings.employer_income_dependency_pct = income_dependency_pct
    settings.employer_ticker = employer_ticker_clean
    settings.concentration_top_holding_alert_pct = top_holding_alert_pct
    settings.concentration_employer_alert_pct = employer_alert_pct
    settings.default_tax_year = default_tax_year
    settings.price_stale_after_days = price_stale_days
    settings.fx_stale_after_minutes = fx_stale_minutes
    settings.monthly_espp_input_reminder_enabled = (
        monthly_espp_input_reminder_enabled.lower() in ("on", "true", "1", "yes")
    )
    settings.monthly_espp_input_reminder_day = monthly_reminder_day
    settings.show_exhausted_lots = show_exhausted_lots.lower() in ("on", "true", "1", "yes")
    settings.hide_values = hide_values.lower() in ("on", "true", "1", "yes")
    settings.save()

    return RedirectResponse("/settings?msg=Settings+saved.", status_code=303)


@router.post("/settings/nuke-db", response_class=HTMLResponse, include_in_schema=False)
async def settings_nuke_db(
    request: Request,
    confirm_text: str = Form(""),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    db_path = _state.get_db_path()
    if db_path is None:
        return _locked_response(request)

    if confirm_text.strip().upper() != "NUKE":
        return _render_settings_page(
            request,
            db_path,
            error='Type "NUKE" to confirm database reset.',
            status_code=422,
        )

    try:
        AppContext.recreate_schema()

        # Keep a fresh security catalog available after reset.
        from ..app import _seed_catalog_if_empty

        _seed_catalog_if_empty()
    except Exception as exc:
        return _render_settings_page(
            request,
            db_path,
            error=f"Database reset failed: {exc}",
            status_code=500,
        )

    return RedirectResponse("/settings?msg=Database+reset+complete.", status_code=303)


# ---------------------------------------------------------------------------
# Glossary — GET /glossary
# ---------------------------------------------------------------------------

@router.get("/glossary", response_class=HTMLResponse, include_in_schema=False)
async def glossary(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "glossary.html", {"request": request})


# ---------------------------------------------------------------------------
# Refresh Prices â€” POST /portfolio/refresh-prices
# ---------------------------------------------------------------------------

@router.post(
    "/portfolio/refresh-prices",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def refresh_prices_ui(request: Request) -> RedirectResponse:  # noqa: ARG001
    """Trigger a live price fetch for all securities, then redirect to portfolio."""
    if _is_locked():
        return _locked_response(request)
    try:
        result = PriceService.fetch_all()
    except CreditBudgetExceededError as exc:
        return RedirectResponse(
            f"/?msg=Refresh+blocked:+{str(exc).replace(' ', '+')}",
            status_code=303,
        )
    _state.record_refresh_result(result)
    fetched = result["fetched"]
    failed  = result["failed"]
    if failed == 0:
        msg = f"Prices+refreshed+({fetched}+updated)."
    else:
        msg = f"Prices+refreshed+({fetched}+updated,+{failed}+failed)."
    return RedirectResponse(f"/?msg={msg}", status_code=303)
