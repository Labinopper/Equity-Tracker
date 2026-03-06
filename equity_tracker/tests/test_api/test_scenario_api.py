from __future__ import annotations

from datetime import date

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Scenario API PLC",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, quantity: str = "5") -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert resp.status_code == 201, resp.text


def _add_lot_with_cost(
    client,
    security_id: str,
    *,
    quantity: str,
    acquisition_price_gbp: str,
    acquisition_date: str,
) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": acquisition_date,
            "quantity": quantity,
            "acquisition_price_gbp": acquisition_price_gbp,
            "true_cost_per_share_gbp": acquisition_price_gbp,
        },
    )
    assert resp.status_code == 201, resp.text


def _add_price(security_id: str, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-scenario-api",
        )


def test_scenario_lab_ui_page_renders(client):
    page = client.get("/scenario-lab")
    assert page.status_code == 200
    assert "Scenario Lab" in page.text
    assert "Build Scenario" in page.text
    assert "Scenario Comparison" in page.text
    assert "Scenario Templates" in page.text
    assert "Load Inputs" in page.text


def test_api_scenario_run_and_get_by_id(client):
    sec_id = _add_security(client, "SCNAPI")
    _add_lot(client, sec_id, quantity="6")
    _add_price(sec_id, date(2026, 2, 25), "20.00")

    run_resp = client.post(
        "/api/scenarios/run",
        json={
            "name": "API scenario",
            "as_of_date": "2026-02-25",
            "price_shock_pct": "0",
            "legs": [
                {
                    "security_id": sec_id,
                    "quantity": "3",
                }
            ],
        },
    )
    assert run_resp.status_code == 200, run_resp.text
    payload = run_resp.json()
    assert payload["hide_values"] is False
    assert payload["totals"]["total_proceeds_gbp"] == "60.00"
    assert payload["totals"]["total_cost_basis_gbp"] == "30.00"
    assert payload["input_snapshot"]["legs"][0]["security_id"] == sec_id
    assert payload["legs"][0]["trace_links"]["reconcile_security_href"].startswith("/reconcile")

    scenario_id = payload["scenario_id"]
    get_resp = client.get(f"/api/scenarios/{scenario_id}")
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()
    assert fetched["scenario_id"] == scenario_id
    assert len(fetched["legs"]) == 1
    assert fetched["input_snapshot"]["legs"][0]["security_id"] == sec_id


def test_api_scenario_run_returns_422_for_quantity_above_available(client):
    sec_id = _add_security(client, "SCNERRAPI")
    _add_lot(client, sec_id, quantity="2")
    _add_price(sec_id, date(2026, 2, 25), "20.00")

    resp = client.post(
        "/api/scenarios/run",
        json={
            "as_of_date": "2026-02-25",
            "legs": [
                {
                    "security_id": sec_id,
                    "quantity": "3",
                }
            ],
        },
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["detail"]["error"] == "validation_error"
    assert "exceeds available" in payload["detail"]["message"]


def test_api_scenario_run_respects_hide_values_mode(client):
    sec_id = _add_security(client, "SCNHIDEAPI")
    _add_lot(client, sec_id, quantity="3")
    _add_price(sec_id, date(2026, 2, 25), "12.00")

    db_path = _state.get_db_path()
    assert db_path is not None
    settings = AppSettings.load(db_path)
    settings.hide_values = True
    settings.save()

    resp = client.post(
        "/api/scenarios/run",
        json={
            "as_of_date": "2026-02-25",
            "legs": [
                {
                    "security_id": sec_id,
                    "quantity": "1",
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["hide_values"] is True
    assert payload["totals"] is None
    assert payload["hidden_reason"] == "Values hidden by privacy mode."

    page = client.get("/scenario-lab")
    assert page.status_code == 200
    assert "Scenario Lab Hidden" in page.text


def test_scenario_lab_navigation_link_present_on_home(client):
    # Scenario Lab is under the Simulate group tab in the two-tier nav.
    # The home page shows the Simulate group tab (always visible in the topbar),
    # and the scenario-lab subnav link is visible when on any Simulate-group page.
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/simulate"' in page.text  # Simulate group tab always present

    simulate_page = client.get("/simulate")
    assert simulate_page.status_code == 200
    assert 'href="/scenario-lab"' in simulate_page.text  # subnav visible within group


def test_api_scenario_sequential_mode_changes_fifo_outcome(client):
    sec_id = _add_security(client, "SCNSEQ")
    _add_lot_with_cost(
        client,
        sec_id,
        quantity="2",
        acquisition_price_gbp="10.00",
        acquisition_date="2025-01-10",
    )
    _add_lot_with_cost(
        client,
        sec_id,
        quantity="2",
        acquisition_price_gbp="20.00",
        acquisition_date="2025-01-11",
    )
    _add_price(sec_id, date(2026, 2, 25), "30.00")

    independent = client.post(
        "/api/scenarios/run",
        json={
            "name": "independent",
            "execution_mode": "INDEPENDENT",
            "as_of_date": "2026-02-25",
            "legs": [
                {"security_id": sec_id, "quantity": "2"},
                {"security_id": sec_id, "quantity": "2"},
            ],
        },
    )
    assert independent.status_code == 200, independent.text
    independent_payload = independent.json()
    assert independent_payload["execution_mode"] == "INDEPENDENT"
    assert independent_payload["legs"][0]["total_cost_basis_gbp"] == "20.00"
    assert independent_payload["legs"][1]["total_cost_basis_gbp"] == "20.00"

    sequential = client.post(
        "/api/scenarios/run",
        json={
            "name": "sequential",
            "execution_mode": "SEQUENTIAL",
            "as_of_date": "2026-02-25",
            "legs": [
                {"security_id": sec_id, "quantity": "2"},
                {"security_id": sec_id, "quantity": "2"},
            ],
        },
    )
    assert sequential.status_code == 200, sequential.text
    sequential_payload = sequential.json()
    assert sequential_payload["execution_mode"] == "SEQUENTIAL"
    assert sequential_payload["legs"][0]["total_cost_basis_gbp"] == "20.00"
    assert sequential_payload["legs"][1]["total_cost_basis_gbp"] == "40.00"
    assert (
        sequential_payload["totals"]["total_cost_basis_gbp"]
        != independent_payload["totals"]["total_cost_basis_gbp"]
    )
