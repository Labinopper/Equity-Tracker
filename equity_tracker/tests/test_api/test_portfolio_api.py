"""
Smoke tests for the Equity Tracker web API — Phase WB.

Covers:
  - Health check (no DB required)
  - Admin status endpoint
  - Portfolio CRUD: securities, lots, simulate disposal, commit disposal
  - Settings GET / PUT
  - Reports: tax-years (no DB), CGT, economic-gain, audit
  - Decimal serialization: no floats anywhere in monetary fields
  - Error mapping: 422 on bad input, 409 on duplicate external_id
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json

from src.app_context import AppContext
from src.db.repository.lots import LotRepository
from src.db.repository.prices import PriceRepository
from src.db.repository.transactions import TransactionRepository


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_tax_years_no_db_required(client):
    """GET /reports/tax-years must work even when the DB is locked."""
    resp = client.get("/reports/tax-years")
    assert resp.status_code == 200
    years = resp.json()
    assert isinstance(years, list)
    assert len(years) >= 1
    # Every entry should be a string like "2024-25"
    for y in years:
        assert isinstance(y, str)
        assert "-" in y


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def test_admin_status_unlocked(client):
    resp = client.get("/admin/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["locked"] is False


# ---------------------------------------------------------------------------
# Securities
# ---------------------------------------------------------------------------

def _add_security(client, ticker="TSCO", name="Tesco PLC", currency="GBP") -> dict:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": name,
            "currency": currency,
            "units_precision": 0,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_add_security_returns_201(client):
    sec = _add_security(client)
    assert sec["ticker"] == "TSCO"
    assert sec["currency"] == "GBP"
    assert "id" in sec


def test_add_security_normalises_ticker_and_currency(client):
    resp = client.post(
        "/portfolio/securities",
        json={"ticker": "aapl", "name": "Apple", "currency": "usd", "is_manual_override": True},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["currency"] == "USD"


def test_add_security_invalid_currency_returns_422(client):
    resp = client.post(
        "/portfolio/securities",
        json={"ticker": "X", "name": "Bad", "currency": "TOOLONG"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Lots
# ---------------------------------------------------------------------------

def _add_lot(client, security_id: str, quantity="100", price="145.32") -> dict:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "RSU",
            "acquisition_date": "2024-06-15",
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
            "tax_year": "2024-25",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_add_lot_returns_201(client):
    sec = _add_security(client)
    lot = _add_lot(client, sec["id"])
    assert lot["scheme_type"] == "RSU"
    assert lot["tax_year"] == "2024-25"
    # Monetary fields must be strings, never floats
    assert isinstance(lot["acquisition_price_gbp"], str)
    assert isinstance(lot["true_cost_per_share_gbp"], str)
    assert isinstance(lot["quantity"], str)


def test_add_lot_invalid_scheme_type_returns_422(client):
    sec = _add_security(client)
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "NOT_VALID",
            "acquisition_date": "2024-06-15",
            "quantity": "10",
            "acquisition_price_gbp": "100",
            "true_cost_per_share_gbp": "100",
        },
    )
    assert resp.status_code == 422


def test_add_lot_allows_isa_scheme_type(client):
    sec = _add_security(client, ticker="ISAAPI")
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ISA",
            "acquisition_date": "2024-06-15",
            "quantity": "10",
            "acquisition_price_gbp": "100",
            "true_cost_per_share_gbp": "100",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["scheme_type"] == "ISA"


def test_add_lot_brokerage_accepts_broker_currency(client):
    sec = _add_security(client, ticker="BROKCCY", currency="USD")
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2024-06-15",
            "quantity": "10",
            "acquisition_price_gbp": "100",
            "true_cost_per_share_gbp": "100",
            "broker_currency": "USD",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["broker_currency"] == "USD"


def test_add_lot_negative_quantity_returns_422(client):
    sec = _add_security(client)
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "RSU",
            "acquisition_date": "2024-06-15",
            "quantity": "-5",
            "acquisition_price_gbp": "100",
            "true_cost_per_share_gbp": "100",
        },
    )
    assert resp.status_code == 422


def test_add_lot_duplicate_external_id_returns_409(client):
    sec = _add_security(client)
    payload = {
        "security_id": sec["id"],
        "scheme_type": "RSU",
        "acquisition_date": "2024-06-15",
        "quantity": "10",
        "acquisition_price_gbp": "100",
        "true_cost_per_share_gbp": "100",
        "external_id": "LOT-001",
    }
    resp1 = client.post("/portfolio/lots", json=payload)
    assert resp1.status_code == 201
    resp2 = client.post("/portfolio/lots", json=payload)
    assert resp2.status_code == 409


def test_edit_lot_patch_updates_fields_and_returns_audit_id(client):
    sec = _add_security(client, ticker="EDLOT")
    lot = _add_lot(client, sec["id"], quantity="10", price="100.00")

    resp = client.patch(
        f"/portfolio/lots/{lot['id']}",
        json={
            "acquisition_date": "2024-07-01",
            "quantity": "12",
            "acquisition_price_gbp": "110.00",
            "true_cost_per_share_gbp": "95.00",
            "tax_year": "2024-25",
            "fmv_at_acquisition_gbp": "120.00",
            "notes": "corrected",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audit_id"] is not None
    assert body["lot"]["quantity"] == "12"
    assert body["lot"]["quantity_remaining"] == "12"
    assert body["lot"]["acquisition_price_gbp"] == "110.00"


def test_edit_lot_rejects_quantity_below_already_disposed(client):
    sec = _add_security(client, ticker="EDREJ")
    lot = _add_lot(client, sec["id"], quantity="10", price="100.00")
    commit = client.post(
        "/portfolio/disposals/commit",
        json={
            "security_id": sec["id"],
            "quantity": "4",
            "price_per_share_gbp": "120.00",
            "transaction_date": "2024-10-01",
        },
    )
    assert commit.status_code == 201, commit.text

    resp = client.patch(
        f"/portfolio/lots/{lot['id']}",
        json={
            "acquisition_date": "2024-06-15",
            "quantity": "3",
            "acquisition_price_gbp": "100.00",
            "true_cost_per_share_gbp": "100.00",
            "tax_year": "2024-25",
            "fmv_at_acquisition_gbp": None,
            "notes": "bad edit",
        },
    )
    assert resp.status_code == 422


def test_transfer_lot_to_brokerage_is_non_disposal(client):
    sec = _add_security(client, ticker="TRLOT")
    lot_resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert lot_resp.status_code == 201, lot_resp.text
    lot = lot_resp.json()

    transfer = client.post(
        f"/portfolio/lots/{lot['id']}/transfer",
        json={
            "destination_scheme": "BROKERAGE",
            "quantity": "4",
            "notes": "move to broker",
        },
    )
    assert transfer.status_code == 200, transfer.text
    body = transfer.json()
    assert body["lot"]["scheme_type"] == "BROKERAGE"
    assert body["lot"]["quantity"] == "4"
    assert body["lot"]["broker_currency"] == "GBP"
    assert body["audit_id"]

    with AppContext.read_session() as sess:
        source = LotRepository(sess).require_by_id(lot["id"])
    assert source.scheme_type == "ESPP"
    assert source.quantity_remaining == "6"

    with AppContext.read_session() as sess:
        txs = TransactionRepository(sess).list_for_security(sec["id"])
    assert txs == []


def test_transfer_lot_rejects_fractional_espp_quantity(client):
    sec = _add_security(client, ticker="TRFRACAPI")
    lot_resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert lot_resp.status_code == 201, lot_resp.text
    lot = lot_resp.json()

    transfer = client.post(
        f"/portfolio/lots/{lot['id']}/transfer",
        json={"destination_scheme": "BROKERAGE", "quantity": "1.5"},
    )
    assert transfer.status_code == 422
    assert "whole shares" in transfer.text


def test_transfer_lot_allows_destination_broker_currency_override(client):
    sec = _add_security(client, ticker="TRCCYAPI", currency="USD")
    lot_resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "RSU",
            "acquisition_date": "2025-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "4.00",
        },
    )
    assert lot_resp.status_code == 201, lot_resp.text
    lot = lot_resp.json()

    transfer = client.post(
        f"/portfolio/lots/{lot['id']}/transfer",
        json={
            "destination_scheme": "BROKERAGE",
            "broker_currency": "USD",
        },
    )
    assert transfer.status_code == 200, transfer.text
    assert transfer.json()["lot"]["broker_currency"] == "USD"


def test_transfer_lot_allows_whole_quantity_when_source_remaining_is_fractional(client):
    sec = _add_security(client, ticker="TRWHOLEAPI")
    lot_resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "2.3",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert lot_resp.status_code == 201, lot_resp.text
    lot = lot_resp.json()

    transfer = client.post(
        f"/portfolio/lots/{lot['id']}/transfer",
        json={"destination_scheme": "BROKERAGE", "quantity": "2"},
    )
    assert transfer.status_code == 200, transfer.text

    with AppContext.read_session() as sess:
        source = LotRepository(sess).require_by_id(lot["id"])
    assert source.quantity_remaining == "0.3"


def test_transfer_lot_fifo_consumes_fractional_head_before_newer_lot(client):
    sec = _add_security(client, ticker="TRFIFORAW")
    first = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "0.3",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-02-15",
            "quantity": "2",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "11.00",
        },
    )
    assert second.status_code == 201, second.text
    first_lot = first.json()
    second_lot = second.json()

    transfer = client.post(
        f"/portfolio/lots/{first_lot['id']}/transfer",
        json={"destination_scheme": "BROKERAGE", "quantity": "2"},
    )
    assert transfer.status_code == 200, transfer.text

    with AppContext.read_session() as sess:
        first_after = LotRepository(sess).require_by_id(first_lot["id"])
        second_after = LotRepository(sess).require_by_id(second_lot["id"])
    assert Decimal(first_after.quantity_remaining) == Decimal("0")
    assert Decimal(second_after.quantity_remaining) == Decimal("0.3")


def test_transfer_lot_rejects_non_fifo_espp_source(client):
    sec = _add_security(client, ticker="TRFIFOAPI")
    first = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert first.status_code == 201, first.text
    later = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "ESPP",
            "acquisition_date": "2025-02-15",
            "quantity": "5",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "11.00",
        },
    )
    assert later.status_code == 201, later.text
    later_lot = later.json()

    transfer = client.post(
        f"/portfolio/lots/{later_lot['id']}/transfer",
        json={"destination_scheme": "BROKERAGE", "quantity": "1"},
    )
    assert transfer.status_code == 422
    assert "FIFO order" in transfer.text


def test_transfer_lot_rejects_brokerage_source(client):
    sec = _add_security(client, ticker="TRREJ")
    lot_resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert lot_resp.status_code == 201, lot_resp.text
    lot = lot_resp.json()

    transfer = client.post(
        f"/portfolio/lots/{lot['id']}/transfer",
        json={"destination_scheme": "BROKERAGE"},
    )
    assert transfer.status_code == 422


def test_transfer_lot_rejects_pre_vest_rsu_source(client):
    sec = _add_security(client, ticker="TRRSUAPI")
    vest_date = (date.today() + timedelta(days=10)).isoformat()
    lot_resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "RSU",
            "acquisition_date": vest_date,
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "4.00",
        },
    )
    assert lot_resp.status_code == 201, lot_resp.text
    lot = lot_resp.json()

    transfer = client.post(
        f"/portfolio/lots/{lot['id']}/transfer",
        json={"destination_scheme": "BROKERAGE"},
    )
    assert transfer.status_code == 422
    assert "after vest date" in transfer.text


# ---------------------------------------------------------------------------
# Portfolio summary
# ---------------------------------------------------------------------------

def test_portfolio_summary_empty(client):
    resp = client.get("/portfolio/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["securities"] == []
    assert body["total_cost_basis_gbp"] == "0"
    assert body["total_true_cost_gbp"] == "0"


def test_portfolio_summary_no_floats(client):
    """Verify the entire summary response contains no JSON floats."""
    sec = _add_security(client)
    _add_lot(client, sec["id"])
    resp = client.get("/portfolio/summary")
    assert resp.status_code == 200
    # Deserialise raw JSON and check no float values appear
    raw = json.loads(resp.text)
    _assert_no_floats(raw)


def test_portfolio_summary_includes_native_and_gbp_fields_with_fx_basis(client):
    sec = _add_security(client, ticker="SUMFX", currency="USD")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec["id"],
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2024-06-15",
            "quantity": "2",
            "acquisition_price_gbp": "80.00",
            "true_cost_per_share_gbp": "80.00",
            "broker_currency": "USD",
        },
    )
    assert add.status_code == 201, add.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec["id"],
            price_date=date.today(),
            close_price_original_ccy="110.00",
            close_price_gbp="88.00",
            currency="USD",
            source="google_sheets:2026-02-24 12:00:00|fx:2026-02-24 12:00:00",
        )

    resp = client.get("/portfolio/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valuation_currency"] == "GBP"
    assert body["fx_conversion_basis"] is not None
    ss = body["securities"][0]
    assert ss["current_price_native"] == "110.00"
    assert ss["current_price_gbp"] == "88.00"
    assert ss["market_value_native"] == "220.00"
    assert ss["market_value_gbp"] == "176.00"
    assert ss["market_value_native_currency"] == "USD"
    ls = ss["active_lots"][0]
    assert ls["market_value_native"] == "220.00"
    assert ls["market_value_gbp"] == "176.00"
    assert ls["market_value_native_currency"] == "USD"


def _assert_no_floats(obj):
    """Recursively assert that no float values exist in a JSON-decoded object."""
    if isinstance(obj, float):
        raise AssertionError(f"Found a float value in response: {obj!r}")
    if isinstance(obj, dict):
        for v in obj.values():
            _assert_no_floats(v)
    if isinstance(obj, list):
        for item in obj:
            _assert_no_floats(item)


# ---------------------------------------------------------------------------
# Simulate disposal
# ---------------------------------------------------------------------------

def test_simulate_disposal(client):
    sec = _add_security(client)
    _add_lot(client, sec["id"], quantity="100", price="150.00")
    resp = client.post(
        "/portfolio/disposals/simulate",
        json={
            "security_id": sec["id"],
            "quantity": "50",
            "price_per_share_gbp": "200.00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_fully_allocated"] is True
    assert body["quantity_sold"] == "50"
    assert isinstance(body["total_proceeds_gbp"], str)
    assert isinstance(body["total_realised_gain_gbp"], str)
    _assert_no_floats(body)


def test_simulate_disposal_insufficient_lots(client):
    sec = _add_security(client)
    _add_lot(client, sec["id"], quantity="10", price="100.00")
    resp = client.post(
        "/portfolio/disposals/simulate",
        json={
            "security_id": sec["id"],
            "quantity": "999",
            "price_per_share_gbp": "100.00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_fully_allocated"] is False
    assert Decimal_gt_zero(body["shortfall"])


def Decimal_gt_zero(s: str) -> bool:
    from decimal import Decimal
    return Decimal(s) > 0


# ---------------------------------------------------------------------------
# Commit disposal
# ---------------------------------------------------------------------------

def test_commit_disposal(client):
    sec = _add_security(client)
    _add_lot(client, sec["id"], quantity="100", price="100.00")
    resp = client.post(
        "/portfolio/disposals/commit",
        json={
            "security_id": sec["id"],
            "quantity": "50",
            "price_per_share_gbp": "120.00",
            "transaction_date": "2024-10-01",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "transaction" in body
    assert "lot_disposals" in body
    assert len(body["lot_disposals"]) == 1
    tx = body["transaction"]
    assert isinstance(tx["total_proceeds_gbp"], str)
    _assert_no_floats(body)


def test_commit_disposal_duplicate_external_id_returns_409(client):
    sec = _add_security(client)
    _add_lot(client, sec["id"], quantity="200", price="100.00")
    payload = {
        "security_id": sec["id"],
        "quantity": "10",
        "price_per_share_gbp": "110.00",
        "transaction_date": "2024-10-01",
        "external_id": "TXN-001",
    }
    resp1 = client.post("/portfolio/disposals/commit", json=payload)
    assert resp1.status_code == 201
    resp2 = client.post("/portfolio/disposals/commit", json=payload)
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_get_settings_defaults(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "default_gross_income" in body
    assert isinstance(body["default_gross_income"], str)
    assert isinstance(body["show_exhausted_lots"], bool)
    assert isinstance(body["hide_values"], bool)


def test_put_settings_round_trip(client):
    payload = {
        "default_gross_income": "85000.00",
        "default_pension_sacrifice": "6000.00",
        "default_student_loan_plan": 2,
        "default_other_income": "500.00",
        "default_tax_year": "2024-25",
        "show_exhausted_lots": True,
        "hide_values": True,
    }
    put_resp = client.put("/api/settings", json=payload)
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["default_gross_income"] == "85000.00"
    assert body["default_pension_sacrifice"] == "6000.00"
    assert body["default_student_loan_plan"] == 2
    assert body["show_exhausted_lots"] is True
    assert body["hide_values"] is True

    # GET should return the saved values
    get_resp = client.get("/api/settings")
    assert get_resp.status_code == 200
    assert get_resp.json()["default_gross_income"] == "85000.00"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def test_cgt_report_empty(client):
    tax_years = client.get("/reports/tax-years").json()
    tax_year = tax_years[0]
    resp = client.get(f"/reports/cgt?tax_year={tax_year}")
    assert resp.status_code == 200
    body = resp.json()
    assert "disposals" in body
    assert "net_gain_gbp" in body
    assert isinstance(body["net_gain_gbp"], str)
    assert body["cgt_result"] is None  # No include_tax_due


def test_cgt_report_with_tax_due(client):
    # Save settings first
    tax_years = client.get("/reports/tax-years").json()
    tax_year = tax_years[0]
    client.put("/api/settings", json={
        "default_gross_income": "80000.00",
        "default_pension_sacrifice": "0.00",
        "default_student_loan_plan": None,
        "default_other_income": "0.00",
        "default_tax_year": tax_year,
        "show_exhausted_lots": False,
    })
    resp = client.get(f"/reports/cgt?tax_year={tax_year}&include_tax_due=true")
    assert resp.status_code == 200
    body = resp.json()
    # cgt_result may be None if no disposals, or populated — either is valid
    # but it must not be a float
    _assert_no_floats(body)


def test_cgt_report_invalid_tax_year(client):
    resp = client.get("/reports/cgt?tax_year=9999-00")
    assert resp.status_code == 400


def test_economic_gain_empty(client):
    tax_years = client.get("/reports/tax-years").json()
    resp = client.get(f"/reports/economic-gain?tax_year={tax_years[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert "disposals" in body
    _assert_no_floats(body)


def test_audit_log_empty(client):
    resp = client.get("/reports/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_audit_log_after_mutations(client):
    _add_security(client)
    resp = client.get("/reports/audit")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) >= 1
    assert entries[0]["table_name"] == "securities"


def test_audit_log_record_id_filter(client):
    sec = _add_security(client, ticker="RIDFILT", name="Record Filter Plc", currency="GBP")

    resp = client.get(f"/reports/audit?table_name=securities&record_id={sec['id']}")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert all(row["table_name"] == "securities" for row in rows)
    assert all(row["record_id"] == sec["id"] for row in rows)
