"""
UK tax band data by tax year.

All monetary values are in GBP as strings to be constructed into Decimal.
All rates are Decimal fractions (e.g. Decimal('0.20') represents 20%).

Accuracy notes:
- NI rates changed mid-year in 2022-23 (health levy added Apr-Nov 2022, then reversed)
  and in 2023-24 (cut from 12% to 10% effective Jan 2024). The stored rates are the
  predominant annual rate. For transactions in transition months, use a manual marginal
  rate override on the TaxContext.
- CGT annual exempt amount has fallen sharply: 12,300 -> 6,000 -> 3,000.
- The additional rate threshold moved from 150,000 to 125,140 from 2023-24 onwards.
- Bands are published through 2026-27; 2027-28+ are carried forward from the most
  recent published year until HMRC confirms changes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal


@dataclass(frozen=True)
class TaxYearBands:
    """
    Immutable tax band data for a single UK tax year.

    Income thresholds represent GROSS income levels (before personal allowance deduction),
    which aligns with HMRC published figures and is easier to reason about.
    The personal allowance is subtracted internally when computing taxable income.
    """

    tax_year: str  # e.g. '2024-25'

    # â”€â”€ Income Tax â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    personal_allowance: Decimal         # Standard PA before any taper (Â£12,570)
    basic_rate_threshold: Decimal       # Gross income where basic rate ends (Â£50,270)
    higher_rate_threshold: Decimal      # Gross income where higher rate ends / additional starts
    pa_taper_start: Decimal             # ANI above which PA reduces (Â£100,000)

    basic_rate: Decimal                 # 0.20
    higher_rate: Decimal                # 0.40
    additional_rate: Decimal            # 0.45

    # â”€â”€ National Insurance (Employee, Class 1, annual equivalents) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ni_primary_threshold: Decimal       # Below this: 0% NI
    ni_upper_earnings_limit: Decimal    # Above this: lower NI rate applies
    ni_rate_below_uel: Decimal          # Rate between PT and UEL (e.g. 0.08)
    ni_rate_above_uel: Decimal          # Rate above UEL (e.g. 0.02)

    # â”€â”€ Student Loan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    student_loan_plan2_threshold: Decimal   # Gross income threshold for Plan 2
    student_loan_plan2_rate: Decimal        # 0.09

    student_loan_plan1_threshold: Decimal   # Gross income threshold for Plan 1
    student_loan_plan1_rate: Decimal        # 0.09

    # â”€â”€ Capital Gains Tax â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    cgt_annual_exempt_amount: Decimal   # Annual exempt amount (Â£3,000 in 2024-25)
    cgt_basic_rate: Decimal             # Rate for basic rate taxpayers (shares: 0.10)
    cgt_higher_rate: Decimal            # Rate for higher/additional rate taxpayers (0.20)

    # â”€â”€ Derived properties â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def pa_taper_end(self) -> Decimal:
        """
        Gross income at which personal allowance is fully withdrawn.

        PA reduces by Â£1 for every Â£2 of ANI above Â£100,000.
        Full withdrawal when: personal_allowance = (ANI - pa_taper_start) / 2
        â†’ ANI = pa_taper_start + personal_allowance * 2
        """
        return self.pa_taper_start + self.personal_allowance * Decimal("2")

    @property
    def taper_zone_effective_it_rate(self) -> Decimal:
        """
        Effective marginal income tax rate inside the PA taper zone.

        For each Â£1 of income above Â£100,000:
        - Â£1.00 is taxed at higher_rate (40p)
        - Â£0.50 of previously-exempt PA income becomes taxable (in higher rate band) â†’ 20p
        Total: 60p per Â£1 = 60% effective rate.

        Generalised formula: higher_rate + (higher_rate Ã— 0.5) = higher_rate Ã— 1.5
        This remains correct if higher_rate ever changes.
        """
        return self.higher_rate * Decimal("1.5")

    @property
    def basic_rate_band_width(self) -> Decimal:
        """Width of the basic rate band in taxable income (standard: Â£37,700)."""
        return self.basic_rate_threshold - self.personal_allowance


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Published band data + forward-projected support window
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_BANDS: dict[str, TaxYearBands] = {
    "2026-27": TaxYearBands(
        tax_year="2026-27",
        personal_allowance=Decimal("12570"),
        basic_rate_threshold=Decimal("50270"),
        higher_rate_threshold=Decimal("125140"),
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("12570"),
        ni_upper_earnings_limit=Decimal("50270"),
        ni_rate_below_uel=Decimal("0.08"),
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("29385"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("26900"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("3000"),
        cgt_basic_rate=Decimal("0.18"),
        cgt_higher_rate=Decimal("0.24"),
    ),
    "2025-26": TaxYearBands(
        tax_year="2025-26",
        personal_allowance=Decimal("12570"),         # Frozen until 2028
        basic_rate_threshold=Decimal("50270"),        # Frozen
        higher_rate_threshold=Decimal("125140"),
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("12570"),
        ni_upper_earnings_limit=Decimal("50270"),
        ni_rate_below_uel=Decimal("0.08"),
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("28470"),  # Up from Â£27,295
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("26065"),  # Up from Â£24,990
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("3000"),
        cgt_basic_rate=Decimal("0.18"),    # Raised from 10% â€” Autumn Budget 2024 (effective 30 Oct 2024)
        cgt_higher_rate=Decimal("0.24"),   # Raised from 20%
    ),
    "2024-25": TaxYearBands(
        tax_year="2024-25",
        personal_allowance=Decimal("12570"),
        basic_rate_threshold=Decimal("50270"),
        higher_rate_threshold=Decimal("125140"),
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("12570"),
        ni_upper_earnings_limit=Decimal("50270"),
        ni_rate_below_uel=Decimal("0.08"),   # Reduced from 12% in Jan 2024, then 10%, then 8%
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("27295"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("24990"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("3000"),
        # CGT rates changed mid-year (Autumn Budget, 30 Oct 2024):
        # pre-30 Oct 2024: basic=10%, higher=20%; post: basic=18%, higher=24%.
        # Stored rates reflect the pre-Budget rates (Aprâ€“Oct 2024 disposals).
        # For post-Oct 2024 disposals in this year, override via TaxContext.
        cgt_basic_rate=Decimal("0.10"),
        cgt_higher_rate=Decimal("0.20"),
    ),
    "2023-24": TaxYearBands(
        tax_year="2023-24",
        personal_allowance=Decimal("12570"),
        basic_rate_threshold=Decimal("50270"),
        higher_rate_threshold=Decimal("125140"),  # Moved down from Â£150,000
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("12570"),
        ni_upper_earnings_limit=Decimal("50270"),
        ni_rate_below_uel=Decimal("0.10"),   # 12% Aprâ€“Dec 2023, 10% Janâ€“Mar 2024; use 10% as conservative
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("27295"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("22015"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("6000"),
        cgt_basic_rate=Decimal("0.10"),
        cgt_higher_rate=Decimal("0.20"),
    ),
    "2022-23": TaxYearBands(
        tax_year="2022-23",
        personal_allowance=Decimal("12570"),
        basic_rate_threshold=Decimal("50270"),
        higher_rate_threshold=Decimal("150000"),  # Pre-2023 threshold
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("12570"),    # Rose from Â£9,880 in July 2022
        ni_upper_earnings_limit=Decimal("50270"),
        ni_rate_below_uel=Decimal("0.12"),   # Health levy (13.25%) reversed Nov 2022; use 12%
        ni_rate_above_uel=Decimal("0.02"),   # Was 3.25% briefly; use 2%
        student_loan_plan2_threshold=Decimal("27295"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("20195"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("12300"),
        cgt_basic_rate=Decimal("0.10"),
        cgt_higher_rate=Decimal("0.20"),
    ),
    "2021-22": TaxYearBands(
        tax_year="2021-22",
        personal_allowance=Decimal("12570"),
        basic_rate_threshold=Decimal("50270"),
        higher_rate_threshold=Decimal("150000"),
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("9568"),
        ni_upper_earnings_limit=Decimal("50270"),
        ni_rate_below_uel=Decimal("0.12"),
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("27295"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("19895"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("12300"),
        cgt_basic_rate=Decimal("0.10"),
        cgt_higher_rate=Decimal("0.20"),
    ),
    "2020-21": TaxYearBands(
        tax_year="2020-21",
        personal_allowance=Decimal("12500"),
        basic_rate_threshold=Decimal("50000"),
        higher_rate_threshold=Decimal("150000"),
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("9500"),
        ni_upper_earnings_limit=Decimal("50000"),
        ni_rate_below_uel=Decimal("0.12"),
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("26575"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("19390"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("12300"),
        cgt_basic_rate=Decimal("0.10"),
        cgt_higher_rate=Decimal("0.20"),
    ),
    "2019-20": TaxYearBands(
        tax_year="2019-20",
        personal_allowance=Decimal("12500"),
        basic_rate_threshold=Decimal("50000"),
        higher_rate_threshold=Decimal("150000"),
        pa_taper_start=Decimal("100000"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
        ni_primary_threshold=Decimal("8632"),
        ni_upper_earnings_limit=Decimal("50000"),
        ni_rate_below_uel=Decimal("0.12"),
        ni_rate_above_uel=Decimal("0.02"),
        student_loan_plan2_threshold=Decimal("25725"),
        student_loan_plan2_rate=Decimal("0.09"),
        student_loan_plan1_threshold=Decimal("18935"),
        student_loan_plan1_rate=Decimal("0.09"),
        cgt_annual_exempt_amount=Decimal("12000"),
        cgt_basic_rate=Decimal("0.10"),
        cgt_higher_rate=Decimal("0.20"),
    ),
}


def _format_tax_year(start_year: int) -> str:
    """Format UK tax year label from a start year (e.g. 2026 -> '2026-27')."""
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _extend_future_tax_years(*, last_published_year: int, through_year: int) -> None:
    """
    Extend supported years by carrying forward the latest known published values.

    This keeps forward-looking reporting usable while preserving a deterministic
    baseline when HMRC has not yet published new thresholds/rates.
    """
    for start_year in range(last_published_year + 1, through_year + 1):
        previous_key = _format_tax_year(start_year - 1)
        next_key = _format_tax_year(start_year)
        _BANDS[next_key] = replace(_BANDS[previous_key], tax_year=next_key)


# Published through 2026-27. For 2027-28 onwards, carry forward until HMRC
# publishes confirmed updates (IT/NI/SF and the rest of the tax-year set).
_extend_future_tax_years(last_published_year=2026, through_year=2035)


def get_bands(tax_year: str) -> TaxYearBands:
    """
    Return band data for a given tax year string (e.g. '2024-25').

    Raises:
        ValueError: If the tax year is not in the database.
    """
    if tax_year not in _BANDS:
        available = sorted(_BANDS.keys())
        raise ValueError(
            f"Tax year '{tax_year}' not found. "
            f"Available years: {available}. "
            "To add a new year, update bands.py with the HMRC published figures."
        )
    return _BANDS[tax_year]


def available_tax_years() -> list[str]:
    """Return sorted list of supported tax year strings."""
    return sorted(_BANDS.keys())


def tax_year_for_date(d: "date") -> str:  # noqa: F821
    """
    Return the UK tax year string for a given date.

    UK tax year runs 6 April to 5 April. '2024-25' covers 6 Apr 2024 â€“ 5 Apr 2025.
    """
    from datetime import date as _date

    if isinstance(d, _date):
        if d.month > 4 or (d.month == 4 and d.day >= 6):
            start_year = d.year
        else:
            start_year = d.year - 1
        return f"{start_year}-{str(start_year + 1)[2:]}"
    raise TypeError(f"Expected datetime.date, got {type(d)}")

