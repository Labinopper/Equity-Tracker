from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _assert_no_floats(value):
    if isinstance(value, float):
        raise AssertionError(f"Unexpected float value: {value}")
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_floats(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_floats(item)


def _db_path():
    db_path = _state.get_db_path()
    assert db_path is not None
    return db_path


def _save_settings(
    *,
    gross_income: str = "100000",
    employer_ticker: str = "",
) -> None:
    settings = AppSettings.load(_db_path())
    settings.default_gross_income = Decimal(gross_income)
    settings.default_other_income = Decimal("0")
    settings.default_pension_sacrifice = Decimal("0")
    settings.employer_ticker = employer_ticker
    settings.concentration_top_holding_alert_pct = Decimal("100")
    settings.concentration_employer_alert_pct = Decimal("100")
    settings.save()


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Decision Brief Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, *, security_id: str, quantity: str = "10", broker_currency: str | None = None) -> None:
    payload = {
        "security_id": security_id,
        "scheme_type": "BROKERAGE",
        "acquisition_date": (date.today() - timedelta(days=120)).isoformat(),
        "quantity": quantity,
        "acquisition_price_gbp": "10.00",
        "true_cost_per_share_gbp": "10.00",
        "tax_year": "2025-26",
    }
    if broker_currency is not None:
        payload["broker_currency"] = broker_currency
    resp = client.post("/portfolio/lots", json=payload)
    assert resp.status_code == 201, resp.text


def _upsert_price(
    security_id: str,
    *,
    price_date: date,
    close_price_original_ccy: str,
    close_price_gbp: str,
    currency: str = "GBP",
) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_price_original_ccy,
            close_price_gbp=close_price_gbp,
            currency=currency,
            source="test-decision-brief-export",
        )


def test_decision_brief_export_returns_major_surface_pack_and_no_floats(client):
    _save_settings(gross_income="120000")
    security_id = _add_security(client, ticker="T80PACK")
    _add_lot(client, security_id=security_id, quantity="10")
    _upsert_price(
        security_id,
        price_date=date.today(),
        close_price_original_ccy="20.00",
        close_price_gbp="20.00",
    )

    resp = client.get("/reports/decision-brief-export")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"metadata", "assumptions", "surfaces", "notes"}
    assert body["metadata"]["export_type"] == "decision_brief_v1"
    assert body["metadata"]["selected_surfaces"] == [
        "portfolio",
        "net_value",
        "capital_stack",
        "tax_plan",
        "risk",
    ]
    assert body["surfaces"]["portfolio"]["metrics"]["gross_market_value_gbp"] == "200.00"
    assert body["surfaces"]["net_value"]["trace_links"]["surface"].startswith("/net-value?as_of=")
    assert body["surfaces"]["capital_stack"]["trace_links"]["reconcile"].startswith("/reconcile?as_of=")
    assert body["surfaces"]["tax_plan"]["assumption_quality"]["input_freshness"]["stale_price_security_count"] == 0
    assert body["surfaces"]["risk"]["metrics"]["deployable_capital_gbp"] == "200.00"
    assert body["assumptions"]["surface_model_scope"]["risk"]["inputs"]
    _assert_no_floats(body)


def test_decision_brief_export_respects_surface_selection_and_as_of(client):
    _save_settings(gross_income="90000")
    security_id = _add_security(client, ticker="T80ASOF")
    _add_lot(client, security_id=security_id, quantity="10")

    prior_date = date.today() - timedelta(days=1)
    _upsert_price(
        security_id,
        price_date=prior_date,
        close_price_original_ccy="12.00",
        close_price_gbp="12.00",
    )
    _upsert_price(
        security_id,
        price_date=date.today(),
        close_price_original_ccy="18.00",
        close_price_gbp="18.00",
    )

    resp = client.get(
        f"/reports/decision-brief-export?surfaces=portfolio,risk&as_of={prior_date.isoformat()}"
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["metadata"]["selected_surfaces"] == ["portfolio", "risk"]
    assert set(body["surfaces"].keys()) == {"portfolio", "risk"}
    assert body["metadata"]["as_of_date"] == prior_date.isoformat()
    assert body["surfaces"]["portfolio"]["metrics"]["gross_market_value_gbp"] == "120.00"
    assert body["surfaces"]["risk"]["metrics"]["total_market_value_gbp"] == "120.00"


def test_decision_brief_buttons_render_on_major_decision_pages(client):
    for path in ("/", "/net-value", "/capital-stack", "/tax-plan", "/risk"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "Download brief JSON" in resp.text
        assert "data-decision-brief-download" in resp.text

    tax_plan = client.get("/tax-plan?bonus_gbp=5000&sell_amount_gbp=3000")
    assert tax_plan.status_code == 200
    assert 'data-include-current-query="true"' in tax_plan.text
