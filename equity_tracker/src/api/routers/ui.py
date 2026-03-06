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

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
from ...db.models import Lot, LotDisposal, Transaction
from ...db.repository import LotRepository, PriceRepository, SecurityRepository
from ...services.fx_service import FxService
from ...services.ibkr_price_service import IbkrPriceService
from ...services.capital_stack_service import CapitalStackService
from ...services.exposure_service import ExposureService
from ...services.portfolio_service import (
    LotSummary,
    PortfolioService,
    SecuritySummary,
    _estimate_sell_all_employment_tax,
)
from ...services.price_service import PriceService
from ...services.report_service import ReportService
from ...services.sheets_price_service import SheetsPriceService
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
                "message": (
                    f"{imminent_count} position(s) have forfeiture windows inside "
                    f"{_GUARDRAIL_FORFEITURE_IMMINENCE_DAYS} days "
                    f"(nearest in {min_days_remaining} days; value at risk £{_q2(at_risk_value)})."
                ),
            }
        )

    return warnings


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


def _utc_now() -> datetime:
    """Current UTC timestamp (helper for deterministic tests)."""
    return datetime.now(timezone.utc)


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
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
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
        unavailable_reason=reason,
    )


def _build_security_daily_changes(summary) -> dict[str, SecurityDailyChange]:
    """
    Build latest-vs-previous-close daily move cards for each security.

    Value change is quantity-aware in GBP:
      (latest_price_gbp - previous_price_gbp) x current_quantity
    """
    changes: dict[str, SecurityDailyChange] = {}
    now_utc = _utc_now()
    with AppContext.read_session() as sess:
        price_repo = PriceRepository(sess)
        for ss in summary.securities:
            security_id = ss.security.id
            last_changed_at = price_repo.get_current_price_run_started_at(security_id)
            freshness_text, freshness_level, freshness_title = _daily_freshness_note(
                exchange=ss.security.exchange,
                price_last_changed_at=last_changed_at,
                now_utc=now_utc,
            )

            market_status, market_opens_in = _market_status_label(
                exchange=ss.security.exchange,
                now_utc=now_utc,
            )

            latest_row = price_repo.get_latest(security_id)
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

            previous_row = price_repo.get_latest_before(security_id, latest_row.price_date)
            if previous_row is None:
                daily = _security_daily_change_unavailable(
                    security_id,
                    "Need at least two price dates.",
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
            previous_price_gbp = _price_row_gbp_value(previous_row)
            if (
                latest_price_gbp is None
                or previous_price_gbp is None
                or previous_price_gbp <= Decimal("0")
            ):
                daily = _security_daily_change_unavailable(
                    security_id,
                    "Previous close unavailable.",
                )
                daily.price_last_changed_at = last_changed_at
                daily.freshness_text = freshness_text
                daily.freshness_level = freshness_level
                daily.freshness_title = freshness_title
                daily.market_status = market_status
                daily.market_opens_in = market_opens_in
                changes[security_id] = daily
                continue

            delta_price = latest_price_gbp - previous_price_gbp
            pct_change = (
                (delta_price / previous_price_gbp) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            value_change = _q2(delta_price * ss.total_quantity)

            # Native-currency value change (uses original-CCY prices directly)
            native_currency = ss.market_value_native_currency or None
            latest_native = _price_row_native_value(latest_row)
            previous_native = _price_row_native_value(previous_row)
            if (
                latest_native is not None
                and previous_native is not None
                and previous_native > Decimal("0")
            ):
                # Calculate portfolio values at previous and current prices to preserve precision
                native_current_value = latest_native * ss.total_quantity
                native_previous_value = previous_native * ss.total_quantity
                value_change_native = _q2(native_current_value - native_previous_value)
            else:
                value_change_native = None

            if delta_price > Decimal("0"):
                direction = "up"
                arrow = "▲"
            elif delta_price < Decimal("0"):
                direction = "down"
                arrow = "▼"
            else:
                direction = "flat"
                arrow = "→"

            changes[security_id] = SecurityDailyChange(
                security_id=security_id,
                direction=direction,
                arrow=arrow,
                percent_change=pct_change,
                value_change_gbp=value_change,
                current_as_of=latest_row.price_date,
                previous_as_of=previous_row.price_date,
                price_last_changed_at=last_changed_at,
                freshness_text=freshness_text,
                freshness_level=freshness_level,
                freshness_title=freshness_title,
                native_currency=native_currency,
                value_change_native=value_change_native,
                market_status=market_status,
                market_opens_in=market_opens_in,
                current_price_gbp=latest_price_gbp,
                previous_price_gbp=previous_price_gbp,
                current_price_native=latest_native,
                previous_price_native=previous_native,
            )
    return changes


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

    if paid_cash is None:
        net_cash_if_sold = None
    elif match_effect == "INCLUDED":
        net_cash_if_sold = _q2(paid_cash + (match_cash or Decimal("0")))
    elif match_effect == "LOCKED" and sellability_status == "LOCKED":
        net_cash_if_sold = None
    else:
        net_cash_if_sold = paid_cash

    # Invariant: Gain = Net – True Economic Cost.
    # Forfeiture is handled via quantity (match shares excluded from net),
    # never as an additional deduction.
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
    try:
        fx_rates = FxService.read_rates()
    except RuntimeError as exc:
        fx_rates = {}
        fx_error = str(exc)

    for currency in currency_options:
        try:
            quote = FxService.get_rate(currency, "GBP", rates=fx_rates)
        except Exception:
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
async def home(request: Request, msg: str | None = None) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    refresh_diag = _state.get_refresh_diagnostics()
    if refresh_diag["next_due_at"] is None:
        _state.set_refresh_next_due(60)
        refresh_diag = _state.get_refresh_diagnostics()
    IbkrPriceService.ingest_all()
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    security_daily_changes = _build_security_daily_changes(summary)
    position_rows_by_security = _build_portfolio_position_rows(
        summary,
        settings=settings,
    )
    portfolio_est_net_liquidity = _portfolio_est_net_liquidity(position_rows_by_security)
    portfolio_blocked_restricted_value = _portfolio_blocked_restricted_value(
        position_rows_by_security
    )
    portfolio_net_gain_if_sold = _portfolio_net_gain_if_sold(position_rows_by_security)
    portfolio_sellable_employment_tax = _portfolio_sellable_employment_tax(position_rows_by_security)
    portfolio_sellable_true_cost = _portfolio_sellable_true_cost(position_rows_by_security)
    exposure_snapshot = ExposureService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
    )
    capital_stack_snapshot = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
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
    behavioral_guardrails = _build_behavioral_guardrails(
        summary=summary,
        settings=settings,
        position_rows_by_security=position_rows_by_security,
        deployable_capital_gbp=exposure_snapshot.get("deployable_capital_gbp"),
        sellable_employment_tax_gbp=portfolio_sellable_employment_tax,
        forfeitable_capital_gbp=exposure_snapshot.get("forfeitable_capital_gbp"),
    )
    today = _utc_now().date()
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "request": request,
            "summary": summary,
            "settings": settings,
            "price_stale_after_days": settings.price_stale_after_days if settings else 1,
            "fx_stale_after_minutes": settings.fx_stale_after_minutes if settings else 10,
            "security_daily_changes": security_daily_changes,
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
            "portfolio_employer_ticker": exposure_snapshot.get("employer_ticker"),
            "portfolio_employer_pct_of_gross": exposure_snapshot.get("employer_pct_of_gross"),
            "portfolio_employer_pct_of_sellable": exposure_snapshot.get("employer_pct_of_sellable"),
            "portfolio_total_sellable_market_value_gbp": exposure_snapshot.get("total_sellable_market_value_gbp"),
            "portfolio_deployable_cash_gbp": exposure_snapshot.get("deployable_cash_gbp"),
            "portfolio_deployable_capital_gbp": exposure_snapshot.get("deployable_capital_gbp"),
            "portfolio_employer_share_of_deployable_pct": exposure_snapshot.get("employer_share_of_deployable_pct"),
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
            "today": today,
            **_flash(msg),
        },
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
async def net_value(request: Request) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    IbkrPriceService.ingest_all()
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    security_daily_changes = _build_security_daily_changes(summary)
    position_rows_by_security = _build_portfolio_position_rows(
        summary,
        settings=settings,
    )
    sell_all_metrics = _build_sell_all_metrics(summary)
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
            "nv_securities": nv_securities,
            "nv_rows_by_security": nv_rows_by_security,
            "settings": settings,
            "fx_stale_after_minutes": settings.fx_stale_after_minutes if settings else 10,
            "security_daily_changes": security_daily_changes,
            "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
        },
    )


@router.get("/capital-stack", response_class=HTMLResponse, include_in_schema=False)
async def capital_stack(request: Request) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    IbkrPriceService.ingest_all()
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=summary,
    )
    return _html_template_response(
        "capital_stack.html",
        {
            "request": request,
            "stack": stack,
            "tax_inputs_incomplete": _tax_inputs_incomplete(settings),
        },
    )




# ---------------------------------------------------------------------------
# Add security â€” GET + POST /portfolio/add-security
# ---------------------------------------------------------------------------

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
        {"request": request, "error": error},
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
    catalog_id: str = Form(""),
    is_manual_override: str = Form("false"),
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)

    _catalog_id = catalog_id.strip() or None
    _is_manual  = is_manual_override.strip().lower() == "true"

    try:
        PortfolioService.add_security(
            ticker=ticker.strip().upper(),
            name=name.strip(),
            currency=currency.strip().upper(),
            isin=isin.strip() or None,
            exchange=exchange.strip() or None,
            units_precision=units_precision,
            catalog_id=_catalog_id,
            is_manual_override=_is_manual,
        )
    except (ValueError, IntegrityError) as exc:
        return templates.TemplateResponse(
            request,
            "add_security.html",
            {
                "request": request,
                "error": str(exc),
                "prev": {
                    "ticker": ticker,
                    "name": name,
                    "currency": currency,
                    "isin": isin,
                    "exchange": exchange,
                    "units_precision": units_precision,
                    "catalog_id": catalog_id,
                    "is_manual_override": _is_manual,
                },
            },
            status_code=422,
        )

    # Auto-sync the new ticker into the Google Sheet (column A).
    # Failure is non-fatal â€” security was already saved to the DB.
    _clean_ticker = ticker.strip().upper()
    try:
        SheetsPriceService.sync_tickers([_clean_ticker])
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Could not sync ticker %r to Google Sheet after add-security "
            "(Sheet may be unavailable). Run POST /prices/sync-tickers manually.",
            _clean_ticker,
        )

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

    return templates.TemplateResponse(
        request,
        "economic_gain.html",
        {
            "request": request,
            "report": report,
            "tax_years": tax_years,
            "active_year": active_year,
            "active_year_index": nav["active_year_index"],
            "previous_tax_year": nav["previous_tax_year"],
            "next_tax_year": nav["next_tax_year"],
        },
    )


# ---------------------------------------------------------------------------
# Audit log â€” GET /audit
# ---------------------------------------------------------------------------

@router.get("/audit", response_class=HTMLResponse, include_in_schema=False)
async def audit_log(
    request: Request,
    table_name: str | None = None,
    record_id: str | None = None,
) -> HTMLResponse:
    if _is_locked():
        return _locked_response(request)
    active_table = (table_name or "").strip()
    active_record_id = (record_id or "").strip()
    entries = ReportService.audit_log(
        table_name=active_table or None,
        record_id=active_record_id or None,
    )
    tables = ["lots", "securities", "transactions", "lot_disposals"]
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "request": request,
            "entries": entries,
            "tables": tables,
            "active_table": active_table,
            "active_record_id": active_record_id,
        },
    )


# ---------------------------------------------------------------------------
# Settings â€” GET + POST /settings
# ---------------------------------------------------------------------------

def _render_settings_page(
    request: Request,
    db_path,
    *,
    msg: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = AppSettings.load(db_path) if db_path else None
    context = {
        "request": request,
        "settings": settings,
        "tax_years": available_tax_years(),
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
    result = PriceService.fetch_all()
    _state.record_refresh_result(result)
    fetched = result["fetched"]
    failed  = result["failed"]
    if failed == 0:
        msg = f"Prices+refreshed+({fetched}+updated)."
    else:
        msg = f"Prices+refreshed+({fetched}+updated,+{failed}+failed)."
    return RedirectResponse(f"/?msg={msg}", status_code=303)
