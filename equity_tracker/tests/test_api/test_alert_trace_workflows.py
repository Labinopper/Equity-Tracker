from __future__ import annotations

import html
import re
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Alert Trace Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, *, quantity: str = "10") -> dict:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
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
            source="test-alert-trace",
        )


def _save_settings(
    *,
    employer_ticker: str = "",
    gross_income: str = "100000",
    top_threshold: str = "50",
    employer_threshold: str = "40",
) -> None:
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.employer_ticker = employer_ticker
    settings.default_gross_income = Decimal(gross_income)
    settings.default_other_income = Decimal("0")
    settings.concentration_top_holding_alert_pct = Decimal(top_threshold)
    settings.concentration_employer_alert_pct = Decimal(employer_threshold)
    settings.save()


def test_alert_thresholds_surface_across_decision_pages_and_clear_when_thresholds_change(client):
    sec_id = _add_security(client, ticker="T85ALRT")
    _add_lot(client, sec_id, quantity="10")
    _add_price(
        sec_id,
        price_date=date.today(),
        close_price_original_ccy="20.00",
        close_price_gbp="20.00",
    )
    _save_settings(
        employer_ticker="T85ALRT",
        gross_income="100000",
        top_threshold="50",
        employer_threshold="40",
    )

    for path in ("/", "/net-value", "/tax-plan"):
        page = client.get(path)
        assert page.status_code == 200, path
        text = page.text
        assert 'href="/risk#alert-center"' in text
        assert re.search(r'topbar__alert-count">\s*2\s*<', text), path
        assert "Alert Center" in text
        assert "Top-Holding Concentration Breach" in text
        assert "Employer Exposure Breach" in text
        assert 'href="/risk#concentration-guardrails"' in text

    risk = client.get("/risk")
    assert risk.status_code == 200
    assert "Alert Center" in risk.text
    assert "Top-Holding Concentration Breach" in risk.text
    assert "Employer Exposure Breach" in risk.text
    assert 'href="/risk#concentration-guardrails"' in risk.text

    _save_settings(
        employer_ticker="T85ALRT",
        gross_income="100000",
        top_threshold="100",
        employer_threshold="100",
    )

    for path in ("/", "/net-value", "/tax-plan"):
        page = client.get(path)
        assert page.status_code == 200, path
        text = page.text
        assert 'href="/risk#alert-center"' in text
        assert 'class="topbar__alert-count"' not in text
        assert 'class="alert-center-bar"' not in text

    risk = client.get("/risk")
    assert risk.status_code == 200
    assert "No active deterministic alerts." in risk.text


def test_decision_trace_links_flow_into_reconcile_and_filtered_audit_views(client):
    sec_id = _add_security(client, ticker="T85TRACE")
    lot = _add_lot(client, sec_id, quantity="4")
    _add_price(
        sec_id,
        price_date=date.today(),
        close_price_original_ccy="15.00",
        close_price_gbp="15.00",
    )
    _save_settings(
        employer_ticker="",
        gross_income="100000",
        top_threshold="100",
        employer_threshold="100",
    )

    for path in ("/", "/net-value", "/tax-plan"):
        page = client.get(path)
        assert page.status_code == 200, path
        text = page.text
        assert 'href="/reconcile#trace-contributing-lots"' in text
        assert 'href="/reconcile#trace-audit-mutations"' in text

    reconcile = client.get("/reconcile")
    assert reconcile.status_code == 200
    text = reconcile.text
    assert 'id="trace-contributing-lots"' in text
    assert 'id="trace-audit-mutations"' in text
    assert "T85TRACE" in text

    lot_audit_match = re.search(
        r'href="(/audit\?table_name=lots(?:&amp;|&)record_id=[^"]+)">Open lot audit<',
        text,
    )
    assert lot_audit_match is not None
    lot_audit_url = html.unescape(lot_audit_match.group(1))
    lot_audit = client.get(lot_audit_url)
    assert lot_audit.status_code == 200
    assert 'value="lots" selected' in lot_audit.text
    assert f'value="{lot["id"]}"' in lot_audit.text
    assert "Audit Log" in lot_audit.text

    mutation_match = re.search(
        r'href="(/audit\?table_name=[^"]+(?:&amp;|&)record_id=[^"]+)">Open mutation<',
        text,
    )
    assert mutation_match is not None
    mutation_url = html.unescape(mutation_match.group(1))
    parsed = urlparse(mutation_url)
    params = parse_qs(parsed.query)
    table_name = params["table_name"][0]
    record_id = params["record_id"][0]

    mutation_audit = client.get(mutation_url)
    assert mutation_audit.status_code == 200
    assert f'value="{table_name}" selected' in mutation_audit.text
    assert f'value="{record_id}"' in mutation_audit.text
    assert "Audit Log" in mutation_audit.text
