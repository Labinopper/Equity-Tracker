from __future__ import annotations

from datetime import date, datetime, timezone

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


def _add_security(client, ticker: str = "IBM", currency: str = "USD") -> dict:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Corp",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_lot(client, security_id: str, quantity: str = "10", price: str = "100.00") -> dict:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upsert_price(
    security_id: str,
    *,
    price_date: date,
    original_ccy: str,
    gbp: str,
    currency: str,
    source: str,
) -> None:
    with AppContext.write_session() as sess:
        row = PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=original_ccy,
            close_price_gbp=gbp,
            currency=currency,
            source=source,
        )
        row.fetched_at = datetime(
            price_date.year,
            price_date.month,
            price_date.day,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        )


def test_validation_report_json_contains_required_sections(client):
    sec = _add_security(client, ticker="VALIBM", currency="USD")
    _add_lot(client, sec["id"], quantity="10", price="100.00")
    _upsert_price(
        sec["id"],
        price_date=date(2026, 2, 24),
        original_ccy="200.00",
        gbp="160.00",
        currency="USD",
        source="google_sheets:2026-02-24 10:00:00|fx:2026-02-24 09:58:00",
    )

    resp = client.get("/admin/validation_report?format=json")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "metadata" in body
    assert "global_settings_snapshot" in body
    assert "market_data_inputs" in body
    assert "scheme_rules_snapshot" in body
    assert "per_security_totals" in body
    assert "per_lot_breakdown" in body
    assert "invariant_warnings" in body

    sec_rows = [r for r in body["per_security_totals"] if r["symbol"] == "VALIBM"]
    assert len(sec_rows) == 1
    lot_rows = [r for r in body["per_lot_breakdown"] if r["symbol"] == "VALIBM"]
    assert len(lot_rows) == 1
    assert lot_rows[0]["employment_tax_intermediates"]["proceeds_gbp"]["raw"] is not None


def test_validation_report_as_of_uses_latest_price_on_or_before(client):
    sec = _add_security(client, ticker="ASOFIBM", currency="USD")
    _add_lot(client, sec["id"], quantity="5", price="100.00")
    _upsert_price(
        sec["id"],
        price_date=date(2026, 2, 23),
        original_ccy="100.00",
        gbp="80.00",
        currency="USD",
        source="google_sheets:2026-02-23 10:00:00|fx:2026-02-23 09:58:00",
    )
    _upsert_price(
        sec["id"],
        price_date=date(2026, 2, 24),
        original_ccy="120.00",
        gbp="96.00",
        currency="USD",
        source="google_sheets:2026-02-24 10:00:00|fx:2026-02-24 09:58:00",
    )

    old = client.get(
        "/admin/validation_report",
        params={
            "format": "json",
            "security_id": "ASOFIBM",
            "as_of": "2026-02-23T23:59:59Z",
        },
    )
    assert old.status_code == 200, old.text
    old_body = old.json()
    old_row = old_body["market_data_inputs"]["security_prices"][0]
    assert old_row["price_date_selected"] == "2026-02-23"
    assert old_row["price_used_gbp"] == "80.00"

    new = client.get(
        "/admin/validation_report",
        params={
            "format": "json",
            "security_id": "ASOFIBM",
            "as_of": "2026-02-24T23:59:59Z",
        },
    )
    assert new.status_code == 200, new.text
    new_body = new.json()
    new_row = new_body["market_data_inputs"]["security_prices"][0]
    assert new_row["price_date_selected"] == "2026-02-24"
    assert new_row["price_used_gbp"] == "96.00"


def test_validation_report_text_sections_present(client):
    sec = _add_security(client, ticker="TXTIBM", currency="GBP")
    _add_lot(client, sec["id"], quantity="1", price="10.00")
    _upsert_price(
        sec["id"],
        price_date=date(2026, 2, 24),
        original_ccy="11.00",
        gbp="11.00",
        currency="GBP",
        source="google_sheets:2026-02-24 10:00:00",
    )

    resp = client.get("/admin/validation_report?format=text&security_id=TXTIBM")
    assert resp.status_code == 200
    text = resp.text
    assert "A) Report Metadata" in text
    assert "B) Global Settings Snapshot (live)" in text
    assert "C) Market Data Inputs (live)" in text
    assert "D) Scheme Rules Snapshot (live + code constants)" in text
    assert "E) Per-Security Totals (recompute)" in text
    assert "F) Per-Lot Deep Breakdown" in text
    assert "G) Invariant / Consistency Warnings" in text
