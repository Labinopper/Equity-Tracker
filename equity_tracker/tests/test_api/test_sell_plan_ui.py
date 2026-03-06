from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from src.api import _state
from src.services.portfolio_service import PortfolioService
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


def _add_espp_plus_pair(
    *,
    security_id: str,
    acquisition_date: date,
    employee_qty: str,
    matched_qty: str,
    forfeiture_period_end: date | None = None,
) -> None:
    PortfolioService.add_espp_plus_lot_pair(
        security_id=security_id,
        acquisition_date=acquisition_date,
        employee_quantity=Decimal(employee_qty),
        employee_acquisition_price_gbp=Decimal("10.00"),
        employee_true_cost_per_share_gbp=Decimal("10.00"),
        employee_fmv_at_acquisition_gbp=Decimal("10.00"),
        matched_quantity=Decimal(matched_qty),
        forfeiture_period_end=forfeiture_period_end,
    )


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
    assert "whole-share sellable quantity" in create.text


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
            "max_daily_quantity": "3",
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
    assert "Planned vs Committed Reconciliation" in page.text
    assert "Variance (Committed - Planned)" in page.text
    assert "Gross (GBP)" in page.text
    assert "&pound;60.00" in page.text


def test_sell_plan_defaults_total_quantity_to_whole_sellable_max(client):
    sec_id = _add_security(client, "SPLNDEF")
    _add_brokerage_lot(client, sec_id, quantity="20")

    page = client.get("/sell-plan")
    assert page.status_code == 200
    assert 'id="total_quantity"' in page.text
    assert 'value="20"' in page.text
    assert 'data-max-qty="20"' in page.text


def test_sell_plan_rejects_fractional_quantity(client):
    sec_id = _add_security(client, "SPLNFRAC")
    _add_brokerage_lot(client, sec_id, quantity="10")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "total_quantity": "6.5",
            "tranche_count": "2",
            "start_date": start_date,
            "cadence_days": "14",
            "min_spacing_days": "1",
        },
    )
    assert create.status_code == 422
    assert "whole number of shares" in create.text


def test_sell_plan_includes_link_to_simulate(client):
    sec_id = _add_security(client, "SPLNSIM")
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
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    page = client.get(f"/sell-plan?plan_id={plan_id}")
    assert page.status_code == 200
    assert "/simulate?security_id=" in page.text
    assert f"sell_plan_id={plan_id}" in page.text


def test_simulate_page_accepts_sell_plan_prefill_params(client):
    sec_id = _add_security(client, "SPLNPREF")
    _add_brokerage_lot(client, sec_id, quantity="10")

    resp = client.get(
        f"/simulate?security_id={sec_id}&quantity=4&price_per_share_gbp=123.45"
        "&sell_plan_id=test-plan-1&tranche_id=test-tranche-1"
    )
    assert resp.status_code == 200
    assert "Prefilled from Sell Plan" in resp.text
    assert 'value="4"' in resp.text
    assert 'value="123.45"' in resp.text


def test_sell_plan_can_be_deleted(client):
    sec_id = _add_security(client, "SPLNDEL")
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
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    page_before = client.get(f"/sell-plan?plan_id={plan_id}")
    assert page_before.status_code == 200
    assert plan_id in page_before.text

    delete_resp = client.post(
        "/sell-plan/delete",
        data={"plan_id": plan_id},
        follow_redirects=False,
    )
    assert delete_resp.status_code == 303

    page_after = client.get("/sell-plan")
    assert page_after.status_code == 200
    assert plan_id not in page_after.text


def test_simulate_result_includes_sell_plan_handoff_link(client):
    sec_id = _add_security(client, "SPLNTOHAND")
    _add_brokerage_lot(client, sec_id, quantity="10")

    sim = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "4",
            "price_per_share_gbp": "12.50",
            "broker_fees_gbp": "0",
            "scheme_type": "",
        },
    )
    assert sim.status_code == 200
    assert "Create Sell Plan From This Simulation" in sim.text
    assert f"/sell-plan?security_id={sec_id}" in sim.text
    assert "total_quantity=4" in sim.text
    assert "reference_price_gbp=12.50" in sim.text


def test_sell_plan_prefill_via_query_from_simulate(client):
    sec_id = _add_security(client, "SPLNPRE")
    _add_brokerage_lot(client, sec_id, quantity="10")

    resp = client.get(f"/sell-plan?security_id={sec_id}&total_quantity=4&reference_price_gbp=15.20")
    assert resp.status_code == 200
    assert f'value="{sec_id}"' in resp.text
    assert 'id="total_quantity"' in resp.text
    assert 'value="4"' in resp.text
    assert 'id="reference_price_gbp"' in resp.text
    assert 'value="15.20"' in resp.text


def test_simulate_max_uses_whole_share_floor(client):
    sec_id = _add_security(client, "SIMWHOLE")
    _add_brokerage_lot(client, sec_id, quantity="5.6")

    page = client.get("/simulate")
    assert page.status_code == 200
    assert f'value=\"{sec_id}\"' in page.text
    assert 'data-max-qty="5"' in page.text


def test_simulate_rejects_fractional_quantity(client):
    sec_id = _add_security(client, "SIMFRAC")
    _add_brokerage_lot(client, sec_id, quantity="10")

    sim = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "5.6",
            "price_per_share_gbp": "12.00",
            "scheme_type": "",
        },
    )
    assert sim.status_code == 422
    assert "whole number of shares" in sim.text


def test_sell_plan_includes_espp_plus_paid_and_matured_matched(client):
    sec_id = _add_security(client, "SPLNESPP")
    today = date.today()

    # Pair 1: matched shares still in forfeiture window -> matched excluded, paid included.
    _add_espp_plus_pair(
        security_id=sec_id,
        acquisition_date=today - timedelta(days=30),
        employee_qty="5",
        matched_qty="2",
        forfeiture_period_end=today + timedelta(days=20),
    )
    # Pair 2: matched shares past forfeiture window -> both paid and matched included.
    _add_espp_plus_pair(
        security_id=sec_id,
        acquisition_date=today - timedelta(days=220),
        employee_qty="3",
        matched_qty="2",
        forfeiture_period_end=today - timedelta(days=10),
    )

    page = client.get("/sell-plan")
    assert page.status_code == 200
    # Expected whole-share sellable = 5 (paid at risk) + 3 (paid matured pair) + 2 (matured matched) = 10
    assert "Sellable: 10" in page.text
    assert 'id="total_quantity"' in page.text
    assert 'value="10"' in page.text


def test_sell_plan_supports_limit_ladder_method(client):
    sec_id = _add_security(client, "SPLNLIM")
    _add_brokerage_lot(client, sec_id, quantity="12")

    start_date = (date.today() + timedelta(days=1)).isoformat()
    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "method": "LIMIT_LADDER",
            "execution_profile": "HYBRID_DE_RISK",
            "total_quantity": "6",
            "tranche_count": "3",
            "start_date": start_date,
            "cadence_days": "14",
            "min_spacing_days": "1",
            "limit_start_gbp": "10.00",
            "limit_step_gbp": "0.25",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    plan = next(
        p for p in SellPlanService.list_plans(_state.get_db_path()) if p["plan_id"] == plan_id
    )
    assert plan["method"] == "LIMIT_LADDER"
    assert plan["tranches"][0]["limit_price_gbp"] == "10.00"
    assert plan["tranches"][1]["limit_price_gbp"] == "10.25"

    page = client.get(f"/sell-plan?plan_id={plan_id}")
    assert page.status_code == 200
    assert "Limit Ladder" in page.text


def test_sell_plan_export_requires_approval_then_returns_csv(client):
    sec_id = _add_security(client, "SPLNEXP")
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
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    export_before = client.get(f"/sell-plan/export?plan_id={plan_id}")
    assert export_before.status_code == 422
    assert "approved" in export_before.text.lower()

    approve = client.post(
        "/sell-plan/approval",
        data={
            "plan_id": plan_id,
            "approval_status": "APPROVED",
        },
        follow_redirects=False,
    )
    assert approve.status_code == 303

    export_after = client.get(f"/sell-plan/export?plan_id={plan_id}")
    assert export_after.status_code == 200
    assert "ExternalId,PlanId,TrancheId" in export_after.text
    assert f",{plan_id}," in export_after.text
    assert "SP-" in export_after.text


def test_sell_plan_export_includes_broker_algo_fields(client):
    sec_id = _add_security(client, "SPLNALGO")
    _add_brokerage_lot(client, sec_id, quantity="10")
    start_date = (date.today() + timedelta(days=1)).isoformat()

    create = client.post(
        "/sell-plan",
        data={
            "security_id": sec_id,
            "method": "BROKER_ALGO",
            "total_quantity": "6",
            "tranche_count": "2",
            "start_date": start_date,
            "cadence_days": "14",
            "min_spacing_days": "1",
            "broker_algo_name": "VWAP",
            "broker_algo_window_minutes": "120",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    plan_id = parse_qs(urlparse(create.headers["location"]).query)["plan_id"][0]

    approve = client.post(
        "/sell-plan/approval",
        data={
            "plan_id": plan_id,
            "approval_status": "APPROVED",
        },
        follow_redirects=False,
    )
    assert approve.status_code == 303

    export = client.get(f"/sell-plan/export?plan_id={plan_id}")
    assert export.status_code == 200
    assert ",VWAP,120," in export.text
