from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.core.tax_engine import tax_year_for_date
from src.db.repository.prices import PriceRepository
from src.services.capital_stack_service import CapitalStackService
from src.services.portfolio_service import PortfolioService
from src.services.strategic_service import StrategicService
from src.services.tax_plan_service import TaxPlanService
from src.settings import AppSettings

_MONEY_TOLERANCE = Decimal("0.01")


def _as_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _assert_money_close(actual: Decimal, expected: Decimal, tolerance: Decimal = _MONEY_TOLERANCE) -> None:
    assert abs(actual - expected) <= tolerance, f"{actual} != {expected} within {tolerance}"


def _db_path():
    db_path = _state.get_db_path()
    assert db_path is not None
    return db_path


def _load_settings() -> AppSettings:
    return AppSettings.load(_db_path())


def _save_settings(
    *,
    gross_income: str = "100000",
    other_income: str = "0",
    pension_sacrifice: str = "0",
) -> AppSettings:
    settings = _load_settings()
    settings.default_tax_year = tax_year_for_date(date.today())
    settings.default_gross_income = Decimal(gross_income)
    settings.default_other_income = Decimal(other_income)
    settings.default_pension_sacrifice = Decimal(pension_sacrifice)
    settings.default_student_loan_plan = None
    settings.hide_values = False
    settings.save()
    return settings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Delta Fixture Plc",
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
    quantity: str,
    acquisition_price_gbp: str,
    acquisition_date: str | None = None,
    broker_currency: str | None = None,
) -> dict:
    payload = {
        "security_id": security_id,
        "scheme_type": "BROKERAGE",
        "acquisition_date": acquisition_date or (date.today() - timedelta(days=120)).isoformat(),
        "quantity": quantity,
        "acquisition_price_gbp": acquisition_price_gbp,
        "true_cost_per_share_gbp": acquisition_price_gbp,
        "tax_year": tax_year_for_date(date.today()),
    }
    if broker_currency is not None:
        payload["broker_currency"] = broker_currency

    resp = client.post("/portfolio/lots", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upsert_price(
    security_id: str,
    *,
    close_price_original_ccy: str,
    close_price_gbp: str,
    currency: str = "GBP",
) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=close_price_original_ccy,
            close_price_gbp=close_price_gbp,
            currency=currency,
            source="test-reconcile-delta-tolerances",
        )


def _capture_snapshot() -> dict[str, Decimal]:
    db_path = _db_path()
    settings = _load_settings()
    portfolio = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    stack = CapitalStackService.get_snapshot(
        settings=settings,
        db_path=db_path,
        summary=portfolio,
    )
    tax_plan = TaxPlanService.get_summary(settings=settings)
    reconcile = StrategicService.get_cross_page_reconcile(
        settings=settings,
        db_path=db_path,
    )
    steps = {
        row["step"]: _as_decimal(row["amount_gbp"])
        for row in reconcile["components"]
    }
    cross_year = tax_plan["summary"]["cross_year_comparison"]

    return {
        "portfolio_gross": _as_decimal(portfolio.total_market_value_gbp),
        "portfolio_net_value": _as_decimal(portfolio.est_total_net_liquidation_gbp),
        "portfolio_employment_tax": _as_decimal(portfolio.est_total_employment_tax_gbp),
        "stack_gross": _as_decimal(stack["gross_market_value_gbp"]),
        "stack_locked": _as_decimal(stack["locked_capital_gbp"]),
        "stack_forfeitable": _as_decimal(stack["forfeitable_capital_gbp"]),
        "stack_taxable_gain": _as_decimal(stack["taxable_liquid_gain_gbp"]),
        "stack_cgt": _as_decimal(stack["estimated_cgt_gbp"]),
        "stack_cgt_rate": _as_decimal(stack["cgt_marginal_rate"]),
        "stack_fees": _as_decimal(stack["estimated_fees_gbp"]),
        "stack_deployable_cash": _as_decimal(stack["gbp_deployable_cash_gbp"]),
        "stack_net": _as_decimal(stack["net_deployable_today_gbp"]),
        "tax_plan_projected_gain": _as_decimal(
            cross_year["additional_realisation_scope"]["projected_net_gain_gbp"]
        ),
        "tax_plan_incremental_cgt": _as_decimal(
            cross_year["sell_before_tax_year_end"]["projected_incremental_cgt_gbp"]
        ),
        "reconcile_gross": steps["Portfolio Gross Market Value"],
        "reconcile_net_value": steps["Equals Net Value (Sell-All Surface)"],
        "reconcile_cgt_and_fees": steps["Less Estimated CGT + Fees"],
        "reconcile_deployable": _as_decimal(
            reconcile["reconciled_deployable_capital_gbp"]
        ),
    }


def _assert_simple_fixture(snapshot: dict[str, Decimal]) -> None:
    assert snapshot["portfolio_employment_tax"] == Decimal("0.00")
    assert snapshot["stack_locked"] == Decimal("0.00")
    assert snapshot["stack_forfeitable"] == Decimal("0.00")
    assert snapshot["stack_fees"] == Decimal("0.00")
    assert snapshot["stack_deployable_cash"] == Decimal("0.00")


def test_price_change_fixture_reconciles_deltas_across_surfaces(client):
    _save_settings(gross_income="100000")
    security_id = _add_security(client, ticker="T88PRICE")
    _add_lot(client, security_id=security_id, quantity="1000", acquisition_price_gbp="10.00")
    _upsert_price(
        security_id,
        close_price_original_ccy="25.00",
        close_price_gbp="25.00",
    )

    before = _capture_snapshot()
    _assert_simple_fixture(before)

    _upsert_price(
        security_id,
        close_price_original_ccy="30.00",
        close_price_gbp="30.00",
    )

    after = _capture_snapshot()
    expected_market_delta = Decimal("5000.00")

    _assert_money_close(after["portfolio_gross"] - before["portfolio_gross"], expected_market_delta)
    _assert_money_close(after["portfolio_net_value"] - before["portfolio_net_value"], expected_market_delta)
    _assert_money_close(after["stack_gross"] - before["stack_gross"], expected_market_delta)
    _assert_money_close(after["stack_taxable_gain"] - before["stack_taxable_gain"], expected_market_delta)
    _assert_money_close(after["tax_plan_projected_gain"] - before["tax_plan_projected_gain"], expected_market_delta)
    _assert_money_close(after["reconcile_gross"] - before["reconcile_gross"], expected_market_delta)
    _assert_money_close(after["reconcile_net_value"] - before["reconcile_net_value"], expected_market_delta)
    _assert_money_close(
        after["stack_cgt"] - before["stack_cgt"],
        after["tax_plan_incremental_cgt"] - before["tax_plan_incremental_cgt"],
    )
    _assert_money_close(
        after["reconcile_cgt_and_fees"] - before["reconcile_cgt_and_fees"],
        -(after["stack_cgt"] - before["stack_cgt"]),
    )
    _assert_money_close(after["reconcile_deployable"] - before["reconcile_deployable"], after["stack_net"] - before["stack_net"])
    assert after["stack_cgt_rate"] == before["stack_cgt_rate"]


def test_fx_change_fixture_reconciles_deltas_across_surfaces(client):
    _save_settings(gross_income="100000")
    security_id = _add_security(client, ticker="T88FX", currency="USD")
    _add_lot(
        client,
        security_id=security_id,
        quantity="1000",
        acquisition_price_gbp="50.00",
        broker_currency="USD",
    )
    _upsert_price(
        security_id,
        close_price_original_ccy="100.00",
        close_price_gbp="80.00",
        currency="USD",
    )

    before = _capture_snapshot()
    _assert_simple_fixture(before)

    _upsert_price(
        security_id,
        close_price_original_ccy="100.00",
        close_price_gbp="90.00",
        currency="USD",
    )

    after = _capture_snapshot()
    expected_market_delta = Decimal("10000.00")

    _assert_money_close(after["portfolio_gross"] - before["portfolio_gross"], expected_market_delta)
    _assert_money_close(after["portfolio_net_value"] - before["portfolio_net_value"], expected_market_delta)
    _assert_money_close(after["stack_gross"] - before["stack_gross"], expected_market_delta)
    _assert_money_close(after["stack_taxable_gain"] - before["stack_taxable_gain"], expected_market_delta)
    _assert_money_close(after["tax_plan_projected_gain"] - before["tax_plan_projected_gain"], expected_market_delta)
    _assert_money_close(after["reconcile_gross"] - before["reconcile_gross"], expected_market_delta)
    _assert_money_close(after["reconcile_net_value"] - before["reconcile_net_value"], expected_market_delta)
    _assert_money_close(
        after["stack_cgt"] - before["stack_cgt"],
        after["tax_plan_incremental_cgt"] - before["tax_plan_incremental_cgt"],
    )
    _assert_money_close(
        after["reconcile_cgt_and_fees"] - before["reconcile_cgt_and_fees"],
        -(after["stack_cgt"] - before["stack_cgt"]),
    )
    _assert_money_close(after["reconcile_deployable"] - before["reconcile_deployable"], after["stack_net"] - before["stack_net"])
    assert after["stack_cgt_rate"] == before["stack_cgt_rate"]


def test_quantity_change_fixture_reconciles_deltas_across_surfaces(client):
    _save_settings(gross_income="100000")
    security_id = _add_security(client, ticker="T88QTY")
    _add_lot(client, security_id=security_id, quantity="1000", acquisition_price_gbp="10.00")
    _upsert_price(
        security_id,
        close_price_original_ccy="20.00",
        close_price_gbp="20.00",
    )

    before = _capture_snapshot()
    _assert_simple_fixture(before)

    _add_lot(
        client,
        security_id=security_id,
        quantity="400",
        acquisition_price_gbp="10.00",
        acquisition_date=(date.today() - timedelta(days=90)).isoformat(),
    )

    after = _capture_snapshot()
    expected_market_delta = Decimal("8000.00")
    expected_gain_delta = Decimal("4000.00")

    _assert_money_close(after["portfolio_gross"] - before["portfolio_gross"], expected_market_delta)
    _assert_money_close(after["portfolio_net_value"] - before["portfolio_net_value"], expected_market_delta)
    _assert_money_close(after["stack_gross"] - before["stack_gross"], expected_market_delta)
    _assert_money_close(after["stack_taxable_gain"] - before["stack_taxable_gain"], expected_gain_delta)
    _assert_money_close(after["tax_plan_projected_gain"] - before["tax_plan_projected_gain"], expected_gain_delta)
    _assert_money_close(after["reconcile_gross"] - before["reconcile_gross"], expected_market_delta)
    _assert_money_close(after["reconcile_net_value"] - before["reconcile_net_value"], expected_market_delta)
    _assert_money_close(
        after["stack_cgt"] - before["stack_cgt"],
        after["tax_plan_incremental_cgt"] - before["tax_plan_incremental_cgt"],
    )
    _assert_money_close(
        after["reconcile_cgt_and_fees"] - before["reconcile_cgt_and_fees"],
        -(after["stack_cgt"] - before["stack_cgt"]),
    )
    _assert_money_close(after["reconcile_deployable"] - before["reconcile_deployable"], after["stack_net"] - before["stack_net"])
    assert after["stack_cgt_rate"] == before["stack_cgt_rate"]


def test_settings_change_fixture_limits_deltas_to_settings_sensitive_surfaces(client):
    _save_settings(gross_income="10000")
    security_id = _add_security(client, ticker="T88SET")
    _add_lot(client, security_id=security_id, quantity="1000", acquisition_price_gbp="10.00")
    _upsert_price(
        security_id,
        close_price_original_ccy="30.00",
        close_price_gbp="30.00",
    )

    before = _capture_snapshot()
    _assert_simple_fixture(before)

    _save_settings(gross_income="100000")
    after = _capture_snapshot()

    _assert_money_close(after["portfolio_gross"] - before["portfolio_gross"], Decimal("0.00"))
    _assert_money_close(after["portfolio_net_value"] - before["portfolio_net_value"], Decimal("0.00"))
    _assert_money_close(after["stack_gross"] - before["stack_gross"], Decimal("0.00"))
    _assert_money_close(after["stack_taxable_gain"] - before["stack_taxable_gain"], Decimal("0.00"))
    _assert_money_close(after["tax_plan_projected_gain"] - before["tax_plan_projected_gain"], Decimal("0.00"))
    _assert_money_close(after["reconcile_gross"] - before["reconcile_gross"], Decimal("0.00"))
    _assert_money_close(after["reconcile_net_value"] - before["reconcile_net_value"], Decimal("0.00"))
    assert after["stack_cgt_rate"] > before["stack_cgt_rate"]
    assert after["stack_cgt"] > before["stack_cgt"]
    assert after["tax_plan_incremental_cgt"] > before["tax_plan_incremental_cgt"]
    _assert_money_close(
        after["reconcile_cgt_and_fees"] - before["reconcile_cgt_and_fees"],
        -(after["stack_cgt"] - before["stack_cgt"]),
    )
    _assert_money_close(after["reconcile_deployable"] - before["reconcile_deployable"], after["stack_net"] - before["stack_net"])
