from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.repository import DividendReferenceEventRepository
from src.services.cash_ledger_service import CashLedgerService
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


def _add_lot(
    client,
    *,
    security_id: str,
    scheme_type: str = "BROKERAGE",
    quantity: str = "1",
) -> str:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": "2025-01-10",
            "quantity": quantity,
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
            "tax_year": "2024-25",
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
    assert "Optional Lot Scope And Traceability" in page.text
    assert "Ex-Date" in page.text
    assert "Lot Explorer" in page.text
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


def test_api_dividend_entry_create_supports_native_currency_and_fx(client):
    sec_id = _add_security(client, "DIVFX")

    create_resp = client.post(
        "/api/dividends/entries",
        json={
            "security_id": sec_id,
            "dividend_date": "2026-02-20",
            "amount_original_ccy": "120.50",
            "original_currency": "USD",
            "fx_rate_to_gbp": "0.8000",
            "fx_rate_source": "manual_test",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "notes": "fx row",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["amount_gbp"] == "96.40"
    assert created["amount_original_ccy"] == "120.50"
    assert created["original_currency"] == "USD"
    assert created["fx_rate_to_gbp"] == "0.800000"

    summary_resp = client.get("/api/dividends/summary")
    assert summary_resp.status_code == 200
    payload = summary_resp.json()
    assert payload["entries"][0]["ticker"] == "DIVFX"
    assert payload["entries"][0]["original_currency"] == "USD"
    assert payload["allocation"]["mode"] == "SECURITY_LEVEL"

    db_path = _state.get_db_path()
    entries = CashLedgerService.load_entries(db_path)
    dividend_cash_entry = next(
        row for row in entries if row.get("metadata", {}).get("dividend_entry_id") == created["id"]
    )
    metadata = dividend_cash_entry.get("metadata", {})
    assert metadata.get("fx_rate") == "0.800000"
    assert metadata.get("fx_source") == "manual_test"


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
    assert submit.headers["location"].startswith("/dividends?msg=")

    page = client.get("/dividends")
    assert page.status_code == 200
    assert "DIVFORM" in page.text
    assert "ISA_EXEMPT" in page.text
    assert "75.25" in page.text

    db_path = _state.get_db_path()
    balances = CashLedgerService.balances(db_path)
    assert balances["BROKER"]["GBP"] == Decimal("75.25")


def test_dividends_ui_add_form_supports_usd_with_optional_gbp_and_fx(client):
    sec_id = _add_security(client, "DIVUSD")

    submit = client.post(
        "/dividends/add",
        data={
            "security_id": sec_id,
            "dividend_date": "2026-02-19",
            "original_currency": "USD",
            "amount_original_ccy": "100.00",
            "amount_gbp": "79.00",
            "fx_rate_to_gbp": "0.7900",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "notes": "usd submit",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303

    page = client.get("/dividends")
    assert page.status_code == 200
    assert "DIVUSD" in page.text
    assert "USD" in page.text
    assert "0.790000" in page.text

    db_path = _state.get_db_path()
    balances = CashLedgerService.balances(db_path)
    assert balances["BROKER"]["USD"] == Decimal("100.00")


def test_dividends_reminder_form_updates_and_clears_security_date(client):
    sec_id = _add_security(client, "DIVREMUI")

    save = client.post(
        "/dividends/reminder",
        data={
            "security_id": sec_id,
            "dividend_reminder_date": "2026-03-10",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/dividends?msg=Dividend+reminder+saved."

    summary = client.get("/portfolio/summary")
    assert summary.status_code == 200
    sec_row = next(
        row["security"]
        for row in summary.json()["securities"]
        if row["security"]["id"] == sec_id
    )
    assert sec_row["dividend_reminder_date"] == "2026-03-10"

    clear = client.post(
        "/dividends/reminder",
        data={
            "security_id": sec_id,
            "dividend_reminder_date": "",
        },
        follow_redirects=False,
    )
    assert clear.status_code == 303
    assert clear.headers["location"] == "/dividends?msg=Dividend+reminder+cleared."

    summary_after_clear = client.get("/portfolio/summary")
    assert summary_after_clear.status_code == 200
    sec_row_after_clear = next(
        row["security"]
        for row in summary_after_clear.json()["securities"]
        if row["security"]["id"] == sec_id
    )
    assert sec_row_after_clear["dividend_reminder_date"] is None


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


def test_dividends_ui_ib_style_fields_feed_cash_stats(client):
    sec_id = _add_security(client, "DIVIB")

    submit = client.post(
        "/dividends/add",
        data={
            "security_id": sec_id,
            "lot_group": "TAXABLE_ONLY",
            "dividend_date": "2026-03-01",
            "ex_date": "2026-02-10",
            "original_currency": "GBP",
            "net_amount_original_ccy": "2.86",
            "tax_withheld_original_ccy": "0.50",
            "fee_original_ccy": "0.00",
            "gross_amount_original_ccy": "3.36",
            "quantity": "2",
            "gross_rate_original_ccy": "1.68",
            "ib_code": "Po",
            "tax_treatment": "TAXABLE",
            "source": "ibkr_statement",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303

    summary = client.get("/api/dividends/summary")
    assert summary.status_code == 200
    payload = summary.json()
    row = payload["entries"][0]
    assert row["gross_amount_original_ccy"] == "3.36"
    assert row["tax_withheld_original_ccy"] == "0.50"
    assert row["net_amount_original_ccy"] == "2.86"
    assert row["lot_group"] == "TAXABLE_ONLY"
    assert payload["summary"]["actual_gross_dividends_gbp"] == "3.36"
    assert payload["summary"]["actual_withholding_tax_gbp"] == "0.50"
    assert payload["summary"]["actual_net_paid_gbp"] == "2.86"

    db_path = _state.get_db_path()
    balances = CashLedgerService.balances(db_path)
    assert balances["BROKER"]["GBP"] == Decimal("2.86")


def test_dividends_manual_entry_with_ex_date_ticks_expected_reference_row(client):
    sec_id = _add_security(client, "DIVMATCH")
    _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="2")

    with AppContext.write_session() as sess:
        DividendReferenceEventRepository(sess).upsert(
            security_id=sec_id,
            ex_dividend_date=date(2026, 2, 10),
            payment_date=date(2026, 3, 13),
            amount_original_ccy=Decimal("1.68"),
            original_currency="GBP",
            source="test_reference",
            provider_event_key=f"test_reference:{sec_id}:2026-02-10",
        )

    submit = client.post(
        "/dividends/add",
        data={
            "security_id": sec_id,
            "lot_ids": [],
            "dividend_date": "2026-03-13",
            "ex_date": "2026-02-10",
            "original_currency": "GBP",
            "amount_original_ccy": "3.36",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "cash_container": "BROKER",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303

    summary = client.get("/api/dividends/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert not any(row["ticker"] == "DIVMATCH" for row in payload["reference_events"])
    assert payload["reference_summary"]["recorded_count"] == 1
    assert payload["reference_summary"]["awaiting_count"] == 0

    page = client.get("/dividends")
    assert page.status_code == 200
    assert "No unmatched reference dividend events available." in page.text
    assert "1 already confirmed." in page.text


def test_dividends_ui_add_allows_cash_container_override(client):
    sec_id = _add_security(client, "DIVISA")

    submit = client.post(
        "/dividends/add",
        data={
            "security_id": sec_id,
            "dividend_date": "2026-03-05",
            "amount_gbp": "40.00",
            "tax_treatment": "ISA_EXEMPT",
            "source": "manual",
            "cash_container": "ISA",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303

    db_path = _state.get_db_path()
    balances = CashLedgerService.balances(db_path)
    assert balances["ISA"]["GBP"] == Decimal("40.00")


def test_dividends_ui_add_allows_lot_first_submission(client):
    sec_id = _add_security(client, "DIVLOT")
    lot_one = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="1")
    lot_two = _add_lot(client, security_id=sec_id, scheme_type="ESPP", quantity="0.5")

    submit = client.post(
        "/dividends/add",
        data={
            "lot_ids": [lot_one, lot_two],
            "dividend_date": "2026-03-05",
            "net_amount_original_ccy": "12.34",
            "tax_treatment": "TAXABLE",
            "source": "manual",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303

    summary = client.get("/api/dividends/summary")
    assert summary.status_code == 200
    payload = summary.json()
    row = payload["entries"][0]
    assert row["ticker"] == "DIVLOT"
    assert row["lot_group"] == "TAXABLE_ONLY"
    assert row["net_amount_original_ccy"] == "12.34"
    assert row["original_currency"] == "GBP"

    db_path = _state.get_db_path()
    balances = CashLedgerService.balances(db_path)
    assert balances["BROKER"]["GBP"] == Decimal("12.34")


def test_dividends_page_prefills_from_lot_query_context(client):
    sec_id = _add_security(client, "DIVPREFILL")
    lot_one = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="1.25")
    lot_two = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="2.75")

    page = client.get(f"/dividends?lot_ids={lot_one}&lot_ids={lot_two}")
    assert page.status_code == 200
    assert lot_one in page.text
    assert lot_two in page.text
    assert "Selected lots" in page.text
    assert "Resolved group" in page.text
    assert "4.00 sh" in page.text


def test_dividends_page_can_prefill_add_form_from_expected_dividend_row(client):
    sec_id = _add_security(client, "DIVEXPECT")
    lot_one = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="1")
    lot_two = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="2")

    with AppContext.write_session() as sess:
        DividendReferenceEventRepository(sess).upsert(
            security_id=sec_id,
            ex_dividend_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 15),
            amount_original_ccy=Decimal("1.50"),
            original_currency="GBP",
            source="test_reference",
            provider_event_key=f"test_reference:{sec_id}:2026-03-01",
        )

    page = client.get(
        "/dividends?"
        f"prefill_security_id={sec_id}&"
        "prefill_dividend_date=2026-03-15&"
        "prefill_original_currency=GBP&"
        "prefill_amount_original_ccy=4.50&"
        "prefill_ex_date=2026-03-01&"
        "prefill_holding_scope=BROKERAGE&"
        "prefill_quantity=3"
    )
    assert page.status_code == 200
    assert "Prefilled from an expected dividend row" in page.text
    assert 'value="2026-03-15"' in page.text
    assert 'value="4.50"' in page.text
    assert 'value="2026-03-01"' in page.text
    assert "Selected lots" in page.text
    assert "3 sh" in page.text
    assert lot_one in page.text
    assert lot_two in page.text

    listing = client.get("/dividends")
    assert listing.status_code == 200
    assert "Add Dividend" in listing.text
    assert "Auto-selects eligible" in listing.text


def test_dividends_lot_prefill_does_not_override_user_selected_currency(client):
    sec_id = _add_security(client, "DIVCURR")
    lot_id = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="1")

    submit = client.post(
        "/dividends/add",
        data={
            "security_id": sec_id,
            "lot_ids": [lot_id],
            "dividend_date": "2026-03-13",
            "original_currency": "GBP",
            "amount_original_ccy": "2.50",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "cash_container": "BROKER",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303

    summary = client.get("/api/dividends/summary")
    assert summary.status_code == 200
    row = next(r for r in summary.json()["entries"] if r["ticker"] == "DIVCURR")
    assert row["original_currency"] == "GBP"


def test_dividends_backfill_cash_posts_missing_entries_once(client):
    sec_id = _add_security(client, "DIVBACK")

    first = client.post(
        "/api/dividends/entries",
        json={
            "security_id": sec_id,
            "dividend_date": "2026-01-10",
            "amount_gbp": "10.00",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "cash_container": "NONE",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/dividends/entries",
        json={
            "security_id": sec_id,
            "dividend_date": "2026-01-20",
            "amount_gbp": "15.00",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "cash_container": "NONE",
        },
    )
    assert second.status_code == 201, second.text

    run_backfill = client.post(
        "/dividends/backfill-cash",
        data={"cash_container": "BROKER"},
        follow_redirects=False,
    )
    assert run_backfill.status_code == 303

    db_path = _state.get_db_path()
    balances = CashLedgerService.balances(db_path)
    assert balances["BROKER"]["GBP"] == Decimal("25.00")

    rerun_backfill = client.post(
        "/dividends/backfill-cash",
        data={"cash_container": "BROKER"},
        follow_redirects=False,
    )
    assert rerun_backfill.status_code == 303

    balances_after_rerun = CashLedgerService.balances(db_path)
    assert balances_after_rerun["BROKER"]["GBP"] == Decimal("25.00")


def test_dividends_relink_existing_entry_to_lots(client):
    sec_id = _add_security(client, "DIVLINK")
    lot_one = _add_lot(client, security_id=sec_id, scheme_type="BROKERAGE", quantity="1")
    lot_two = _add_lot(client, security_id=sec_id, scheme_type="ESPP", quantity="1")

    created = client.post(
        "/api/dividends/entries",
        json={
            "security_id": sec_id,
            "dividend_date": "2026-02-15",
            "amount_gbp": "20.00",
            "tax_treatment": "TAXABLE",
            "source": "manual",
            "cash_container": "NONE",
        },
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]

    relink = client.post(
        "/dividends/relink",
        data={
            "entry_id": entry_id,
            "lot_ids": [lot_one, lot_two],
        },
        follow_redirects=False,
    )
    assert relink.status_code == 303

    summary = client.get("/api/dividends/summary")
    assert summary.status_code == 200
    payload = summary.json()
    row = next(item for item in payload["entries"] if item["id"] == entry_id)
    assert row["has_lot_links"] is True
    assert row["lot_link_count"] == 2
    assert row["lot_group"] == "TAXABLE_ONLY"
