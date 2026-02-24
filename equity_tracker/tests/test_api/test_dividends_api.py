from __future__ import annotations

from src.api import _state
from src.settings import AppSettings


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Dividend PLC",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_api_dividends_summary_empty_and_ui_renders(client):
    resp = client.get("/api/dividends/summary")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["hide_values"] is False
    assert payload["entries"] == []
    assert "summary" in payload
    assert "tax_years" in payload

    page = client.get("/dividends")
    assert page.status_code == 200
    assert "Dividends" in page.text
    assert "Add Dividend" in page.text
    assert "Tax-Year Dividend Summary" in page.text


def test_api_dividend_entry_create_and_summary_rows(client):
    sec_id = _add_security(client, "DIVAPI")

    create_resp = client.post(
        "/api/dividends/entries",
        json={
            "security_id": sec_id,
            "dividend_date": "2026-02-20",
            "amount_gbp": "120.50",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "notes": "test row",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["security_id"] == sec_id
    assert created["amount_gbp"] == "120.50"

    summary_resp = client.get("/api/dividends/summary")
    assert summary_resp.status_code == 200
    payload = summary_resp.json()
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["ticker"] == "DIVAPI"
    assert payload["entries"][0]["tax_treatment"] == "TAXABLE"


def test_dividends_ui_add_form_submission(client):
    sec_id = _add_security(client, "DIVFORM")

    submit = client.post(
        "/dividends/add",
        data={
            "security_id": sec_id,
            "dividend_date": "2026-02-18",
            "amount_gbp": "75.25",
            "tax_treatment": "ISA_EXEMPT",
            "source": "manual",
            "notes": "form submit",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303
    assert submit.headers["location"] == "/dividends?msg=Dividend+entry+added."

    page = client.get("/dividends")
    assert page.status_code == 200
    assert "DIVFORM" in page.text
    assert "ISA_EXEMPT" in page.text
    assert "75.25" in page.text


def test_api_dividends_respects_hide_values_setting(client):
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.hide_values = True
    settings.save()

    resp = client.get("/api/dividends/summary")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["hide_values"] is True
    assert payload["entries"] == []

    page = client.get("/dividends")
    assert page.status_code == 200
    assert "privacy mode is enabled" in page.text
