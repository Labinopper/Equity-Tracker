"""API coverage for strategic Stage-10 surfaces."""

from __future__ import annotations


def _add_security(client, ticker: str) -> dict:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Corp",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_lot(client, security_id: str) -> dict:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-02",
            "quantity": "10",
            "acquisition_price_gbp": "5.00",
            "true_cost_per_share_gbp": "5.00",
            "tax_year": "2024-25",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_strategic_reconcile_includes_trace_sections(client):
    sec = _add_security(client, "STRACE")
    lot = _add_lot(client, sec["id"])

    resp = client.get("/api/strategic/reconcile")
    assert resp.status_code == 200
    body = resp.json()

    assert "contributing_lot_rows" in body
    assert "recent_audit_rows" in body
    assert body["trace_links"]["contributing_lots"].endswith("#trace-contributing-lots")
    assert body["trace_links"]["audit_mutations"].endswith("#trace-audit-mutations")

    lot_rows = [row for row in body["contributing_lot_rows"] if row["lot_id"] == lot["id"]]
    assert lot_rows
    assert lot_rows[0]["audit_href"].endswith(f"/audit?table_name=lots&record_id={lot['id']}")

    assert any(row["record_id"] == sec["id"] for row in body["recent_audit_rows"])


def test_strategic_reconcile_page_renders_trace_anchors(client):
    resp = client.get("/reconcile")
    assert resp.status_code == 200
    text = resp.text
    assert 'id="trace-contributing-lots"' in text
    assert 'id="trace-audit-mutations"' in text
