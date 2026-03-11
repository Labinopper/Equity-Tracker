from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.services.capital_stack_service import CapitalStackService
from src.services.cash_ledger_service import CashLedgerService
from src.services.dividend_service import DividendService
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings
from src.db.repository.prices import PriceRepository


def _add_security(client, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Capital Stack PLC",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(
    client,
    *,
    security_id: str,
    scheme_type: str,
    acquisition_date: str,
    quantity: str,
    price: str,
) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": acquisition_date,
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
        },
    )
    assert resp.status_code == 201, resp.text


def _set_price(security_id: str, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-capital-stack-ui",
        )


def test_capital_stack_page_renders(client):
    resp = client.get("/capital-stack")
    assert resp.status_code == 200
    assert "Capital Stack" in resp.text
    assert "Stack Formula" in resp.text
    assert "Net Deployable Today" in resp.text


def test_capital_stack_snapshot_reconciles_formula(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("60000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.save()

    sellable_id = _add_security(client, "CSTACKA")
    locked_id = _add_security(client, "CSTACKB")
    _add_lot(
        client,
        security_id=sellable_id,
        scheme_type="BROKERAGE",
        acquisition_date=(date.today() - timedelta(days=120)).isoformat(),
        quantity="10",
        price="10.00",
    )
    _add_lot(
        client,
        security_id=locked_id,
        scheme_type="RSU",
        acquisition_date=(date.today() + timedelta(days=30)).isoformat(),
        quantity="5",
        price="12.00",
    )
    _set_price(sellable_id, "20.00")
    _set_price(locked_id, "30.00")

    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=_state.get_db_path(),
        summary=summary,
    )

    gross = Decimal(str(stack["gross_market_value_gbp"]))
    locked = Decimal(str(stack["locked_capital_gbp"]))
    forfeitable = Decimal(str(stack["forfeitable_capital_gbp"]))
    hypo = Decimal(str(stack["hypothetical_liquid_gbp"]))
    assert hypo == (gross - locked - forfeitable).quantize(Decimal("0.01"))

    assert stack["net_deployable_today_gbp"] is not None
    net = Decimal(str(stack["net_deployable_today_gbp"]))
    emp = Decimal(str(stack["estimated_employment_tax_gbp"]))
    cgt = Decimal(str(stack["estimated_cgt_gbp"]))
    fees = Decimal(str(stack["estimated_fees_gbp"]))
    assert net == (hypo - emp - cgt - fees).quantize(Decimal("0.01"))


def test_dividend_adjusted_capital_at_risk_uses_separate_metric(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("0")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = None
    settings.save()

    sec_id = _add_security(client, "CSTACKDIV")
    _add_lot(
        client,
        security_id=sec_id,
        scheme_type="BROKERAGE",
        acquisition_date=(date.today() - timedelta(days=30)).isoformat(),
        quantity="10",
        price="100.00",
    )

    DividendService.add_dividend_entry(
        security_id=sec_id,
        dividend_date=date.today(),
        amount_gbp=Decimal("100.00"),
        tax_treatment="TAXABLE",
        source="test",
    )

    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=_state.get_db_path(),
        summary=summary,
    )

    assert Decimal(str(stack["true_cost_acquisition_gbp"])) == Decimal("1000.00")
    assert Decimal(str(stack["estimated_net_dividends_gbp"])) == Decimal("100.00")
    assert Decimal(str(stack["dividend_adjusted_capital_at_risk_gbp"])) == Decimal("900.00")

    home = client.get("/")
    assert home.status_code == 200
    assert "Dividend-Adjusted Capital at Risk" in home.text


def test_capital_stack_combined_deployable_includes_gbp_cash(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("60000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.save()

    sec_id = _add_security(client, "CSTACKCASH")
    _add_lot(
        client,
        security_id=sec_id,
        scheme_type="BROKERAGE",
        acquisition_date=(date.today() - timedelta(days=90)).isoformat(),
        quantity="10",
        price="10.00",
    )
    _set_price(sec_id, "20.00")

    CashLedgerService.record_entry(
        db_path=_state.get_db_path(),
        entry_date=date.today(),
        container="BROKER",
        currency="GBP",
        amount=Decimal("75.00"),
        source="test-capital-stack-ui",
    )

    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=_state.get_db_path(),
        summary=summary,
    )

    assert stack["net_deployable_today_gbp"] is not None
    expected_combined = (
        Decimal(str(stack["net_deployable_today_gbp"]))
        + Decimal(str(stack["gbp_deployable_cash_gbp"]))
    ).quantize(Decimal("0.01"))
    assert Decimal(str(stack["combined_deployable_with_cash_gbp"])) == expected_combined

    page = client.get("/capital-stack")
    assert page.status_code == 200
    assert "Combined Deployable (Holdings + Cash)" in page.text


def test_capital_stack_uses_tax_year_cgt_projection_and_order_fee_estimate(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_tax_year = "2025-26"
    settings.default_gross_income = Decimal("65000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("10000")
    settings.default_student_loan_plan = None
    settings.save()

    sec_id = _add_security(client, "CSTACKUSD", currency="USD")
    _add_lot(
        client,
        security_id=sec_id,
        scheme_type="BROKERAGE",
        acquisition_date="2026-01-10",
        quantity="10",
        price="10.00",
    )

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date.today(),
            close_price_original_ccy="20.00",
            close_price_gbp="15.00",
            currency="USD",
            source="test-capital-stack-ui-usd",
        )

    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=_state.get_db_path(),
        summary=summary,
    )

    assert Decimal(str(stack["estimated_cgt_gbp"])) == Decimal("0.00")
    assert Decimal(str(stack["estimated_fees_gbp"])) == Decimal("0.75")
    assert Decimal(str(stack["taxable_liquid_gain_gbp"])) == Decimal("0.00")
    assert stack["fee_model"]["method"] == "ibkr_uk_us_stock_fixed"
