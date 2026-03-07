from __future__ import annotations

import html
import re
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Trace Flow Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, *, quantity: str = "6") -> dict:
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
            source="yfinance:test-trace-workflow-friction",
        )


def _save_settings() -> None:
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.default_gross_income = Decimal("100000")
    settings.default_other_income = Decimal("0")
    settings.concentration_top_holding_alert_pct = Decimal("100")
    settings.concentration_employer_alert_pct = Decimal("100")
    settings.save()


def _extract_first_href(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    assert match is not None
    return html.unescape(match.group(1))


@pytest.mark.parametrize(
    ("source_path", "source_marker"),
    [
        ("/", "Portfolio"),
        ("/net-value", "Net Value"),
        ("/tax-plan", "Tax Plan"),
    ],
)
def test_decision_trace_journeys_reach_filtered_audit_views_with_visible_context_within_three_clicks(
    client,
    source_path: str,
    source_marker: str,
):
    security_id = _add_security(client, ticker="T89FLOW")
    lot = _add_lot(client, security_id, quantity="6")
    _add_price(
        security_id,
        price_date=date.today(),
        close_price_original_ccy="18.00",
        close_price_gbp="18.00",
    )
    _save_settings()

    clicks = 0
    source_page = client.get(source_path)
    assert source_page.status_code == 200
    source_text = source_page.text
    assert source_marker in source_text
    assert "Trace key totals:" in source_text

    reconcile_url = _extract_first_href(
        source_text,
        r'href="(/reconcile#trace-contributing-lots)"',
    )
    clicks += 1
    assert clicks <= 3

    reconcile_page = client.get(reconcile_url)
    assert reconcile_page.status_code == 200
    reconcile_text = reconcile_page.text
    assert "Cross-Page Reconciliation" in reconcile_text
    assert "Trace paths:" in reconcile_text
    assert 'id="trace-contributing-lots"' in reconcile_text
    assert 'id="trace-audit-mutations"' in reconcile_text
    assert "T89FLOW" in reconcile_text
    assert "Open lot audit" in reconcile_text
    assert "Open mutation" in reconcile_text

    lot_audit_url = _extract_first_href(
        reconcile_text,
        r'href="(/audit\?table_name=lots(?:&amp;|&)record_id=[^"]+)">Open lot audit<',
    )
    clicks += 1
    assert clicks <= 3

    lot_audit_page = client.get(lot_audit_url)
    assert lot_audit_page.status_code == 200
    lot_audit_text = lot_audit_page.text
    assert "Audit Log" in lot_audit_text
    assert "Showing" in lot_audit_text
    assert "for table <strong>lots</strong>" in lot_audit_text
    assert f"for record <code>{lot['id']}</code>" in lot_audit_text
    assert "Clear filter" in lot_audit_text
    assert "No audit entries" not in lot_audit_text
    assert 'value="lots" selected' in lot_audit_text
    assert f'value="{lot["id"]}"' in lot_audit_text

    mutation_audit_url = _extract_first_href(
        reconcile_text,
        r'href="(/audit\?table_name=[^"]+(?:&amp;|&)record_id=[^"]+)">Open mutation<',
    )
    parsed = urlparse(mutation_audit_url)
    params = parse_qs(parsed.query)
    mutation_table = params["table_name"][0]
    mutation_record = params["record_id"][0]

    mutation_audit_page = client.get(mutation_audit_url)
    assert mutation_audit_page.status_code == 200
    mutation_audit_text = mutation_audit_page.text
    assert "Audit Log" in mutation_audit_text
    assert "Showing" in mutation_audit_text
    assert f"for table <strong>{mutation_table}</strong>" in mutation_audit_text
    assert f"for record <code>{mutation_record}</code>" in mutation_audit_text
    assert "Clear filter" in mutation_audit_text
    assert "No audit entries" not in mutation_audit_text
    assert f'value="{mutation_table}" selected' in mutation_audit_text
    assert f'value="{mutation_record}"' in mutation_audit_text
