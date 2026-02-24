from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from src.core.tax_engine.employment_tax_engine import (
    EmploymentTaxContext,
    estimate_employment_tax_for_lot,
)
from src.core.tax_engine.marginal_rates import MarginalRates


def _rates() -> MarginalRates:
    return MarginalRates(
        income_tax=Decimal("0.20"),
        national_insurance=Decimal("0.12"),
        student_loan=Decimal("0.09"),
        combined=Decimal("0.41"),
        taper_zone=False,
        notes=[],
    )


def _lot(
    *,
    lot_id: str,
    scheme: str,
    acq: date,
    acquisition_price: str,
    fmv: str | None = None,
    matching_lot_id: str | None = None,
    forfeiture_end: date | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=lot_id,
        scheme_type=scheme,
        acquisition_date=acq,
        acquisition_price_gbp=acquisition_price,
        fmv_at_acquisition_gbp=fmv,
        matching_lot_id=matching_lot_id,
        forfeiture_period_end=forfeiture_end,
    )


def test_espp_disposal_employment_tax_is_always_zero() -> None:
    lot = _lot(
        lot_id="E1",
        scheme="ESPP",
        acq=date(2024, 1, 1),
        acquisition_price="150.00",
        fmv="200.00",
    )

    est = estimate_employment_tax_for_lot(
        lot=lot,
        quantity=Decimal("3"),
        event_date=date(2025, 2, 1),
        disposal_price_per_share_gbp=Decimal("210.00"),
        rates=_rates(),
        context=None,
    )

    assert est.income_taxable_base_gbp == Decimal("0")
    assert est.ni_base_gbp == Decimal("0")
    assert est.student_loan_base_gbp == Decimal("0")
    assert est.est_total_gbp == Decimal("0")


def test_espp_plus_under_183d_matched_leg_is_zero_tax() -> None:
    acq = date(2025, 1, 1)
    matched = _lot(
        lot_id="M1",
        scheme="ESPP_PLUS",
        acq=acq,
        acquisition_price="0.00",
        fmv="100.00",
        matching_lot_id="EMP1",
        forfeiture_end=acq + timedelta(days=183),
    )

    est = estimate_employment_tax_for_lot(
        lot=matched,
        quantity=Decimal("1"),
        event_date=acq + timedelta(days=30),
        disposal_price_per_share_gbp=Decimal("200.00"),
        rates=_rates(),
        context=EmploymentTaxContext(lots_by_id={}),
    )

    assert est.est_total_gbp == Decimal("0")


def test_espp_plus_183d_to_3y_uses_market_value_removal_for_all_bases() -> None:
    lot = _lot(
        lot_id="EP2",
        scheme="ESPP_PLUS",
        acq=date(2024, 1, 1),
        acquisition_price="100.00",
        fmv="100.00",
    )

    est = estimate_employment_tax_for_lot(
        lot=lot,
        quantity=Decimal("2"),
        event_date=date(2024, 10, 1),  # >183d and <3y
        disposal_price_per_share_gbp=Decimal("150.00"),
        rates=_rates(),
        context=None,
    )

    assert est.holding_period_category == "UNDER_THREE_YEARS"
    assert est.income_taxable_base_gbp == Decimal("300.00")
    assert est.ni_base_gbp == Decimal("300.00")
    assert est.student_loan_base_gbp == Decimal("300.00")
    assert est.est_it_gbp == Decimal("60.00")
    assert est.est_ni_gbp == Decimal("36.00")
    assert est.est_sl_gbp == Decimal("27.00")
    assert est.est_total_gbp == Decimal("123.00")


def test_espp_plus_3y_to_5y_uses_min_removal_vs_award_for_it_and_zero_ni_sl() -> None:
    lot = _lot(
        lot_id="EP3",
        scheme="ESPP_PLUS",
        acq=date(2021, 1, 1),
        acquisition_price="100.00",
        fmv="100.00",
    )

    est = estimate_employment_tax_for_lot(
        lot=lot,
        quantity=Decimal("2"),
        event_date=date(2024, 6, 1),  # >=3y and <5y
        disposal_price_per_share_gbp=Decimal("140.00"),
        rates=_rates(),
        context=None,
    )

    assert est.holding_period_category == "THREE_TO_FIVE_YEARS"
    assert est.income_taxable_base_gbp == Decimal("200.00")  # min(280, 200)
    assert est.ni_base_gbp == Decimal("0")
    assert est.student_loan_base_gbp == Decimal("0")
    assert est.est_it_gbp == Decimal("40.00")
    assert est.est_ni_gbp == Decimal("0.00")
    assert est.est_sl_gbp == Decimal("0.00")
    assert est.est_total_gbp == Decimal("40.00")


def test_espp_plus_5y_plus_has_zero_bases_and_zero_tax() -> None:
    lot = _lot(
        lot_id="EP4",
        scheme="ESPP_PLUS",
        acq=date(2019, 1, 1),
        acquisition_price="100.00",
        fmv="120.00",
    )

    est = estimate_employment_tax_for_lot(
        lot=lot,
        quantity=Decimal("1"),
        event_date=date(2025, 1, 2),  # >=5y
        disposal_price_per_share_gbp=Decimal("200.00"),
        rates=_rates(),
        context=None,
    )

    assert est.holding_period_category == "FIVE_PLUS_YEARS"
    assert est.income_taxable_base_gbp == Decimal("0")
    assert est.ni_base_gbp == Decimal("0")
    assert est.student_loan_base_gbp == Decimal("0")
    assert est.est_total_gbp == Decimal("0")


def test_espp_plus_matched_3y_to_5y_uses_linked_employee_award_fmv_fallback() -> None:
    employee = _lot(
        lot_id="EMP5",
        scheme="ESPP_PLUS",
        acq=date(2021, 1, 1),
        acquisition_price="90.00",
        fmv="110.00",
    )
    matched = _lot(
        lot_id="MATCH5",
        scheme="ESPP_PLUS",
        acq=date(2021, 1, 1),
        acquisition_price="0.00",
        fmv=None,
        matching_lot_id="EMP5",
    )

    est = estimate_employment_tax_for_lot(
        lot=matched,
        quantity=Decimal("1"),
        event_date=date(2024, 6, 1),  # >=3y and <5y
        disposal_price_per_share_gbp=Decimal("120.00"),
        rates=_rates(),
        context=EmploymentTaxContext(
            lots_by_id={
                "EMP5": employee,
                "MATCH5": matched,
            }
        ),
    )

    assert est.holding_period_category == "THREE_TO_FIVE_YEARS"
    assert est.income_taxable_base_gbp == Decimal("110.00")  # min(120, linked award FMV 110)
    assert est.est_it_gbp == Decimal("22.00")
