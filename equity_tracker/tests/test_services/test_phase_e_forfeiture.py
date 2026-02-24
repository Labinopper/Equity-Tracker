"""
Phase E — ESPP+ Forfeiture Tracking tests.

Coverage:
  - Lot model persists forfeiture_period_end and matching_lot_id (schema smoke test)
  - _forfeiture_risk_for_lot(): DB field takes precedence over 183-day fallback
  - _forfeiture_risk_for_lot(): 183-day fallback for legacy ESPP_PLUS lots
  - _sip_qualifying_status_for_lot() covers ESPP_PLUS and SIP_* scheme types
  - simulate_disposal() returns ForfeitureWarning when ESPP lot is sold in window
  - simulate_disposal() returns no warning when forfeiture window has expired
  - simulate_disposal() returns zero disposal employment tax for ESPP lots
  - simulate_disposal() returns zero employment tax for 5yr+ ESPP lot
  - get_forfeiture_risk() delegates to simulate_disposal and returns warnings
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.services.portfolio_service import (
    PortfolioService,
    _forfeiture_risk_for_lot,
    _sip_qualifying_status_for_lot,
)
from src.settings import AppSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_security(ticker: str = "IBM") -> object:
    return PortfolioService.add_security(ticker, f"{ticker} Corp", "GBP", is_manual_override=True)


def _add_lot(
    security_id: str,
    scheme_type: str = "ESPP",
    acquisition_date: date = date(2024, 1, 1),
    quantity: str = "100",
    price: str = "10.00",
    true_cost: str = "9.00",
    *,
    forfeiture_period_end: date | None = None,
    matching_lot_id: str | None = None,
) -> object:
    return PortfolioService.add_lot(
        security_id=security_id,
        scheme_type=scheme_type,
        acquisition_date=acquisition_date,
        quantity=Decimal(quantity),
        acquisition_price_gbp=Decimal(price),
        true_cost_per_share_gbp=Decimal(true_cost),
        forfeiture_period_end=forfeiture_period_end,
        matching_lot_id=matching_lot_id,
    )


def _make_settings(gross_income: str = "80000") -> AppSettings:
    """Return an AppSettings with a high income (higher-rate taxpayer) and no pension/loan."""
    settings = AppSettings()
    settings.default_gross_income = Decimal(gross_income)
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = None
    return settings


# ---------------------------------------------------------------------------
# Schema smoke test — forfeiture fields persist on Lot
# ---------------------------------------------------------------------------

class TestLotForfeitureFields:
    def test_espp_plus_lot_persists_forfeiture_period_end(self, app_context):
        """forfeiture_period_end is stored and retrievable on an ESPP_PLUS lot."""
        sec = _add_security("IBM")
        fpe = date(2025, 7, 1)
        lot = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=date(2025, 1, 1),
            forfeiture_period_end=fpe,
            true_cost="0",
        )
        assert lot.forfeiture_period_end == fpe

    def test_espp_plus_lot_persists_matching_lot_id(self, app_context):
        """matching_lot_id FK is stored on an ESPP_PLUS lot."""
        sec = _add_security("IBM")
        # Create the "parent" ESPP lot first
        espp_lot = _add_lot(sec.id, scheme_type="ESPP", quantity="50")
        # Create the ESPP_PLUS lot linked to the ESPP lot
        espp_plus_lot = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            quantity="25",
            price="0",
            true_cost="0",
            matching_lot_id=espp_lot.id,
        )
        assert espp_plus_lot.matching_lot_id == espp_lot.id


# ---------------------------------------------------------------------------
# _forfeiture_risk_for_lot()
# ---------------------------------------------------------------------------

class TestForfeitureRiskForLot:
    def test_non_espp_plus_returns_none(self, app_context):
        sec = _add_security()
        lot = _add_lot(sec.id, scheme_type="BROKERAGE")
        assert _forfeiture_risk_for_lot(lot, date.today()) is None

    def test_espp_returns_none(self, app_context):
        sec = _add_security()
        lot = _add_lot(sec.id, scheme_type="ESPP")
        assert _forfeiture_risk_for_lot(lot, date.today()) is None

    def test_uses_db_forfeiture_period_end_when_set(self, app_context):
        """When forfeiture_period_end is in DB, uses that exact date."""
        sec = _add_security()
        acq = date(2025, 1, 1)
        exact_end = date(2025, 8, 15)  # not 183 days after acq
        employee = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity="10",
            price="10.00",
            true_cost="10.00",
        )
        lot = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            forfeiture_period_end=exact_end,
            true_cost="0",
            matching_lot_id=employee.id,
        )
        risk = _forfeiture_risk_for_lot(lot, date(2025, 7, 1))
        assert risk is not None
        assert risk.end_date == exact_end
        assert risk.in_window is True

    def test_fallback_183_days_when_no_db_field(self, app_context):
        """Legacy lots without forfeiture_period_end fall back to acq + 183 days."""
        sec = _add_security()
        acq = date(2024, 1, 1)
        employee = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity="10",
            price="10.00",
            true_cost="10.00",
        )
        lot = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            true_cost="0",
            matching_lot_id=employee.id,
        )
        # No forfeiture_period_end set
        expected_end = acq + timedelta(days=183)
        risk = _forfeiture_risk_for_lot(lot, acq + timedelta(days=1))
        assert risk is not None
        assert risk.end_date == expected_end
        assert risk.in_window is True

    def test_outside_window_is_not_in_window(self, app_context):
        sec = _add_security()
        acq = date(2024, 1, 1)
        exact_end = date(2024, 7, 3)
        employee = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity="10",
            price="10.00",
            true_cost="10.00",
        )
        lot = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            forfeiture_period_end=exact_end,
            true_cost="0",
            matching_lot_id=employee.id,
        )
        # Check on the day after the forfeiture ends
        risk = _forfeiture_risk_for_lot(lot, exact_end)
        assert risk is not None
        assert risk.in_window is False
        assert risk.days_remaining == 0


# ---------------------------------------------------------------------------
# _sip_qualifying_status_for_lot()
# ---------------------------------------------------------------------------

class TestSIPQualifyingStatusForLot:
    def test_brokerage_returns_none(self, app_context):
        sec = _add_security()
        lot = _add_lot(sec.id, scheme_type="BROKERAGE")
        assert _sip_qualifying_status_for_lot(lot, date.today()) is None

    def test_rsu_returns_none(self, app_context):
        sec = _add_security()
        lot = _add_lot(sec.id, scheme_type="RSU")
        assert _sip_qualifying_status_for_lot(lot, date.today()) is None

    def test_espp_returns_none(self, app_context):
        """ESPP lots do not surface the Tax Impact Window status badge."""
        sec = _add_security()
        lot = _add_lot(sec.id, scheme_type="ESPP", acquisition_date=date(2024, 1, 1))
        status = _sip_qualifying_status_for_lot(lot, date(2025, 1, 1))
        assert status is None

    def test_espp_plus_returns_qualifying_status(self, app_context):
        """ESPP_PLUS (matching) lots now participate in SIP qualifying period rules."""
        sec = _add_security()
        lot = _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=date(2024, 1, 1),
            true_cost="0",
        )
        status = _sip_qualifying_status_for_lot(lot, date(2025, 1, 1))
        assert status is not None

    def test_espp_plus_under_3yr_category(self, app_context):
        from src.core.tax_engine import SIPHoldingPeriodCategory
        sec = _add_security()
        acq = date(2023, 6, 1)
        lot = _add_lot(sec.id, scheme_type="ESPP_PLUS", acquisition_date=acq)
        status = _sip_qualifying_status_for_lot(lot, acq + timedelta(days=500))
        assert status is not None
        assert status.category == SIPHoldingPeriodCategory.UNDER_THREE_YEARS


# ---------------------------------------------------------------------------
# simulate_disposal() — forfeiture warnings
# ---------------------------------------------------------------------------

class TestSimulateDisposalForfeitureWarnings:
    def test_forfeiture_warning_present_when_in_window(self, app_context):
        """Selling ESPP lot in window produces a ForfeitureWarning for linked ESPP_PLUS lot."""
        sec = _add_security()
        acq = date(2025, 1, 1)
        disposal_date = date(2025, 3, 1)  # 59 days after acq — in window

        espp_lot = _add_lot(
            sec.id,
            scheme_type="ESPP",
            acquisition_date=acq,
            quantity="50",
            price="10.00",
        )
        # ESPP_PLUS lot linked to the ESPP lot; forfeiture end is 6 months out
        fpe = date(2025, 7, 1)
        _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity="25",
            price="0.00",
            true_cost="0",
            forfeiture_period_end=fpe,
            matching_lot_id=espp_lot.id,
        )

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("12.00"),
            as_of_date=disposal_date,
        )

        assert len(result.forfeiture_warnings) == 1
        w = result.forfeiture_warnings[0]
        assert w.forfeiture_end_date == fpe
        assert w.days_remaining == (fpe - disposal_date).days
        assert w.quantity_at_risk == Decimal("25")
        assert w.value_at_risk_gbp == Decimal("300.00")  # 25 × 12.00
        assert w.linked_partnership_lot_id == espp_lot.id

    def test_no_warning_after_forfeiture_window_expires(self, app_context):
        """No warning when disposal_date >= forfeiture_period_end."""
        sec = _add_security()
        acq = date(2024, 1, 1)
        fpe = date(2024, 7, 3)
        disposal_date = fpe  # on the exact end date — window is closed

        espp_lot = _add_lot(
            sec.id,
            scheme_type="ESPP",
            acquisition_date=acq,
            quantity="50",
            price="10.00",
        )
        _add_lot(
            sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity="25",
            price="0.00",
            true_cost="0",
            forfeiture_period_end=fpe,
            matching_lot_id=espp_lot.id,
        )

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("12.00"),
            as_of_date=disposal_date,
        )

        assert result.forfeiture_warnings == ()
        assert result.total_forfeiture_value_gbp == Decimal("0")

    def test_total_forfeiture_value_sums_all_warnings(self, app_context):
        """total_forfeiture_value_gbp is the sum of all linked ESPP_PLUS lots' values."""
        sec = _add_security()
        acq = date(2025, 1, 1)
        fpe = date(2025, 7, 1)

        espp_lot = _add_lot(
            sec.id, scheme_type="ESPP", acquisition_date=acq, quantity="100", price="10.00"
        )
        _add_lot(
            sec.id, scheme_type="ESPP_PLUS", acquisition_date=acq, quantity="50",
            price="0.00", true_cost="0", forfeiture_period_end=fpe, matching_lot_id=espp_lot.id,
        )

        disposal_date = date(2025, 3, 1)
        price = Decimal("15.00")
        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=price,
            as_of_date=disposal_date,
        )

        assert result.total_forfeiture_value_gbp == Decimal("750.00")  # 50 × 15


# ---------------------------------------------------------------------------
# simulate_disposal() — SIP employment tax estimates
# ---------------------------------------------------------------------------

class TestSimulateDisposalSIPTaxEstimates:
    def test_employment_tax_is_zero_for_under_3yr_espp(self, app_context):
        """ESPP lot under 3 years: disposal employment tax is always zero."""
        sec = _add_security()
        # Acquisition 1 year ago
        acq = date.today().replace(year=date.today().year - 1)
        espp_lot = _add_lot(
            sec.id, scheme_type="ESPP", acquisition_date=acq, quantity="50", price="10.00"
        )
        settings = _make_settings("80000")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("15.00"),
            settings=settings,
        )

        assert len(result.sip_tax_estimates) == 1
        est = result.sip_tax_estimates[0]
        assert est.lot_id == espp_lot.id
        assert est.holding_period_category == "ESPP_ZERO"
        assert est.est_total_employment_tax_gbp == Decimal("0")
        assert result.total_sip_employment_tax_gbp == Decimal("0")

    def test_no_employment_tax_for_5yr_plus_espp(self, app_context):
        """ESPP lot over 5 years: total employment tax is 0 (5yr+ qualifying period)."""
        sec = _add_security()
        # Acquisition 6 years ago
        acq = date.today().replace(year=date.today().year - 6)
        _add_lot(sec.id, scheme_type="ESPP", acquisition_date=acq, quantity="50", price="10.00")
        settings = _make_settings("80000")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("15.00"),
            settings=settings,
        )

        assert result.total_sip_employment_tax_gbp == Decimal("0")

    def test_no_employment_tax_estimate_without_settings(self, app_context):
        """Without settings, sip_tax_estimates is empty and total tax is 0."""
        sec = _add_security()
        acq = date.today().replace(year=date.today().year - 1)
        _add_lot(sec.id, scheme_type="ESPP", acquisition_date=acq, quantity="50", price="10.00")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("15.00"),
            settings=None,
        )

        assert result.sip_tax_estimates == ()
        assert result.total_sip_employment_tax_gbp == Decimal("0")

    def test_brokerage_lot_has_no_employment_tax_estimate(self, app_context):
        """BROKERAGE lots are excluded from employment tax estimates."""
        sec = _add_security()
        _add_lot(sec.id, scheme_type="BROKERAGE", quantity="50", price="10.00")
        settings = _make_settings("80000")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("15.00"),
            settings=settings,
        )

        assert result.sip_tax_estimates == ()
        assert result.total_sip_employment_tax_gbp == Decimal("0")


# ---------------------------------------------------------------------------
# get_forfeiture_risk()
# ---------------------------------------------------------------------------

class TestGetForfeitureRisk:
    def test_returns_empty_when_no_espp_plus_in_window(self, app_context):
        """get_forfeiture_risk returns empty tuple when no forfeiture risk exists."""
        sec = _add_security()
        _add_lot(sec.id, scheme_type="BROKERAGE", quantity="100")

        warnings = PortfolioService.get_forfeiture_risk(
            security_id=sec.id,
            quantity=Decimal("50"),
            disposal_date=date.today(),
        )

        assert warnings == ()

    def test_returns_warnings_when_in_window(self, app_context):
        """get_forfeiture_risk identifies ESPP_PLUS lots at risk."""
        sec = _add_security()
        acq = date(2025, 1, 1)
        fpe = date(2025, 7, 1)
        disposal_date = date(2025, 3, 1)

        espp_lot = _add_lot(
            sec.id, scheme_type="ESPP", acquisition_date=acq, quantity="50", price="10.00"
        )
        _add_lot(
            sec.id, scheme_type="ESPP_PLUS", acquisition_date=acq, quantity="25",
            price="0.00", true_cost="0", forfeiture_period_end=fpe, matching_lot_id=espp_lot.id,
        )

        warnings = PortfolioService.get_forfeiture_risk(
            security_id=sec.id,
            quantity=Decimal("50"),
            disposal_date=disposal_date,
        )

        assert len(warnings) == 1
        assert warnings[0].forfeiture_end_date == fpe
        # value_at_risk_gbp uses £0 price (convenience wrapper)
        assert warnings[0].value_at_risk_gbp == Decimal("0.00")
