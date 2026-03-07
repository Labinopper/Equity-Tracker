"""
PortfolioService — application service for portfolio read/write operations.

All read operations use AppContext.read_session().
All write operations use AppContext.write_session().

Methods are static — no instance state is required. Callers never construct
a PortfolioService object; they call PortfolioService.method() directly.

Design notes:
  - ORM objects returned from write methods become detached after the session
    closes. Scalar attributes (id, ticker, quantity, etc.) remain accessible.
    Do NOT traverse lazy-loaded relationships on returned objects.
  - add_lot() and commit_disposal() write audit entries automatically.
  - simulate_disposal() is pure (no writes); use it to preview disposal
    committing. Check FIFOResult.is_fully_allocated before calling commit_disposal().
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from ..app_context import AppContext
from ..core.lot_engine.fifo import (
    FIFOResult,
    ForfeitureWarning,
    LotForFIFO,
    SIPTaxEstimate,
    allocate_fifo,
)
from ..core.tax_engine import (
    SIPEvent,
    SIPEventType,
    SIPHolding,
    SIPHoldingPeriodCategory,
    SIPShareType,
    TaxContext,
    get_bands,
    get_marginal_rates,
    marginal_cgt_rate,
    personal_allowance,
    process_sip_event,
    tax_year_for_date,
)
from ..core.tax_engine.employment_tax_engine import (
    EmploymentTaxContext,
    estimate_employment_tax_for_lot,
)
from ..db.models import Lot, LotDisposal, Security, Transaction, _new_uuid
from ..db.repository import (
    AuditRepository,
    DisposalRepository,
    EmploymentTaxEventRepository,
    LotRepository,
    PriceRepository,
    SecurityRepository,
)
from ..settings import AppSettings
from .staleness_service import StalenessService


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ForfeitureRisk:
    """
    ESPP_PLUS forfeiture window status for a single lot.

    in_window     : True when today is before end_date
    days_remaining: Calendar days until end (0 when not in window)
    end_date      : exact forfeiture_period_end from DB when set;
                    falls back to acquisition_date + 183 days for legacy lots
    """
    in_window: bool
    days_remaining: int
    end_date: date


@dataclass
class SIPQualifyingStatus:
    """
    SIP income-tax qualifying period status for a single SIP lot.

    category       : UNDER_THREE_YEARS, THREE_TO_FIVE_YEARS, or FIVE_PLUS_YEARS
    three_year_date: Date when the 3-year threshold is crossed
    five_year_date : Date when the 5-year threshold is crossed
    """
    category: SIPHoldingPeriodCategory
    three_year_date: date
    five_year_date: date


@dataclass
class NetLiquidationEstimate:
    """
    Result of estimate_net_liquidation_value().

    quantity                : Shares included in the estimate.
    market_value_gbp        : quantity × current price (GBP).
    cost_basis_gbp          : FIFO cost basis for the quantity.
    unrealised_gain_cgt_gbp : market_value − cost_basis (may be negative).
    est_cgt_gbp             : marginal_rate × max(unrealised_gain, 0).
    est_net_proceeds_gbp    : market_value − est_cgt.
    marginal_rate_used      : CGT rate applied (from settings or 20% fallback).
    as_of_date              : Reference date used for the estimate.
    notes                   : Informational strings for UI display.
    """
    quantity: Decimal
    market_value_gbp: Decimal
    cost_basis_gbp: Decimal
    unrealised_gain_cgt_gbp: Decimal
    est_cgt_gbp: Decimal
    est_net_proceeds_gbp: Decimal
    marginal_rate_used: Decimal
    as_of_date: date
    notes: list[str]


@dataclass
class LotSummary:
    """
    Aggregated data for a single active lot.

    cost_basis_total_gbp         : quantity_remaining × acquisition_price_gbp (CGT view)
    true_cost_total_gbp          : quantity_remaining × true_cost_per_share_gbp (economic view)
    market_value_gbp             : quantity_remaining × current_price_gbp (None when no price)
    unrealised_gain_cgt_gbp      : market_value − cost_basis (None when no price)
    unrealised_gain_economic_gbp : market_value − true_cost  (None when no price)
    est_employment_tax_on_lot_gbp: estimated employment tax (IT + NIC + SL) for this lot (None when no price)
    est_net_proceeds_gbp         : market_value − est_employment_tax_on_lot (None when no price)
    forfeiture_risk              : Populated for ESPP_PLUS lots only
    sip_qualifying_status        : Populated for SIP_* lots only
    """
    lot: Lot
    quantity_remaining: Decimal
    true_cost_per_share_gbp: Decimal
    cost_basis_total_gbp: Decimal
    true_cost_total_gbp: Decimal
    market_value_gbp: Decimal | None = field(default=None)
    market_value_native: Decimal | None = field(default=None)
    market_value_native_currency: str | None = field(default=None)
    unrealised_gain_cgt_gbp: Decimal | None = field(default=None)
    unrealised_gain_economic_gbp: Decimal | None = field(default=None)
    # Phase V
    est_employment_tax_on_lot_gbp: Decimal | None = field(default=None)
    est_net_proceeds_gbp: Decimal | None = field(default=None)
    sell_now_economic_gbp: Decimal | None = field(default=None)
    est_net_proceeds_reason: str | None = field(default=None)
    sellability_status: str = field(default="SELLABLE")
    sellability_unlock_date: date | None = field(default=None)
    forfeiture_risk: ForfeitureRisk | None = field(default=None)
    sip_qualifying_status: SIPQualifyingStatus | None = field(default=None)


@dataclass
class SecuritySummary:
    """Aggregated data for one security and all its active lots."""
    security: Security
    active_lots: list[LotSummary]
    total_quantity: Decimal
    total_cost_basis_gbp: Decimal
    total_true_cost_gbp: Decimal
    # Phase L: live price data (None when no price has been fetched yet)
    current_price_native: Decimal | None = field(default=None)
    current_price_gbp: Decimal | None = field(default=None)
    market_value_native: Decimal | None = field(default=None)
    market_value_native_currency: str | None = field(default=None)
    market_value_gbp: Decimal | None = field(default=None)
    unrealised_gain_cgt_gbp: Decimal | None = field(default=None)
    unrealised_gain_economic_gbp: Decimal | None = field(default=None)
    locked_unrealised_gain_cgt_gbp: Decimal | None = field(default=None)
    locked_unrealised_gain_economic_gbp: Decimal | None = field(default=None)
    forfeit_risk_market_value_gbp: Decimal | None = field(default=None)
    forfeit_risk_unrealised_gain_cgt_gbp: Decimal | None = field(default=None)
    forfeit_risk_unrealised_gain_economic_gbp: Decimal | None = field(default=None)
    price_as_of: date | None = field(default=None)
    price_is_stale: bool = field(default=False)
    # Google Sheets column D "last_refresh_timestamp" for display in portfolio UI
    price_refreshed_at: str | None = field(default=None)
    # FX tab timestamp used when converting this security's price to GBP.
    # None for GBP-denominated securities (no FX conversion needed).
    fx_as_of: str | None = field(default=None)
    fx_is_stale: bool = field(default=False)
    # Phase V: net liquidation estimates (None when no price)
    est_employment_tax_gbp: Decimal | None = field(default=None)
    est_net_proceeds_gbp: Decimal | None = field(default=None)
    marginal_cgt_rate_used: Decimal | None = field(default=None)
    has_forfeiture_risk: bool = field(default=False)
    has_sip_qualifying_risk: bool = field(default=False)
    refresh_last_success_at: str | None = field(default=None)
    refresh_last_error: str | None = field(default=None)
    refresh_next_due_at: str | None = field(default=None)
    # Post-tax economic gain on all lots that can be sold today (matching table totals).
    # None when any non-locked lot lacks a tax estimate (e.g. no settings configured).
    sellable_net_gain_gbp: Decimal | None = field(default=None)
    # Market-value breakdown by constraint type — used for the three summary badges.
    sellable_pure_market_value_gbp: Decimal | None = field(default=None)
    espp_plus_pending_market_value_gbp: Decimal | None = field(default=None)
    rsu_vesting_market_value_gbp: Decimal | None = field(default=None)


@dataclass
class PortfolioSummary:
    """Aggregated data for the entire portfolio."""
    securities: list[SecuritySummary]
    total_cost_basis_gbp: Decimal
    total_true_cost_gbp: Decimal
    # Phase L: sum of market values for securities that have a live price
    total_market_value_gbp: Decimal | None = field(default=None)
    # FX info: timestamp from the "fx" Sheet tab used for USD→GBP conversion.
    # None when no USD securities have a stored price.
    fx_as_of: str | None = field(default=None)
    fx_is_stale: bool = field(default=False)
    valuation_currency: str = field(default="GBP")
    fx_conversion_basis: str | None = field(default=None)
    est_total_employment_tax_gbp: Decimal | None = field(default=None)
    est_total_net_liquidation_gbp: Decimal | None = field(default=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_fx_stale(fx_as_of: str | None, stale_minutes: int = 10) -> bool:
    """Backward-compatible wrapper around shared staleness logic."""
    return StalenessService.is_fx_stale(
        fx_as_of,
        stale_after_minutes=stale_minutes,
    )


def _normalize_broker_currency(raw_value: str | None) -> str | None:
    """
    Normalize broker holding currency.

    Returns None for blank values. Phase B supports generalized 3-letter ISO.
    """
    if raw_value is None:
        return None
    cleaned = raw_value.strip().upper()
    if not cleaned:
        return None
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise ValueError("broker_currency must be a 3-letter ISO currency code.")
    return cleaned


def _espp_plus_employee_lot_ids(lots: list[Lot]) -> set[str]:
    """
    Return ESPP employee-lot IDs linked from ESPP_PLUS matching lots.

    Matching lots (scheme_type="ESPP_PLUS" with matching_lot_id set) reference
    their employee lot via matching_lot_id.
    """
    return {
        lot.matching_lot_id
        for lot in lots
        if lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None
    }


def _is_espp_plus_matched_lot(lot: Lot) -> bool:
    """Return True for ESPP+ matched-share lots (locked for 6 months)."""
    return lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None


def _live_true_cost_per_share(
    lot: Lot,
    *,
    settings: AppSettings | None,
    espp_plus_employee_ids: set[str],
) -> Decimal:
    """
    Return effective true-cost/share for a lot.

    True cost is locked at acquisition/vesting and persisted on the lot.
    The preview/disposal pipeline reads this stored value directly.
    """
    _ = settings
    _ = espp_plus_employee_ids
    return Decimal(lot.true_cost_per_share_gbp)


def _rsu_live_true_cost_per_share(lot: Lot, settings: AppSettings) -> Decimal:
    # RSU true economic cost per share: FMV * (1 - combined_marginal_rate).
    # Represents the after-tax net value retained at vest.
    # Result stored at 4dp; multiply by qty_remaining for lot total (2dp).
    # RSU disposal employment tax remains zero by design (tax paid at vest).
    fmv = Decimal(lot.fmv_at_acquisition_gbp)
    ty = tax_year_for_date(lot.acquisition_date)
    ctx = TaxContext(
        tax_year=ty,
        gross_employment_income=settings.default_gross_income,
        pension_sacrifice=settings.default_pension_sacrifice,
        other_income=settings.default_other_income,
        student_loan_plan=settings.default_student_loan_plan,
    )
    rates = get_marginal_rates(ctx)
    return (fmv * (Decimal("1") - rates.combined)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _lot_to_fifo(
    lot: Lot,
    *,
    settings: AppSettings | None = None,
    espp_plus_employee_ids: set[str] | None = None,
    use_live_true_cost: bool = True,
) -> LotForFIFO:
    """Convert a Lot ORM object to a LotForFIFO with live true-cost where applicable."""
    employee_ids = espp_plus_employee_ids or set()
    true_cost_per_share = (
        _live_true_cost_per_share(
            lot,
            settings=settings,
            espp_plus_employee_ids=employee_ids,
        )
        if use_live_true_cost
        else Decimal(lot.true_cost_per_share_gbp)
    )
    return LotForFIFO(
        lot_id=lot.id,
        acquisition_date=lot.acquisition_date,
        quantity_remaining=Decimal(lot.quantity_remaining),
        acquisition_price_gbp=Decimal(lot.acquisition_price_gbp),
        true_cost_per_share_gbp=true_cost_per_share,
    )


def _is_lot_sellable_on(lot: Lot, disposal_date: date) -> bool:
    """
    Return whether a lot is eligible for disposal on disposal_date.

    RSU lots are locked until vest date (stored as lot.acquisition_date in UI flow).
    ESPP+ matched-share lots are locked until forfeiture_period_end
    (or acquisition_date + 183 days for legacy rows). Employee-paid ESPP+ lots
    are immediately sellable.
    """
    if lot.scheme_type == "RSU":
        return disposal_date >= lot.acquisition_date

    if not _is_espp_plus_matched_lot(lot):
        return True
    end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
    return disposal_date >= end


def _apply_broker_fees_to_fifo_result(
    result: FIFOResult,
    broker_fees_gbp: Decimal | None,
) -> FIFOResult:
    """
    Return FIFOResult with broker fees allocated across allocation lines.

    Fees are allocated proportionally by allocated quantity, rounded to pennies,
    and applied exactly once to realised gains (tax-basis + economic).
    Proceeds remain gross sale proceeds; realised gains become net-of-fees.
    """
    if broker_fees_gbp is None:
        return result
    if broker_fees_gbp < Decimal("0"):
        raise ValueError("broker_fees_gbp must be non-negative.")
    if not result.allocations or result.quantity_sold <= Decimal("0"):
        return result

    fee_total = broker_fees_gbp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if fee_total == Decimal("0.00"):
        return result

    total_fee_cents = int((fee_total * 100).to_integral_value(rounding=ROUND_HALF_UP))
    qty_sold = result.quantity_sold

    # Largest-remainder cent allocation to ensure line-fees sum exactly to fee_total.
    raw_cents: list[Decimal] = [
        (alloc.quantity_allocated / qty_sold) * Decimal(total_fee_cents)
        for alloc in result.allocations
    ]
    base_cents: list[int] = [
        int(raw.to_integral_value(rounding=ROUND_FLOOR))
        for raw in raw_cents
    ]
    remainder = total_fee_cents - sum(base_cents)
    order = sorted(
        range(len(raw_cents)),
        key=lambda i: (raw_cents[i] - Decimal(base_cents[i]), -i),
        reverse=True,
    )
    for i in range(remainder):
        base_cents[order[i]] += 1

    fee_by_index = [Decimal(c) / Decimal("100") for c in base_cents]

    adjusted_allocs = []
    for alloc, fee in zip(result.allocations, fee_by_index):
        adjusted_allocs.append(
            dataclasses.replace(
                alloc,
                realised_gain_gbp=(alloc.realised_gain_gbp - fee),
                realised_gain_economic_gbp=(alloc.realised_gain_economic_gbp - fee),
            )
        )

    return dataclasses.replace(
        result,
        allocations=tuple(adjusted_allocs),
        total_realised_gain_gbp=(result.total_realised_gain_gbp - fee_total),
        total_realised_gain_economic_gbp=(
            result.total_realised_gain_economic_gbp - fee_total
        ),
    )


# ---------------------------------------------------------------------------
# Phase E: SIP/ESPP scheme-type map for process_sip_event()
# ---------------------------------------------------------------------------

_SIP_SHARE_TYPE_MAP: dict[str, SIPShareType] = {
    "SIP_PARTNERSHIP": SIPShareType.PARTNERSHIP,
    "SIP_MATCHING":    SIPShareType.MATCHING,
    "SIP_DIVIDEND":    SIPShareType.DIVIDEND,
}


def _sip_share_type_for_lot(lot: Lot) -> SIPShareType | None:
    """
    Resolve legacy SIP share type for SIP_* lots only.

    ESPP/ESPP_PLUS employment-tax estimation uses the dedicated
    employment_tax_engine and does not pass through sip_rules.py.
    """
    return _SIP_SHARE_TYPE_MAP.get(lot.scheme_type)


def _build_forfeiture_warnings(
    lot_repo: LotRepository,
    security_id: str,
    sold_lot_ids: set[str],
    disposal_price_gbp: Decimal,
    disposal_date: date,
) -> list[ForfeitureWarning]:
    """
    Return ForfeitureWarnings for active ESPP_PLUS lots whose matching_lot_id is
    in sold_lot_ids and whose forfeiture window is still open on disposal_date.

    Called inside an open read session (lot_repo is session-bound).
    """
    espp_plus_lots = lot_repo.get_active_lots_for_security(
        security_id, scheme_type="ESPP_PLUS"
    )
    warnings: list[ForfeitureWarning] = []
    for lot in espp_plus_lots:
        if lot.matching_lot_id not in sold_lot_ids:
            continue
        end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
        if disposal_date >= end:
            continue
        qty = Decimal(lot.quantity_remaining)
        value = (qty * disposal_price_gbp).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        warnings.append(ForfeitureWarning(
            lot_id=lot.id,
            acquisition_date=lot.acquisition_date,
            forfeiture_end_date=end,
            days_remaining=(end - disposal_date).days,
            quantity_at_risk=qty,
            value_at_risk_gbp=value,
            linked_partnership_lot_id=lot.matching_lot_id,
        ))
    return warnings


def _build_sip_tax_estimates(
    lots_by_id: dict[str, Lot],
    allocations: tuple,
    disposal_price_gbp: Decimal,
    disposal_date: date,
    settings: AppSettings | None,
) -> tuple[list[SIPTaxEstimate], Decimal]:
    """
    Return (estimates, total_employment_tax) for SIP-like allocations.

    ESPP/ESPP_PLUS use the dedicated employment-tax engine.
    SIP_* schemes continue to use process_sip_event() legacy logic.
    Returns ([], Decimal("0")) when settings is None (income context unavailable).

    Student Loan is calculated on the NIC-liable base (PAYE earnings treatment).
    If ni_liable_gbp is zero for a given lot/event, SL is also zero.
    """
    estimates: list[SIPTaxEstimate] = []
    total_tax = Decimal("0")

    if settings is None:
        return estimates, total_tax

    ty = tax_year_for_date(disposal_date)
    ctx = TaxContext(
        tax_year=ty,
        gross_employment_income=settings.default_gross_income,
        pension_sacrifice=settings.default_pension_sacrifice,
        other_income=settings.default_other_income,
        student_loan_plan=settings.default_student_loan_plan,
    )
    rates = get_marginal_rates(ctx)
    q = Decimal("0.01")
    emp_tax_ctx = EmploymentTaxContext(lots_by_id=lots_by_id)

    for alloc in allocations:
        lot = lots_by_id.get(alloc.lot_id)
        if lot is None:
            continue

        if lot.scheme_type in ("ESPP", "ESPP_PLUS"):
            est = estimate_employment_tax_for_lot(
                lot=lot,
                quantity=alloc.quantity_allocated,
                event_date=disposal_date,
                disposal_price_per_share_gbp=disposal_price_gbp,
                rates=rates,
                context=emp_tax_ctx,
            )
            estimates.append(SIPTaxEstimate(
                lot_id=alloc.lot_id,
                holding_period_category=est.holding_period_category,
                income_taxable_gbp=est.income_taxable_base_gbp,
                ni_liable_gbp=est.ni_base_gbp,
                est_income_tax_gbp=est.est_it_gbp,
                est_ni_gbp=est.est_ni_gbp,
                est_student_loan_gbp=est.est_sl_gbp,
                est_total_employment_tax_gbp=est.est_total_gbp,
            ))
            total_tax += est.est_total_gbp
            continue

        share_type = _sip_share_type_for_lot(lot)
        if share_type is None:
            continue
        fmv_per_share = Decimal(lot.fmv_at_acquisition_gbp or lot.acquisition_price_gbp)
        gross_salary = (
            alloc.quantity_allocated * Decimal(lot.acquisition_price_gbp)
            if share_type == SIPShareType.PARTNERSHIP
            else Decimal("0")
        )

        holding = SIPHolding(
            lot_id=0,  # placeholder — we don't use matching_lots_forfeited from result
            share_type=share_type,
            acquisition_date=lot.acquisition_date,
            quantity=alloc.quantity_allocated,
            acquisition_market_value_gbp=fmv_per_share,
            gross_salary_deducted_gbp=gross_salary,
        )
        # IN_PLAN_SALE: shares sold directly while held within the SIP trust.
        # Both WITHDRAWAL and IN_PLAN_SALE compute income_taxable_gbp / ni_liable_gbp
        # identically. IN_PLAN_SALE also computes cgt_gain_gbp, which we ignore here
        # (CGT is already handled by the FIFO allocations).
        event = SIPEvent(
            event_type=SIPEventType.IN_PLAN_SALE,
            event_date=disposal_date,
            holding=holding,
            quantity=alloc.quantity_allocated,
            market_value_per_share_gbp=disposal_price_gbp,
        )
        sip_result = process_sip_event(event)

        est_it = (sip_result.income_taxable_gbp * rates.income_tax).quantize(q, ROUND_HALF_UP)
        est_ni = (sip_result.ni_liable_gbp * rates.national_insurance).quantize(q, ROUND_HALF_UP)
        # Student Loan uses the NIC-liable base (PAYE earnings treatment).
        # If ni_liable_gbp is zero for a given lot/event, SL is also zero.
        est_sl = (sip_result.ni_liable_gbp * rates.student_loan).quantize(q, ROUND_HALF_UP)
        est_total = est_it + est_ni + est_sl

        estimates.append(SIPTaxEstimate(
            lot_id=alloc.lot_id,
            holding_period_category=sip_result.holding_period_category.value,
            income_taxable_gbp=sip_result.income_taxable_gbp,
            ni_liable_gbp=sip_result.ni_liable_gbp,
            est_income_tax_gbp=est_it,
            est_ni_gbp=est_ni,
            est_student_loan_gbp=est_sl,
            est_total_employment_tax_gbp=est_total,
        ))
        total_tax += est_total

    return estimates, total_tax


def _forfeit_linked_matched_lots_on_disposal(
    lot_repo: LotRepository,
    audit: AuditRepository,
    security_id: str,
    sold_lot_ids: set[str],
    disposal_date: date,
) -> tuple[int, Decimal]:
    """
    Persist ESPP+ matched-share forfeiture when linked employee lots are sold in-window.

    Returns:
        (forfeited_lot_count, forfeited_quantity_total)
    """
    forfeited_count = 0
    forfeited_qty = Decimal("0")

    for matched in lot_repo.get_active_lots_for_security(security_id, scheme_type="ESPP_PLUS"):
        if matched.matching_lot_id not in sold_lot_ids:
            continue
        if Decimal(matched.quantity_remaining) <= Decimal("0"):
            continue

        end = matched.forfeiture_period_end or (matched.acquisition_date + timedelta(days=183))
        if disposal_date >= end:
            continue

        old_qty = matched.quantity_remaining
        old_import_source = matched.import_source or ""
        old_notes = matched.notes or ""

        forfeited_count += 1
        forfeited_qty += Decimal(old_qty)

        matched.quantity_remaining = "0"
        matched.import_source = "commit_disposal_forfeiture"
        forfeiture_note = (
            "Forfeited on disposal of linked ESPP+ employee lot "
            f"{matched.matching_lot_id} ({disposal_date.isoformat()})."
        )
        matched.notes = f"{old_notes}\n{forfeiture_note}".strip() if old_notes else forfeiture_note

        audit.log_update(
            table_name="lots",
            record_id=matched.id,
            old_values={
                "quantity_remaining": old_qty,
                "import_source": old_import_source,
                "notes": old_notes,
            },
            new_values={
                "quantity_remaining": matched.quantity_remaining,
                "import_source": matched.import_source or "",
                "notes": matched.notes or "",
            },
            notes=(
                "ESPP_PLUS matched lot forfeited due to disposal "
                f"of linked employee lot {matched.matching_lot_id}"
            ),
        )

    return forfeited_count, forfeited_qty


def _estimate_sell_all_employment_tax(
    lots: list[Lot],
    price_per_share_gbp: Decimal,
    disposal_date: date,
    settings: AppSettings | None,
) -> Decimal | None:
    """
    Estimate employment tax for a hypothetical full disposal at one price.

    Uses the same SIP/ESPP employment-tax pathway as simulate_disposal()
    so summary tiles and simulation are aligned.
    """
    sellable_lots = [
        lot
        for lot in lots
        if Decimal(lot.quantity_remaining) > Decimal("0")
        and _is_lot_sellable_on(lot, disposal_date)
    ]
    if not sellable_lots:
        return Decimal("0.00")
    if all(lot.scheme_type == "ISA" for lot in sellable_lots):
        return Decimal("0.00")
    if settings is None:
        return None

    quantity = sum(
        (Decimal(lot.quantity_remaining) for lot in sellable_lots),
        Decimal("0"),
    )
    if quantity <= Decimal("0"):
        return Decimal("0.00")

    espp_plus_employee_ids = _espp_plus_employee_lot_ids(lots)
    fifo_lots = [
        _lot_to_fifo(
            lot,
            settings=settings,
            espp_plus_employee_ids=espp_plus_employee_ids,
        )
        for lot in sellable_lots
    ]
    fifo_result = allocate_fifo(fifo_lots, quantity, price_per_share_gbp)
    lots_by_id = {lot.id: lot for lot in sellable_lots}
    _, total_emp_tax = _build_sip_tax_estimates(
        lots_by_id,
        fifo_result.allocations,
        price_per_share_gbp,
        disposal_date,
        settings,
    )
    return total_emp_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _derive_marginal_cgt_rate(settings: AppSettings | None) -> Decimal:
    """
    Return the marginal CGT rate from AppSettings income context.

    Falls back to 20% (higher rate) when settings is None or income is zero —
    this is the worst-case conservative estimate.
    """
    FALLBACK = Decimal("0.20")
    if settings is None:
        return FALLBACK
    try:
        ty = tax_year_for_date(date.today())
        bands = get_bands(ty)
        ani = (
            settings.default_gross_income
            - settings.default_pension_sacrifice
            + settings.default_other_income
        )
        pa = personal_allowance(bands, ani)
        taxable = max(Decimal("0"), settings.default_gross_income - pa)
        return marginal_cgt_rate(bands, taxable)
    except (ValueError, KeyError):
        return FALLBACK


def _forfeiture_risk_for_lot(lot: Lot, as_of: date) -> ForfeitureRisk | None:
    """
    Return ForfeitureRisk for ESPP+ matched-share lots only.

    Uses lot.forfeiture_period_end (exact DB field) when available.
    Falls back to acquisition_date + 183 days for legacy lots without the field.
    """
    if not _is_espp_plus_matched_lot(lot):
        return None
    end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
    in_window = as_of < end
    days_remaining = max(0, (end - as_of).days)
    return ForfeitureRisk(
        in_window=in_window,
        days_remaining=days_remaining,
        end_date=end,
    )


# All scheme types that follow SIP-style holding period tax rules.
# ESPP (partnership) and ESPP_PLUS (matching) shares are held in a UK SIP trust
# and follow the same 0–3yr / 3–5yr / 5yr+ income-tax treatment as SIP_* lots.
_SIP_LIKE_SCHEMES = frozenset({
    "ESPP_PLUS", "SIP_PARTNERSHIP", "SIP_MATCHING", "SIP_DIVIDEND"
})

SELLABILITY_SELLABLE = "SELLABLE"
SELLABILITY_LOCKED = "LOCKED"
SELLABILITY_AT_RISK = "AT_RISK"


def _sip_qualifying_status_for_lot(lot: Lot, as_of: date) -> SIPQualifyingStatus | None:
    """
    Return SIPQualifyingStatus for SIP-like lots; None for all other scheme types.

    Covers ESPP_PLUS, SIP_PARTNERSHIP, SIP_MATCHING, SIP_DIVIDEND.
    Uses acq.replace(year=acq.year + N) for exact threshold dates. Handles the
    Feb 29 leap-year edge case by falling back to Feb 28.
    """
    if lot.scheme_type not in _SIP_LIKE_SCHEMES:
        return None
    acq = lot.acquisition_date
    try:
        three_yr = acq.replace(year=acq.year + 3)
        five_yr = acq.replace(year=acq.year + 5)
    except ValueError:
        three_yr = acq.replace(year=acq.year + 3, day=28)
        five_yr = acq.replace(year=acq.year + 5, day=28)
    if as_of < three_yr:
        cat = SIPHoldingPeriodCategory.UNDER_THREE_YEARS
    elif as_of < five_yr:
        cat = SIPHoldingPeriodCategory.THREE_TO_FIVE_YEARS
    else:
        cat = SIPHoldingPeriodCategory.FIVE_PLUS_YEARS
    return SIPQualifyingStatus(
        category=cat,
        three_year_date=three_yr,
        five_year_date=five_yr,
    )


def _matched_employee_lot_ids_at_risk(lots: list[Lot], as_of: date) -> set[str]:
    """
    Return employee lot IDs currently at risk of forfeiture.

    A lot is considered "at risk" when there is an active matched ESPP_PLUS lot
    linked to it and that matched lot is still inside the forfeiture window.
    """
    ids: set[str] = set()
    for lot in lots:
        if Decimal(lot.quantity_remaining) <= Decimal("0"):
            continue
        if not _is_espp_plus_matched_lot(lot):
            continue
        risk = _forfeiture_risk_for_lot(lot, as_of)
        if risk is not None and risk.in_window and lot.matching_lot_id:
            ids.add(lot.matching_lot_id)
    return ids


def _lot_lock_end_date(lot: Lot, as_of: date) -> date | None:
    """Return lock end date for currently unsellable lots; otherwise None."""
    if lot.scheme_type == "RSU" and as_of < lot.acquisition_date:
        return lot.acquisition_date
    if _is_espp_plus_matched_lot(lot):
        end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
        if as_of < end:
            return end
    return None


def _sellability_for_lot(
    lot: Lot,
    *,
    as_of: date,
    at_risk_employee_lot_ids: set[str],
) -> tuple[str, date | None]:
    """Return (status, unlock_date) for the lot on as_of date."""
    lock_end = _lot_lock_end_date(lot, as_of)
    if lock_end is not None:
        return SELLABILITY_LOCKED, lock_end
    if lot.id in at_risk_employee_lot_ids:
        return SELLABILITY_AT_RISK, None
    return SELLABILITY_SELLABLE, None


# ---------------------------------------------------------------------------
# PortfolioService
# ---------------------------------------------------------------------------

class PortfolioService:
    """
    Application service for portfolio data access and mutations.

    All methods are static. Reads use AppContext.read_session(); writes use
    AppContext.write_session(). AppContext must be initialised before calling
    any method (raises AppContextError otherwise).
    """

    # ── Read ─────────────────────────────────────────────────────────────────

    @staticmethod
    def get_portfolio_summary(
        settings: AppSettings | None = None,
        *,
        use_live_true_cost: bool = True,
        as_of: date | None = None,
    ) -> PortfolioSummary:
        """
        Return aggregated portfolio data across all securities.

        For each security, includes all lots with quantity_remaining > 0.
        Securities with no active lots are included with zero totals.

        Args:
            settings: Optional AppSettings used for employment-tax estimation on
                      SIP-like schemes. When None, employment-tax estimates are
                      unavailable in summary tiles.
            use_live_true_cost: When True, income-sensitive lot true costs may be
                      recalculated from current settings. When False, summary uses
                      persisted lot.true_cost_per_share_gbp values.
            as_of: Optional deterministic date override used for sellability,
                      lock, forfeiture, and estimate timing context.

        Returned ORM objects (Security, Lot) are detached; scalar attributes
        are safe to access, relationships are not.
        """
        today = as_of or date.today()

        with AppContext.read_session() as sess:
            sec_repo   = SecurityRepository(sess)
            lot_repo   = LotRepository(sess)
            price_repo = PriceRepository(sess)

            security_summaries: list[SecuritySummary] = []

            show_exhausted = settings.show_exhausted_lots if settings else False
            price_stale_after_days = settings.price_stale_after_days if settings else 1
            fx_stale_after_minutes = settings.fx_stale_after_minutes if settings else 10

            for security in sec_repo.list_all():
                all_lots = lot_repo.get_all_lots_for_security(security.id)
                lots = (
                    all_lots
                    if show_exhausted
                    else [lot for lot in all_lots if Decimal(lot.quantity_remaining) > Decimal("0")]
                )
                espp_plus_employee_ids = _espp_plus_employee_lot_ids(all_lots)
                at_risk_employee_lot_ids = _matched_employee_lot_ids_at_risk(lots, today)
                lot_summaries: list[LotSummary] = []

                for lot in lots:
                    qty = Decimal(lot.quantity_remaining)
                    if (
                        lot.scheme_type == "RSU"
                        and lot.fmv_at_acquisition_gbp is not None
                        and settings is not None
                    ):
                        # Always derive live from configured marginal rates.
                        true_cost_per_share = _rsu_live_true_cost_per_share(lot, settings)
                    elif use_live_true_cost:
                        true_cost_per_share = _live_true_cost_per_share(
                            lot,
                            settings=settings,
                            espp_plus_employee_ids=espp_plus_employee_ids,
                        )
                    else:
                        true_cost_per_share = Decimal(lot.true_cost_per_share_gbp)
                    cost_basis = qty * Decimal(lot.acquisition_price_gbp)
                    true_cost = (qty * true_cost_per_share).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    sellability_status, sellability_unlock_date = _sellability_for_lot(
                        lot,
                        as_of=today,
                        at_risk_employee_lot_ids=at_risk_employee_lot_ids,
                    )
                    lot_summaries.append(LotSummary(
                        lot=lot,
                        quantity_remaining=qty,
                        true_cost_per_share_gbp=true_cost_per_share,
                        cost_basis_total_gbp=cost_basis,
                        true_cost_total_gbp=true_cost,
                        sellability_status=sellability_status,
                        sellability_unlock_date=sellability_unlock_date,
                    ))

                total_qty  = sum((ls.quantity_remaining    for ls in lot_summaries), Decimal("0"))
                total_cost = sum((ls.cost_basis_total_gbp  for ls in lot_summaries), Decimal("0"))
                total_true = sum((ls.true_cost_total_gbp   for ls in lot_summaries), Decimal("0"))

                # Badge values are computed from priced lots. Keep explicit defaults
                # so unpriced securities still render without runtime errors.
                badge_sellable_mv: Decimal | None = None
                badge_espp_plus_pending_mv: Decimal | None = None
                badge_rsu_vesting_mv: Decimal | None = None

                # Phase L: attach live price data when available
                price_row = price_repo.get_latest(security.id)
                if price_row is not None and price_row.close_price_gbp is not None:
                    try:
                        current_price_native = Decimal(price_row.close_price_original_ccy)
                    except (ValueError, TypeError):
                        current_price_native = None
                    native_currency = (
                        (price_row.currency or security.currency or "").strip().upper()
                        or None
                    )
                    current_price = Decimal(price_row.close_price_gbp)
                    market_value_native = (
                        (total_qty * current_price_native).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        if current_price_native is not None
                        else None
                    )
                    market_value  = (total_qty * current_price).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    unrealised_cgt      = market_value - total_cost
                    unrealised_economic = market_value - total_true
                    price_as_of = price_row.price_date
                    # Extract price-tab and FX timestamps from the source field.
                    # Source formats:
                    #   GBP: "google_sheets:{price_ts}" or "yfinance:{price_ts}"
                    #   USD: "{provider}:{price_ts}|fx:{fx_ts}"
                    #   IB:  "ibkr" or "ibkr|fx:{fx_ts}" (snapshot time lives in
                    #        PriceTickerSnapshot.observed_at)
                    _src = price_row.source or ""
                    _legacy_prefix = "google_sheets:"
                    _live_prefix = "yfinance:"
                    _fx_sep = "|fx:"
                    if _src.startswith(_legacy_prefix) or _src.startswith(_live_prefix):
                        if _src.startswith(_legacy_prefix):
                            _after = _src[len(_legacy_prefix):]
                        else:
                            _after = _src[len(_live_prefix):]
                        if _fx_sep in _after:
                            _pts, _fts = _after.split(_fx_sep, 1)
                            price_refreshed_at: str | None = _pts or None
                            sec_fx_as_of: str | None = _fts or None
                        else:
                            price_refreshed_at = _after or None
                            sec_fx_as_of = None
                    elif _src.startswith("ibkr"):
                        # Timestamp is stored in PriceTickerSnapshot.observed_at
                        # and surfaced via freshness_text — no separate display needed.
                        price_refreshed_at = None
                        if _fx_sep in _src:
                            _, _fts = _src.split(_fx_sep, 1)
                            sec_fx_as_of = _fts or None
                        else:
                            sec_fx_as_of = None
                    else:
                        price_refreshed_at = None
                        sec_fx_as_of = None
                    price_is_stale = StalenessService.is_price_stale(
                        price_as_of,
                        stale_after_days=price_stale_after_days,
                        today=today,
                    )
                    sec_fx_is_stale = StalenessService.is_fx_stale(
                        sec_fx_as_of,
                        stale_after_minutes=fx_stale_after_minutes,
                    )
                    # Back-fill per-lot market value and P&L
                    for ls in lot_summaries:
                        lot_mkt_native = (
                            (ls.quantity_remaining * current_price_native).quantize(
                                Decimal("0.01"), rounding=ROUND_HALF_UP
                            )
                            if current_price_native is not None
                            else None
                        )
                        lot_mkt = (ls.quantity_remaining * current_price).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        ls.market_value_native = lot_mkt_native
                        ls.market_value_native_currency = native_currency
                        ls.market_value_gbp = lot_mkt
                        ls.unrealised_gain_cgt_gbp = lot_mkt - ls.cost_basis_total_gbp
                        ls.unrealised_gain_economic_gbp = lot_mkt - ls.true_cost_total_gbp
                        # Phase V: risk flags (always, no settings needed)
                        ls.forfeiture_risk = _forfeiture_risk_for_lot(ls.lot, today)
                        ls.sip_qualifying_status = _sip_qualifying_status_for_lot(ls.lot, today)
                        if (
                            ls.sellability_status == SELLABILITY_LOCKED
                            and ls.sellability_unlock_date is not None
                        ):
                            ls.est_net_proceeds_reason = (
                                f"Locked until {ls.sellability_unlock_date.isoformat()}."
                            )
                            continue
                        if ls.lot.scheme_type == "ISA":
                            ls.est_employment_tax_on_lot_gbp = Decimal("0.00")
                            ls.est_net_proceeds_gbp = lot_mkt
                            ls.sell_now_economic_gbp = (
                                lot_mkt - ls.true_cost_total_gbp
                            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            ls.est_net_proceeds_reason = None
                            continue
                        if settings is None:
                            ls.est_net_proceeds_reason = (
                                "Set income profile in Settings to estimate employment tax."
                            )
                            continue
                        lot_est_tax = _estimate_sell_all_employment_tax(
                            [ls.lot],
                            current_price,
                            today,
                            settings,
                        )
                        if lot_est_tax is None:
                            ls.est_net_proceeds_reason = "Employment-tax estimate unavailable."
                            continue
                        ls.est_employment_tax_on_lot_gbp = lot_est_tax
                        ls.est_net_proceeds_gbp = (
                            lot_mkt - lot_est_tax
                        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        ls.sell_now_economic_gbp = (
                            ls.est_net_proceeds_gbp - ls.true_cost_total_gbp
                        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        ls.est_net_proceeds_reason = None
                    # Calculate unrealised gains by category: sellable, locked, and forfeit-risk
                    # Sellable: not locked and not in forfeiture window
                    sellable_market_value = Decimal("0")
                    sellable_cost = Decimal("0")
                    sellable_true = Decimal("0")
                    # Locked: status is LOCKED
                    locked_market_value = Decimal("0")
                    locked_cost = Decimal("0")
                    locked_true = Decimal("0")
                    # Forfeit risk: in forfeiture window
                    forfeit_market_value = Decimal("0")
                    forfeit_cost = Decimal("0")
                    forfeit_true = Decimal("0")
                    # Badge sub-totals (market value only)
                    at_risk_market_value = Decimal("0")     # AT_RISK paid ESPP+ in window
                    sellable_strict_mv = Decimal("0")       # purely SELLABLE lots
                    rsu_vesting_mv = Decimal("0")           # LOCKED RSU lots pre-vest
                    for ls in lot_summaries:
                        if ls.market_value_gbp is None:
                            continue
                        if ls.forfeiture_risk is not None and ls.forfeiture_risk.in_window and ls.lot.matching_lot_id is not None:
                            # Only matched shares (not paid shares) are at forfeit risk
                            forfeit_market_value += ls.market_value_gbp
                            forfeit_cost += ls.cost_basis_total_gbp
                            forfeit_true += ls.true_cost_total_gbp
                        elif ls.sellability_status == SELLABILITY_LOCKED:
                            if ls.lot.scheme_type == "RSU":
                                rsu_vesting_mv += ls.market_value_gbp
                            locked_market_value += ls.market_value_gbp
                            locked_cost += ls.cost_basis_total_gbp
                            locked_true += ls.true_cost_total_gbp
                        else:
                            if ls.sellability_status == SELLABILITY_AT_RISK:
                                at_risk_market_value += ls.market_value_gbp
                            else:
                                sellable_strict_mv += ls.market_value_gbp
                            sellable_market_value += ls.market_value_gbp
                            sellable_cost += ls.cost_basis_total_gbp
                            sellable_true += ls.true_cost_total_gbp
                    # Update unrealised_cgt and unrealised_economic to reflect sellable items only
                    unrealised_cgt = (
                        sellable_market_value - sellable_cost
                        if sellable_market_value > 0
                        else unrealised_cgt  # Keep the total if no sellable items
                    )
                    unrealised_economic = (
                        sellable_market_value - sellable_true
                        if sellable_market_value > 0
                        else unrealised_economic  # Keep the total if no sellable items
                    )
                    locked_unrealised_cgt = (
                        locked_market_value - locked_cost
                        if locked_market_value > 0
                        else None
                    )
                    locked_unrealised_economic = (
                        locked_market_value - locked_true
                        if locked_market_value > 0
                        else None
                    )
                    forfeit_risk_unrealised_cgt = (
                        forfeit_market_value - forfeit_cost
                        if forfeit_market_value > 0
                        else None
                    )
                    forfeit_risk_unrealised_economic = (
                        forfeit_market_value - forfeit_true
                        if forfeit_market_value > 0
                        else None
                    )
                    forfeit_risk_market_value = (
                        -forfeit_market_value
                        if forfeit_market_value > 0
                        else None
                    )
                    _q2 = lambda v: v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    _espp_pending_total = forfeit_market_value + at_risk_market_value
                    badge_sellable_mv = _q2(sellable_strict_mv) if sellable_strict_mv > 0 else None
                    badge_espp_plus_pending_mv = _q2(_espp_pending_total) if _espp_pending_total > 0 else None
                    badge_rsu_vesting_mv = _q2(rsu_vesting_mv) if rsu_vesting_mv > 0 else None
                    sec_est_cgt = _estimate_sell_all_employment_tax(
                        all_lots,
                        current_price,
                        today,
                        settings,
                    )
                    sec_est_net = (
                        (market_value - sec_est_cgt).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        if sec_est_cgt is not None
                        else None
                    )
                else:
                    current_price_native = None
                    current_price = None
                    market_value_native = None
                    native_currency = security.currency
                    market_value  = None
                    unrealised_cgt      = None
                    unrealised_economic = None
                    locked_unrealised_cgt = None
                    locked_unrealised_economic = None
                    forfeit_risk_unrealised_cgt = None
                    forfeit_risk_unrealised_economic = None
                    forfeit_risk_market_value = None
                    price_as_of = None
                    price_is_stale = False
                    price_refreshed_at = None
                    sec_fx_as_of = None
                    sec_fx_is_stale = False
                    # Phase V: risk flags still applied even without price data
                    for ls in lot_summaries:
                        ls.forfeiture_risk = _forfeiture_risk_for_lot(ls.lot, today)
                        ls.sip_qualifying_status = _sip_qualifying_status_for_lot(ls.lot, today)
                        if (
                            ls.sellability_status == SELLABILITY_LOCKED
                            and ls.sellability_unlock_date is not None
                        ):
                            ls.est_net_proceeds_reason = (
                                f"Locked until {ls.sellability_unlock_date.isoformat()}."
                            )
                        else:
                            ls.est_net_proceeds_reason = "No live price available."
                    sec_est_cgt = None
                    sec_est_net = None

                # Post-tax sellable net gain: sum sell_now_economic_gbp for non-locked lots.
                # Mirrors the table's "Gain If Sold Today" column total.
                _sellable_gain_parts: list[Decimal] = []
                _sellable_gain_incomplete = False
                for ls in lot_summaries:
                    if ls.sellability_status == SELLABILITY_LOCKED:
                        continue
                    if ls.sell_now_economic_gbp is None:
                        _sellable_gain_incomplete = True
                        break
                    _sellable_gain_parts.append(ls.sell_now_economic_gbp)
                sellable_net_gain: Decimal | None = (
                    sum(_sellable_gain_parts, Decimal("0")).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    if not _sellable_gain_incomplete and _sellable_gain_parts
                    else None
                )

                # Phase V: security-level risk summary flags
                has_forfeiture = any(
                    ls.forfeiture_risk is not None and ls.forfeiture_risk.in_window
                    for ls in lot_summaries
                )
                has_sip_risk = any(
                    ls.sip_qualifying_status is not None
                    and ls.sip_qualifying_status.category != SIPHoldingPeriodCategory.FIVE_PLUS_YEARS
                    for ls in lot_summaries
                )

                security_summaries.append(SecuritySummary(
                    security=security,
                    active_lots=lot_summaries,
                    total_quantity=total_qty,
                    total_cost_basis_gbp=total_cost,
                    total_true_cost_gbp=total_true,
                    current_price_native=current_price_native,
                    current_price_gbp=current_price,
                    market_value_native=market_value_native,
                    market_value_native_currency=native_currency,
                    market_value_gbp=market_value,
                    unrealised_gain_cgt_gbp=unrealised_cgt,
                    unrealised_gain_economic_gbp=unrealised_economic,
                    locked_unrealised_gain_cgt_gbp=locked_unrealised_cgt,
                    locked_unrealised_gain_economic_gbp=locked_unrealised_economic,
                    forfeit_risk_market_value_gbp=forfeit_risk_market_value,
                    forfeit_risk_unrealised_gain_cgt_gbp=forfeit_risk_unrealised_cgt,
                    forfeit_risk_unrealised_gain_economic_gbp=forfeit_risk_unrealised_economic,
                    price_as_of=price_as_of,
                    price_is_stale=price_is_stale,
                    price_refreshed_at=price_refreshed_at,
                    fx_as_of=sec_fx_as_of,
                    fx_is_stale=sec_fx_is_stale,
                    est_employment_tax_gbp=sec_est_cgt,
                    est_net_proceeds_gbp=sec_est_net,
                    marginal_cgt_rate_used=None,
                    has_forfeiture_risk=has_forfeiture,
                    has_sip_qualifying_risk=has_sip_risk,
                    refresh_last_success_at=None,
                    refresh_last_error=None,
                    refresh_next_due_at=None,
                    sellable_net_gain_gbp=sellable_net_gain,
                    sellable_pure_market_value_gbp=badge_sellable_mv,
                    espp_plus_pending_market_value_gbp=badge_espp_plus_pending_mv,
                    rsu_vesting_market_value_gbp=badge_rsu_vesting_mv,
                ))

            total_cost = sum((ss.total_cost_basis_gbp for ss in security_summaries), Decimal("0"))
            total_true = sum((ss.total_true_cost_gbp  for ss in security_summaries), Decimal("0"))

            market_values = [
                ss.market_value_gbp
                for ss in security_summaries
                if ss.market_value_gbp is not None
            ]
            total_market = (
                sum(market_values, Decimal("0")) if market_values else None
            )

            # Portfolio-level FX info: first non-None fx_as_of across all
            # securities (all USD securities share the same "fx" tab rate).
            fx_as_of_vals = [ss.fx_as_of for ss in security_summaries if ss.fx_as_of]
            portfolio_fx_as_of = fx_as_of_vals[0] if fx_as_of_vals else None
            fx_is_stale = StalenessService.is_fx_stale(
                portfolio_fx_as_of,
                stale_after_minutes=fx_stale_after_minutes,
            )
            has_non_gbp_native_values = any(
                ss.market_value_gbp is not None
                and ss.market_value_native_currency is not None
                and ss.market_value_native_currency != "GBP"
                for ss in security_summaries
            )
            if total_market is None:
                fx_conversion_basis = None
            elif has_non_gbp_native_values:
                if portfolio_fx_as_of:
                    fx_conversion_basis = (
                        "Totals are GBP-based; non-GBP values use latest stored FX conversion."
                    )
                else:
                    fx_conversion_basis = None
            else:
                fx_conversion_basis = (
                    "All valued positions are GBP-denominated; no FX conversion applied."
                )

            # Portfolio-level estimated employment tax (sum of per-security
            # hypothetical sell-all estimates).
            portfolio_est_cgt: Decimal | None = None
            portfolio_est_net: Decimal | None = None
            if total_market is not None:
                per_security_estimates = [
                    ss.est_employment_tax_gbp
                    for ss in security_summaries
                    if ss.market_value_gbp is not None
                ]
                if per_security_estimates and all(v is not None for v in per_security_estimates):
                    portfolio_est_cgt = sum(
                        (v for v in per_security_estimates if v is not None),
                        Decimal("0"),
                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    portfolio_est_net = (total_market - portfolio_est_cgt).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )

            return PortfolioSummary(
                securities=security_summaries,
                total_cost_basis_gbp=total_cost,
                total_true_cost_gbp=total_true,
                total_market_value_gbp=total_market,
                fx_as_of=portfolio_fx_as_of,
                fx_is_stale=fx_is_stale,
                valuation_currency="GBP",
                fx_conversion_basis=fx_conversion_basis,
                est_total_employment_tax_gbp=portfolio_est_cgt,
                est_total_net_liquidation_gbp=portfolio_est_net,
            )

    @staticmethod
    def simulate_disposal(
        security_id: str,
        quantity: Decimal,
        price_per_share_gbp: Decimal,
        *,
        scheme_type: str | None = None,
        as_of_date: date | None = None,
        settings: AppSettings | None = None,
        broker_fees_gbp: Decimal | None = None,
        use_live_true_cost: bool = True,
    ) -> FIFOResult:
        """
        Run FIFO allocation without writing to the database.

        Fetches active lots (optionally filtered by scheme_type / as_of_date),
        runs FIFO, and returns the result enriched with Phase E forfeiture
        warnings and employment tax estimates. Use this to preview the full
        economic cost of a disposal before committing.

        Args:
            security_id        : Security to dispose of.
            quantity           : Number of shares to sell.
            price_per_share_gbp: Disposal price per share in GBP.
            scheme_type        : Optional — restrict FIFO to a single scheme type
                                 (e.g. "SIP_PARTNERSHIP").
            as_of_date         : Optional — only consider lots acquired on or before
                                 this date; also used as the disposal date for tax
                                 and forfeiture calculations (defaults to today).
            settings           : Optional AppSettings for employment tax estimates.
                                 When None, sip_tax_estimates will be empty.
            broker_fees_gbp    : Optional broker fees in GBP. When provided,
                                 realised gains are reduced by fees exactly once.
            use_live_true_cost : When True, income-sensitive lot true cost may be
                                 recalculated using current settings; when False,
                                 FIFO uses persisted lot.true_cost_per_share_gbp.

        Returns FIFOResult with forfeiture_warnings and sip_tax_estimates populated.
        Inspect .is_fully_allocated and .shortfall before calling commit_disposal().
        """
        with AppContext.read_session() as sess:
            lot_repo = LotRepository(sess)
            disposal_date = as_of_date or date.today()
            all_lots = lot_repo.get_all_lots_for_security(security_id)
            espp_plus_employee_ids = _espp_plus_employee_lot_ids(all_lots)
            lots = lot_repo.get_active_lots_for_security(
                security_id,
                scheme_type=scheme_type,
                as_of_date=as_of_date,
            )
            lots = [lot for lot in lots if _is_lot_sellable_on(lot, disposal_date)]
            if not lots:
                raise ValueError(
                    "No sellable lots are available for the selected security/date."
                )
            fifo_lots = [
                _lot_to_fifo(
                    lot,
                    settings=settings,
                    espp_plus_employee_ids=espp_plus_employee_ids,
                    use_live_true_cost=use_live_true_cost,
                )
                for lot in lots
            ]
            result = allocate_fifo(fifo_lots, quantity, price_per_share_gbp)
            result = _apply_broker_fees_to_fifo_result(result, broker_fees_gbp)

            sold_lot_ids = {a.lot_id for a in result.allocations}

            # Forfeiture warnings: ESPP_PLUS lots linked to sold ESPP lots
            forfeiture_warnings = _build_forfeiture_warnings(
                lot_repo, security_id, sold_lot_ids, price_per_share_gbp, disposal_date
            )

            # Employment tax estimates: SIP-like lots only, requires settings
            lots_by_id = {lot.id: lot for lot in lots}
            sip_estimates, total_emp_tax = _build_sip_tax_estimates(
                lots_by_id, result.allocations, price_per_share_gbp, disposal_date, settings
            )

        total_forfeiture_value = sum(
            (w.value_at_risk_gbp for w in forfeiture_warnings), Decimal("0")
        )
        return dataclasses.replace(
            result,
            forfeiture_warnings=tuple(forfeiture_warnings),
            total_forfeiture_value_gbp=total_forfeiture_value,
            sip_tax_estimates=tuple(sip_estimates),
            total_sip_employment_tax_gbp=total_emp_tax,
        )

    @staticmethod
    def get_forfeiture_risk(
        security_id: str,
        quantity: Decimal,
        disposal_date: date,
    ) -> tuple[ForfeitureWarning, ...]:
        """
        Return forfeiture warnings for a proposed disposal on disposal_date.

        Convenience wrapper around simulate_disposal() that uses a £0 price
        (value_at_risk_gbp will be £0 in all returned warnings).

        Useful for checking whether a disposal date is inside the 6-month
        ESPP+ forfeiture window before the user enters a sale price.
        """
        result = PortfolioService.simulate_disposal(
            security_id=security_id,
            quantity=quantity,
            price_per_share_gbp=Decimal("0"),
            as_of_date=disposal_date,
        )
        return result.forfeiture_warnings

    @staticmethod
    def estimate_net_liquidation_value(
        security_id: str,
        quantity: Decimal,
        as_of_date: date,
        settings: AppSettings | None = None,
    ) -> NetLiquidationEstimate:
        """
        Estimate the net after-tax proceeds from selling ``quantity`` shares
        of a given security on ``as_of_date``.

        Pure calculation — no DB writes. Uses FIFO order to determine which
        lots would be consumed, then applies a marginal CGT estimate (no AEA).

        Args:
            security_id : Security to simulate selling.
            quantity    : Number of shares to sell.
            as_of_date  : Reference date for the hypothetical sale.
            settings    : Optional AppSettings for CGT rate derivation.
                          Falls back to 20% (higher rate) if None.

        Returns:
            NetLiquidationEstimate with full breakdown.

        Raises:
            ValueError if no price data is available for the security.
        """
        with AppContext.read_session() as sess:
            price_row = PriceRepository(sess).get_latest(security_id)

        if price_row is None or price_row.close_price_gbp is None:
            raise ValueError(
                f"No price data for security {security_id!r}. "
                "Cannot estimate net liquidation value without a current price."
            )

        current_price = Decimal(price_row.close_price_gbp)
        market_value = (quantity * current_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        fifo_result = PortfolioService.simulate_disposal(
            security_id=security_id,
            quantity=quantity,
            price_per_share_gbp=current_price,
            as_of_date=as_of_date,
        )
        cost_basis = fifo_result.total_cost_basis_gbp
        unrealised = market_value - cost_basis

        marg = _derive_marginal_cgt_rate(settings)
        est_cgt = (max(unrealised, Decimal("0")) * marg).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        est_net = (market_value - est_cgt).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        notes: list[str] = [
            f"Hypothetical sale of {quantity} shares at £{current_price}/share.",
            f"FIFO cost basis: £{cost_basis}. Unrealised gain: £{unrealised}.",
            f"Marginal CGT rate: {marg * 100:.0f}% "
            f"({'from settings' if settings else 'fallback — no income settings saved'}).",
            "AEA not applied here; see full CGT report for AEA-adjusted figure.",
        ]
        if not fifo_result.is_fully_allocated:
            notes.append(
                f"Warning: partial allocation only "
                f"({fifo_result.shortfall} shares unallocated)."
            )

        return NetLiquidationEstimate(
            quantity=quantity,
            market_value_gbp=market_value,
            cost_basis_gbp=cost_basis,
            unrealised_gain_cgt_gbp=unrealised,
            est_cgt_gbp=est_cgt,
            est_net_proceeds_gbp=est_net,
            marginal_rate_used=marg,
            as_of_date=as_of_date,
            notes=notes,
        )

    # ── Write ─────────────────────────────────────────────────────────────────

    @staticmethod
    def add_security(
        ticker: str,
        name: str,
        currency: str,
        *,
        isin: str | None = None,
        exchange: str | None = None,
        units_precision: int = 0,
        dividend_reminder_date: date | None = None,
        catalog_id: str | None = None,
        is_manual_override: bool = False,
    ) -> Security:
        """
        Create and persist a new Security with an audit INSERT entry.

        Args:
            ticker              : Exchange ticker symbol (e.g. "AAPL").
            name                : Full descriptive name (e.g. "Apple Inc.").
            currency            : ISO 4217 currency code (e.g. "USD", "GBP").
            isin                : Optional ISIN identifier.
            exchange            : Optional exchange code (e.g. "NASDAQ").
            units_precision     : Decimal places for quantity (0 = whole shares).
            dividend_reminder_date: Optional annual reminder anchor date for
                                    dividend check/logging workflows.
            catalog_id          : FK to security_catalog.id (Phase S).
            is_manual_override  : True if user bypassed the catalogue lookup.

        Raises ValueError if neither catalog_id nor is_manual_override=True is
        provided — the caller must either pick from the catalogue or explicitly
        acknowledge the manual entry.

        Returns the persisted Security (detached from session after commit).
        Caller should NOT modify the returned object.
        """
        ticker_clean = (ticker or "").strip().upper()
        name_clean = (name or "").strip()
        currency_clean = (currency or "").strip().upper()
        isin_clean = (isin or "").strip().upper() or None
        exchange_clean = (exchange or "").strip().upper() or None

        if not ticker_clean:
            raise ValueError("ticker is required.")
        if not name_clean:
            raise ValueError("name is required.")
        if len(currency_clean) != 3 or not currency_clean.isalpha():
            raise ValueError("currency must be a 3-letter ISO code.")

        if not is_manual_override and catalog_id is None:
            raise ValueError(
                "catalog_id is required unless is_manual_override=True. "
                "Select an instrument from the catalogue or enable "
                "'Add unlisted instrument'."
            )

        with AppContext.write_session() as sess:
            repo = SecurityRepository(sess)
            audit = AuditRepository(sess)

            for existing in repo.list_all():
                existing_ticker = str(existing.ticker or "").strip().upper()
                existing_currency = str(existing.currency or "").strip().upper()
                existing_isin = (existing.isin or "").strip().upper()
                if existing_ticker == ticker_clean and existing_currency == currency_clean:
                    raise ValueError(
                        f"Security {ticker_clean} ({currency_clean}) already exists."
                    )
                if existing_ticker == ticker_clean and existing_currency != currency_clean:
                    raise ValueError(
                        f"Ticker {ticker_clean} already exists with currency "
                        f"{existing_currency}; add ISIN/exchange disambiguation."
                    )
                if isin_clean and existing_isin and isin_clean == existing_isin:
                    raise ValueError(
                        f"ISIN {isin_clean} is already linked to ticker "
                        f"{existing_ticker} ({existing_currency})."
                    )

            security = Security(
                ticker=ticker_clean,
                name=name_clean,
                currency=currency_clean,
                isin=isin_clean,
                exchange=exchange_clean,
                units_precision=units_precision,
                dividend_reminder_date=dividend_reminder_date,
                catalog_id=catalog_id,
                is_manual_override=is_manual_override,
            )
            # Pre-assign ID so the audit entry can reference it before flush.
            security.id = _new_uuid()

            repo.add(security)
            audit.log_insert(
                table_name="securities",
                record_id=security.id,
                new_values={
                    "ticker": ticker_clean,
                    "name": name_clean,
                    "currency": currency_clean,
                    "isin": str(isin_clean),
                    "exchange": str(exchange_clean),
                    "units_precision": str(units_precision),
                    "dividend_reminder_date": str(dividend_reminder_date),
                    "catalog_id": str(catalog_id),
                    "is_manual_override": str(is_manual_override),
                },
            )

        return security

    @staticmethod
    def set_security_dividend_reminder_date(
        *,
        security_id: str,
        dividend_reminder_date: date | None,
    ) -> Security:
        """
        Update (or clear) the annual dividend reminder date for one security.
        """
        with AppContext.write_session() as sess:
            repo = SecurityRepository(sess)
            audit = AuditRepository(sess)
            security = repo.require_by_id(security_id)
            repo.update(
                security,
                dividend_reminder_date=dividend_reminder_date,
                audit=audit,
            )
        return security

    @staticmethod
    def _add_lot_in_session(
        sess: Session,
        *,
        security_id: str,
        scheme_type: str,
        acquisition_date: date,
        quantity: Decimal,
        acquisition_price_gbp: Decimal,
        true_cost_per_share_gbp: Decimal,
        tax_year: str,
        grant_id: str | None = None,
        fmv_at_acquisition_gbp: Decimal | None = None,
        acquisition_price_original_ccy: Decimal | None = None,
        original_currency: str | None = None,
        broker_currency: str | None = None,
        fx_rate_at_acquisition: Decimal | None = None,
        fx_rate_source: str | None = None,
        external_id: str | None = None,
        broker_reference: str | None = None,
        import_source: str | None = None,
        notes: str | None = None,
        forfeiture_period_end: date | None = None,
        matching_lot_id: str | None = None,
    ) -> Lot:
        lot_repo = LotRepository(sess)
        audit = AuditRepository(sess)
        normalized_original_currency = (
            original_currency.strip().upper()
            if original_currency is not None and original_currency.strip()
            else None
        )
        normalized_broker_currency = _normalize_broker_currency(broker_currency)
        if normalized_broker_currency is None:
            normalized_broker_currency = _normalize_broker_currency(
                normalized_original_currency
            )
        if normalized_broker_currency is None and scheme_type in {"BROKERAGE", "ISA"}:
            security = SecurityRepository(sess).get_by_id(security_id)
            normalized_broker_currency = _normalize_broker_currency(
                security.currency if security is not None else None
            ) or "GBP"

        lot = Lot(
            security_id=security_id,
            scheme_type=scheme_type,
            acquisition_date=acquisition_date,
            quantity=str(quantity),
            quantity_remaining=str(quantity),
            acquisition_price_gbp=str(acquisition_price_gbp),
            true_cost_per_share_gbp=str(true_cost_per_share_gbp),
            fmv_at_acquisition_gbp=(
                str(fmv_at_acquisition_gbp) if fmv_at_acquisition_gbp is not None else None
            ),
            acquisition_price_original_ccy=(
                str(acquisition_price_original_ccy)
                if acquisition_price_original_ccy is not None
                else None
            ),
            original_currency=normalized_original_currency,
            broker_currency=normalized_broker_currency,
            fx_rate_at_acquisition=(
                str(fx_rate_at_acquisition) if fx_rate_at_acquisition is not None else None
            ),
            fx_rate_source=fx_rate_source.strip() if fx_rate_source else None,
            tax_year=tax_year,
            grant_id=grant_id,
            external_id=external_id,
            broker_reference=broker_reference,
            import_source=import_source,
            notes=notes,
            forfeiture_period_end=forfeiture_period_end,
            matching_lot_id=matching_lot_id,
        )
        lot_repo.add(lot, audit=audit)
        return lot

    @staticmethod
    def add_lot(
        security_id: str,
        scheme_type: str,
        acquisition_date: date,
        quantity: Decimal,
        acquisition_price_gbp: Decimal,
        true_cost_per_share_gbp: Decimal,
        *,
        tax_year: str | None = None,
        grant_id: str | None = None,
        fmv_at_acquisition_gbp: Decimal | None = None,
        acquisition_price_original_ccy: Decimal | None = None,
        original_currency: str | None = None,
        broker_currency: str | None = None,
        fx_rate_at_acquisition: Decimal | None = None,
        fx_rate_source: str | None = None,
        external_id: str | None = None,
        broker_reference: str | None = None,
        import_source: str | None = None,
        notes: str | None = None,
        forfeiture_period_end: date | None = None,
        matching_lot_id: str | None = None,
    ) -> Lot:
        """
        Create and persist a new acquisition Lot with an audit INSERT entry.

        quantity_remaining is set equal to quantity on creation (full lot).
        tax_year defaults to the UK tax year that contains acquisition_date.

        Args:
            security_id            : FK to the parent Security.
            scheme_type            : One of VALID_SCHEME_TYPES (e.g. "RSU", "SIP_PARTNERSHIP").
            acquisition_date       : Date of the vest / purchase.
            quantity               : Shares acquired.
            acquisition_price_gbp  : CGT cost basis per share in GBP.
            true_cost_per_share_gbp: Economic cost per share (after tax savings).
            tax_year               : UK tax year string (e.g. "2024-25"). Auto-derived if None.
            grant_id               : Optional FK to a Grant record.
            fmv_at_acquisition_gbp : Optional fair market value per share at acquisition.
            acquisition_price_original_ccy:
                                     Optional original-currency acquisition price/share.
            original_currency      : Optional original currency code (e.g. "USD").
            broker_currency        : Optional broker holding currency (3-letter ISO).
            fx_rate_at_acquisition : Optional FX rate used to convert original currency
                                     to GBP at acquisition.
            fx_rate_source         : Optional FX source label.
            external_id            : Idempotency key for duplicate-import detection.
            broker_reference       : Broker confirmation number.
            import_source          : Tag for the import origin (e.g. "manual", "etrade_csv").
            notes                  : Free-text notes.
            forfeiture_period_end  : Exact end of 6-month forfeiture window (ESPP_PLUS lots).
                                     Falls back to acquisition_date + 183 days when None.
            matching_lot_id        : For ESPP_PLUS lots, the lot_id of the linked ESPP
                                     partnership lot. Selling the partnership lot within
                                     6 months forfeits this lot.

        Returns the persisted Lot (detached from session after commit).
        """
        from ..core.tax_engine import tax_year_for_date

        if tax_year is None:
            tax_year = tax_year_for_date(acquisition_date)

        with AppContext.write_session() as sess:
            lot = PortfolioService._add_lot_in_session(
                sess,
                security_id=security_id,
                scheme_type=scheme_type,
                acquisition_date=acquisition_date,
                quantity=quantity,
                acquisition_price_gbp=acquisition_price_gbp,
                true_cost_per_share_gbp=true_cost_per_share_gbp,
                tax_year=tax_year,
                grant_id=grant_id,
                fmv_at_acquisition_gbp=fmv_at_acquisition_gbp,
                acquisition_price_original_ccy=acquisition_price_original_ccy,
                original_currency=original_currency,
                broker_currency=broker_currency,
                fx_rate_at_acquisition=fx_rate_at_acquisition,
                fx_rate_source=fx_rate_source,
                external_id=external_id,
                broker_reference=broker_reference,
                import_source=import_source,
                notes=notes,
                forfeiture_period_end=forfeiture_period_end,
                matching_lot_id=matching_lot_id,
            )

        return lot

    @staticmethod
    def add_espp_plus_lot_pair(
        *,
        security_id: str,
        acquisition_date: date,
        employee_quantity: Decimal,
        employee_acquisition_price_gbp: Decimal,
        employee_true_cost_per_share_gbp: Decimal,
        employee_fmv_at_acquisition_gbp: Decimal,
        matched_quantity: Decimal = Decimal("0"),
        tax_year: str | None = None,
        acquisition_price_original_ccy: Decimal | None = None,
        original_currency: str | None = None,
        broker_currency: str | None = None,
        fx_rate_at_acquisition: Decimal | None = None,
        fx_rate_source: str | None = None,
        employee_import_source: str | None = None,
        notes: str | None = None,
        forfeiture_period_end: date | None = None,
    ) -> tuple[Lot, Lot | None]:
        """
        Create ESPP+ employee and matched lots in one atomic DB transaction.

        This preserves existing lot/audit behavior while ensuring the employee
        lot does not persist if matched-lot creation fails.
        """
        from ..core.tax_engine import tax_year_for_date

        if employee_quantity <= Decimal("0"):
            raise ValueError("employee_quantity must be greater than zero.")
        if matched_quantity < Decimal("0"):
            raise ValueError("matched_quantity cannot be negative.")

        if tax_year is None:
            tax_year = tax_year_for_date(acquisition_date)

        matched_lot: Lot | None = None
        with AppContext.write_session() as sess:
            with sess.begin():
                employee_lot = PortfolioService._add_lot_in_session(
                    sess,
                    security_id=security_id,
                    scheme_type="ESPP_PLUS",
                    acquisition_date=acquisition_date,
                    quantity=employee_quantity,
                    acquisition_price_gbp=employee_acquisition_price_gbp,
                    true_cost_per_share_gbp=employee_true_cost_per_share_gbp,
                    tax_year=tax_year,
                    fmv_at_acquisition_gbp=employee_fmv_at_acquisition_gbp,
                    acquisition_price_original_ccy=acquisition_price_original_ccy,
                    original_currency=original_currency,
                    broker_currency=broker_currency,
                    fx_rate_at_acquisition=fx_rate_at_acquisition,
                    fx_rate_source=fx_rate_source,
                    import_source=employee_import_source,
                    notes=notes,
                )

                if matched_quantity > Decimal("0"):
                    matched_lot = PortfolioService._add_lot_in_session(
                        sess,
                        security_id=security_id,
                        scheme_type="ESPP_PLUS",
                        acquisition_date=acquisition_date,
                        quantity=matched_quantity,
                        acquisition_price_gbp=Decimal("0"),
                        true_cost_per_share_gbp=Decimal("0"),
                        tax_year=tax_year,
                        fmv_at_acquisition_gbp=employee_fmv_at_acquisition_gbp,
                        acquisition_price_original_ccy=Decimal("0"),
                        original_currency=original_currency,
                        broker_currency=broker_currency,
                        fx_rate_at_acquisition=fx_rate_at_acquisition,
                        fx_rate_source=fx_rate_source,
                        notes=notes,
                        forfeiture_period_end=forfeiture_period_end
                        or (acquisition_date + timedelta(days=183)),
                        matching_lot_id=employee_lot.id,
                    )

        return employee_lot, matched_lot

    @staticmethod
    def edit_lot(
        lot_id: str,
        *,
        acquisition_date: date,
        quantity: Decimal,
        acquisition_price_gbp: Decimal,
        true_cost_per_share_gbp: Decimal,
        tax_year: str,
        fmv_at_acquisition_gbp: Decimal | None,
        notes: str | None,
        broker_currency: str | None = None,
    ) -> tuple[Lot, str | None]:
        """
        Edit safe-to-correct lot fields and write a single audit UPDATE entry.

        Editable fields:
          - acquisition_date, tax_year
          - quantity (quantity_remaining is adjusted to preserve disposed amount)
          - acquisition_price_gbp, true_cost_per_share_gbp, fmv_at_acquisition_gbp
          - notes
          - broker_currency (optional update; 3-letter ISO)
        """
        if quantity <= Decimal("0"):
            raise ValueError("quantity must be greater than zero.")
        if acquisition_price_gbp < Decimal("0"):
            raise ValueError("acquisition_price_gbp must be non-negative.")
        if true_cost_per_share_gbp < Decimal("0"):
            raise ValueError("true_cost_per_share_gbp must be non-negative.")
        if fmv_at_acquisition_gbp is not None and fmv_at_acquisition_gbp < Decimal("0"):
            raise ValueError("fmv_at_acquisition_gbp must be non-negative when set.")
        if len(tax_year.strip()) == 0:
            raise ValueError("tax_year is required.")

        with AppContext.write_session() as sess:
            lot_repo = LotRepository(sess)
            audit = AuditRepository(sess)
            lot = lot_repo.require_by_id(lot_id)

            old_values: dict[str, str] = {}
            new_values: dict[str, str] = {}

            def _set(field: str, value: str | date | None) -> None:
                current = getattr(lot, field)
                if current == value:
                    return
                old_values[field] = str(current) if current is not None else ""
                new_values[field] = str(value) if value is not None else ""
                setattr(lot, field, value)

            old_qty = Decimal(lot.quantity)
            old_remaining = Decimal(lot.quantity_remaining)
            disposed_qty = old_qty - old_remaining
            if quantity < disposed_qty:
                raise ValueError(
                    "quantity cannot be lower than already disposed quantity "
                    f"({disposed_qty})."
                )
            new_remaining = quantity - disposed_qty

            _set("acquisition_date", acquisition_date)
            _set("tax_year", tax_year.strip())
            _set("quantity", str(quantity))
            _set("quantity_remaining", str(new_remaining))
            _set("acquisition_price_gbp", str(acquisition_price_gbp))
            _set("true_cost_per_share_gbp", str(true_cost_per_share_gbp))
            _set(
                "fmv_at_acquisition_gbp",
                (str(fmv_at_acquisition_gbp) if fmv_at_acquisition_gbp is not None else None),
            )
            _set("notes", notes.strip() if notes is not None and notes.strip() else None)
            if broker_currency is not None:
                _set("broker_currency", _normalize_broker_currency(broker_currency))

            audit_id: str | None = None
            if old_values:
                entry = audit.log_update(
                    table_name="lots",
                    record_id=lot.id,
                    old_values=old_values,
                    new_values=new_values,
                    notes="Lot edited via UI correction flow",
                )
                sess.flush()
                audit_id = entry.id

        return lot, audit_id

    @staticmethod
    def transfer_lot_to_brokerage(
        lot_id: str,
        *,
        notes: str | None = None,
        settings: AppSettings | None = None,
        quantity: Decimal | None = None,
        destination_broker_currency: str | None = None,
    ) -> tuple[Lot, str]:
        """
        Transfer an eligible lot into BROKERAGE custody without disposal.

        This is a non-disposal reclassification for account/custody tracking.
        No Transaction or LotDisposal rows are created.
        """
        with AppContext.write_session() as sess:
            lot_repo = LotRepository(sess)
            employment_tax_event_repo = EmploymentTaxEventRepository(sess)
            audit = AuditRepository(sess)
            lot = lot_repo.require_by_id(lot_id)
            security = SecurityRepository(sess).require_by_id(lot.security_id)
            today = date.today()

            requested_qty = quantity
            if requested_qty is not None and requested_qty <= Decimal("0"):
                raise ValueError("Transfer quantity must be greater than zero.")

            allowed_source = {"RSU", "ESPP", "ESPP_PLUS"}
            if lot.scheme_type not in allowed_source:
                raise ValueError(
                    "Only RSU, ESPP, and ESPP_PLUS lots can be transferred to BROKERAGE. "
                    "ISA is not a transfer destination; use disposal then Add Lot."
                )
            if Decimal(lot.quantity_remaining) <= Decimal("0"):
                raise ValueError("Cannot transfer an exhausted lot.")
            if lot.scheme_type == "RSU" and today < lot.acquisition_date:
                raise ValueError(
                    "RSU lots can only be transferred after vest date "
                    f"({lot.acquisition_date.isoformat()})."
                )
            if lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None:
                raise ValueError(
                    "Select the linked ESPP+ employee lot for transfer. "
                    "Matched lots in forfeiture window are lost on transfer."
                )

            explicit_destination_broker_currency = (
                _normalize_broker_currency(destination_broker_currency)
                if destination_broker_currency is not None
                else None
            )

            def _merge_notes(existing: str | None, extra: str) -> str:
                base = existing.strip() if existing else ""
                return f"{base}\n{extra}".strip() if base else extra

            def _as_integral_shares(value: Decimal) -> bool:
                return value == value.to_integral_value()

            def _whole_shares(value: Decimal) -> Decimal:
                return value.to_integral_value(rounding=ROUND_FLOOR)

            def _broker_transfer_external_id(source_lot_id: str) -> str:
                return f"transfer-origin-lot:{source_lot_id}"

            def _try_phase_a_currency(value: str | None) -> str | None:
                try:
                    return _normalize_broker_currency(value)
                except ValueError:
                    return None

            def _resolve_destination_broker_currency(source_lot: Lot) -> str:
                if explicit_destination_broker_currency is not None:
                    return explicit_destination_broker_currency
                for candidate in (
                    source_lot.broker_currency,
                    source_lot.original_currency,
                    security.currency,
                ):
                    normalized = _try_phase_a_currency(candidate)
                    if normalized is not None:
                        return normalized
                return "GBP"

            def _upsert_broker_lot_for_source(
                source_lot: Lot,
                move_qty: Decimal,
                note_line: str,
                destination_currency: str,
            ) -> Lot:
                transfer_key = _broker_transfer_external_id(source_lot.id)
                existing_broker = lot_repo.get_by_external_id(transfer_key)

                if existing_broker is not None:
                    if (
                        existing_broker.security_id != source_lot.security_id
                        or existing_broker.scheme_type != "BROKERAGE"
                    ):
                        raise ValueError(
                            "Transfer key conflict for existing broker lot. "
                            "Resolve lot data integrity before transferring."
                        )
                    old_qty = existing_broker.quantity
                    old_remaining = existing_broker.quantity_remaining
                    old_notes = existing_broker.notes or ""
                    old_broker_currency = existing_broker.broker_currency
                    existing_currency = _try_phase_a_currency(old_broker_currency)
                    if (
                        existing_currency is not None
                        and existing_currency != destination_currency
                    ):
                        raise ValueError(
                            "Transfer currency conflicts with existing source-linked "
                            "BROKERAGE lot currency."
                        )
                    new_qty = Decimal(old_qty) + move_qty
                    new_remaining = Decimal(old_remaining) + move_qty
                    existing_broker.quantity = str(new_qty)
                    existing_broker.quantity_remaining = str(new_remaining)
                    existing_broker.notes = _merge_notes(old_notes, note_line)
                    existing_broker.broker_currency = destination_currency
                    audit.log_update(
                        table_name="lots",
                        record_id=existing_broker.id,
                        old_values={
                            "quantity": old_qty,
                            "quantity_remaining": old_remaining,
                            "notes": old_notes,
                            "broker_currency": old_broker_currency or "",
                        },
                        new_values={
                            "quantity": existing_broker.quantity,
                            "quantity_remaining": existing_broker.quantity_remaining,
                            "notes": existing_broker.notes or "",
                            "broker_currency": existing_broker.broker_currency or "",
                        },
                        notes=(
                            "Merged FIFO transfer quantity from source lot "
                            f"{source_lot.id} into existing BROKERAGE lot"
                        ),
                    )
                    return existing_broker

                broker_lot = Lot(
                    security_id=source_lot.security_id,
                    grant_id=source_lot.grant_id,
                    scheme_type="BROKERAGE",
                    tax_year=source_lot.tax_year,
                    acquisition_date=source_lot.acquisition_date,
                    quantity=str(move_qty),
                    quantity_remaining=str(move_qty),
                    acquisition_price_gbp=source_lot.acquisition_price_gbp,
                    true_cost_per_share_gbp=source_lot.true_cost_per_share_gbp,
                    fmv_at_acquisition_gbp=source_lot.fmv_at_acquisition_gbp,
                    acquisition_price_original_ccy=source_lot.acquisition_price_original_ccy,
                    original_currency=source_lot.original_currency,
                    broker_currency=destination_currency,
                    fx_rate_at_acquisition=source_lot.fx_rate_at_acquisition,
                    fx_rate_source=source_lot.fx_rate_source,
                    broker_reference=source_lot.broker_reference,
                    import_source="ui_transfer_to_brokerage",
                    external_id=transfer_key,
                    notes=note_line,
                )
                lot_repo.add(broker_lot, audit=audit)
                return broker_lot

            # ESPP transfer supports editable quantity with strict FIFO order.
            # Quantity input must be an integer number of shares.
            if lot.scheme_type == "ESPP":
                fifo_lots = lot_repo.get_active_lots_for_security(
                    lot.security_id,
                    scheme_type="ESPP",
                )
                if not fifo_lots:
                    raise ValueError("No active ESPP lots are available for transfer.")

                fifo_head = fifo_lots[0]
                if fifo_head.id != lot.id:
                    raise ValueError(
                        "ESPP transfers must follow FIFO order. "
                        f"Select the oldest active ESPP lot first ({fifo_head.acquisition_date.isoformat()})."
                    )

                total_available_raw = sum(
                    (Decimal(src.quantity_remaining) for src in fifo_lots),
                    Decimal("0"),
                )
                total_available_whole = _whole_shares(total_available_raw)

                transfer_qty = (
                    requested_qty
                    if requested_qty is not None
                    else total_available_whole
                )
                if not _as_integral_shares(transfer_qty):
                    raise ValueError(
                        "ESPP transfer quantity must be whole shares (no fractional quantity)."
                    )
                if transfer_qty <= Decimal("0"):
                    raise ValueError(
                        "No whole ESPP shares are available to transfer."
                    )

                if transfer_qty > total_available_whole:
                    raise ValueError(
                        f"Transfer quantity ({transfer_qty}) exceeds active ESPP quantity "
                        f"available as whole shares ({total_available_whole})."
                    )

                resolved_transfer_currency = _resolve_destination_broker_currency(lot)
                remaining_to_move = transfer_qty
                primary_transferred_lot: Lot | None = None
                affected_source_lot_ids: list[str] = []

                for src in fifo_lots:
                    if remaining_to_move <= Decimal("0"):
                        break

                    src_qty = Decimal(src.quantity_remaining)
                    if src_qty <= Decimal("0"):
                        continue

                    move_qty = min(src_qty, remaining_to_move)
                    if move_qty <= Decimal("0"):
                        continue

                    transfer_line = (
                        f"Transferred {move_qty} shares to BROKERAGE "
                        f"(FIFO from ESPP source lot {src.id} on {today.isoformat()})."
                    )
                    broker_lot = _upsert_broker_lot_for_source(
                        src,
                        move_qty,
                        transfer_line,
                        resolved_transfer_currency,
                    )
                    if primary_transferred_lot is None:
                        primary_transferred_lot = broker_lot

                    old_src_qty = src.quantity_remaining
                    old_src_notes = src.notes or ""
                    src.quantity_remaining = str(src_qty - move_qty)
                    src.notes = _merge_notes(old_src_notes, transfer_line)
                    audit.log_update(
                        table_name="lots",
                        record_id=src.id,
                        old_values={
                            "quantity_remaining": old_src_qty,
                            "notes": old_src_notes,
                        },
                        new_values={
                            "quantity_remaining": src.quantity_remaining,
                            "notes": src.notes or "",
                        },
                        notes="ESPP FIFO transfer to BROKERAGE (partial/full by lot).",
                    )

                    affected_source_lot_ids.append(src.id)
                    remaining_to_move -= move_qty

                if remaining_to_move > Decimal("0"):
                    raise ValueError(
                        "Could not satisfy transfer quantity under FIFO constraints."
                    )
                if primary_transferred_lot is None:
                    raise ValueError("Transfer did not move quantity from selected source lot.")

                entry = audit.log_update(
                    table_name="lots",
                    record_id=lot.id,
                    old_values={},
                    new_values={},
                    notes=(
                        "Lot transfer source_scheme=ESPP destination_scheme=BROKERAGE "
                        f"non_disposal=true fifo_quantity={transfer_qty} "
                        f"affected_source_lots={','.join(affected_source_lot_ids)}"
                    ),
                )
                sess.flush()
                return primary_transferred_lot, entry.id

            # RSU/ESPP+ keep full-lot transfer semantics.
            full_lot_qty = Decimal(lot.quantity_remaining)
            if requested_qty is not None and requested_qty != full_lot_qty:
                raise ValueError(
                    f"{lot.scheme_type} transfer must use full remaining quantity "
                    f"({full_lot_qty})."
                )

            forfeited_match_qty = Decimal("0")
            forfeited_match_count = 0
            est_transfer_tax: Decimal | None = None

            if lot.scheme_type == "ESPP_PLUS":
                linked_matched = [
                    ml
                    for ml in lot_repo.get_active_lots_for_security(
                        lot.security_id,
                        scheme_type="ESPP_PLUS",
                    )
                    if ml.matching_lot_id == lot.id and Decimal(ml.quantity_remaining) > Decimal("0")
                ]
                for matched in linked_matched:
                    risk = _forfeiture_risk_for_lot(matched, today)
                    if risk is None or not risk.in_window:
                        continue

                    old_qty = matched.quantity_remaining
                    old_notes = matched.notes or ""
                    old_import_source = matched.import_source or ""

                    forfeited_match_qty += Decimal(old_qty)
                    forfeited_match_count += 1

                    matched.quantity_remaining = "0"
                    matched.import_source = "ui_transfer_forfeiture"
                    matched.notes = _merge_notes(
                        old_notes,
                        (
                            "Forfeited on transfer of linked ESPP+ employee lot "
                            f"{lot.id} ({today.isoformat()})."
                        ),
                    )

                    audit.log_update(
                        table_name="lots",
                        record_id=matched.id,
                        old_values={
                            "quantity_remaining": old_qty,
                            "import_source": old_import_source,
                            "notes": old_notes,
                        },
                        new_values={
                            "quantity_remaining": matched.quantity_remaining,
                            "import_source": matched.import_source or "",
                            "notes": matched.notes or "",
                        },
                        notes=(
                            "ESPP_PLUS matched lot forfeited due to transfer "
                            f"of linked employee lot {lot.id}"
                        ),
                    )

                price_repo = PriceRepository(sess)
                latest_price = price_repo.get_latest(lot.security_id)
                transfer_price = (
                    Decimal(latest_price.close_price_gbp)
                    if latest_price is not None and latest_price.close_price_gbp is not None
                    else Decimal(lot.acquisition_price_gbp)
                )
                est_transfer_tax = _estimate_sell_all_employment_tax(
                    [lot],
                    transfer_price,
                    today,
                    settings,
                )

            source_scheme = lot.scheme_type
            old_notes = lot.notes or ""
            old_broker_currency = lot.broker_currency
            resolved_transfer_currency = _resolve_destination_broker_currency(lot)
            transfer_note_parts = [
                f"Transfer {source_scheme} -> BROKERAGE (non-disposal)"
            ]
            if notes and notes.strip():
                transfer_note_parts.append(notes.strip())
            if source_scheme == "ESPP_PLUS":
                if forfeited_match_count > 0:
                    transfer_note_parts.append(
                        "matched_forfeited="
                        f"{forfeited_match_count} lots ({forfeited_match_qty} shares)"
                    )
            transfer_note = "; ".join(transfer_note_parts)
            merged_notes = (
                f"{old_notes}\n{transfer_note}".strip() if old_notes else transfer_note
            )

            old_values = {
                "scheme_type": source_scheme,
                "import_source": lot.import_source or "",
                "notes": old_notes,
                "broker_currency": old_broker_currency or "",
            }

            lot.scheme_type = "BROKERAGE"
            lot.import_source = "ui_transfer_to_brokerage"
            lot.notes = merged_notes
            lot.broker_currency = resolved_transfer_currency

            if source_scheme == "ESPP_PLUS":
                employment_tax_event_repo.add(
                    lot_id=lot.id,
                    security_id=lot.security_id,
                    event_type="ESPP_PLUS_TRANSFER",
                    event_date=today,
                    estimated_tax_gbp=(
                        est_transfer_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if est_transfer_tax is not None
                        else None
                    ),
                    estimation_notes=(
                        None
                        if est_transfer_tax is not None
                        else "Estimate unavailable; configure Settings."
                    ),
                    source="ui_transfer_to_brokerage",
                )

            new_values = {
                "scheme_type": lot.scheme_type,
                "import_source": lot.import_source,
                "notes": lot.notes,
                "broker_currency": lot.broker_currency or "",
            }

            entry = audit.log_update(
                table_name="lots",
                record_id=lot.id,
                old_values=old_values,
                new_values=new_values,
                notes=(
                    "Lot transfer source_scheme="
                    f"{source_scheme} destination_scheme=BROKERAGE non_disposal=true "
                    f"forfeited_matched_lots={forfeited_match_count} "
                    f"forfeited_matched_qty={forfeited_match_qty}"
                ),
            )
            sess.flush()
            audit_id = entry.id

        return lot, audit_id

    @staticmethod
    def commit_disposal(
        security_id: str,
        quantity: Decimal,
        price_per_share_gbp: Decimal,
        transaction_date: date,
        *,
        scheme_type: str | None = None,
        settings: AppSettings | None = None,
        broker_fees_gbp: Decimal | None = None,
        broker_reference: str | None = None,
        import_source: str | None = None,
        external_id: str | None = None,
        notes: str | None = None,
        use_live_true_cost: bool = True,
    ) -> tuple[Transaction, list[LotDisposal]]:
        """
        Run FIFO allocation and persist the disposal atomically.

        Fetches active lots (filtered by scheme_type if provided), allocates
        them FIFO, creates a DISPOSAL Transaction and per-lot LotDisposal records,
        and reduces each lot's quantity_remaining — all in a single session.

        Raises ValueError if there are insufficient active lots (shortfall > 0).

        Args:
            security_id        : Security being sold.
            quantity           : Number of shares sold.
            price_per_share_gbp: Disposal price per share in GBP.
            transaction_date   : Settlement date (used for tax year classification).
            scheme_type        : Optional — restrict FIFO to a single scheme type.
            settings           : Optional AppSettings for live income-sensitive
                                 true-cost calculation during disposal commit.
            use_live_true_cost : When False, commit uses persisted lot true cost.
            broker_fees_gbp    : Broker fees reducing CGT net proceeds.
            broker_reference   : Broker confirmation number.
            import_source      : Source tag (e.g. "manual", "etrade_csv").
            external_id        : Idempotency key for duplicate-import detection.
            notes              : Free-text notes.

        Returns:
            Tuple of (Transaction, list[LotDisposal]). All objects are detached
            from the session after this call returns but scalar attributes are safe.
        """
        with AppContext.write_session() as sess:
            lot_repo  = LotRepository(sess)
            disp_repo = DisposalRepository(sess)
            audit     = AuditRepository(sess)

            all_lots = lot_repo.get_all_lots_for_security(security_id)
            espp_plus_employee_ids = _espp_plus_employee_lot_ids(all_lots)
            lots = lot_repo.get_active_lots_for_security(
                security_id, scheme_type=scheme_type
            )
            lots = [lot for lot in lots if _is_lot_sellable_on(lot, transaction_date)]
            if not lots:
                raise ValueError(
                    f"Insufficient active lots for security {security_id!r}: "
                    "no active lots found. Cannot persist a disposal."
                )

            fifo_lots = [
                _lot_to_fifo(
                    lot,
                    settings=settings,
                    espp_plus_employee_ids=espp_plus_employee_ids,
                    use_live_true_cost=use_live_true_cost,
                )
                for lot in lots
            ]
            fifo_result = allocate_fifo(fifo_lots, quantity, price_per_share_gbp)

            if not fifo_result.is_fully_allocated:
                raise ValueError(
                    f"Insufficient active lots for security {security_id!r}: "
                    f"shortfall of {fifo_result.shortfall} shares. "
                    "Cannot persist an incomplete disposal."
                )
            fifo_result = _apply_broker_fees_to_fifo_result(
                fifo_result, broker_fees_gbp
            )
            sold_lot_ids = {a.lot_id for a in fifo_result.allocations}
            _forfeit_linked_matched_lots_on_disposal(
                lot_repo=lot_repo,
                audit=audit,
                security_id=security_id,
                sold_lot_ids=sold_lot_ids,
                disposal_date=transaction_date,
            )

            total_proceeds = (quantity * price_per_share_gbp).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            transaction = Transaction(
                security_id=security_id,
                transaction_type="DISPOSAL",
                transaction_date=transaction_date,
                quantity=str(quantity),
                price_per_share_gbp=str(price_per_share_gbp),
                total_proceeds_gbp=str(total_proceeds),
                broker_fees_gbp=(
                    str(broker_fees_gbp) if broker_fees_gbp is not None else None
                ),
                broker_reference=broker_reference,
                import_source=import_source,
                external_id=external_id,
                notes=notes,
            )

            lots_by_id = {lot.id: lot for lot in lots}
            disposals  = disp_repo.record_disposal_from_fifo(
                transaction, fifo_result, lots_by_id, audit=audit
            )

        return transaction, disposals
