"""
Tests for ReportService.

Coverage:
  - cgt_summary: empty year, single disposal, cross-year exclusion, loss handling,
                 CGT calculation with TaxContext (basic-rate and higher-rate taxpayers)
  - economic_gain_summary: CGT vs economic gain divergence (SIP partnership scheme),
                           empty year, multi-disposal aggregation
  - audit_log: passthrough filter behaviour
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.core.tax_engine.context import TaxContext
from src.services import (
    CgtSummaryReport,
    DisposalLine,
    EconomicGainReport,
    PortfolioService,
    ReportService,
)
from src.db.repository import AuditRepository
from src.app_context import AppContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_security_and_lot(
    ticker: str,
    *,
    quantity: str = "100",
    acquisition_price: str = "10.00",
    true_cost: str = "10.00",
    scheme_type: str = "BROKERAGE",
    acquisition_date: date = date(2024, 1, 15),
):
    """Create a security with a single lot; return the security."""
    sec = PortfolioService.add_security(ticker, f"{ticker} Corp", "GBP", is_manual_override=True)
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type=scheme_type,
        acquisition_date=acquisition_date,
        quantity=Decimal(quantity),
        acquisition_price_gbp=Decimal(acquisition_price),
        true_cost_per_share_gbp=Decimal(true_cost),
    )
    return sec


def _dispose(
    security_id: str,
    *,
    quantity: str = "100",
    price: str = "15.00",
    transaction_date: date = date(2024, 6, 1),
):
    PortfolioService.commit_disposal(
        security_id=security_id,
        quantity=Decimal(quantity),
        price_per_share_gbp=Decimal(price),
        transaction_date=transaction_date,
    )


# ===========================================================================
# TestCgtSummary
# ===========================================================================

class TestCgtSummary:
    def test_empty_year_returns_zero_report(self, app_context):
        report = ReportService.cgt_summary("2024-25")

        assert isinstance(report, CgtSummaryReport)
        assert report.disposal_lines    == []
        assert report.total_gains_gbp   == Decimal("0")
        assert report.total_losses_gbp  == Decimal("0")
        assert report.net_gain_gbp      == Decimal("0")
        assert report.cgt_result is None

    def test_single_disposal_in_year(self, app_context):
        # 100 shares @ £10 cost, sold @ £15 → gain £500
        sec = _setup_security_and_lot("CGTA", quantity="100",
                                      acquisition_price="10.00", true_cost="10.00")
        _dispose(sec.id, quantity="100", price="15.00",
                 transaction_date=date(2024, 6, 1))  # 2024-25

        report = ReportService.cgt_summary("2024-25")

        assert len(report.disposal_lines) == 1
        dl = report.disposal_lines[0]
        assert isinstance(dl, DisposalLine)
        assert dl.total_quantity         == Decimal("100")
        assert dl.total_proceeds_gbp     == Decimal("1500.00")
        assert dl.total_gain_gbp         == Decimal("500.00")

        assert report.total_proceeds_gbp  == Decimal("1500.00")
        assert report.total_gains_gbp     == Decimal("500.00")
        assert report.total_losses_gbp    == Decimal("0")
        assert report.net_gain_gbp        == Decimal("500.00")

    def test_disposal_excluded_from_different_tax_year(self, app_context):
        # Disposal on 2023-06-01 is in 2023-24, not 2024-25
        sec = _setup_security_and_lot("CGTB", acquisition_date=date(2023, 1, 1))
        _dispose(sec.id, transaction_date=date(2023, 6, 1))

        report_current  = ReportService.cgt_summary("2024-25")
        report_previous = ReportService.cgt_summary("2023-24")

        assert report_current.disposal_lines  == []
        assert len(report_previous.disposal_lines) == 1

    def test_disposal_on_tax_year_boundary_5_april(self, app_context):
        # 5 April 2025 is the last day of 2024-25
        sec = _setup_security_and_lot("BOUND", acquisition_date=date(2024, 1, 1))
        _dispose(sec.id, transaction_date=date(2025, 4, 5))

        report = ReportService.cgt_summary("2024-25")
        assert len(report.disposal_lines) == 1

    def test_disposal_on_tax_year_start_6_april(self, app_context):
        # 6 April 2025 is the first day of 2025-26
        sec = _setup_security_and_lot("START", acquisition_date=date(2024, 1, 1))
        _dispose(sec.id, transaction_date=date(2025, 4, 6))

        assert ReportService.cgt_summary("2024-25").disposal_lines == []
        assert len(ReportService.cgt_summary("2025-26").disposal_lines) == 1

    def test_loss_disposal(self, app_context):
        # 100 shares @ £10 cost, sold @ £7 → loss £300
        sec = _setup_security_and_lot("LOSS",
                                      quantity="100",
                                      acquisition_price="10.00",
                                      true_cost="10.00")
        _dispose(sec.id, quantity="100", price="7.00",
                 transaction_date=date(2024, 6, 1))

        report = ReportService.cgt_summary("2024-25")

        assert report.total_gains_gbp  == Decimal("0")
        assert report.total_losses_gbp == Decimal("300.00")
        assert report.net_gain_gbp     == Decimal("-300.00")

    def test_multi_disposal_aggregation(self, app_context):
        # Two disposals: one gain, one loss
        sec1 = _setup_security_and_lot("GAIN", quantity="100",
                                        acquisition_price="10.00", true_cost="10.00")
        sec2 = _setup_security_and_lot("LOSS2", quantity="100",
                                        acquisition_price="10.00", true_cost="10.00")

        _dispose(sec1.id, quantity="100", price="20.00",
                 transaction_date=date(2024, 6, 1))   # +£1,000 gain
        _dispose(sec2.id, quantity="100", price="8.00",
                 transaction_date=date(2024, 7, 1))    # -£200 loss

        report = ReportService.cgt_summary("2024-25")

        assert len(report.disposal_lines) == 2
        assert report.total_gains_gbp    == Decimal("1000.00")
        assert report.total_losses_gbp   == Decimal("200.00")
        assert report.net_gain_gbp       == Decimal("800.00")

    def test_broker_fees_reduce_reported_gain_and_match_simulation(self, app_context):
        sec = _setup_security_and_lot("FEEGAIN", quantity="100",
                                      acquisition_price="10.00", true_cost="10.00")
        fee = Decimal("12.50")

        simulated = PortfolioService.simulate_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
            broker_fees_gbp=fee,
        )
        PortfolioService.commit_disposal(
            security_id=sec.id,
            quantity=Decimal("100"),
            price_per_share_gbp=Decimal("15.00"),
            transaction_date=date(2024, 6, 1),
            broker_fees_gbp=fee,
        )

        report = ReportService.cgt_summary("2024-25")
        assert len(report.disposal_lines) == 1
        line = report.disposal_lines[0]

        # Gross gain = 500.00; fee must be applied exactly once.
        assert line.total_gain_gbp == Decimal("487.50")
        assert line.total_gain_gbp == simulated.total_realised_gain_gbp
        assert line.total_gain_gbp == Decimal("500.00") - fee

    def test_cgt_summary_applies_hmrc_30_day_matching_for_rebuy(self, app_context):
        sec = _setup_security_and_lot(
            "BEDBREAK",
            quantity="100",
            acquisition_price="10.00",
            true_cost="10.00",
            acquisition_date=date(2024, 1, 1),
        )
        _dispose(
            sec.id,
            quantity="100",
            price="15.00",
            transaction_date=date(2024, 6, 1),
        )
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2024, 6, 15),
            quantity=Decimal("100"),
            acquisition_price_gbp=Decimal("14.00"),
            true_cost_per_share_gbp=Decimal("14.00"),
        )

        report = ReportService.cgt_summary("2024-25")

        assert len(report.disposal_lines) == 1
        assert report.disposal_lines[0].total_gain_gbp == Decimal("100.00")
        assert report.total_gains_gbp == Decimal("100.00")
        assert report.net_gain_gbp == Decimal("100.00")

    def test_cgt_result_none_without_tax_context(self, app_context):
        sec = _setup_security_and_lot("NOCTX")
        _dispose(sec.id)

        report = ReportService.cgt_summary("2024-25")
        assert report.cgt_result is None

    def test_cgt_calculated_basic_rate_taxpayer(self, app_context):
        """
        Basic-rate taxpayer (£40k gross, no pension, no SL).

        Disposal: 100 shares, cost £10, price £50 → gain £4,000.
        AEA 2024-25 = £3,000 → taxable gain = £1,000.
        Taxable income = £40,000 - £12,570 PA = £27,430.
        Basic rate band remaining = £37,700 - £27,430 = £10,270.
        CGT at 10%: £1,000 × 10% = £100.
        """
        sec = _setup_security_and_lot("BASIC",
                                      quantity="100",
                                      acquisition_price="10.00",
                                      true_cost="10.00")
        _dispose(sec.id, quantity="100", price="50.00",
                 transaction_date=date(2024, 6, 1))

        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("40000"),
        )
        report = ReportService.cgt_summary("2024-25", tax_context=ctx)

        assert report.cgt_result is not None
        cr = report.cgt_result
        assert cr.taxable_gain        == Decimal("1000.00")
        assert cr.tax_at_basic_rate   == Decimal("100.00")
        assert cr.tax_at_higher_rate  == Decimal("0")
        assert cr.total_cgt           == Decimal("100.00")

    def test_cgt_calculated_higher_rate_taxpayer(self, app_context):
        """
        Higher-rate taxpayer (£80k gross, no pension, no SL).

        Disposal: 100 shares, cost £10, price £50 → gain £4,000.
        AEA £3,000 → taxable gain = £1,000.
        Taxable income = £80,000 - £12,570 = £67,430 (exceeds basic band £37,700).
        Basic rate band remaining = £0 → all CGT at 20%.
        CGT: £1,000 × 20% = £200.
        """
        sec = _setup_security_and_lot("HIGH",
                                      quantity="100",
                                      acquisition_price="10.00",
                                      true_cost="10.00")
        _dispose(sec.id, quantity="100", price="50.00",
                 transaction_date=date(2024, 6, 1))

        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("80000"),
        )
        report = ReportService.cgt_summary("2024-25", tax_context=ctx)

        cr = report.cgt_result
        assert cr.taxable_gain       == Decimal("1000.00")
        assert cr.tax_at_basic_rate  == Decimal("0")
        assert cr.tax_at_higher_rate == Decimal("200.00")
        assert cr.total_cgt          == Decimal("200.00")

    def test_cgt_below_aea_no_tax(self, app_context):
        """Gain below AEA (£3,000 in 2024-25) → no CGT."""
        sec = _setup_security_and_lot("AEA",
                                      quantity="100",
                                      acquisition_price="10.00",
                                      true_cost="10.00")
        # Gain = 100 × (12 - 10) = £200 < AEA £3,000
        _dispose(sec.id, quantity="100", price="12.00",
                 transaction_date=date(2024, 6, 1))

        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("80000"),
        )
        report = ReportService.cgt_summary("2024-25", tax_context=ctx)

        cr = report.cgt_result
        assert cr.taxable_gain == Decimal("0")
        assert cr.total_cgt    == Decimal("0")

    def test_disposal_line_references_correct_security(self, app_context):
        sec = _setup_security_and_lot("SECREF")
        _dispose(sec.id)

        report = ReportService.cgt_summary("2024-25")
        assert report.disposal_lines[0].security.ticker == "SECREF"

    def test_disposal_line_contains_lot_disposals(self, app_context):
        sec = _setup_security_and_lot("LOTS")
        _dispose(sec.id)

        report = ReportService.cgt_summary("2024-25")
        dl = report.disposal_lines[0]
        assert len(dl.lot_disposals) == 1

    def test_isa_disposals_are_excluded_from_cgt_totals(self, app_context):
        sec = _setup_security_and_lot(
            "ISAEX",
            quantity="100",
            acquisition_price="10.00",
            true_cost="10.00",
            scheme_type="ISA",
        )
        _dispose(sec.id, quantity="100", price="15.00", transaction_date=date(2024, 6, 1))

        report = ReportService.cgt_summary("2024-25")
        assert report.disposal_lines == []
        assert report.total_proceeds_gbp == Decimal("0")
        assert report.total_gains_gbp == Decimal("0")
        assert report.total_losses_gbp == Decimal("0")
        assert report.net_gain_gbp == Decimal("0")
        assert report.isa_exempt_proceeds_gbp == Decimal("1500.00")
        assert report.isa_exempt_gain_gbp == Decimal("500.00")


# ===========================================================================
# TestEconomicGainSummary
# ===========================================================================

class TestEconomicGainSummary:
    def test_empty_year(self, app_context):
        report = ReportService.economic_gain_summary("2024-25")

        assert isinstance(report, EconomicGainReport)
        assert report.disposal_lines           == []
        assert report.total_economic_gains_gbp  == Decimal("0")
        assert report.net_economic_gain_gbp     == Decimal("0")

    def test_rsu_economic_gain_equals_cgt_gain(self, app_context):
        """RSU: true_cost_per_share == acquisition_price_gbp, so gains are identical."""
        sec = _setup_security_and_lot("RSU",
                                      quantity="100",
                                      acquisition_price="10.00",
                                      true_cost="10.00",
                                      scheme_type="RSU")
        _dispose(sec.id, quantity="100", price="15.00",
                 transaction_date=date(2024, 6, 1))

        report = ReportService.economic_gain_summary("2024-25")

        dl = report.disposal_lines[0]
        assert dl.total_gain_gbp          == Decimal("500.00")
        assert dl.total_economic_gain_gbp == Decimal("500.00")

    def test_sip_partnership_economic_gain_higher_than_cgt(self, app_context):
        """
        SIP partnership shares bought from gross salary:
          acquisition_price_gbp   = £10.00  (CGT cost basis = FMV at acquisition)
          true_cost_per_share_gbp = £4.90   (after ~51% combined tax saving at acquisition)

        Disposed at £15.00:
          CGT gain:      100 × (15 - 10)   = £500
          Economic gain: 100 × (15 - 4.90) = £1,010
        """
        sec = PortfolioService.add_security("SIP", "SIP Corp", "GBP", is_manual_override=True)
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="SIP_PARTNERSHIP",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("100"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("4.90"),
        )
        _dispose(sec.id, quantity="100", price="15.00",
                 transaction_date=date(2024, 6, 1))

        report = ReportService.economic_gain_summary("2024-25")

        assert len(report.disposal_lines) == 1
        dl = report.disposal_lines[0]
        assert dl.total_gain_gbp          == Decimal("500.00")
        assert dl.total_economic_gain_gbp == Decimal("1010.00")

        assert report.total_economic_gains_gbp  == Decimal("1010.00")
        assert report.total_economic_losses_gbp == Decimal("0")
        assert report.net_economic_gain_gbp     == Decimal("1010.00")

    def test_economic_loss(self, app_context):
        """Economic loss when disposal price is below true cost per share."""
        sec = PortfolioService.add_security("ELOSS", "Economic Loss Corp", "GBP", is_manual_override=True)
        # true_cost = £12 (high tax rate at acquisition), disposal price = £10
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="RSU",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("100"),
            acquisition_price_gbp=Decimal("10.00"),   # CGT breakeven at £10
            true_cost_per_share_gbp=Decimal("12.00"), # higher economic cost
        )
        _dispose(sec.id, quantity="100", price="11.00",
                 transaction_date=date(2024, 6, 1))

        report = ReportService.economic_gain_summary("2024-25")

        dl = report.disposal_lines[0]
        # CGT gain: 100 × (11 - 10) = £100
        assert dl.total_gain_gbp          == Decimal("100.00")
        # Economic loss: 100 × (11 - 12) = -£100
        assert dl.total_economic_gain_gbp == Decimal("-100.00")

        assert report.total_economic_gains_gbp  == Decimal("0")
        assert report.total_economic_losses_gbp == Decimal("100.00")
        assert report.net_economic_gain_gbp     == Decimal("-100.00")

    def test_excluded_from_different_tax_year(self, app_context):
        sec = _setup_security_and_lot("EYEAR", acquisition_date=date(2023, 1, 1))
        _dispose(sec.id, transaction_date=date(2023, 6, 1))

        assert ReportService.economic_gain_summary("2024-25").disposal_lines == []

    def test_isa_disposals_are_excluded_from_economic_totals(self, app_context):
        sec = _setup_security_and_lot(
            "ISAECO",
            quantity="100",
            acquisition_price="10.00",
            true_cost="10.00",
            scheme_type="ISA",
        )
        _dispose(sec.id, quantity="100", price="15.00", transaction_date=date(2024, 6, 1))

        report = ReportService.economic_gain_summary("2024-25")
        assert report.disposal_lines == []
        assert report.total_proceeds_gbp == Decimal("0")
        assert report.total_economic_gains_gbp == Decimal("0")
        assert report.total_economic_losses_gbp == Decimal("0")
        assert report.net_economic_gain_gbp == Decimal("0")
        assert report.isa_exempt_proceeds_gbp == Decimal("1500.00")
        assert report.isa_exempt_economic_gain_gbp == Decimal("500.00")


# ===========================================================================
# TestAuditLog
# ===========================================================================

class TestAuditLog:
    def test_returns_all_entries(self, app_context):
        # add_security writes an audit INSERT
        PortfolioService.add_security("AUDITME", "Audit Corp", "GBP", is_manual_override=True)

        entries = ReportService.audit_log()
        assert len(entries) >= 1

    def test_table_name_filter(self, app_context):
        PortfolioService.add_security("FILTER", "Filter Corp", "GBP", is_manual_override=True)

        securities_entries = ReportService.audit_log(table_name="securities")
        assert all(e.table_name == "securities" for e in securities_entries)
        assert len(securities_entries) >= 1

        # "lots" table should be empty at this point
        lots_entries = ReportService.audit_log(table_name="lots")
        assert lots_entries == []

    def test_entries_after_add_lot(self, app_context):
        sec = PortfolioService.add_security("LOTAUDIT", "Lot Audit Corp", "GBP", is_manual_override=True)
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="RSU",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("100"),
            acquisition_price_gbp=Decimal("10.00"),
            true_cost_per_share_gbp=Decimal("10.00"),
        )

        lots_entries = ReportService.audit_log(table_name="lots")
        assert len(lots_entries) >= 1
        assert lots_entries[0].action == "INSERT"

    def test_record_id_filter(self, app_context):
        sec_a = PortfolioService.add_security("RID1", "Record Filter 1", "GBP", is_manual_override=True)
        _ = PortfolioService.add_security("RID2", "Record Filter 2", "GBP", is_manual_override=True)

        entries = ReportService.audit_log(table_name="securities", record_id=sec_a.id)
        assert entries
        assert all(e.table_name == "securities" for e in entries)
        assert all(e.record_id == sec_a.id for e in entries)
