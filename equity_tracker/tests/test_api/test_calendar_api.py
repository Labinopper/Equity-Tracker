from __future__ import annotations

from datetime import date

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Calendar PLC",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "RSU",
            "acquisition_date": "2026-03-10",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
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
            source="test-calendar-api",
        )


def test_api_calendar_events_empty_portfolio_returns_countdowns(client):
    resp = client.get("/api/calendar/events")
    assert resp.status_code == 200

    body = resp.json()
    assert "generated_at_utc" in body
    assert body["as_of_date"]
    assert body["horizon_days"] == 400
    assert "events" in body
    assert "countdowns" in body
    assert "next_tax_year_end" in body["countdowns"]


def test_api_calendar_events_returns_lot_events_when_present(client):
    sec_id = _add_security(client, "CALAPI")
    _add_lot(client, sec_id)
    _add_price(sec_id, date(2026, 2, 24), "20.00")

    resp = client.get("/api/calendar/events?days=60")
    assert resp.status_code == 200
    body = resp.json()

    vest_events = [e for e in body["events"] if e["event_type"] == "VEST_DATE"]
    assert len(vest_events) == 1
    assert vest_events[0]["ticker"] == "CALAPI"
    assert vest_events[0]["event_date"] == "2026-03-10"
    assert vest_events[0]["value_at_stake_gbp"] == "100.00"
    assert vest_events[0]["price_as_of"] == "2026-02-24"
    assert vest_events[0]["fx_basis_note"] == "GBP security (no FX conversion)"


def test_api_calendar_events_rejects_invalid_days(client):
    resp = client.get("/api/calendar/events?days=0")
    assert resp.status_code == 422


def test_calendar_ui_page_renders(client):
    resp = client.get("/calendar")
    assert resp.status_code == 200
    assert "Calendar" in resp.text
    assert "Upcoming Events" in resp.text
    assert "Horizon" in resp.text
    assert "Price/FX Basis" in resp.text
