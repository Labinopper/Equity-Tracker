"""
Combined marginal rate calculator.

This is the central module for the "true economic cost" model. It derives
the combined marginal rate from a TaxContext, accounting for:
    - Income tax (including 60% PA taper zone)
    - National Insurance (employee, Class 1)
    - Student Loan Plan 2 (9% above threshold)
    - Manual overrides for mid-year rate changes or unusual circumstances

Design principle:
    Marginal rates are NEVER stored as static values. They are always derived
    dynamically from a TaxContext that captures the income position at a specific
    point in time. This ensures accuracy when income varies (bonuses, multiple vest
    events) and when rate boundaries are crossed mid-year.

Usage:
    ctx = TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("110000"),
        pension_sacrifice=Decimal("0"),
        student_loan_plan=2,
    )
    rates = get_marginal_rates(ctx)
    # rates.combined → Decimal('0.71')  (60% IT + 2% NI + 9% SL in taper zone)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .bands import TaxYearBands
from .context import TaxContext
from .income_tax import marginal_income_tax_rate
from .national_insurance import marginal_ni_rate
from .student_loan import marginal_student_loan_rate


@dataclass(frozen=True)
class MarginalRates:
    """
    Marginal tax rates at a specific income position.

    All rates are Decimal fractions (0.40 = 40%).

    Attributes:
        income_tax:      Marginal income tax rate (0%, 20%, 40%, 60%, or 45%).
        national_insurance: Marginal employee NI rate (0%, 8%, or 2%).
        student_loan:    Marginal student loan rate (0% or 9%).
        combined:        Sum of all three — the rate at which the next pound of
                         employment income is effectively taxed. This is the key
                         figure for true economic cost calculations.
        taper_zone:      True if income is in the PA taper zone (60% effective IT).
        notes:           Human-readable explanation of rates for audit trail.
    """

    income_tax: Decimal
    national_insurance: Decimal
    student_loan: Decimal
    combined: Decimal
    taper_zone: bool
    notes: list[str]

    @property
    def pence_kept_per_pound(self) -> Decimal:
        """After-tax retention rate: how many pence are kept per pound earned."""
        return Decimal("1") - self.combined

    def __str__(self) -> str:
        return (
            f"IT={self.income_tax * 100:.0f}%, "
            f"NI={self.national_insurance * 100:.0f}%, "
            f"SL={self.student_loan * 100:.0f}%, "
            f"Combined={self.combined * 100:.0f}%"
            + (" [TAPER ZONE]" if self.taper_zone else "")
        )


def get_marginal_rates(context: TaxContext) -> MarginalRates:
    """
    Derive all marginal rates from a TaxContext.

    This is the primary interface for the rest of the system.
    Call this at the income position AT THE TIME of the transaction,
    not with full-year projected income.

    Args:
        context: TaxContext representing the tax position at calculation time.

    Returns:
        MarginalRates with full breakdown and combined rate.

    Critical note on income figures:
        - IT marginal rate: based on gross_employment_income vs ANI
        - NI marginal rate: based on ni_relevant_income (= gross - pension_sacrifice)
        - SL marginal rate: based on sl_relevant_income (= gross - pension_sacrifice)

        For salary sacrifice pension:
            - Reduces ANI (helps avoid/reduce PA taper for IT)
            - Reduces NI-relevant income
            - Reduces SL-relevant income
        For SIP partnership shares (pre-tax purchase):
            - Reduces NI and SL income (per HMRC)
            - Does NOT reduce ANI for IT purposes (per HMRC SIP guidance)
            This distinction is handled by passing appropriate context values.

    Examples (2024-25):
        Basic rate taxpayer, income £40,000, Plan 2 above threshold:
            IT=20%, NI=8%, SL=9%, Combined=37%

        Higher rate taxpayer, income £80,000, Plan 2:
            IT=40%, NI=2%, SL=9%, Combined=51%

        Taper zone, income £110,000, Plan 2:
            IT=60%, NI=2%, SL=9%, Combined=71%

        Above taper zone, income £130,000, Plan 2:
            IT=45%, NI=2%, SL=9%, Combined=56%
    """
    bands: TaxYearBands = context.bands
    notes: list[str] = []

    # ── Income Tax ────────────────────────────────────────────────────────────
    if context.manual_marginal_it_rate is not None:
        it_rate = context.manual_marginal_it_rate
        notes.append(
            f"Income tax marginal rate: {it_rate * 100:.1f}% (MANUAL OVERRIDE — "
            "verify this is correct for the transaction date)."
        )
    else:
        it_rate = marginal_income_tax_rate(
            bands=bands,
            gross_income=context.gross_employment_income,
            adjusted_net_income=context.adjusted_net_income,
        )
        notes.append(
            f"Income tax marginal rate: {it_rate * 100:.0f}% "
            f"(gross: £{context.gross_employment_income:,.0f}, "
            f"ANI: £{context.adjusted_net_income:,.0f})."
        )

    # ── National Insurance ────────────────────────────────────────────────────
    if context.manual_marginal_ni_rate is not None:
        ni_rate = context.manual_marginal_ni_rate
        notes.append(
            f"NI marginal rate: {ni_rate * 100:.1f}% (MANUAL OVERRIDE)."
        )
    else:
        ni_rate = marginal_ni_rate(
            bands=bands,
            ni_relevant_income=context.ni_relevant_income,
        )
        notes.append(
            f"NI marginal rate: {ni_rate * 100:.0f}% "
            f"(NI-relevant income: £{context.ni_relevant_income:,.0f})."
        )

    # ── Student Loan ──────────────────────────────────────────────────────────
    # SL-relevant income = same as NI-relevant income (pension sacrifice reduces both)
    sl_rate = marginal_student_loan_rate(
        bands=bands,
        sl_relevant_income=context.ni_relevant_income,  # same basis as NI
        plan=context.student_loan_plan,
    )
    if context.student_loan_plan is not None:
        notes.append(
            f"Student Loan Plan {context.student_loan_plan} rate: {sl_rate * 100:.0f}% "
            f"(threshold: £{_sl_threshold(bands, context.student_loan_plan):,.0f})."
        )

    # ── Combined ──────────────────────────────────────────────────────────────
    combined = it_rate + ni_rate + sl_rate

    in_taper = (
        bands.pa_taper_start < context.adjusted_net_income <= bands.pa_taper_end
    )

    if in_taper:
        notes.append(
            f"WARNING: Income is in the PA taper zone "
            f"(ANI: £{context.adjusted_net_income:,.0f}, "
            f"taper zone: £{bands.pa_taper_start:,.0f}–£{bands.pa_taper_end:,.0f}). "
            f"Effective IT rate is {it_rate * 100:.0f}% (the '60% tax trap'). "
            "Consider whether additional pension sacrifice would reduce ANI below £100,000."
        )

    notes.append(
        f"Combined marginal rate: {combined * 100:.1f}% "
        f"({it_rate * 100:.0f}% IT + {ni_rate * 100:.0f}% NI + {sl_rate * 100:.0f}% SL). "
        f"Retention: {(1 - combined) * 100:.1f}p per £1."
    )

    return MarginalRates(
        income_tax=it_rate,
        national_insurance=ni_rate,
        student_loan=sl_rate,
        combined=combined,
        taper_zone=in_taper,
        notes=notes,
    )


def _sl_threshold(bands: TaxYearBands, plan: int | None) -> Decimal:
    """Internal helper to retrieve the SL threshold for a plan."""
    if plan == 1:
        return bands.student_loan_plan1_threshold
    if plan == 2:
        return bands.student_loan_plan2_threshold
    return Decimal("0")
