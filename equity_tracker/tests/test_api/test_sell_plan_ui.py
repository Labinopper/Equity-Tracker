from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from src.api import _state
from src.services.sell_plan_service import SellPlanService


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Sell Plan PLC",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_brokerage_lot(client, security_id: str, quantity: str = "20") -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2024-01-02",
            "quantity": quantity,
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert resp.status_code == 201, resp.text


def test_sell_plan_page_renders(client):
    resp = client.get("/sell-plan")
    assert resp.status_code == 200
    assert "Sell Plan" in resp.text
    assert "Create Plan" in resp.text


def test_sell_plan_create_persists_and_links_to_calendar(client):
    sec_id = _add_security(client, "SPLAN")
    _add_brokerage_lot(client, sec_id, quantity="20")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "12",
            "tranche_count": "3",
            "start_date": start_date,
            "cadence_days": "14",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    location = create.headers["location"]
    parsed = urlparse(location)
    plan_id = parse_qs(parsed.query)["plan_id"][0]

    plans = SellPlanService.list_plans(_state.get_db_path())
    assert any(plan["plan_id"] == plan_id for plan in plans)

    calendar = client.get(f"/calendar?days=120&sell_plan_id={plan_id}")
    assert calendar.status_code == 200
    assert "Sell tranche" in calendar.text
    assert f"/sell-plan?plan_id={plan_id}" in calendar.text


def test_sell_plan_rejects_quantity_above_sellable(client):
    sec_id = _add_security(client, "SPLNERR")
    _add_brokerage_lot(client, sec_id, quantity="5")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "8",
            "tranche_count": "2",
            "start_date": start_date,
            "cadence_days": "7",
        },
    )
    assert create.status_code == 422
    assert "exceeds sellable quantity" in create.text


def test_sell_plan_tranche_status_filter_executed(client):
    sec_id = _add_security(client, "SPLNEXE")
    _add_brokerage_lot(client, sec_id, quantity="10")

    start_date = date.today().isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "6",
            "tranche_count": "2",
            "start_date": start_date,
            "cadence_days": "7",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    plans = SellPlanService.list_plans(_state.get_db_path())
    target = next(plan for plan in plans if plan["plan_id"] == plan_id)
    tranche_id = target["tranches"][0]["tranche_id"]

    update = client.post(
        "/sell-plan/tranche-status",
        data={
            "plan_id": plan_id,
            "tranche_id": tranche_id,
            "status": "EXECUTED",
        },
        follow_redirects=False,
    )
    assert update.status_code == 303

    calendar = client.get(f"/calendar?days=120&sell_plan_id={plan_id}&sell_status=EXECUTED")
    assert calendar.status_code == 200
    assert "Executed" in calendar.text
    assert "Sell tranche" in calendar.text


def test_sell_plan_rejects_min_spacing_constraint_breach(client):
    sec_id = _add_security(client, "SPLNSPACE")
    _add_brokerage_lot(client, sec_id, quantity="20")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "12",
            "tranche_count": "3",
            "start_date": start_date,
            "cadence_days": "7",
            "min_spacing_days": "10",
        },
    )
    assert create.status_code == 422
    assert "Constraint breach" in create.text
    assert "Minimum spacing breach" in create.text


def test_sell_plan_rejects_daily_quantity_cap_breach(client):
    sec_id = _add_security(client, "SPLNQCAP")
    _add_brokerage_lot(client, sec_id, quantity="20")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "12",
            "tranche_count": "3",
            "start_date": start_date,
            "cadence_days": "14",
            "min_spacing_days": "1",
            "max_daily_quantity": "3.5",
        },
    )
    assert create.status_code == 422
    assert "Constraint breach" in create.text
    assert "Daily quantity cap breach" in create.text


def test_sell_plan_rejects_daily_notional_cap_breach(client):
    sec_id = _add_security(client, "SPLNNCAP")
    _add_brokerage_lot(client, sec_id, quantity="20")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "12",
            "tranche_count": "3",
            "start_date": start_date,
            "cadence_days": "14",
            "min_spacing_days": "1",
            "max_daily_notional_gbp": "39.99",
            "reference_price_gbp": "10",
        },
    )
    assert create.status_code == 422
    assert "Constraint breach" in create.text
    assert "Daily notional cap breach" in create.text


def test_sell_plan_shows_impact_preview_when_reference_price_set(client):
    sec_id = _add_security(client, "SPLNIMPACT")
    _add_brokerage_lot(client, sec_id, quantity="10")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "6",
            "tranche_count": "2",
            "start_date": start_date,
            "cadence_days": "14",
            "min_spacing_days": "1",
            "reference_price_gbp": "10",
            "fee_per_tranche_gbp": "1.00",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    page = client.get(f"/sell-plan?plan_id={plan_id}")
    assert page.status_code == 200
    assert "Impact totals (planned model)" in page.text
    assert "Gross (GBP)" in page.text
    assert "&pound;60.00" in page.text
