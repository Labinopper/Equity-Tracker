from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.models import Lot
from src.db.repository import LotRepository, LotTransferEventRepository
from src.services.cash_ledger_service import CashLedgerService
from src.services.dividend_service import DividendService
from src.services.dividend_service import _eligible_quantities_by_holding_bucket_on_ex_date
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def _add_security(ticker: str):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Dividend Co",
        currency="GBP",
        is_manual_override=True,
    )


def test_dividend_summary_empty_portfolio_returns_zeroed_state(app_context):
    payload = DividendService.get_summary(as_of=date(2026, 2, 24))

    assert payload["hide_values"] is False
    assert payload["entries"] == []
    assert payload["summary"]["all_time_total_gbp"] == "0.00"
    assert payload["summary"]["estimated_tax_gbp"] == "0.00"


def test_dividend_summary_trailing_forecast_and_tax_year_totals(app_context):
    sec = _add_security("DIVSVC")
    as_of = date(2026, 2, 24)

    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2025, 6, 1),
        amount_gbp=Decimal("1000"),
        tax_treatment="TAXABLE",
    )
    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2025, 7, 1),
        amount_gbp=Decimal("50"),
        tax_treatment="ISA_EXEMPT",
    )
    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2026, 6, 1),
        amount_gbp=Decimal("200"),
        tax_treatment="TAXABLE",
    )

    payload = DividendService.get_summary(as_of=as_of)
    summary = payload["summary"]

    assert summary["trailing_12m_total_gbp"] == "1050.00"
    assert summary["forecast_12m_total_gbp"] == "200.00"
    assert summary["actual_to_date_total_gbp"] == "1050.00"
    assert summary["forecast_entry_total_gbp"] == "200.00"
    assert summary["actual_entry_count"] == 2
    assert summary["forecast_entry_count"] == 1
    assert summary["all_time_total_gbp"] == "1250.00"
    assert summary["all_time_taxable_dividends_gbp"] == "1200.00"
    assert summary["all_time_isa_exempt_dividends_gbp"] == "50.00"
    assert summary["estimated_tax_gbp"] == "43.75"
    assert summary["estimated_net_dividends_gbp"] == "1206.25"
    assert summary["tax_drag_pct"] == "3.65"

    years = {row["tax_year"]: row for row in payload["tax_years"]}
    assert years["2025-26"]["taxable_dividends_gbp"] == "1000.00"
    assert years["2025-26"]["estimated_dividend_tax_gbp"] == "43.75"
    assert years["2026-27"]["taxable_dividends_gbp"] == "200.00"
    assert years["2026-27"]["estimated_dividend_tax_gbp"] == "0.00"


def test_dividend_summary_respects_hide_values_mode(app_context):
    settings = AppSettings()
    settings.hide_values = True

    payload = DividendService.get_summary(settings=settings, as_of=date(2026, 2, 24))
    assert payload["hide_values"] is True
    assert payload["entries"] == []
    assert payload["tax_years"] == []


def test_dividend_entry_supports_native_currency_with_fx_provenance(app_context):
    sec = _add_security("DIVUSD")

    created = DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2026, 2, 10),
        amount_original_ccy=Decimal("100.00"),
        original_currency="USD",
        fx_rate_to_gbp=Decimal("0.8000"),
        fx_rate_source="manual_test",
        tax_treatment="TAXABLE",
    )
    assert created["amount_gbp"] == "80.00"
    assert created["amount_original_ccy"] == "100.00"
    assert created["original_currency"] == "USD"
    assert created["fx_rate_to_gbp"] == "0.800000"

    payload = DividendService.get_summary(as_of=date(2026, 2, 24))
    entry = payload["entries"][0]
    assert entry["amount_gbp"] == "80.00"
    assert entry["amount_original_ccy"] == "100.00"
    assert entry["original_currency"] == "USD"
    assert entry["fx_rate_to_gbp"] == "0.800000"
    assert payload["allocation"]["mode"] == "SECURITY_LEVEL"
    assert payload["allocation"]["rows"][0]["ticker"] == "DIVUSD"


def test_dividend_entry_auto_resolves_fx_when_non_gbp_rate_missing(app_context, monkeypatch):
    sec = _add_security("DIVAUTO")

    def _fake_auto_fx(*, from_currency: str, dividend_date: date):
        assert from_currency == "USD"
        assert dividend_date == date(2026, 2, 12)
        return Decimal("0.800000"), "auto_test:2026-02-12"

    monkeypatch.setattr(
        "src.services.dividend_service._auto_fx_rate_to_gbp",
        _fake_auto_fx,
    )

    created = DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2026, 2, 12),
        amount_original_ccy=Decimal("10.00"),
        original_currency="USD",
        tax_treatment="TAXABLE",
    )
    assert created["amount_gbp"] == "8.00"
    assert created["fx_rate_to_gbp"] == "0.800000"
    assert created["fx_rate_source"] == "auto_test:2026-02-12"

    payload = DividendService.get_summary(as_of=date(2026, 2, 24))
    entry = payload["entries"][0]
    assert entry["fx_rate_to_gbp"] == "0.800000"
    assert entry["fx_rate_source"] == "auto_test:2026-02-12"


def test_net_dividend_timeline_returns_cumulative_portfolio_and_security_maps(app_context):
    sec_a = _add_security("DIVTL1")
    sec_b = _add_security("DIVTL2")

    DividendService.add_dividend_entry(
        security_id=sec_a.id,
        dividend_date=date(2026, 1, 1),
        amount_gbp=Decimal("30.00"),
        tax_treatment="TAXABLE",
    )
    DividendService.add_dividend_entry(
        security_id=sec_b.id,
        dividend_date=date(2026, 2, 1),
        amount_gbp=Decimal("20.00"),
        tax_treatment="TAXABLE",
    )
    # Excluded by as_of cutoff.
    DividendService.add_dividend_entry(
        security_id=sec_a.id,
        dividend_date=date(2026, 3, 1),
        amount_gbp=Decimal("50.00"),
        tax_treatment="ISA_EXEMPT",
    )

    timeline = DividendService.get_net_dividends_timeline(as_of=date(2026, 2, 15))
    assert timeline["total_net_dividends_gbp"] == "50.00"
    assert timeline["cumulative_net_dividends_by_date"] == {
        "2026-01-01": "30.00",
        "2026-02-01": "50.00",
    }
    assert timeline["net_dividends_by_security_gbp"][sec_a.id] == "30.00"
    assert timeline["net_dividends_by_security_gbp"][sec_b.id] == "20.00"
    assert timeline["cumulative_net_dividends_by_security"][sec_a.id] == {
        "2026-01-01": "30.00"
    }


def test_dividend_summary_and_timeline_use_actual_net_cash_when_withholding_logged(app_context):
    sec = _add_security("DIVWHT")

    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2026, 2, 10),
        amount_original_ccy=Decimal("2.50"),
        original_currency="GBP",
        tax_withheld_original_ccy=Decimal("0.37"),
        tax_treatment="TAXABLE",
        source="manual",
    )

    payload = DividendService.get_summary(as_of=date(2026, 2, 24))
    summary = payload["summary"]
    assert summary["actual_gross_dividends_gbp"] == "2.50"
    assert summary["actual_withholding_tax_gbp"] == "0.37"
    assert summary["actual_net_paid_gbp"] == "2.13"
    assert summary["estimated_tax_gbp"] == "0.00"
    assert summary["estimated_net_dividends_gbp"] == "2.13"

    allocation_row = payload["allocation"]["rows"][0]
    assert allocation_row["cash_base_dividends_gbp"] == "2.13"
    assert allocation_row["allocated_net_dividends_gbp"] == "2.13"

    timeline = DividendService.get_net_dividends_timeline(as_of=date(2026, 2, 24))
    assert timeline["total_net_dividends_gbp"] == "2.13"
    assert timeline["net_dividends_by_security_gbp"][sec.id] == "2.13"


def test_delete_dividend_entry_removes_linked_cash_ledger_rows(app_context, tmp_path):
    prior_db_path = _state.get_db_path()
    ledger_db_path = tmp_path / "dividend-delete.db"
    _state.set_db_path(ledger_db_path)
    try:
        sec = _add_security("DIVDEL")
        created = DividendService.add_dividend_entry(
            security_id=sec.id,
            dividend_date=date(2026, 2, 10),
            amount_gbp=Decimal("12.34"),
            tax_treatment="TAXABLE",
        )

        CashLedgerService.record_entry(
            db_path=ledger_db_path,
            entry_date=date(2026, 2, 10),
            container="BROKER",
            currency="GBP",
            amount=Decimal("12.34"),
            entry_type="DIVIDEND_PAYOUT",
            source="manual",
            metadata={"dividend_entry_id": created["id"]},
        )

        assert CashLedgerService.balances(ledger_db_path)["BROKER"]["GBP"] == Decimal("12.34")

        deleted = DividendService.delete_dividend_entry(created["id"])
        assert deleted is True
        assert CashLedgerService.balances(ledger_db_path)["BROKER"]["GBP"] == Decimal("0.00")
    finally:
        _state.set_db_path(prior_db_path)


def test_ex_dividend_eligibility_uses_transfer_events_without_double_counting_legacy_backfill(app_context):
    sec = PortfolioService.add_security(
        ticker="IBM",
        name="IBM",
        currency="USD",
        exchange="NYSE",
        is_manual_override=True,
    )

    note1 = (
        "Transferred 2.00 shares to BROKERAGE "
        "(FIFO from ESPP source lot source-1 on 2026-02-02)."
    )
    note2 = (
        "Transferred 2.00 shares to BROKERAGE "
        "(FIFO from ESPP source lot source-2 on 2026-02-13)."
    )

    with AppContext.write_session() as sess:
        lot_repo = LotRepository(sess)
        transfer_repo = LotTransferEventRepository(sess)

        source_1 = Lot(
            id="source-1",
            security_id=sec.id,
            grant_id=None,
            scheme_type="ESPP",
            tax_year="2025-26",
            acquisition_date=date(2026, 1, 6),
            quantity="2.07",
            quantity_remaining="0.07",
            acquisition_price_gbp="100.00",
            true_cost_per_share_gbp="100.00",
            fmv_at_acquisition_gbp=None,
            acquisition_price_original_ccy="100.00",
            original_currency="USD",
            broker_currency="USD",
            fx_rate_at_acquisition="0.80",
            fx_rate_source="test",
            broker_reference=None,
            import_source=None,
            external_id=None,
            notes=note1,
        )
        broker_1 = Lot(
            id="broker-1",
            security_id=sec.id,
            grant_id=None,
            scheme_type="BROKERAGE",
            tax_year="2025-26",
            acquisition_date=date(2026, 1, 6),
            quantity="2.00",
            quantity_remaining="2.00",
            acquisition_price_gbp="100.00",
            true_cost_per_share_gbp="100.00",
            fmv_at_acquisition_gbp=None,
            acquisition_price_original_ccy="100.00",
            original_currency="USD",
            broker_currency="USD",
            fx_rate_at_acquisition="0.80",
            fx_rate_source="test",
            broker_reference=None,
            import_source="ui_transfer_to_brokerage",
            external_id="transfer-origin-lot:source-1",
            notes=note1,
        )
        source_2 = Lot(
            id="source-2",
            security_id=sec.id,
            grant_id=None,
            scheme_type="ESPP",
            tax_year="2025-26",
            acquisition_date=date(2026, 2, 6),
            quantity="2.16",
            quantity_remaining="0.16",
            acquisition_price_gbp="100.00",
            true_cost_per_share_gbp="100.00",
            fmv_at_acquisition_gbp=None,
            acquisition_price_original_ccy="100.00",
            original_currency="USD",
            broker_currency="USD",
            fx_rate_at_acquisition="0.80",
            fx_rate_source="test",
            broker_reference=None,
            import_source=None,
            external_id=None,
            notes=note2,
        )
        broker_2 = Lot(
            id="broker-2",
            security_id=sec.id,
            grant_id=None,
            scheme_type="BROKERAGE",
            tax_year="2025-26",
            acquisition_date=date(2026, 2, 6),
            quantity="2.00",
            quantity_remaining="2.00",
            acquisition_price_gbp="100.00",
            true_cost_per_share_gbp="100.00",
            fmv_at_acquisition_gbp=None,
            acquisition_price_original_ccy="100.00",
            original_currency="USD",
            broker_currency="USD",
            fx_rate_at_acquisition="0.80",
            fx_rate_source="test",
            broker_reference=None,
            import_source="ui_transfer_to_brokerage",
            external_id="transfer-origin-lot:source-2",
            notes=note2,
        )
        espp_plus = Lot(
            id="espp-plus",
            security_id=sec.id,
            grant_id=None,
            scheme_type="ESPP_PLUS",
            tax_year="2025-26",
            acquisition_date=date(2026, 1, 6),
            quantity="1.57",
            quantity_remaining="1.57",
            acquisition_price_gbp="100.00",
            true_cost_per_share_gbp="100.00",
            fmv_at_acquisition_gbp=None,
            acquisition_price_original_ccy="100.00",
            original_currency="USD",
            broker_currency="USD",
            fx_rate_at_acquisition="0.80",
            fx_rate_source="test",
            broker_reference=None,
            import_source=None,
            external_id=None,
            notes=None,
        )
        for row in (source_1, broker_1, source_2, broker_2, espp_plus):
            lot_repo.add(row)

        transfer_repo.add(
            security_id=sec.id,
            source_lot_id="source-1",
            destination_lot_id="broker-1",
            source_scheme="ESPP",
            destination_scheme="BROKERAGE",
            transfer_date=date(2026, 2, 2),
            quantity=Decimal("2.00"),
            source="test",
            external_id="event-1",
            notes=note1,
        )
        transfer_repo.add(
            security_id=sec.id,
            source_lot_id="source-2",
            destination_lot_id="broker-2",
            source_scheme="ESPP",
            destination_scheme="BROKERAGE",
            transfer_date=date(2026, 2, 13),
            quantity=Decimal("2.00"),
            source="test",
            external_id="event-2",
            notes=note2,
        )

    quantities = _eligible_quantities_by_holding_bucket_on_ex_date(
        security_id=sec.id,
        ex_dividend_date=date(2026, 2, 10),
    )

    assert quantities == {
        "BROKERAGE": Decimal("2.00"),
        "ESPP": Decimal("2.23"),
        "ESPP_PLUS": Decimal("1.57"),
    }
