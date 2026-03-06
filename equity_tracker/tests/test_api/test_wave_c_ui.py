from __future__ import annotations

from datetime import date
import re

from src.app_context import AppContext
from src.db.repository import PriceRepository


_GLOSSARY_ANCHORS = (
    "true-cost-acquisition",
    "dividend-adjusted-capital-at-risk",
    "cost-basis",
    "allowable-cost",
    "economic-gain",
    "employment-tax",
    "aea",
    "ani-adjusted-net-income",
    "est-net-liquidity-sellable",
    "hypothetical-full-liquidation",
    "locked-capital",
    "forfeitable-capital",
    "forfeiture-window",
)


def _glossary_row_html(page_html: str, anchor_id: str) -> str:
    pattern = re.compile(
        rf'<tr>\s*<th id="{re.escape(anchor_id)}"[^>]*>.*?</th>\s*<td>(.*?)</td>\s*</tr>',
        re.DOTALL,
    )
    match = pattern.search(page_html)
    assert match is not None, f"Missing glossary row for anchor: {anchor_id}"
    return match.group(1)


def test_glossary_anchor_rows_include_reverse_page_links(client):
    page = client.get("/glossary")
    assert page.status_code == 200
    for anchor_id in _GLOSSARY_ANCHORS:
        row_html = _glossary_row_html(page.text, anchor_id)
        assert 'href="' in row_html, f"Missing reverse page link for anchor: {anchor_id}"


def test_login_page_includes_rate_limit_help_and_recovery_checklist(client):
    page = client.get("/auth/login")
    assert page.status_code == 200
    text = page.text
    assert "Too many incorrect code entries can temporarily pause sign in" in text
    assert "wait 15 minutes, then retry with a fresh code." in text
    assert "Recovery checklist" in text


def test_locked_page_includes_recovery_checklist_and_unlock_guidance(client):
    lock_resp = client.post("/admin/lock")
    assert lock_resp.status_code == 200

    page = client.get("/")
    assert page.status_code == 503
    text = page.text
    assert "Database Locked" in text
    assert "Recovery checklist" in text
    assert "POST /admin/unlock" in text
    assert "EQUITY_DB_PATH" in text
    assert "EQUITY_DB_PASSWORD" in text


def test_portfolio_valuation_basis_marks_missing_fx_as_incomplete(client):
    sec = client.post(
        "/portfolio/securities",
        json={
            "ticker": "WAVECFX",
            "name": "Wave C FX Missing Plc",
            "currency": "USD",
            "is_manual_override": True,
        },
    )
    assert sec.status_code == 201, sec.text
    security_id = sec.json()["id"]

    lot = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": date.today().isoformat(),
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert lot.status_code == 201, lot.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy="20.00",
            close_price_gbp="15.00",
            currency="USD",
            source="ibkr",
        )

    page = client.get("/")
    assert page.status_code == 200
    assert "FX basis incomplete" in page.text
    assert "1 missing FX basis" in page.text
    assert "FX basis fresh" not in page.text
