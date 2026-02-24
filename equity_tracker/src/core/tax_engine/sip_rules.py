"""
Share Incentive Plan (SIP) rules engine.

Design principle: event-driven state transitions, not simple holding-period checks.
Each SIP event (withdrawal, sale, forfeiture, employer-leaver, transfer) is processed
as a discrete state transition that may trigger income tax clawback, NI, and/or CGT.

SIP Share Types:
    PARTNERSHIP: Bought from gross salary (up to £1,800/year, 2024-25).
                 Tax saving at purchase = gross × combined_marginal_rate.
    MATCHING:    Employer-awarded, linked to partnership shares.
                 Free at acquisition. Subject to forfeiture if partnership shares
                 removed within 3 years.
    DIVIDEND:    Acquired from dividends reinvested within the plan.
                 No income tax at acquisition. Same holding rules as matching shares.

Key holding period rules (same for all SIP share types):
    < 3 years in plan:  Income tax + NI charged on market value at disposal/withdrawal.
                        This effectively claws back the original tax saving.
    3–5 years in plan:  Income tax on LOWER of:
                          (a) market value when removed from plan, or
                          (b) market value when acquired (= original cost/gross deduction
                              for partnership; FMV for matching/dividend shares).
                        NI is NOT charged (only IT).
    5+ years in plan:   No income tax, no NI. Disposal is completely income-tax free.

CGT treatment:
    For all SIP shares, when removed from the plan (withdrawn or sold):
    - The CGT base cost = market value at the point of removal from the plan.
    - Any subsequent sale of shares (after removal) is a normal CGT disposal
      using this base cost.
    - For in-plan disposals (sold while still in SIP): proceeds = CGT disposal amount,
      cost = market value at acquisition (for matching/dividend) or gross deduction
      (for partnership shares).

Employer leaver rules:
    - On leaving employer, the SIP plan ends.
    - Partnership and dividend shares are transferred to the employee.
    - Matching shares: depends on the plan rules, but commonly:
        - Forfeited if held < 3 years (employer discretion — plan-specific).
        - Released if held 3+ years.
    - The income tax/NI rules above still apply based on the holding period
      at the date of leaving.

References:
    HMRC Employment Income Manual (EIM) — SIP sections
    HMRC ESSP guidance document (2023)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Types and enumerations
# ─────────────────────────────────────────────────────────────────────────────

class SIPShareType(Enum):
    PARTNERSHIP = "PARTNERSHIP"
    MATCHING = "MATCHING"
    DIVIDEND = "DIVIDEND"


class SIPEventType(Enum):
    """All events that trigger a SIP tax state transition."""
    ACQUISITION = "ACQUISITION"
    # Removal events — all trigger holding-period-based tax assessment
    WITHDRAWAL = "WITHDRAWAL"               # Employee voluntarily withdraws from plan
    IN_PLAN_SALE = "IN_PLAN_SALE"           # Sold while still held within the SIP
    POST_PLAN_SALE = "POST_PLAN_SALE"       # Sold after withdrawal (standard CGT only)
    EMPLOYER_LEAVER = "EMPLOYER_LEAVER"     # Employment ends — plan terminates
    IN_PLAN_TRANSFER = "IN_PLAN_TRANSFER"   # Transfer between SIP sub-plans (rare)
    # Matching-share-specific
    MATCHING_FORFEITURE = "MATCHING_FORFEITURE"  # Partnership shares withdrawn early


class SIPHoldingPeriodCategory(Enum):
    """Holding period bracket determines the tax treatment on removal."""
    UNDER_THREE_YEARS = "UNDER_THREE_YEARS"     # Full IT + NI clawback
    THREE_TO_FIVE_YEARS = "THREE_TO_FIVE_YEARS"  # IT on lower of acquisition/current; no NI
    FIVE_PLUS_YEARS = "FIVE_PLUS_YEARS"          # No IT, no NI


@dataclass(frozen=True)
class SIPHolding:
    """
    Represents a single SIP lot (one acquisition event).

    For PARTNERSHIP shares: acquisition_market_value_gbp == gross_salary_deducted_gbp
    (since partnership shares are bought at market value from gross salary).
    For MATCHING/DIVIDEND shares: gross_salary_deducted_gbp is Decimal('0')
    (these are employer/dividend-funded, no salary deduction).
    """

    lot_id: int
    share_type: SIPShareType
    acquisition_date: date
    quantity: Decimal
    acquisition_market_value_gbp: Decimal  # Per-share FMV at acquisition (in GBP)
    gross_salary_deducted_gbp: Decimal     # Total gross salary deducted (partnership only)

    # For matching shares: the lot_id of the linked partnership lot
    linked_partnership_lot_id: int | None = None

    @property
    def total_acquisition_value_gbp(self) -> Decimal:
        """Total market value at acquisition (quantity × per-share FMV)."""
        return self.quantity * self.acquisition_market_value_gbp

    def holding_period_days(self, event_date: date) -> int:
        """Days between acquisition and event date."""
        return (event_date - self.acquisition_date).days

    def holding_period_category(self, event_date: date) -> SIPHoldingPeriodCategory:
        """Classify the holding period for tax purposes."""
        days = self.holding_period_days(event_date)
        three_year_days = 3 * 365 + 1   # Approximate 3 years (HMRC uses calendar years)
        five_year_days = 5 * 365 + 2    # Approximate 5 years
        # Note: HMRC uses "3 years from the date of acquisition" as a calendar date,
        # not a day count. The day counts above are conservative approximations.
        # For precise calculations, use the three_year_date / five_year_date properties.
        if days < three_year_days:
            return SIPHoldingPeriodCategory.UNDER_THREE_YEARS
        elif days < five_year_days:
            return SIPHoldingPeriodCategory.THREE_TO_FIVE_YEARS
        else:
            return SIPHoldingPeriodCategory.FIVE_PLUS_YEARS

    @property
    def three_year_date(self) -> date:
        """Date after which the 3-year holding period is satisfied."""
        from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]
        return self.acquisition_date + relativedelta(years=3)

    @property
    def five_year_date(self) -> date:
        """Date after which the 5-year holding period is satisfied."""
        from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]
        return self.acquisition_date + relativedelta(years=5)


@dataclass(frozen=True)
class SIPEvent:
    """An event that triggers a SIP state transition and tax assessment."""

    event_type: SIPEventType
    event_date: date
    holding: SIPHolding
    quantity: Decimal                       # Shares affected by this event
    market_value_per_share_gbp: Decimal     # Per-share market value at event date

    # For MATCHING_FORFEITURE: the event that triggered it (partnership withdrawal)
    triggering_event: "SIPEvent | None" = None

    @property
    def total_market_value_gbp(self) -> Decimal:
        """Total market value of shares at event date."""
        return self.quantity * self.market_value_per_share_gbp


@dataclass
class SIPTaxResult:
    """
    Tax implications of a SIP event.

    This is the output of process_sip_event(). All monetary values in GBP.
    The notes list provides a human-readable audit trail of the calculation.
    """

    event_type: SIPEventType
    event_date: date
    holding_period_category: SIPHoldingPeriodCategory

    # Amounts subject to income tax and NI (employment income)
    income_taxable_gbp: Decimal             # Amount on which IT will be charged
    ni_liable_gbp: Decimal                  # Amount on which NI will be charged

    # CGT base cost for future disposals (if shares transferred rather than sold in-plan)
    cgt_base_cost_per_share_gbp: Decimal    # Market value at removal (becomes CGT base)

    # For in-plan sales: immediate gain/loss for CGT purposes
    cgt_gain_gbp: Decimal                   # Gain if sold in-plan (else Decimal('0'))
    cgt_loss_gbp: Decimal                   # Loss if sold in-plan (else Decimal('0'))

    # Forfeiture (matching shares only)
    matching_lots_forfeited: list[int]      # lot_ids of matching share lots forfeited

    notes: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Core processing function
# ─────────────────────────────────────────────────────────────────────────────

def process_sip_event(event: SIPEvent) -> SIPTaxResult:
    """
    Process a SIP event and return its tax implications.

    This is the primary interface for the SIP rules engine.
    All tax calculations are based on the event type, share type, and holding period.

    Args:
        event: The SIP event to process.

    Returns:
        SIPTaxResult with all tax implications and an audit trail.

    State transitions:
        ACQUISITION       → No tax. Records opening position.
        WITHDRAWAL        → Income tax (± NI) based on holding period.
        IN_PLAN_SALE      → Income tax (± NI) based on holding period + CGT on gain.
        POST_PLAN_SALE    → No income tax (shares already taxed at withdrawal). CGT only.
        EMPLOYER_LEAVER   → Same as WITHDRAWAL for tax purposes.
        MATCHING_FORFEITURE → No tax. Lot ceases to exist.
    """
    notes: list[str] = []
    holding = event.holding
    quantity = event.quantity

    period = holding.holding_period_category(event.event_date)
    notes.append(
        f"SIP event: {event.event_type.value} | "
        f"Share type: {holding.share_type.value} | "
        f"Holding period: {holding.holding_period_days(event.event_date)} days "
        f"({period.value})"
    )

    # ── ACQUISITION — no tax ────────────────────────────────────────────────
    if event.event_type == SIPEventType.ACQUISITION:
        notes.append("Acquisition: no immediate tax. Cost basis recorded.")
        return SIPTaxResult(
            event_type=event.event_type,
            event_date=event.event_date,
            holding_period_category=period,
            income_taxable_gbp=Decimal("0"),
            ni_liable_gbp=Decimal("0"),
            cgt_base_cost_per_share_gbp=holding.acquisition_market_value_gbp,
            cgt_gain_gbp=Decimal("0"),
            cgt_loss_gbp=Decimal("0"),
            matching_lots_forfeited=[],
            notes=notes,
        )

    # ── FORFEITURE — no tax (asset lost) ────────────────────────────────────
    if event.event_type == SIPEventType.MATCHING_FORFEITURE:
        notes.append(
            "Matching share forfeiture: no income tax, no NI, no CGT. "
            "The forfeited shares have zero value to the employee."
        )
        return SIPTaxResult(
            event_type=event.event_type,
            event_date=event.event_date,
            holding_period_category=period,
            income_taxable_gbp=Decimal("0"),
            ni_liable_gbp=Decimal("0"),
            cgt_base_cost_per_share_gbp=Decimal("0"),
            cgt_gain_gbp=Decimal("0"),
            cgt_loss_gbp=Decimal("0"),
            matching_lots_forfeited=[holding.lot_id],
            notes=notes,
        )

    # ── POST_PLAN_SALE — income tax already settled at withdrawal ───────────
    if event.event_type == SIPEventType.POST_PLAN_SALE:
        notes.append(
            "Post-plan disposal: income tax was settled at withdrawal. "
            "This disposal is subject to CGT only. CGT base cost = "
            "market value at time of withdrawal from plan."
        )
        # CGT gain/loss computed by caller (they know the withdrawal base cost).
        # This event just documents that no income tax applies.
        return SIPTaxResult(
            event_type=event.event_type,
            event_date=event.event_date,
            holding_period_category=period,
            income_taxable_gbp=Decimal("0"),
            ni_liable_gbp=Decimal("0"),
            cgt_base_cost_per_share_gbp=event.market_value_per_share_gbp,
            cgt_gain_gbp=Decimal("0"),
            cgt_loss_gbp=Decimal("0"),
            matching_lots_forfeited=[],
            notes=notes,
        )

    # ── REMOVAL EVENTS: WITHDRAWAL, IN_PLAN_SALE, EMPLOYER_LEAVER ───────────
    # All three follow the same income tax / NI logic based on holding period.
    income_taxable, ni_liable = _compute_income_tax_amount(event, period, notes)

    # CGT base cost: market value at removal from plan (standard rule)
    cgt_base_per_share = event.market_value_per_share_gbp
    notes.append(
        f"CGT base cost for future disposals: "
        f"£{cgt_base_per_share:,.4f}/share (market value at removal from plan)."
    )

    # For IN_PLAN_SALE: compute the CGT gain/loss immediately
    cgt_gain = Decimal("0")
    cgt_loss = Decimal("0")
    if event.event_type == SIPEventType.IN_PLAN_SALE:
        proceeds = event.total_market_value_gbp
        # CGT cost basis for in-plan sale = acquisition market value (proportional)
        cost_basis = (holding.acquisition_market_value_gbp * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        gain = proceeds - cost_basis
        if gain > 0:
            cgt_gain = gain
            notes.append(f"In-plan sale CGT gain: £{cgt_gain:,.2f}.")
        elif gain < 0:
            cgt_loss = abs(gain)
            notes.append(f"In-plan sale CGT loss: £{cgt_loss:,.2f}.")
        else:
            notes.append("In-plan sale: no CGT gain or loss.")

    return SIPTaxResult(
        event_type=event.event_type,
        event_date=event.event_date,
        holding_period_category=period,
        income_taxable_gbp=income_taxable,
        ni_liable_gbp=ni_liable,
        cgt_base_cost_per_share_gbp=cgt_base_per_share,
        cgt_gain_gbp=cgt_gain,
        cgt_loss_gbp=cgt_loss,
        matching_lots_forfeited=[],
        notes=notes,
    )


def _compute_income_tax_amount(
    event: SIPEvent,
    period: SIPHoldingPeriodCategory,
    notes: list[str],
) -> tuple[Decimal, Decimal]:
    """
    Compute (income_taxable_gbp, ni_liable_gbp) for a removal event.

    Returns:
        Tuple of (income_taxable, ni_liable) — both >= 0.
        These are the AMOUNTS on which tax is charged; the actual tax
        payable depends on the employee's marginal rates at the event date
        (computed separately via get_marginal_rates()).
    """
    holding = event.holding
    quantity = event.quantity
    market_value_total = event.total_market_value_gbp

    if period == SIPHoldingPeriodCategory.FIVE_PLUS_YEARS:
        # No income tax, no NI on removal after 5 years
        notes.append(
            "Held 5+ years: no income tax and no NI on removal. "
            "Tax-efficient disposal — full tax saving on partnership shares retained."
        )
        return Decimal("0"), Decimal("0")

    elif period == SIPHoldingPeriodCategory.THREE_TO_FIVE_YEARS:
        # Income tax on LOWER of: (a) market value at removal, (b) acquisition value
        # NI is NOT charged in this period (HMRC EIM guidance)
        acquisition_value_total = (holding.acquisition_market_value_gbp * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        income_taxable = min(market_value_total, acquisition_value_total)
        notes.append(
            f"Held 3–5 years: income tax on lower of "
            f"market value (£{market_value_total:,.2f}) and "
            f"acquisition value (£{acquisition_value_total:,.2f}). "
            f"Taxable amount: £{income_taxable:,.2f}. No NI."
        )
        return income_taxable, Decimal("0")

    else:  # UNDER_THREE_YEARS
        # Full IT + NI on market value at removal
        # This claws back the original tax saving made at acquisition.
        income_taxable = market_value_total
        ni_liable = market_value_total
        notes.append(
            f"Held < 3 years: income tax AND NI on full market value "
            f"(£{market_value_total:,.2f}). "
            "Original tax saving is fully clawed back."
        )
        if holding.share_type == SIPShareType.MATCHING:
            notes.append(
                "Note: For matching shares removed < 3 years, income tax and NI apply "
                "to the market value. Additionally, if partnership shares are being "
                "simultaneously withdrawn, matching shares may be forfeited instead — "
                "check forfeiture rules for this plan."
            )
        return income_taxable, ni_liable


# ─────────────────────────────────────────────────────────────────────────────
# Employer leaver simulation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EmployerLeaverSIPOutcome:
    """
    Summary of SIP implications when an employee leaves their employer.
    Used by the Scenario Simulator.
    """

    leaver_date: date
    partnership_events: list[SIPTaxResult]
    matching_events: list[SIPTaxResult]
    dividend_events: list[SIPTaxResult]
    total_income_taxable_gbp: Decimal
    total_ni_liable_gbp: Decimal
    lots_forfeited: list[int]
    notes: list[str]


def simulate_employer_leaver(
    leaver_date: date,
    partnership_holdings: list[SIPHolding],
    matching_holdings: list[SIPHolding],
    dividend_holdings: list[SIPHolding],
    market_values: dict[int, Decimal],  # lot_id → market_value_per_share_gbp
    plan_forfeits_matching_under_3yr: bool = True,
) -> EmployerLeaverSIPOutcome:
    """
    Simulate the SIP tax implications of an employee leaving their employer.

    Args:
        leaver_date:                    Date employment ends.
        partnership_holdings:           All active partnership share lots.
        matching_holdings:              All active matching share lots.
        dividend_holdings:              All active dividend share lots.
        market_values:                  Market value per share at leaver_date for each lot.
        plan_forfeits_matching_under_3yr: Whether the plan rules forfeit matching shares
                                         held < 3 years on leaving. Typical default: True.
                                         Set False if the plan has different rules.

    Returns:
        EmployerLeaverSIPOutcome with full breakdown.
    """
    notes: list[str] = [f"Employer leaver simulation — date: {leaver_date}"]
    p_results: list[SIPTaxResult] = []
    m_results: list[SIPTaxResult] = []
    d_results: list[SIPTaxResult] = []
    forfeited_lots: list[int] = []

    # Identify partnership lots < 3 years (will trigger matching forfeiture)
    early_partnership_lot_ids = {
        h.lot_id
        for h in partnership_holdings
        if h.holding_period_category(leaver_date) == SIPHoldingPeriodCategory.UNDER_THREE_YEARS
    }

    # Process partnership holdings
    for h in partnership_holdings:
        mv = market_values.get(h.lot_id, h.acquisition_market_value_gbp)
        e = SIPEvent(
            event_type=SIPEventType.EMPLOYER_LEAVER,
            event_date=leaver_date,
            holding=h,
            quantity=h.quantity,
            market_value_per_share_gbp=mv,
        )
        p_results.append(process_sip_event(e))

    # Process matching holdings
    for h in matching_holdings:
        mv = market_values.get(h.lot_id, h.acquisition_market_value_gbp)
        linked = h.linked_partnership_lot_id
        is_early = linked in early_partnership_lot_ids if linked is not None else False

        if plan_forfeits_matching_under_3yr and is_early:
            # Matching shares linked to an early-withdrawn partnership lot are forfeited
            e = SIPEvent(
                event_type=SIPEventType.MATCHING_FORFEITURE,
                event_date=leaver_date,
                holding=h,
                quantity=h.quantity,
                market_value_per_share_gbp=mv,
            )
            result = process_sip_event(e)
            forfeited_lots.append(h.lot_id)
        else:
            e = SIPEvent(
                event_type=SIPEventType.EMPLOYER_LEAVER,
                event_date=leaver_date,
                holding=h,
                quantity=h.quantity,
                market_value_per_share_gbp=mv,
            )
            result = process_sip_event(e)
        m_results.append(result)

    # Process dividend holdings
    for h in dividend_holdings:
        mv = market_values.get(h.lot_id, h.acquisition_market_value_gbp)
        e = SIPEvent(
            event_type=SIPEventType.EMPLOYER_LEAVER,
            event_date=leaver_date,
            holding=h,
            quantity=h.quantity,
            market_value_per_share_gbp=mv,
        )
        d_results.append(process_sip_event(e))

    all_results = p_results + m_results + d_results
    total_it = sum((r.income_taxable_gbp for r in all_results), Decimal("0"))
    total_ni = sum((r.ni_liable_gbp for r in all_results), Decimal("0"))

    notes.append(
        f"Total income taxable on leaver event: £{total_it:,.2f}. "
        f"Total NI-liable: £{total_ni:,.2f}. "
        f"Lots forfeited: {len(forfeited_lots)}."
    )

    return EmployerLeaverSIPOutcome(
        leaver_date=leaver_date,
        partnership_events=p_results,
        matching_events=m_results,
        dividend_events=d_results,
        total_income_taxable_gbp=total_it,
        total_ni_liable_gbp=total_ni,
        lots_forfeited=forfeited_lots,
        notes=notes,
    )
