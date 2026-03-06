from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.cash_ledger_service import (
    CONTAINER_BANK,
    CONTAINER_BROKER,
    CashLedgerService,
)
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def _set_price(security_id: str, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-stage6-ui",
        )


def test_portfolio_stage6_shows_split_buckets_and_concentration(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.employer_ticker = "IBM"
    settings.employer_income_dependency_pct = Decimal("25.00")
    settings.save()

    ibm = PortfolioService.add_security(
        ticker="IBM",
        name="IBM Corp",
        currency="GBP",
        is_manual_override=True,
    )
    other = PortfolioService.add_security(
        ticker="OTHER",
        name="Other Corp",
        currency="GBP",
        is_manual_override=True,
    )

    PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="BROKERAGE",
        acquisition_date=date.today() - timedelta(days=120),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    employee = PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("4"),
        acquisition_price_gbp=Decimal("8.00"),
        true_cost_per_share_gbp=Decimal("8.00"),
        fmv_at_acquisition_gbp=Decimal("8.00"),
    )
    PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("1"),
        acquisition_price_gbp=Decimal("0.00"),
        true_cost_per_share_gbp=Decimal("0.00"),
        fmv_at_acquisition_gbp=Decimal("8.00"),
        matching_lot_id=employee.id,
        forfeiture_period_end=date.today() + timedelta(days=15),
    )
    PortfolioService.add_lot(
        security_id=other.id,
        scheme_type="RSU",
        acquisition_date=date.today() + timedelta(days=20),
        quantity=Decimal("5"),
        acquisition_price_gbp=Decimal("20.00"),
        true_cost_per_share_gbp=Decimal("20.00"),
    )

    _set_price(ibm.id, "30.00")
    _set_price(other.id, "40.00")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Locked Capital" in resp.text
    assert "Forfeitable Capital" in resp.text
    assert "&pound;200.00" in resp.text
    assert "&pound;30.00" in resp.text
    assert "Top Holding Concentration" in resp.text
    assert "69.23%" in resp.text
    assert "Employer Exposure" in resp.text
    assert "100.00%" in resp.text


def test_portfolio_deployable_capital_includes_gbp_cash(client, db_engine):
    _, db_path = db_engine

    sec = PortfolioService.add_security(
        ticker="CASHDEP",
        name="Cash Deploy PLC",
        currency="GBP",
        is_manual_override=True,
    )
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date.today() - timedelta(days=90),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    _set_price(sec.id, "20.00")

    CashLedgerService.record_entry(
        db_path=db_path,
        entry_date=date.today(),
        container=CONTAINER_BROKER,
        currency="GBP",
        amount=Decimal("50.00"),
        notes="stage6 deployable broker cash",
    )
    CashLedgerService.record_entry(
        db_path=db_path,
        entry_date=date.today(),
        container=CONTAINER_BANK,
        currency="GBP",
        amount=Decimal("25.00"),
        notes="stage6 deployable bank cash",
    )

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Deployable Capital (Holdings + GBP Cash)" in resp.text
    assert "&pound;275.00" in resp.text


def test_risk_api_stage6_employer_dependence_breakdown(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("100000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.employer_income_dependency_pct = Decimal("50.00")
    settings.employer_ticker = "IBM"
    settings.save()

    ibm = PortfolioService.add_security(
        ticker="IBM",
        name="IBM Corp",
        currency="GBP",
        is_manual_override=True,
    )
    other = PortfolioService.add_security(
        ticker="NONIBM",
        name="Non IBM Corp",
        currency="GBP",
        is_manual_override=True,
    )
    PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="BROKERAGE",
        acquisition_date=date.today() - timedelta(days=60),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("20.00"),
        true_cost_per_share_gbp=Decimal("20.00"),
    )
    PortfolioService.add_lot(
        security_id=other.id,
        scheme_type="BROKERAGE",
        acquisition_date=date.today() - timedelta(days=60),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("20.00"),
        true_cost_per_share_gbp=Decimal("20.00"),
    )
    _set_price(ibm.id, "30.00")
    _set_price(other.id, "20.00")

    CashLedgerService.record_entry(
        db_path=db_path,
        entry_date=date.today(),
        container=CONTAINER_BROKER,
        currency="GBP",
        amount=Decimal("100.00"),
        notes="stage6 risk deployable cash",
    )

    resp = client.get("/api/risk/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["top_holding_pct"] == "60.00"
    assert body["top_holding_sellable_pct"] == "60.00"
    assert body["deployable"]["deployable_capital_gbp"] == "600.00"
    assert body["deployable"]["employer_share_of_deployable_pct"] == "50.00"
    assert body["employer_dependence"]["employer_ticker"] == "IBM"
    assert body["employer_dependence"]["employer_equity_gbp"] == "300.00"
    assert body["employer_dependence"]["income_dependency_proxy_gbp"] == "50000.00"
    assert body["employer_dependence"]["denominator_gbp"] == "50600.00"
    assert body["employer_dependence"]["ratio_pct"] == "99.41"
