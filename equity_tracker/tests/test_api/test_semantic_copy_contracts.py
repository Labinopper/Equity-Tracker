from __future__ import annotations

from datetime import date

import pytest

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Copy Contract Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, *, security_id: str, quantity: str = "5") -> None:
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


def _upsert_price(security_id: str, *, currency: str = "GBP") -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency=currency,
            source="test-semantic-copy-contracts",
        )


def _seed_priced_position(client, *, ticker: str) -> None:
    security_id = _add_security(client, ticker=ticker)
    _add_lot(client, security_id=security_id)
    _upsert_price(security_id)


def test_net_value_screen_uses_documented_name_and_non_actionable_copy(client):
    _seed_priced_position(client, ticker="T86NET")

    resp = client.get("/net-value")
    assert resp.status_code == 200
    text = resp.text

    assert "<title>Net Value - Equity Tracker</title>" in text
    assert "<h1>Net Value</h1>" in text
    assert "Net Value info" in text
    assert "Sell-All Breakdown" not in text
    assert "Hypothetical net value if all active lots were sold today. Not actionable liquidity." in text
    assert "Deployable Today (From Capital Stack)" in text
    assert "Net Value vs Deployable Today Delta" in text


@pytest.mark.parametrize(
    ("path", "expected_scope_phrase"),
    [
        ("/", "Employment tax is estimated on sellable lots only"),
        ("/net-value", "Sell-all framing is hypothetical and includes locked/forfeitable value"),
        ("/tax-plan", "Assumption-quality labels are explicit per projection block"),
    ],
)
def test_decision_pages_keep_documented_model_scope_and_trace_contracts(
    client,
    path,
    expected_scope_phrase,
):
    _seed_priced_position(client, ticker=f"T86{path.strip('/').replace('-', '').upper() or 'PORT'}")

    resp = client.get(path)
    assert resp.status_code == 200, path
    text = resp.text

    assert "Model Scope" in text
    assert "Inputs" in text
    assert "Assumptions" in text
    assert "Exclusions" in text
    assert expected_scope_phrase in text
    assert 'href="/reconcile#trace-contributing-lots"' in text
    assert 'href="/reconcile#trace-audit-mutations"' in text


def test_glossary_anchors_and_decision_page_links_remain_stable(client):
    _seed_priced_position(client, ticker="T86GLOS")

    glossary = client.get("/glossary")
    assert glossary.status_code == 200
    glossary_text = glossary.text

    for anchor_id in (
        "true-cost-acquisition",
        "dividend-adjusted-capital-at-risk",
        "cost-basis",
        "employment-tax",
        "aea",
        "ani-adjusted-net-income",
        "est-net-liquidity-sellable",
        "hypothetical-full-liquidation",
        "locked-capital",
        "forfeitable-capital",
    ):
        assert f'id="{anchor_id}"' in glossary_text

    page_links = {
        "/": (
            'href="/glossary#est-net-liquidity-sellable"',
            'href="/glossary#employment-tax"',
            'href="/glossary#locked-capital"',
            'href="/glossary#forfeitable-capital"',
        ),
        "/net-value": (
            'href="/glossary#hypothetical-full-liquidation"',
            'href="/glossary#employment-tax"',
            'href="/glossary#cost-basis"',
        ),
        "/tax-plan": (
            'href="/glossary#aea"',
            'href="/glossary#ani-adjusted-net-income"',
        ),
    }

    for path, required_links in page_links.items():
        resp = client.get(path)
        assert resp.status_code == 200, path
        for required_link in required_links:
            assert required_link in resp.text, f"{path} missing glossary link {required_link}"
