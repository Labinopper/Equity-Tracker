"""
FIFO lot allocation engine — pure module (no DB, no I/O).

Rules:
  - Lots are consumed oldest-first: acquisition_date ASC, lot_id ASC for same-day.
  - The caller is responsible for passing lots in the correct FIFO order.
    LotRepository.get_active_lots_for_security() returns them pre-sorted.
  - Exhausted lots (quantity_remaining == 0) are silently skipped.
  - If insufficient lots exist, FIFOResult.shortfall > 0 and no exception is raised.
    It is the caller's responsibility to check for shortfall before persisting.

Decimal rounding:
  - Monetary totals are rounded to 2 d.p. using ROUND_HALF_UP at the per-lot level.
  - Summation of rounded per-lot values may differ from rounding the grand total —
    this is standard practice in HMRC CGT calculations (rounding at the lot level).

All monetary inputs and outputs are decimal.Decimal. Float is never used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_TWO_DP = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LotForFIFO:
    """
    Minimal lot projection for FIFO allocation.

    The repository populates this from Lot ORM objects before calling allocate_fifo().

    Attributes:
        lot_id               : Lot.id (UUID string)
        acquisition_date     : Date for FIFO ordering (must be pre-sorted by caller)
        quantity_remaining   : Shares available for disposal (> 0)
        acquisition_price_gbp: CGT cost basis per share (HMRC sees this)
        true_cost_per_share_gbp: Economic net cost per share (after tax savings)
    """

    lot_id: str
    acquisition_date: date
    quantity_remaining: Decimal
    acquisition_price_gbp: Decimal
    true_cost_per_share_gbp: Decimal


@dataclass(frozen=True)
class FIFOAllocation:
    """
    The result of consuming (part of) one lot in a FIFO disposal.

    All monetary fields are totals for quantity_allocated (not per-share).
    """

    lot_id: str
    acquisition_date: date
    quantity_allocated: Decimal

    cost_basis_gbp: Decimal             # qty * acquisition_price_gbp
    true_cost_gbp: Decimal              # qty * true_cost_per_share_gbp
    proceeds_gbp: Decimal               # qty * disposal_price_per_share

    realised_gain_gbp: Decimal          # proceeds - cost_basis  (CGT gain/loss)
    realised_gain_economic_gbp: Decimal # proceeds - true_cost   (economic gain/loss)


@dataclass(frozen=True)
class ForfeitureWarning:
    """
    An ESPP_PLUS matching lot that would be forfeited by a proposed ESPP disposal.

    Populated by simulate_disposal() via service-layer DB lookup.
    allocate_fifo() always returns forfeiture_warnings=() — it has no DB access.

    lot_id / acquisition_date       : the ESPP_PLUS matching lot at risk of forfeiture
    forfeiture_end_date             : exact end of window (DB field) or estimated (acq + 183d)
    days_remaining                  : forfeiture_end_date - disposal_date (always > 0 here)
    quantity_at_risk                : quantity_remaining of the ESPP_PLUS lot
    value_at_risk_gbp               : quantity_at_risk × disposal price per share
    linked_partnership_lot_id       : matching_lot_id FK — the ESPP lot being sold
    """
    lot_id: str
    acquisition_date: date
    forfeiture_end_date: date
    days_remaining: int
    quantity_at_risk: Decimal
    value_at_risk_gbp: Decimal
    linked_partnership_lot_id: str | None


@dataclass(frozen=True)
class SIPTaxEstimate:
    """
    Estimated employment tax for one FIFOAllocation involving a SIP-like lot.

    Populated by simulate_disposal() via process_sip_event() + get_marginal_rates().
    Only present for ESPP, ESPP_PLUS, SIP_PARTNERSHIP, SIP_MATCHING, SIP_DIVIDEND lots.
    Requires AppSettings (income context) to be passed to simulate_disposal().

    holding_period_category         : SIPHoldingPeriodCategory.value string (for Jinja)
    income_taxable_gbp              : amount on which IT (and SL) is charged
    ni_liable_gbp                   : amount on which NI is charged
    est_income_tax_gbp              : income_taxable × marginal IT rate
    est_ni_gbp                      : ni_liable × marginal NI rate
    est_student_loan_gbp            : ni_liable × marginal SL rate (NIC-liable base)
    est_total_employment_tax_gbp    : sum of IT + NI + SL estimates

    Note: sip_rules.py currently returns ni_liable_gbp = Decimal("0") for 3–5yr lots
    (known limitation — NIC should apply for ESPP/ESPP_PLUS/SIP_PARTNERSHIP/SIP_MATCHING
    in the 3–5yr window). This will be corrected in a future phase.
    """
    lot_id: str
    holding_period_category: str
    income_taxable_gbp: Decimal
    ni_liable_gbp: Decimal
    est_income_tax_gbp: Decimal
    est_ni_gbp: Decimal
    est_student_loan_gbp: Decimal
    est_total_employment_tax_gbp: Decimal


@dataclass(frozen=True)
class FIFOResult:
    """
    Aggregated result of a full FIFO disposal across one or more lots.

    Check shortfall == 0 before persisting to the database.
    If shortfall > 0, insufficient lots exist and the sale cannot be completed.

    Phase E additions (forfeiture_warnings, sip_tax_estimates, totals):
    Always () / Decimal("0") when built by allocate_fifo(). Enriched by
    simulate_disposal() via dataclasses.replace() after service-layer lookups.
    """

    allocations: tuple[FIFOAllocation, ...]  # ordered oldest-first

    quantity_requested: Decimal
    quantity_sold: Decimal      # sum(a.quantity_allocated for a in allocations)
    shortfall: Decimal          # quantity_requested - quantity_sold; 0 if fully allocated

    disposal_price_gbp: Decimal

    total_proceeds_gbp: Decimal
    total_cost_basis_gbp: Decimal
    total_true_cost_gbp: Decimal
    total_realised_gain_gbp: Decimal
    total_realised_gain_economic_gbp: Decimal

    # Phase E: forfeiture and employment tax enrichment
    forfeiture_warnings: tuple[ForfeitureWarning, ...] = field(default_factory=tuple)
    total_forfeiture_value_gbp: Decimal = field(default_factory=lambda: Decimal("0"))
    sip_tax_estimates: tuple[SIPTaxEstimate, ...] = field(default_factory=tuple)
    total_sip_employment_tax_gbp: Decimal = field(default_factory=lambda: Decimal("0"))

    @property
    def is_fully_allocated(self) -> bool:
        return self.shortfall == Decimal("0")


# ---------------------------------------------------------------------------
# Core allocation function
# ---------------------------------------------------------------------------

def allocate_fifo(
    lots: list[LotForFIFO],
    quantity_to_sell: Decimal,
    disposal_price_gbp: Decimal,
) -> FIFOResult:
    """
    Allocate a disposal quantity across lots using FIFO order.

    Args:
        lots              : Lots available for disposal. MUST be pre-sorted
                            (acquisition_date ASC, lot_id ASC). Lots with
                            quantity_remaining <= 0 are silently skipped.
        quantity_to_sell  : Total shares to dispose of (must be > 0).
        disposal_price_gbp: Per-share disposal price in GBP (must be >= 0).

    Returns:
        FIFOResult with per-lot allocations and aggregated totals.
        Does NOT modify the input lots — the caller updates the DB.

    Raises:
        ValueError: if lots is empty, quantity_to_sell <= 0, or
                    disposal_price_gbp < 0.
    """
    if not lots:
        raise ValueError("No lots provided for FIFO allocation.")
    if quantity_to_sell <= Decimal("0"):
        raise ValueError(
            f"quantity_to_sell must be positive; got {quantity_to_sell!r}"
        )
    if disposal_price_gbp < Decimal("0"):
        raise ValueError(
            f"disposal_price_gbp must be non-negative; got {disposal_price_gbp!r}"
        )

    remaining = quantity_to_sell
    allocations: list[FIFOAllocation] = []

    for lot in lots:
        if remaining <= Decimal("0"):
            break
        if lot.quantity_remaining <= Decimal("0"):
            continue  # exhausted lot; skip

        allocated = min(lot.quantity_remaining, remaining)
        remaining -= allocated

        cost_basis = _round(allocated * lot.acquisition_price_gbp)
        true_cost = _round(allocated * lot.true_cost_per_share_gbp)
        proceeds = _round(allocated * disposal_price_gbp)

        allocations.append(
            FIFOAllocation(
                lot_id=lot.lot_id,
                acquisition_date=lot.acquisition_date,
                quantity_allocated=allocated,
                cost_basis_gbp=cost_basis,
                true_cost_gbp=true_cost,
                proceeds_gbp=proceeds,
                realised_gain_gbp=proceeds - cost_basis,
                realised_gain_economic_gbp=proceeds - true_cost,
            )
        )

    quantity_sold = sum(
        (a.quantity_allocated for a in allocations), Decimal("0")
    )

    return FIFOResult(
        allocations=tuple(allocations),
        quantity_requested=quantity_to_sell,
        quantity_sold=quantity_sold,
        shortfall=quantity_to_sell - quantity_sold,
        disposal_price_gbp=disposal_price_gbp,
        total_proceeds_gbp=sum(
            (a.proceeds_gbp for a in allocations), Decimal("0")
        ),
        total_cost_basis_gbp=sum(
            (a.cost_basis_gbp for a in allocations), Decimal("0")
        ),
        total_true_cost_gbp=sum(
            (a.true_cost_gbp for a in allocations), Decimal("0")
        ),
        total_realised_gain_gbp=sum(
            (a.realised_gain_gbp for a in allocations), Decimal("0")
        ),
        total_realised_gain_economic_gbp=sum(
            (a.realised_gain_economic_gbp for a in allocations), Decimal("0")
        ),
    )
