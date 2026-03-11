"""
Tests for PortfolioService.

Coverage:
  - PortfolioSummary aggregation (empty, active lots, post-disposal, multi-security)
  - simulate_disposal: FIFO correctness, multi-lot ordering, shortfall detection
  - commit_disposal:   persistence, lot quantity_remaining update, ValueError on shortfall
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.app_context import AppContext
from src.db.repository import (
    AuditRepository,
    EmploymentTaxEventRepository,
    TransactionRepository,
)
from src.db.repository.lots import LotRepository
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings
from src.services import LotSummary, PortfolioService, PortfolioSummary, SecuritySummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_basic_security(
    ticker: str = "TEST",
    name: str = "Test Corp",
    currency: str = "GBP",
) -> object:
    """Add a security and return the Security object."""
    return PortfolioService.add_security(
        ticker,
        name,
        currency,
        is_manual_override=True,
    )


def _add_basic_lot(
    security_id: str,
    *,
    acquisition_date: date = date(2024, 1, 15),
    quantity: str = "100",
    acquisition_price: str = "10.00",
    true_cost: str = "10.00",
    scheme_type: str = "BROKERAGE",
) -> object:
    return PortfolioService.add_lot(
        security_id=security_id,
        scheme_type=scheme_type,
        acquisition_date=acquisition_date,
        quantity=Decimal(quantity),
        acquisition_price_gbp=Decimal(acquisition_price),
        true_cost_per_share_gbp=Decimal(true_cost),
    )


# ===========================================================================
# TestGetPortfolioSummary
# ===========================================================================

class TestGetPortfolioSummary:
    def test_empty_portfolio_returns_zeroes(self, app_context):
        summary = PortfolioService.get_portfolio_summary()

        assert isinstance(summary, PortfolioSummary)
        assert summary.securities == []
        assert summary.total_cost_basis_gbp == Decimal("0")
        assert summary.total_true_cost_gbp == Decimal("0")

    def test_single_security_single_lot(self, app_context):
        sec = _add_basic_security("AAPL")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="8.00")

        summary = PortfolioService.get_portfolio_summary()

        assert len(summary.securities) == 1
        ss = summary.securities[0]
        assert isinstance(ss, SecuritySummary)
        assert ss.security.ticker == "AAPL"
        assert ss.total_quantity == Decimal("100")
        assert ss.total_cost_basis_gbp == Decimal("1000.00")   # 100 Ã— 10
        assert ss.total_true_cost_gbp  == Decimal("800.00")    # 100 Ã— 8
        assert len(ss.active_lots) == 1
        assert isinstance(ss.active_lots[0], LotSummary)

    def test_aggregates_two_lots_for_same_security(self, app_context):
        sec = _add_basic_security("MSFT")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00",
                       acquisition_date=date(2024, 1, 1))
        _add_basic_lot(sec.id, quantity="50",  acquisition_price="12.00", true_cost="12.00",
                       acquisition_date=date(2024, 6, 1))

        summary = PortfolioService.get_portfolio_summary()

        ss = summary.securities[0]
        assert ss.total_quantity      == Decimal("150")
        assert ss.total_cost_basis_gbp == Decimal("1600.00")  # 100Ã—10 + 50Ã—12
        assert ss.total_true_cost_gbp  == Decimal("1600.00")
        assert summary.total_cost_basis_gbp == Decimal("1600.00")

    def test_excludes_exhausted_lots(self, app_context):
        sec = _add_basic_security("GOOG")
        _add_basic_lot(sec.id, quantity="10", acquisition_price="100.00", true_cost="100.00")

        # Dispose all 10 â€” lot is now exhausted
        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("10"),
            price_per_share_gbp=Decimal("120.00"),
            transaction_date=date(2024, 6, 1),
        )

        summary = PortfolioService.get_portfolio_summary()
        ss = next(s for s in summary.securities if s.security.ticker == "GOOG")
        assert ss.total_quantity == Decimal("0")


class TestShareMatching:
    def test_simulate_disposal_applies_same_day_rule_before_section_104_pool(
        self, app_context
    ):
        sec = _add_basic_security("HMRCDAY")
        _add_basic_lot(
            sec.id,
            acquisition_date=date(2024, 1, 1),
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
        )
        _add_basic_lot(
            sec.id,
            acquisition_date=date(2024, 6, 1),
            quantity="5",
            acquisition_price="20.00",
            true_cost="20.00",
        )

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("5"),
            price_per_share_gbp=Decimal("30.00"),
            as_of_date=date(2024, 6, 1),
        )

        assert result.is_fully_allocated
        assert len(result.allocations) == 1
        assert result.allocations[0].acquisition_date == date(2024, 6, 1)
        assert result.total_cost_basis_gbp == Decimal("100.00")
        assert ss.active_lots == []

    def test_partial_disposal_reduces_cost_basis(self, app_context):
        sec = _add_basic_security("PART")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="7.00")

        # Dispose 60 shares
        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("60"),
            price_per_share_gbp=Decimal("15.00"),
            transaction_date=date(2024, 6, 1),
        )

        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.total_quantity      == Decimal("40")
        assert ss.total_cost_basis_gbp == Decimal("400.00")   # 40 Ã— 10
        assert ss.total_true_cost_gbp  == Decimal("280.00")   # 40 Ã— 7

    def test_multi_security_portfolio_totals(self, app_context):
        sec1 = _add_basic_security("AA", "Company A")
        sec2 = _add_basic_security("BB", "Company B")

        _add_basic_lot(sec1.id, quantity="100", acquisition_price="10.00", true_cost="8.00")
        _add_basic_lot(sec2.id, quantity="50",  acquisition_price="20.00", true_cost="15.00")

        summary = PortfolioService.get_portfolio_summary()

        assert len(summary.securities) == 2
        # total cost basis: 100Ã—10 + 50Ã—20 = 2000
        assert summary.total_cost_basis_gbp == Decimal("2000")
        # total true cost:  100Ã—8  + 50Ã—15  = 800 + 750 = 1550
        assert summary.total_true_cost_gbp  == Decimal("1550")

    def test_security_with_no_active_lots_included(self, app_context):
        sec = _add_basic_security("EMPTY")
        # Add and fully dispose a lot
        _add_basic_lot(sec.id, quantity="5", acquisition_price="10.00", true_cost="10.00")
        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("5"),
            price_per_share_gbp=Decimal("12.00"),
            transaction_date=date(2024, 6, 1),
        )

        summary = PortfolioService.get_portfolio_summary()
        # Security still appears with zero totals
        tickers = [ss.security.ticker for ss in summary.securities]
        assert "EMPTY" in tickers
        ss = next(s for s in summary.securities if s.security.ticker == "EMPTY")
        assert ss.total_quantity == Decimal("0")

    def test_portfolio_employment_tax_matches_simulate_sell_all(self, app_context):
        sec = _add_basic_security("TAXX")
        _add_basic_lot(
            sec.id,
            scheme_type="SIP_PARTNERSHIP",
            acquisition_date=date.today() - timedelta(days=90),
            quantity="100",
            acquisition_price="10.00",
            true_cost="10.00",
        )

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                close_price_original_ccy="50.00",
                close_price_gbp="50.00",
                currency="GBP",
                source="test",
            )

        settings = AppSettings()
        settings.default_gross_income = Decimal("100000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_other_income = Decimal("0")

        summary = PortfolioService.get_portfolio_summary(settings=settings)

        simulated = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("50.00"),
            as_of_date=date.today(),
            settings=settings,
        )

        ss = summary.securities[0]
        assert ss.est_employment_tax_gbp == simulated.total_sip_employment_tax_gbp
        assert summary.est_total_employment_tax_gbp == simulated.total_sip_employment_tax_gbp
        assert summary.est_total_net_liquidation_gbp == (
            summary.total_market_value_gbp - simulated.total_sip_employment_tax_gbp
        )

    def test_summary_uses_persisted_true_cost_for_espp_plus_employee(self, app_context):
        sec = _add_basic_security("SUMTC")
        acq = date(2025, 3, 1)
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("100.00"),
            true_cost_per_share_gbp=Decimal("100.00"),
            import_source="ui_espp_plus_employee",
        )

        settings = AppSettings()
        settings.default_gross_income = Decimal("100000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_other_income = Decimal("0")
        settings.default_student_loan_plan = 2

        live = PortfolioService.get_portfolio_summary(settings=settings)
        stored = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )

        assert live.total_true_cost_gbp == Decimal("1000.00")
        assert stored.total_true_cost_gbp == Decimal("1000.00")

    def test_lot_est_net_proceeds_has_value_with_price_and_settings(self, app_context):
        sec = _add_basic_security("NETVAL")
        _add_basic_lot(sec.id, quantity="10", acquisition_price="10.00", true_cost="10.00")

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                close_price_original_ccy="12.00",
                close_price_gbp="12.00",
                currency="GBP",
                source="test",
            )

        settings = AppSettings()
        settings.default_gross_income = Decimal("60000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_other_income = Decimal("0")

        summary = PortfolioService.get_portfolio_summary(settings=settings)
        ls = summary.securities[0].active_lots[0]
        assert ls.est_net_proceeds_gbp is not None
        assert ls.est_net_proceeds_reason is None

    def test_lot_est_net_proceeds_has_reason_without_settings(self, app_context):
        sec = _add_basic_security("NETREASON")
        _add_basic_lot(sec.id, quantity="10", acquisition_price="10.00", true_cost="10.00")

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                close_price_original_ccy="12.00",
                close_price_gbp="12.00",
                currency="GBP",
                source="test",
            )

        summary = PortfolioService.get_portfolio_summary(settings=None)
        ls = summary.securities[0].active_lots[0]
        assert ls.est_net_proceeds_gbp is None
        assert ls.est_net_proceeds_reason is not None
        assert "Settings" in ls.est_net_proceeds_reason

    def test_lot_est_net_proceeds_has_reason_without_price(self, app_context):
        sec = _add_basic_security("NOPRICE")
        _add_basic_lot(sec.id, quantity="10", acquisition_price="10.00", true_cost="10.00")

        settings = AppSettings()
        settings.default_gross_income = Decimal("60000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_other_income = Decimal("0")

        summary = PortfolioService.get_portfolio_summary(settings=settings)
        ls = summary.securities[0].active_lots[0]
        assert ls.est_net_proceeds_gbp is None
        assert ls.est_net_proceeds_reason == "No live price available."

    def test_isa_lot_net_proceeds_are_available_without_settings(self, app_context):
        sec = _add_basic_security("ISANET")
        _add_basic_lot(
            sec.id,
            scheme_type="ISA",
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
        )

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                close_price_original_ccy="12.00",
                close_price_gbp="12.00",
                currency="GBP",
                source="test",
            )

        summary = PortfolioService.get_portfolio_summary(settings=None)
        ss = summary.securities[0]
        ls = ss.active_lots[0]
        assert ls.est_employment_tax_on_lot_gbp == Decimal("0.00")
        assert ls.est_net_proceeds_gbp == Decimal("120.00")
        assert ls.est_net_proceeds_reason is None
        assert ss.est_employment_tax_gbp == Decimal("0.00")
        assert ss.est_net_proceeds_gbp == Decimal("120.00")

    def test_lot_est_net_proceeds_has_locked_reason_for_pre_vest_rsu(self, app_context):
        sec = _add_basic_security("LOCKNET")
        vest_date = date.today() + timedelta(days=30)
        _add_basic_lot(
            sec.id,
            scheme_type="RSU",
            acquisition_date=vest_date,
            quantity="10",
            acquisition_price="10.00",
            true_cost="4.00",
        )

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                close_price_original_ccy="12.00",
                close_price_gbp="12.00",
                currency="GBP",
                source="test",
            )

        settings = AppSettings()
        settings.default_gross_income = Decimal("60000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_other_income = Decimal("0")

        summary = PortfolioService.get_portfolio_summary(settings=settings)
        ls = summary.securities[0].active_lots[0]
        assert ls.est_net_proceeds_gbp is None
        assert ls.est_net_proceeds_reason == f"Locked until {vest_date.isoformat()}."

    def test_security_price_is_marked_stale_when_price_date_is_old(self, app_context):
        sec = _add_basic_security("STALEPX")
        _add_basic_lot(sec.id, quantity="10", acquisition_price="10.00", true_cost="10.00")

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date.today() - timedelta(days=1),
                close_price_original_ccy="12.00",
                close_price_gbp="12.00",
                currency="GBP",
                source="google_sheets:2026-02-24 09:00:00",
            )

        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.price_is_stale is True

    def test_security_price_is_not_marked_stale_when_market_is_closed(self, app_context, monkeypatch):
        sec = PortfolioService.add_security(
            "CLOSEDPX", "Closed Price Corp", "USD", exchange="NASDAQ", is_manual_override=True
        )
        _add_basic_lot(sec.id, quantity="10", acquisition_price="10.00", true_cost="10.00")

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date(2026, 3, 6),
                close_price_original_ccy="12.00",
                close_price_gbp="9.00",
                currency="USD",
                source="twelvedata:2026-03-06",
            )

        from src.services import staleness_service as staleness_module

        class _ClosedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                fixed = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
                if tz is None:
                    return fixed.replace(tzinfo=None)
                return fixed.astimezone(tz)

        monkeypatch.setattr(staleness_module, "datetime", _ClosedDateTime)

        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.price_is_stale is False

    def test_security_fx_is_marked_stale_when_fx_timestamp_is_old(self, app_context):
        sec = PortfolioService.add_security(
            "STALEFX", "Stale FX Corp", "USD", is_manual_override=True
        )
        _add_basic_lot(sec.id, quantity="10", acquisition_price="10.00", true_cost="10.00")

        old_fx = "2026-02-24 08:00:00"
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date.today(),
                close_price_original_ccy="12.00",
                close_price_gbp="9.00",
                currency="USD",
                source=f"google_sheets:2026-02-24 09:00:00|fx:{old_fx}",
            )

        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.fx_as_of == old_fx
        assert ss.fx_is_stale is True

    def test_summary_exposes_native_and_gbp_values_with_fx_basis(self, app_context):
        sec = _add_basic_security("NATIVE", currency="USD")
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2024, 1, 15),
            quantity=Decimal("2"),
            acquisition_price_gbp=Decimal("80.00"),
            true_cost_per_share_gbp=Decimal("80.00"),
            broker_currency="USD",
        )

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date.today(),
                close_price_original_ccy="110.00",
                close_price_gbp="88.00",
                currency="USD",
                source="google_sheets:2026-02-24 12:00:00|fx:2026-02-24 12:00:00",
            )

        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        ls = ss.active_lots[0]
        assert ss.current_price_native == Decimal("110.00")
        assert ss.current_price_gbp == Decimal("88.00")
        assert ss.market_value_native == Decimal("220.00")
        assert ss.market_value_native_currency == "USD"
        assert ss.market_value_gbp == Decimal("176.00")
        assert ls.market_value_native == Decimal("220.00")
        assert ls.market_value_native_currency == "USD"
        assert ls.market_value_gbp == Decimal("176.00")
        assert summary.valuation_currency == "GBP"
        assert summary.fx_conversion_basis is not None


# ===========================================================================
# TestSimulateDisposal
# ===========================================================================

class TestSimulateDisposal:
    def test_single_lot_full_disposal(self, app_context):
        sec = _add_basic_security("SIM")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
        )

        assert result.is_fully_allocated
        assert result.quantity_sold == Decimal("100")
        assert result.shortfall     == Decimal("0")
        assert len(result.allocations) == 1

        alloc = result.allocations[0]
        assert alloc.proceeds_gbp      == Decimal("1500.00")   # 100 Ã— 15
        assert alloc.realised_gain_gbp == Decimal("500.00")    # 1500 - 1000

    def test_single_lot_partial_disposal(self, app_context):
        sec = _add_basic_security("PART")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="4.90")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("15.00"),
        )

        assert result.is_fully_allocated
        alloc = result.allocations[0]
        assert alloc.quantity_allocated          == Decimal("50")
        assert alloc.proceeds_gbp                == Decimal("750.00")
        assert alloc.realised_gain_gbp           == Decimal("250.00")  # 750 - 500
        assert alloc.realised_gain_economic_gbp  == Decimal("505.00")  # 750 - 50Ã—4.90

    def test_fifo_multi_lot_order(self, app_context):
        """Earliest-dated lot is consumed first."""
        sec = _add_basic_security("FIFO")

        # Lot 1: earlier (should be consumed first)
        _add_basic_lot(sec.id, acquisition_date=date(2024, 1, 1),
                       quantity="30", acquisition_price="10.00", true_cost="10.00")
        # Lot 2: later
        _add_basic_lot(sec.id, acquisition_date=date(2024, 6, 1),
                       quantity="50", acquisition_price="20.00", true_cost="20.00")

        # Dispose 40 shares: all 30 from lot1, then 10 from lot2
        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("40"),
            price_per_share_gbp=Decimal("25.00"),
        )

        assert result.is_fully_allocated
        assert len(result.allocations) == 2
        assert result.allocations[0].quantity_allocated == Decimal("30")  # lot1
        assert result.allocations[1].quantity_allocated == Decimal("10")  # lot2

    def test_simulate_does_not_persist(self, app_context):
        """simulate_disposal must not alter lot quantity_remaining."""
        sec = _add_basic_security("NOPERSIST")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
        )

        # Portfolio should be unchanged
        summary = PortfolioService.get_portfolio_summary()
        assert summary.securities[0].total_quantity == Decimal("100")

    def test_simulate_shortfall(self, app_context):
        """Returns partial FIFOResult when quantity exceeds available lots."""
        sec = _add_basic_security("SHORT")
        _add_basic_lot(sec.id, quantity="5", acquisition_price="10.00", true_cost="10.00")

        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("10"),
            price_per_share_gbp=Decimal("15.00"),
        )

        assert not result.is_fully_allocated
        assert result.shortfall == Decimal("5")

    def test_simulate_broker_fees_reduce_realised_gain_once(self, app_context):
        sec = _add_basic_security("SIMFEE")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        baseline = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
        )
        with_fees = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
            broker_fees_gbp=Decimal("9.99"),
        )

        assert with_fees.total_realised_gain_gbp == Decimal("490.01")
        assert (
            with_fees.total_realised_gain_gbp
            == baseline.total_realised_gain_gbp - Decimal("9.99")
        )
        assert (
            with_fees.total_realised_gain_economic_gbp
            == baseline.total_realised_gain_economic_gbp - Decimal("9.99")
        )
        assert (
            with_fees.allocations[0].realised_gain_gbp
            < baseline.allocations[0].realised_gain_gbp
        )

    def test_simulate_scheme_type_filter(self, app_context):
        """scheme_type filter restricts FIFO to matching lots only."""
        sec = _add_basic_security("SCHEME")
        # SIP_PARTNERSHIP lot
        _add_basic_lot(sec.id, scheme_type="SIP_PARTNERSHIP",
                       quantity="50", acquisition_price="8.00", true_cost="4.00",
                       acquisition_date=date(2024, 1, 1))
        # BROKERAGE lot
        _add_basic_lot(sec.id, scheme_type="BROKERAGE",
                       quantity="50", acquisition_price="10.00", true_cost="10.00",
                       acquisition_date=date(2024, 2, 1))

        # Simulate only against SIP_PARTNERSHIP lots
        result = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("50"),
            price_per_share_gbp=Decimal("12.00"),
            scheme_type="SIP_PARTNERSHIP",
        )

        assert result.is_fully_allocated
        assert len(result.allocations) == 1
        assert result.allocations[0].cost_basis_gbp == Decimal("400.00")   # 50 Ã— 8

    def test_simulate_espp_plus_employee_true_cost_is_locked_after_acquisition(self, app_context):
        sec = _add_basic_security("EPLIVE")
        acq = date(2025, 3, 1)

        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("100.00"),
            true_cost_per_share_gbp=Decimal("100.00"),  # acquisition-locked stored value
            import_source="ui_espp_plus_employee",
        )
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("1"),
            acquisition_price_gbp=Decimal("0"),
            true_cost_per_share_gbp=Decimal("0"),
            matching_lot_id=employee.id,
            forfeiture_period_end=acq + timedelta(days=183),
        )

        low = AppSettings()
        low.default_gross_income = Decimal("0")
        low.default_pension_sacrifice = Decimal("0")
        low.default_other_income = Decimal("0")
        low.default_student_loan_plan = 2

        high = AppSettings()
        high.default_gross_income = Decimal("100000")
        high.default_pension_sacrifice = Decimal("0")
        high.default_other_income = Decimal("0")
        high.default_student_loan_plan = 2

        result_low = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("10"),
            price_per_share_gbp=Decimal("120.00"),
            scheme_type="ESPP_PLUS",
            as_of_date=date(2025, 4, 1),
            settings=low,
        )
        result_high = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("10"),
            price_per_share_gbp=Decimal("120.00"),
            scheme_type="ESPP_PLUS",
            as_of_date=date(2025, 4, 1),
            settings=high,
        )

        assert result_low.total_true_cost_gbp == Decimal("1000.00")
        assert result_high.total_true_cost_gbp == Decimal("1000.00")

    # Additional scheme lock behaviour
    def test_simulate_excludes_locked_espp_plus_lots_until_forfeiture_end(self, app_context):
        sec = _add_basic_security("LOCKED")
        acq = date(2025, 1, 1)
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("100"),
            true_cost_per_share_gbp=Decimal("100"),
            import_source="ui_espp_plus_employee",
        )
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("0"),
            true_cost_per_share_gbp=Decimal("0"),
            matching_lot_id=employee.id,
            forfeiture_period_end=acq + timedelta(days=183),
        )

        locked = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("11"),
            price_per_share_gbp=Decimal("10"),
            scheme_type="ESPP_PLUS",
            as_of_date=date(2025, 3, 1),
        )
        assert locked.is_fully_allocated is False
        assert locked.quantity_sold == Decimal("10")
        assert locked.shortfall == Decimal("1")

        eligible = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("11"),
            price_per_share_gbp=Decimal("10"),
            scheme_type="ESPP_PLUS",
            as_of_date=date(2025, 8, 1),
        )
        assert eligible.is_fully_allocated is True
        assert eligible.quantity_sold == Decimal("11")

    def test_simulate_excludes_pre_vest_rsu_lots_until_vest_date(self, app_context):
        sec = _add_basic_security("RSULOCK")
        vest_date = date(2026, 6, 1)
        _add_basic_lot(
            sec.id,
            scheme_type="RSU",
            acquisition_date=vest_date,
            quantity="10",
            acquisition_price="100.00",
            true_cost="42.00",
        )

        with pytest.raises(ValueError, match="No sellable lots"):
            PortfolioService.simulate_disposal(
                security_id=sec.id,
                quantity=Decimal("1"),
                price_per_share_gbp=Decimal("120.00"),
                scheme_type="RSU",
                as_of_date=date(2026, 5, 31),
            )

        eligible = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("1"),
            price_per_share_gbp=Decimal("120.00"),
            scheme_type="RSU",
            as_of_date=vest_date,
        )
        assert eligible.is_fully_allocated is True
        assert eligible.quantity_sold == Decimal("1")


# ===========================================================================
# TestCommitDisposal
# ===========================================================================

class TestCommitDisposal:
    def test_persists_transaction_and_disposals(self, app_context):
        sec = _add_basic_security("COMMIT")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        tx, disposals = PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
            transaction_date=date(2024, 6, 1),
        )

        assert tx.id is not None
        assert tx.transaction_type == "DISPOSAL"
        assert Decimal(tx.total_proceeds_gbp) == Decimal("1500.00")
        assert len(disposals) == 1
        assert Decimal(disposals[0].realised_gain_gbp) == Decimal("500.00")   # 1500-1000

    def test_lot_quantity_remaining_reduced(self, app_context):
        """After a partial disposal, quantity_remaining is reduced in the DB."""
        sec = _add_basic_security("REDUCE")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("60"),
            price_per_share_gbp=Decimal("15.00"),
            transaction_date=date(2024, 6, 1),
        )

        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.total_quantity == Decimal("40")

    def test_multi_lot_disposal_allocates_fifo(self, app_context):
        """Disposal across two lots creates two LotDisposal records."""
        sec = _add_basic_security("MULTI")
        _add_basic_lot(sec.id, acquisition_date=date(2024, 1, 1),
                       quantity="30", acquisition_price="10.00", true_cost="10.00")
        _add_basic_lot(sec.id, acquisition_date=date(2024, 6, 1),
                       quantity="50", acquisition_price="20.00", true_cost="20.00")

        tx, disposals = PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("40"),
            price_per_share_gbp=Decimal("25.00"),
            transaction_date=date(2024, 9, 1),
        )

        assert len(disposals) == 2
        # First disposal: lot1 â€” 30 shares
        assert Decimal(disposals[0].quantity_allocated) == Decimal("30")
        # Second disposal: lot2 â€” 10 shares
        assert Decimal(disposals[1].quantity_allocated) == Decimal("10")

    def test_commit_espp_plus_employee_sale_forfeits_linked_matched_in_window(self, app_context):
        sec = _add_basic_security("FORFCOMMIT")
        acq = date(2025, 1, 1)
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
            fmv_at_acquisition_gbp=Decimal("12.00"),
        )
        matched = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("2"),
            acquisition_price_gbp=Decimal("0"),
            true_cost_per_share_gbp=Decimal("0"),
            fmv_at_acquisition_gbp=Decimal("12.00"),
            matching_lot_id=employee.id,
            forfeiture_period_end=acq + timedelta(days=183),
        )

        tx, disposals = PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("10"),
            price_per_share_gbp=Decimal("11.00"),
            transaction_date=acq + timedelta(days=30),
            scheme_type="ESPP_PLUS",
        )

        assert tx.id is not None
        assert len(disposals) == 1

        with AppContext.read_session() as sess:
            lots = LotRepository(sess).get_all_lots_for_security(sec.id)
            employee_after = LotRepository(sess).require_by_id(employee.id)
            matched_after = LotRepository(sess).require_by_id(matched.id)
            entries = AuditRepository(sess).list_for_record("lots", matched.id)

        assert employee_after.quantity_remaining == "0"
        assert matched_after.quantity_remaining == "0"
        assert matched_after.import_source == "commit_disposal_forfeiture"
        assert "Forfeited on disposal of linked ESPP+ employee lot" in (matched_after.notes or "")
        assert any("forfeited due to disposal" in (e.notes or "") for e in entries)

        active_qty = sum((Decimal(l.quantity_remaining) for l in lots), Decimal("0"))
        assert active_qty == Decimal("0")

    def test_raises_value_error_on_insufficient_lots(self, app_context):
        sec = _add_basic_security("NOSTOCK")

        with pytest.raises(ValueError, match="Insufficient active lots"):
            PortfolioService.commit_disposal(
                security_id=sec.id,
                quantity=Decimal("10"),
                price_per_share_gbp=Decimal("15.00"),
                transaction_date=date(2024, 6, 1),
            )

    def test_raises_value_error_when_quantity_exceeds_lots(self, app_context):
        sec = _add_basic_security("OVER")
        _add_basic_lot(sec.id, quantity="5", acquisition_price="10.00", true_cost="10.00")

        with pytest.raises(ValueError, match="Insufficient active lots"):
            PortfolioService.commit_disposal(
                security_id=sec.id,
                quantity=Decimal("10"),
                price_per_share_gbp=Decimal("15.00"),
                transaction_date=date(2024, 6, 1),
            )

    def test_broker_fees_stored_on_transaction(self, app_context):
        sec = _add_basic_security("FEE")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        tx, _ = PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
            transaction_date=date(2024, 6, 1),
            broker_fees_gbp=Decimal("9.99"),
        )

        assert tx.broker_fees_gbp == "9.99"

    def test_portfolio_summary_updated_after_commit(self, app_context):
        """End-to-end: add â†’ commit_disposal â†’ get_portfolio_summary shows new state."""
        sec = _add_basic_security("E2E")
        _add_basic_lot(sec.id, quantity="100", acquisition_price="10.00", true_cost="10.00")

        # Before
        before = PortfolioService.get_portfolio_summary()
        assert before.securities[0].total_quantity == Decimal("100")

        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("40"),
            price_per_share_gbp=Decimal("15.00"),
            transaction_date=date(2024, 6, 1),
        )

        # After
        after = PortfolioService.get_portfolio_summary()
        assert after.securities[0].total_quantity == Decimal("60")


class TestEditLot:
    def test_edit_lot_updates_fields_and_writes_audit_entry(self, app_context):
        sec = _add_basic_security("EDITSVC")
        lot = _add_basic_lot(
            sec.id,
            quantity="10",
            acquisition_price="100.00",
            true_cost="90.00",
            acquisition_date=date(2024, 6, 1),
        )

        updated, audit_id = PortfolioService.edit_lot(
            lot_id=lot.id,
            acquisition_date=date(2024, 7, 1),
            quantity=Decimal("12"),
            acquisition_price_gbp=Decimal("110.00"),
            true_cost_per_share_gbp=Decimal("95.00"),
            tax_year="2024-25",
            fmv_at_acquisition_gbp=Decimal("120.00"),
            notes="corrected",
        )

        assert updated.id == lot.id
        assert audit_id is not None

        summary = PortfolioService.get_portfolio_summary()
        ls = summary.securities[0].active_lots[0]
        assert ls.lot.quantity == "12"
        assert ls.lot.quantity_remaining == "12"
        assert ls.lot.acquisition_price_gbp == "110.00"

        with AppContext.read_session() as sess:
            entries = AuditRepository(sess).list_for_record("lots", lot.id)
        assert any(e.id == audit_id for e in entries)

    def test_edit_lot_rejects_quantity_below_disposed_amount(self, app_context):
        sec = _add_basic_security("EDITREJ")
        lot = _add_basic_lot(
            sec.id,
            quantity="10",
            acquisition_price="100.00",
            true_cost="90.00",
            acquisition_date=date(2024, 6, 1),
        )

        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("4"),
            price_per_share_gbp=Decimal("120.00"),
            transaction_date=date(2024, 10, 1),
        )

        with pytest.raises(ValueError, match="already disposed quantity"):
            PortfolioService.edit_lot(
                lot_id=lot.id,
                acquisition_date=date(2024, 7, 1),
                quantity=Decimal("3"),
                acquisition_price_gbp=Decimal("110.00"),
                true_cost_per_share_gbp=Decimal("95.00"),
                tax_year="2024-25",
                fmv_at_acquisition_gbp=Decimal("120.00"),
                notes="bad edit",
            )


class TestTransferLot:
    def test_transfer_lot_to_brokerage_updates_scheme_and_audit(self, app_context):
        sec = _add_basic_security("TRSVC")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )

        updated, audit_id = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
            quantity=Decimal("10"),
            notes="move to broker",
        )
        assert updated.scheme_type == "BROKERAGE"
        assert audit_id is not None

        with AppContext.read_session() as sess:
            source_after = LotRepository(sess).require_by_id(lot.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)
        with AppContext.read_session() as sess:
            txs = TransactionRepository(sess).list_for_security(sec.id)
        assert txs == []
        assert source_after.scheme_type == "ESPP"
        assert Decimal(source_after.quantity_remaining) == Decimal("0")
        broker_lots = [
            l
            for l in all_lots
            if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
        ]
        assert len(broker_lots) == 1
        assert Decimal(broker_lots[0].quantity_remaining) == Decimal("10")
        assert broker_lots[0].broker_currency == "GBP"

    def test_transfer_lot_accepts_explicit_destination_broker_currency(self, app_context):
        sec = _add_basic_security("TRCUR", currency="USD")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="RSU",
            quantity="10",
            acquisition_price="10.00",
            true_cost="4.00",
            acquisition_date=date.today() - timedelta(days=10),
        )

        updated, _ = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
            destination_broker_currency="USD",
        )
        assert updated.scheme_type == "BROKERAGE"
        assert updated.broker_currency == "USD"

    def test_transfer_espp_rejects_fractional_quantity(self, app_context):
        sec = _add_basic_security("TRFRAC")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )

        with pytest.raises(ValueError, match="whole shares"):
            PortfolioService.transfer_lot_to_brokerage(
                lot_id=lot.id,
                quantity=Decimal("1.5"),
            )

    def test_transfer_espp_partial_creates_source_and_broker_lots(self, app_context):
        sec = _add_basic_security("TRPART")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )

        transferred, _ = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
            quantity=Decimal("4"),
            notes="partial move",
        )
        assert transferred.scheme_type == "BROKERAGE"
        assert Decimal(transferred.quantity_remaining) == Decimal("4")

        with AppContext.read_session() as sess:
            source_after = LotRepository(sess).require_by_id(lot.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)

        assert source_after.scheme_type == "ESPP"
        assert Decimal(source_after.quantity_remaining) == Decimal("6")
        active_broker = [
            l for l in all_lots
            if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
        ]
        assert len(active_broker) == 1
        assert Decimal(active_broker[0].quantity_remaining) == Decimal("4")

    def test_transfer_espp_allows_whole_quantity_from_fractional_remaining_source(self, app_context):
        sec = _add_basic_security("TRFRACOK")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="2.3",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )

        transferred, _ = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
            quantity=Decimal("2"),
        )
        assert transferred.scheme_type == "BROKERAGE"
        assert Decimal(transferred.quantity_remaining) == Decimal("2")

        with AppContext.read_session() as sess:
            source_after = LotRepository(sess).require_by_id(lot.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)

        assert Decimal(source_after.quantity_remaining) == Decimal("0.3")
        active_broker = [
            l for l in all_lots
            if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
        ]
        assert len(active_broker) == 1
        assert Decimal(active_broker[0].quantity_remaining) == Decimal("2")

    def test_transfer_espp_default_quantity_uses_max_fifo_whole_shares(self, app_context):
        sec = _add_basic_security("TRDEFWHOLE")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="2.3",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )

        transferred, _ = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
        )
        assert transferred.scheme_type == "BROKERAGE"
        assert Decimal(transferred.quantity_remaining) == Decimal("2")

        with AppContext.read_session() as sess:
            source_after = LotRepository(sess).require_by_id(lot.id)
        assert Decimal(source_after.quantity_remaining) == Decimal("0.3")

    def test_transfer_espp_default_quantity_uses_floor_of_total_fifo_quantity(self, app_context):
        sec = _add_basic_security("TRDEFFLOOR")
        lot1 = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="0.6",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )
        lot2 = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="0.6",
            acquisition_price="11.00",
            true_cost="11.00",
            acquisition_date=date(2025, 2, 1),
        )

        PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot1.id,
        )

        with AppContext.read_session() as sess:
            lot1_after = LotRepository(sess).require_by_id(lot1.id)
            lot2_after = LotRepository(sess).require_by_id(lot2.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)

        assert Decimal(lot1_after.quantity_remaining) == Decimal("0")
        assert Decimal(lot2_after.quantity_remaining) == Decimal("0.2")
        active_broker = sorted(
            [
                l for l in all_lots
                if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
            ],
            key=lambda l: (l.acquisition_date, l.id),
        )
        assert len(active_broker) == 2
        assert Decimal(active_broker[0].quantity_remaining) == Decimal("0.6")
        assert Decimal(active_broker[1].quantity_remaining) == Decimal("0.4")

    def test_transfer_espp_fifo_consumes_fractional_head_before_newer_lot(self, app_context):
        sec = _add_basic_security("TRFIFOFRACT")
        lot1 = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="0.3",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )
        lot2 = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="2",
            acquisition_price="11.00",
            true_cost="11.00",
            acquisition_date=date(2025, 2, 1),
        )

        PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot1.id,
            quantity=Decimal("2"),
        )

        with AppContext.read_session() as sess:
            lot1_after = LotRepository(sess).require_by_id(lot1.id)
            lot2_after = LotRepository(sess).require_by_id(lot2.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)

        assert Decimal(lot1_after.quantity_remaining) == Decimal("0")
        assert Decimal(lot2_after.quantity_remaining) == Decimal("0.3")
        active_broker = sorted(
            [
                l for l in all_lots
                if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
            ],
            key=lambda l: (l.acquisition_date, l.id),
        )
        assert len(active_broker) == 2
        assert Decimal(active_broker[0].quantity_remaining) == Decimal("0.3")
        assert Decimal(active_broker[1].quantity_remaining) == Decimal("1.7")

    def test_transfer_espp_remainder_merges_to_single_broker_lot(self, app_context):
        sec = _add_basic_security("TRMERGE")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )

        PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
            quantity=Decimal("4"),
        )
        PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot.id,
            quantity=Decimal("6"),
        )

        with AppContext.read_session() as sess:
            source_after = LotRepository(sess).require_by_id(lot.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)

        assert Decimal(source_after.quantity_remaining) == Decimal("0")
        active_broker = [
            l for l in all_lots
            if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
        ]
        assert len(active_broker) == 1
        assert Decimal(active_broker[0].quantity_remaining) == Decimal("10")

    def test_transfer_espp_fifo_spans_multiple_lots_and_creates_multiple_broker_lots(self, app_context):
        sec = _add_basic_security("TRFIFO")
        lot1 = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="5",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )
        lot2 = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="7",
            acquisition_price="11.00",
            true_cost="11.00",
            acquisition_date=date(2025, 2, 1),
        )

        PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot1.id,
            quantity=Decimal("9"),
        )

        with AppContext.read_session() as sess:
            lot1_after = LotRepository(sess).require_by_id(lot1.id)
            lot2_after = LotRepository(sess).require_by_id(lot2.id)
            all_lots = LotRepository(sess).get_all_lots_for_security(sec.id)

        assert Decimal(lot1_after.quantity_remaining) == Decimal("0")
        assert Decimal(lot2_after.quantity_remaining) == Decimal("3")
        active_broker = sorted(
            [
                l for l in all_lots
                if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
            ],
            key=lambda l: (l.acquisition_date, l.id),
        )
        assert len(active_broker) == 2
        assert Decimal(active_broker[0].quantity_remaining) == Decimal("5")
        assert Decimal(active_broker[1].quantity_remaining) == Decimal("4")

    def test_transfer_espp_rejects_non_fifo_lot_selection(self, app_context):
        sec = _add_basic_security("TRNOFIFO")
        _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="5",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2025, 1, 1),
        )
        later = _add_basic_lot(
            sec.id,
            scheme_type="ESPP",
            quantity="7",
            acquisition_price="11.00",
            true_cost="11.00",
            acquisition_date=date(2025, 2, 1),
        )

        with pytest.raises(ValueError, match="FIFO order"):
            PortfolioService.transfer_lot_to_brokerage(
                lot_id=later.id,
                quantity=Decimal("1"),
            )

    def test_transfer_lot_rejects_non_eligible_scheme(self, app_context):
        sec = _add_basic_security("TRBAD")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="BROKERAGE",
            quantity="10",
            acquisition_price="10.00",
            true_cost="10.00",
        )

        with pytest.raises(ValueError, match="Only RSU, ESPP, and ESPP_PLUS"):
            PortfolioService.transfer_lot_to_brokerage(lot_id=lot.id)

    def test_transfer_lot_rejects_pre_vest_rsu(self, app_context):
        sec = _add_basic_security("TRRSULOCK")
        lot = _add_basic_lot(
            sec.id,
            scheme_type="RSU",
            quantity="10",
            acquisition_price="10.00",
            true_cost="4.00",
            acquisition_date=date.today() + timedelta(days=10),
        )

        with pytest.raises(ValueError, match="after vest date"):
            PortfolioService.transfer_lot_to_brokerage(lot_id=lot.id)

    def test_transfer_espp_plus_employee_forfeits_linked_matched_in_window(self, app_context):
        sec = _add_basic_security("TREPLFORF")
        acq = date.today()
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
        )
        matched = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("2"),
            acquisition_price_gbp=Decimal("0.00"),
            true_cost_per_share_gbp=Decimal("0.00"),
            matching_lot_id=employee.id,
            forfeiture_period_end=acq + timedelta(days=183),
        )

        updated, _ = PortfolioService.transfer_lot_to_brokerage(
            lot_id=employee.id,
            notes="move",
            settings=None,
        )
        assert updated.scheme_type == "BROKERAGE"

        with AppContext.read_session() as sess:
            match_after = LotRepository(sess).require_by_id(matched.id)
            employee_after = LotRepository(sess).require_by_id(employee.id)
            transfer_events = EmploymentTaxEventRepository(sess).list_for_lot(employee.id)
        assert Decimal(match_after.quantity_remaining) == Decimal("0")
        assert "Forfeited on transfer of linked ESPP+ employee lot" in (match_after.notes or "")
        assert employee_after.scheme_type == "BROKERAGE"
        assert "employment_tax_due_on_transfer" not in (employee_after.notes or "")
        assert len(transfer_events) == 1
        assert transfer_events[0].event_type == "ESPP_PLUS_TRANSFER"
        assert transfer_events[0].estimated_tax_gbp is None
        assert "configure Settings" in (transfer_events[0].estimation_notes or "")

    def test_transfer_espp_plus_records_structured_tax_event_with_estimate(self, app_context):
        sec = _add_basic_security("TREPLTAX")
        acq = date.today() - timedelta(days=30)
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
            fmv_at_acquisition_gbp=Decimal("14.00"),
        )

        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=date.today(),
                close_price_original_ccy="15.00",
                close_price_gbp="15.00",
                currency="GBP",
                source="test",
            )

        settings = AppSettings()
        settings.default_gross_income = Decimal("100000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_other_income = Decimal("0")
        settings.default_student_loan_plan = 2

        updated, _ = PortfolioService.transfer_lot_to_brokerage(
            lot_id=employee.id,
            settings=settings,
        )
        assert updated.scheme_type == "BROKERAGE"

        with AppContext.read_session() as sess:
            transfer_events = EmploymentTaxEventRepository(sess).list_for_lot(employee.id)

        assert len(transfer_events) == 1
        assert transfer_events[0].event_type == "ESPP_PLUS_TRANSFER"
        assert transfer_events[0].estimated_tax_gbp is not None
        assert transfer_events[0].estimation_notes is None

    def test_transfer_rejects_espp_plus_matched_lot_source(self, app_context):
        sec = _add_basic_security("TREPLM")
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=date.today(),
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
        )
        matched = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=date.today(),
            quantity=Decimal("2"),
            acquisition_price_gbp=Decimal("0.00"),
            true_cost_per_share_gbp=Decimal("0.00"),
            matching_lot_id=employee.id,
            forfeiture_period_end=date.today() + timedelta(days=183),
        )

        with pytest.raises(ValueError, match="linked ESPP\\+ employee lot"):
            PortfolioService.transfer_lot_to_brokerage(lot_id=matched.id)


class TestSellabilityStatus:
    def test_rsu_pre_vest_is_locked_with_unlock_date(self, app_context):
        sec = _add_basic_security("SELLRSU")
        vest_date = date.today() + timedelta(days=10)
        _add_basic_lot(
            sec.id,
            scheme_type="RSU",
            acquisition_date=vest_date,
            quantity="5",
            acquisition_price="10.00",
            true_cost="4.00",
        )

        summary = PortfolioService.get_portfolio_summary()
        ls = summary.securities[0].active_lots[0]
        assert ls.sellability_status == "LOCKED"
        assert ls.sellability_unlock_date == vest_date

    def test_espp_plus_employee_is_at_risk_while_match_window_open(self, app_context):
        sec = _add_basic_security("SELLEPL")
        acq = date.today()
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
        )
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("2"),
            acquisition_price_gbp=Decimal("0.00"),
            true_cost_per_share_gbp=Decimal("0.00"),
            matching_lot_id=employee.id,
            forfeiture_period_end=acq + timedelta(days=183),
        )

        summary = PortfolioService.get_portfolio_summary()
        lots = summary.securities[0].active_lots
        employee_summary = next(ls for ls in lots if ls.lot.id == employee.id)
        matched_summary = next(ls for ls in lots if ls.lot.id != employee.id)
        assert employee_summary.sellability_status == "AT_RISK"
        assert matched_summary.sellability_status == "LOCKED"

    def test_espp_plus_statuses_turn_sellable_after_forfeiture_window(self, app_context):
        sec = _add_basic_security("SELLDONE")
        acq = date.today() - timedelta(days=200)
        employee = PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
        )
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="ESPP_PLUS",
            acquisition_date=acq,
            quantity=Decimal("2"),
            acquisition_price_gbp=Decimal("0.00"),
            true_cost_per_share_gbp=Decimal("0.00"),
            matching_lot_id=employee.id,
            forfeiture_period_end=acq + timedelta(days=183),
        )

        summary = PortfolioService.get_portfolio_summary()
        lots = summary.securities[0].active_lots
        assert all(ls.sellability_status == "SELLABLE" for ls in lots)
