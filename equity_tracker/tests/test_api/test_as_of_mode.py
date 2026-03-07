from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} As Of Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(
    client,
    security_id: str,
    *,
    scheme_type: str = "BROKERAGE",
    acquisition_date: str = "2025-01-15",
    quantity: str = "10",
    acquisition_price_gbp: str = "10.00",
    true_cost_per_share_gbp: str = "10.00",
) -> dict:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": acquisition_date,
            "quantity": quantity,
            "acquisition_price_gbp": acquisition_price_gbp,
            "true_cost_per_share_gbp": true_cost_per_share_gbp,
            "tax_year": "2024-25",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_price(
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
            source="test-as-of",
        )


def test_as_of_mode_uses_price_on_or_before_selected_date_and_preserves_navigation(client):
    sec_id = _add_security(client, ticker="T54PRICE")
    _add_lot(client, sec_id, quantity="10")
    _add_price(
        sec_id,
        price_date=date(2026, 2, 1),
        close_price_original_ccy="10.00",
        close_price_gbp="10.00",
    )
    _add_price(
        sec_id,
        price_date=date(2026, 3, 1),
        close_price_original_ccy="20.00",
        close_price_gbp="20.00",
    )

    portfolio = client.get("/?as_of=2026-02-15")
    assert portfolio.status_code == 200
    text = portfolio.text
    assert "As of 2026-02-15." in text
    assert "Historical as of 2026-02-15" in text
    assert "2026-02-01" in text
    assert 'href="/net-value?as_of=2026-02-15"' in text
    assert 'href="/risk?as_of=2026-02-15"' in text

    risk_old = client.get("/api/risk/summary?as_of=2026-02-15")
    assert risk_old.status_code == 200
    old_payload = risk_old.json()
    assert old_payload["as_of_date"] == "2026-02-15"
    assert old_payload["total_market_value_gbp"] == "100.00"
    assert old_payload["optionality_timeline"][0]["as_of_date"] == "2026-02-15"

    risk_new = client.get("/api/risk/summary?as_of=2026-03-07")
    assert risk_new.status_code == 200
    new_payload = risk_new.json()
    assert new_payload["as_of_date"] == "2026-03-07"
    assert new_payload["total_market_value_gbp"] == "200.00"
    assert new_payload["optionality_timeline"][0]["as_of_date"] == "2026-03-07"

    net_value = client.get("/net-value?as_of=2026-02-15")
    assert net_value.status_code == 200
    assert "As of 2026-02-15." in net_value.text
    assert "Uses the latest stored price on or before the selected date" in net_value.text


def test_calendar_api_honors_as_of_date_for_countdowns(client):
    sec_id = _add_security(client, ticker="T54CAL")
    _add_lot(
        client,
        sec_id,
        scheme_type="RSU",
        acquisition_date="2026-03-20",
        quantity="5",
        acquisition_price_gbp="8.00",
        true_cost_per_share_gbp="4.00",
    )
    _add_price(
        sec_id,
        price_date=date(2026, 3, 1),
        close_price_original_ccy="15.00",
        close_price_gbp="15.00",
    )

    early = client.get("/api/calendar/events?as_of=2026-03-01&days=30")
    assert early.status_code == 200
    early_payload = early.json()
    assert early_payload["as_of_date"] == "2026-03-01"
    vest_event = next(
        event for event in early_payload["events"] if event["event_type"] == "VEST_DATE"
    )
    assert vest_event["days_until"] == 19

    late = client.get("/api/calendar/events?as_of=2026-03-19&days=30")
    assert late.status_code == 200
    late_payload = late.json()
    assert late_payload["as_of_date"] == "2026-03-19"
    vest_event_late = next(
        event for event in late_payload["events"] if event["event_type"] == "VEST_DATE"
    )
    assert vest_event_late["days_until"] == 1


def test_scenario_lab_page_reloads_builder_context_for_selected_as_of_date(client):
    sec_id = _add_security(client, ticker="T54SCEN")
    _add_lot(
        client,
        sec_id,
        scheme_type="RSU",
        acquisition_date="2026-03-20",
        quantity="5",
        acquisition_price_gbp="8.00",
        true_cost_per_share_gbp="4.00",
    )
    _add_price(
        sec_id,
        price_date=date(2026, 3, 20),
        close_price_original_ccy="15.00",
        close_price_gbp="15.00",
    )

    before_vest = client.get("/scenario-lab?as_of=2026-03-01")
    assert before_vest.status_code == 200
    assert 'value="2026-03-01"' in before_vest.text
    assert '"ticker": "T54SCEN"' not in before_vest.text

    after_vest = client.get("/scenario-lab?as_of=2026-03-25")
    assert after_vest.status_code == 200
    assert 'value="2026-03-25"' in after_vest.text
    assert '"ticker": "T54SCEN"' in after_vest.text
    assert re.search(r'"available_quantity":\s*"5"', after_vest.text)
