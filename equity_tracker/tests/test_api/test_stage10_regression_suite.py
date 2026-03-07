from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.models import EmploymentTaxEvent, LotDisposal, Transaction
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Stage10 Plc",
            "currency": currency,
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
    acquisition_date: str = "2025-01-15",
    quantity: str = "10",
    acquisition_price_gbp: str = "10.00",
    true_cost_per_share_gbp: str | None = None,
    tax_year: str = "2024-25",
) -> dict:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": acquisition_date,
            "quantity": quantity,
            "acquisition_price_gbp": acquisition_price_gbp,
            "true_cost_per_share_gbp": true_cost_per_share_gbp or acquisition_price_gbp,
            "tax_year": tax_year,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upsert_price(
    security_id: str,
    *,
    price_date: date,
    close_price_original_ccy: str,
    close_price_gbp: str,
    currency: str = "GBP",
    source: str = "yfinance:test-stage10-regression",
) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_price_original_ccy,
            close_price_gbp=close_price_gbp,
            currency=currency,
            source=source,
        )


def _save_settings(
    *,
    default_tax_year: str = "2025-26",
    gross_income: str = "120000",
    other_income: str = "0",
    price_stale_after_days: int = 1,
    fx_stale_after_minutes: int = 10,
) -> None:
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.default_tax_year = default_tax_year
    settings.default_gross_income = Decimal(gross_income)
    settings.default_other_income = Decimal(other_income)
    settings.price_stale_after_days = price_stale_after_days
    settings.fx_stale_after_minutes = fx_stale_after_minutes
    settings.save()


def _record_disposal_and_tax_event(
    *,
    security_id: str,
    lot_id: str,
    transaction_date: date,
    quantity: str = "2",
    price_per_share_gbp: str = "15.00",
    broker_fees_gbp: str = "1.50",
    estimated_tax_gbp: str = "12.34",
) -> None:
    qty = Decimal(quantity)
    px = Decimal(price_per_share_gbp)
    proceeds = qty * px
    cost_basis = qty * Decimal("10.00")

    with AppContext.write_session() as sess:
        tx = Transaction(
            security_id=security_id,
            transaction_type="DISPOSAL",
            transaction_date=transaction_date,
            quantity=quantity,
            price_per_share_gbp=price_per_share_gbp,
            total_proceeds_gbp=f"{proceeds:.2f}",
            broker_fees_gbp=broker_fees_gbp,
            notes="Seeded Stage-10 disposal",
            is_reversal=False,
        )
        sess.add(tx)
        sess.flush()

        sess.add(
            LotDisposal(
                transaction_id=tx.id,
                lot_id=lot_id,
                quantity_allocated=quantity,
                cost_basis_gbp=f"{cost_basis:.2f}",
                true_cost_gbp=f"{cost_basis:.2f}",
                proceeds_gbp=f"{proceeds:.2f}",
                realised_gain_gbp=f"{(proceeds - cost_basis):.2f}",
                realised_gain_economic_gbp=f"{(proceeds - cost_basis):.2f}",
            )
        )
        sess.add(
            EmploymentTaxEvent(
                lot_id=lot_id,
                security_id=security_id,
                event_type="TRANSFER_EMPLOYMENT_TAX",
                event_date=transaction_date - timedelta(days=2),
                estimated_tax_gbp=estimated_tax_gbp,
                estimation_notes="Seeded persisted employment tax event.",
                source="test-stage10-regression",
            )
        )


def test_insights_page_lists_every_stage10_surface_and_action_link(client):
    resp = client.get("/insights")
    assert resp.status_code == 200

    text = resp.text
    expected_links = (
        ("Capital Efficiency", "/capital-efficiency", "/fee-drag"),
        ("Employment Exit", "/employment-exit", "/scenario-lab"),
        ("ISA Efficiency", "/isa-efficiency", "/cash"),
        ("Fee Drag", "/fee-drag", "/sell-plan"),
        ("Data Quality", "/data-quality", "/settings"),
        ("Employment Tax Events", "/employment-tax-events", "/tax-plan"),
        ("Reconcile", "/reconcile", "/reconcile?lookback_days=30#trace-drift-decomposition"),
        ("Price/FX Basis Timeline", "/basis-timeline", "/history"),
        ("Pension", "/pension", "/pension#pension-ledger"),
    )
    for label, href, action_href in expected_links:
        assert label in text
        assert f'href="{href}"' in text
        assert f'href="{action_href}"' in text


def test_employment_exit_query_state_round_trips_in_api_and_page(client):
    security_id = _add_security(client, ticker="T87EXIT")
    _add_lot(
        client,
        security_id=security_id,
        acquisition_date="2025-01-15",
        quantity="10",
        acquisition_price_gbp="10.00",
        tax_year="2024-25",
    )
    _upsert_price(
        security_id,
        price_date=date.today(),
        close_price_original_ccy="100.00",
        close_price_gbp="100.00",
    )

    resp = client.get(
        "/api/strategic/employment-exit",
        params={"exit_date": "2026-02-01", "price_shock_pct": "-12.5"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_date"] == "2026-02-01"
    assert body["price_shock_pct"] == "-12.50"
    assert body["rows"][0]["ticker"] == "T87EXIT"
    assert body["rows"][0]["exit_price_used_gbp"] == "87.50"

    page = client.get(
        "/employment-exit",
        params={"exit_date": "2026-02-01", "price_shock_pct": "-12.5"},
    )
    assert page.status_code == 200
    text = page.text
    assert 'value="2026-02-01"' in text
    assert 'value="-12.5"' in text
    assert "87.50%" not in text
    assert "-12.50%" in text


def test_isa_efficiency_selected_tax_year_changes_contribution_window(client):
    _save_settings(default_tax_year="2025-26")

    isa_security_id = _add_security(client, ticker="T87ISA")
    _add_lot(
        client,
        security_id=isa_security_id,
        scheme_type="ISA",
        acquisition_date="2025-03-15",
        quantity="2",
        acquisition_price_gbp="50.00",
        tax_year="2024-25",
    )
    _upsert_price(
        isa_security_id,
        price_date=date.today(),
        close_price_original_ccy="60.00",
        close_price_gbp="60.00",
    )

    taxable_security_id = _add_security(client, ticker="T87TAX")
    _add_lot(
        client,
        security_id=taxable_security_id,
        scheme_type="BROKERAGE",
        acquisition_date="2025-07-01",
        quantity="3",
        acquisition_price_gbp="100.00",
        tax_year="2025-26",
    )
    _upsert_price(
        taxable_security_id,
        price_date=date.today(),
        close_price_original_ccy="120.00",
        close_price_gbp="120.00",
    )

    default_payload = client.get("/api/strategic/isa-efficiency").json()
    selected_payload = client.get("/api/strategic/isa-efficiency?tax_year=2024-25").json()

    assert default_payload["active_tax_year"] == "2025-26"
    assert default_payload["estimated_isa_contributions_gbp"] == "0.00"
    assert selected_payload["active_tax_year"] == "2024-25"
    assert selected_payload["estimated_isa_contributions_gbp"] == "100.00"
    assert selected_payload["potential_shelterable_today_gbp"] == "360.00"

    page = client.get("/isa-efficiency?tax_year=2024-25")
    assert page.status_code == 200
    text = page.text
    assert "Active Tax Year" in text
    assert "2024-25" in text
    assert "&pound;100.00" in text


def test_reconcile_lookback_query_changes_drift_window_in_api_and_page(client):
    security_id = _add_security(client, ticker="T87RECON")
    _add_lot(client, security_id=security_id, quantity="5")

    today = date.today()
    _upsert_price(
        security_id,
        price_date=today - timedelta(days=90),
        close_price_original_ccy="10.00",
        close_price_gbp="10.00",
    )
    _upsert_price(
        security_id,
        price_date=today - timedelta(days=20),
        close_price_original_ccy="12.00",
        close_price_gbp="12.00",
    )
    _upsert_price(
        security_id,
        price_date=today,
        close_price_original_ccy="15.00",
        close_price_gbp="15.00",
    )

    short_payload = client.get("/api/strategic/reconcile?lookback_days=7").json()
    long_payload = client.get("/api/strategic/reconcile?lookback_days=60").json()

    assert short_payload["drift_panel"]["has_data"] is True
    assert short_payload["drift_panel"]["lookback_days"] == 7
    assert short_payload["drift_panel"]["prior_date"] == (today - timedelta(days=20)).isoformat()
    assert long_payload["drift_panel"]["lookback_days"] == 60
    assert long_payload["drift_panel"]["prior_date"] == (today - timedelta(days=90)).isoformat()

    page = client.get("/reconcile?lookback_days=7")
    assert page.status_code == 200
    text = page.text
    assert 'value="7"' in text
    assert f"{(today - timedelta(days=20)).isoformat()} to {today.isoformat()}" in text


def test_basis_timeline_lookback_filters_rows_in_api_and_page(client):
    security_id = _add_security(client, ticker="T87BASIS", currency="USD")
    _add_lot(client, security_id=security_id, quantity="10")

    today = date.today()
    _upsert_price(
        security_id,
        price_date=today - timedelta(days=40),
        close_price_original_ccy="100.00",
        close_price_gbp="80.00",
        currency="USD",
    )
    _upsert_price(
        security_id,
        price_date=today - timedelta(days=10),
        close_price_original_ccy="120.00",
        close_price_gbp="96.00",
        currency="USD",
    )
    _upsert_price(
        security_id,
        price_date=today,
        close_price_original_ccy="125.00",
        close_price_gbp="105.00",
        currency="USD",
    )

    short_payload = client.get("/api/strategic/basis-timeline?lookback_days=30").json()
    long_payload = client.get("/api/strategic/basis-timeline?lookback_days=120").json()

    assert short_payload["lookback_days"] == 30
    assert [row["date"] for row in short_payload["date_rows"]] == [today.isoformat()]
    assert [row["date"] for row in short_payload["security_rows"]] == [today.isoformat()]

    assert long_payload["lookback_days"] == 120
    assert [row["date"] for row in long_payload["date_rows"]] == [
        today.isoformat(),
        (today - timedelta(days=10)).isoformat(),
    ]

    page = client.get("/basis-timeline?lookback_days=120")
    assert page.status_code == 200
    text = page.text
    assert 'value="120"' in text
    assert today.isoformat() in text
    assert (today - timedelta(days=10)).isoformat() in text


def test_stage10_seeded_surfaces_render_non_empty_rows_and_metrics(client):
    _save_settings(default_tax_year="2025-26", gross_income="150000")

    today = date.today()

    flow_security_id = _add_security(client, ticker="T87FLOW")
    flow_lot = _add_lot(
        client,
        security_id=flow_security_id,
        quantity="10",
        acquisition_price_gbp="10.00",
        tax_year="2024-25",
    )
    _upsert_price(
        flow_security_id,
        price_date=today - timedelta(days=30),
        close_price_original_ccy="12.00",
        close_price_gbp="12.00",
    )
    _upsert_price(
        flow_security_id,
        price_date=today,
        close_price_original_ccy="15.00",
        close_price_gbp="15.00",
    )
    _record_disposal_and_tax_event(
        security_id=flow_security_id,
        lot_id=flow_lot["id"],
        transaction_date=today - timedelta(days=5),
    )

    stale_security_id = _add_security(client, ticker="T87OLD", currency="USD")
    _add_lot(client, security_id=stale_security_id, quantity="4")
    _upsert_price(
        stale_security_id,
        price_date=today - timedelta(days=45),
        close_price_original_ccy="50.00",
        close_price_gbp="40.00",
        currency="USD",
    )

    missing_security_id = _add_security(client, ticker="T87MISS")
    _add_lot(client, security_id=missing_security_id, quantity="3")

    capital_payload = client.get("/api/strategic/capital-efficiency").json()
    assert capital_payload["realized_fee_drag_total_gbp"] == "1.50"
    assert any(
        row["label"] == "Employment Tax Events (Recorded)" and row["amount_gbp"] == "12.34"
        for row in capital_payload["components"]
    )

    capital_page = client.get("/capital-efficiency")
    assert capital_page.status_code == 200
    assert "Employment Tax Events (Recorded)" in capital_page.text
    assert "Fee Realization Context" in capital_page.text

    fee_payload = client.get("/api/strategic/fee-drag").json()
    assert fee_payload["totals"]["broker_fees_gbp"] == "1.50"
    assert fee_payload["transaction_rows"][0]["ticker"] == "T87FLOW"
    assert fee_payload["tax_year_rows"][0]["transaction_count"] == 1

    fee_page = client.get("/fee-drag")
    assert fee_page.status_code == 200
    assert "T87FLOW" in fee_page.text
    assert "Transaction Ledger" in fee_page.text

    quality_payload = client.get("/api/strategic/data-quality").json()
    assert quality_payload["summary"]["missing_price_security_count"] >= 1
    assert quality_payload["summary"]["missing_price_lot_count"] >= 1
    assert quality_payload["summary"]["stale_price_security_count"] >= 1

    quality_page = client.get("/data-quality")
    assert quality_page.status_code == 200
    assert "Impact by Surface" in quality_page.text
    assert "Tax Plan Freshness Cross-Check" in quality_page.text

    event_payload = client.get("/api/strategic/employment-tax-events").json()
    sources = {row["event_source"] for row in event_payload["event_rows"]}
    assert {"PERSISTED", "DERIVED_DISPOSAL"} <= sources
    assert any(row["ticker"] == "T87FLOW" for row in event_payload["event_rows"])

    event_page = client.get("/employment-tax-events")
    assert event_page.status_code == 200
    assert "PERSISTED" in event_page.text
    assert "DERIVED_DISPOSAL" in event_page.text
    assert "T87FLOW" in event_page.text
