"""
TaxContext — the dynamic tax scenario input.

Design rationale (per architecture decision):
  The system does NOT store a single fixed marginal rate per tax year.
  Income varies (bonuses, equity vests) and can temporarily push into the
  PA taper zone (creating a 60% effective IT rate) mid-year.

  Instead, TaxContext is constructed at calculation time from:
    - Income received to date in the tax year
    - Pension sacrifice to date
    - Expected full-year figures (for annual tax liability estimates)
    - Student loan plan type

  All marginal rate calculations derive dynamically from this context.
  This means two transactions in the same tax year can correctly have
  different marginal rates if one occurs before and one after a bonus.

Usage:
    ctx = TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("110000"),   # full-year estimate or YTD
        pension_sacrifice=Decimal("15000"),          # salary sacrifice pension
        other_income=Decimal("0"),
        student_loan_plan=2,
    )
    rates = get_marginal_rates(ctx)  # → MarginalRates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .bands import TaxYearBands, get_bands


@dataclass(frozen=True)
class TaxContext:
    """
    Immutable tax scenario for a specific point-in-time calculation.

    All income figures are annualised GBP unless documented otherwise.
    For intra-year calculations (e.g. SIP purchase in month 3), pass
    the income position at that point in time — see `at_income_position()`.

    Attributes:
        tax_year:               UK tax year, e.g. '2024-25'.
        gross_employment_income: Total gross employment income (salary + bonus +
                                 taxable benefits). Does NOT deduct pension sacrifice.
        pension_sacrifice:      Annual salary sacrifice pension contribution.
                                Reduces adjusted net income (ANI) and hence can
                                prevent PA taper. Does NOT reduce NI/SL income.
                                Note: standard pension contributions (not salary sacrifice)
                                do NOT reduce ANI — only salary sacrifice does.
        other_income:           Other non-savings income (rental, self-employment etc.)
                                Adds to ANI.
        student_loan_plan:      None, 1, or 2. Plan 4 (Scottish) not yet supported.
        manual_marginal_it_rate: Optional override for the income tax marginal rate.
                                 Use this for tax years with mid-year NI/rate changes
                                 where the stored band rates are not precise enough.
        manual_marginal_ni_rate: Optional override for the NI marginal rate.
    """

    tax_year: str
    gross_employment_income: Decimal
    pension_sacrifice: Decimal = field(default_factory=lambda: Decimal("0"))
    other_income: Decimal = field(default_factory=lambda: Decimal("0"))
    student_loan_plan: int | None = None

    # Manual overrides — used when mid-year rate changes or unusual circumstances
    # make the stored band rates inaccurate for a specific transaction.
    manual_marginal_it_rate: Decimal | None = None
    manual_marginal_ni_rate: Decimal | None = None

    # ── Derived properties ───────────────────────────────────────────────────

    @property
    def bands(self) -> TaxYearBands:
        """Convenience accessor for this year's tax bands."""
        return get_bands(self.tax_year)

    @property
    def adjusted_net_income(self) -> Decimal:
        """
        Adjusted Net Income (ANI) — the figure HMRC uses to determine personal
        allowance taper and pension annual allowance tapering.

        ANI = gross_employment_income - pension_sacrifice + other_income

        Note: Gift Aid donations also reduce ANI but are not modelled here (v1).
        Note: SIP partnership share purchases are NOT deducted from ANI (unlike
              pension sacrifice), even though they reduce income for IT/NI purposes.
        """
        return self.gross_employment_income - self.pension_sacrifice + self.other_income

    @property
    def ni_relevant_income(self) -> Decimal:
        """
        Income for NI purposes. Pension sacrifice reduces this; other income does not.
        SIP partnership share deductions also reduce NI-liable income (handled in schemes).
        """
        return self.gross_employment_income - self.pension_sacrifice

    def at_income_position(self, income_ytd: Decimal, pension_sacrifice_ytd: Decimal) -> "TaxContext":
        """
        Return a new TaxContext representing the tax position at a specific point
        in the tax year (e.g. when a SIP purchase or RSU vest occurs).

        This is the primary mechanism for intra-year marginal rate accuracy:
        a vest in April (early year, low income) has a different marginal rate
        than a vest in March after a December bonus.

        Args:
            income_ytd:            Gross employment income received up to this point.
            pension_sacrifice_ytd: Pension sacrifice made up to this point.

        Returns:
            A new TaxContext with the point-in-time income figures.
        """
        return TaxContext(
            tax_year=self.tax_year,
            gross_employment_income=income_ytd,
            pension_sacrifice=pension_sacrifice_ytd,
            other_income=self.other_income,
            student_loan_plan=self.student_loan_plan,
            manual_marginal_it_rate=self.manual_marginal_it_rate,
            manual_marginal_ni_rate=self.manual_marginal_ni_rate,
        )

    def with_additional_income(self, extra: Decimal) -> "TaxContext":
        """
        Return a TaxContext as if `extra` GBP of additional income had been received.
        Used to compute marginal rates for incremental income (e.g. an RSU vest).
        """
        return TaxContext(
            tax_year=self.tax_year,
            gross_employment_income=self.gross_employment_income + extra,
            pension_sacrifice=self.pension_sacrifice,
            other_income=self.other_income,
            student_loan_plan=self.student_loan_plan,
            manual_marginal_it_rate=self.manual_marginal_it_rate,
            manual_marginal_ni_rate=self.manual_marginal_ni_rate,
        )

    def __str__(self) -> str:
        return (
            f"TaxContext({self.tax_year}, "
            f"gross=£{self.gross_employment_income:,.0f}, "
            f"pension_sacrifice=£{self.pension_sacrifice:,.0f}, "
            f"ANI=£{self.adjusted_net_income:,.0f}, "
            f"SL_plan={self.student_loan_plan})"
        )
