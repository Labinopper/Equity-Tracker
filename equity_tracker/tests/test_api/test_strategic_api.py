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
    assert "drift_panel" in body
    assert "rows" in body["drift_panel"]
    assert body["trace_links"]["contributing_lots"].endswith("#trace-contributing-lots")
    assert body["trace_links"]["audit_mutations"].endswith("#trace-audit-mutations")
    assert body["trace_links"]["drift_panel"].endswith("#trace-drift-decomposition")

    lot_rows = [row for row in body["contributing_lot_rows"] if row["lot_id"] == lot["id"]]
    assert lot_rows
    assert lot_rows[0]["audit_href"].endswith(f"/audit?table_name=lots&record_id={lot['id']}")

    assert any(row["record_id"] == sec["id"] for row in body["recent_audit_rows"])


def test_strategic_reconcile_page_renders_trace_anchors(client):
    resp = client.get("/reconcile")
    assert resp.status_code == 200
    text = resp.text
    assert 'id="trace-drift-decomposition"' in text
    assert 'id="trace-contributing-lots"' in text
    assert 'id="trace-audit-mutations"' in text


def test_strategic_api_endpoints_smoke(client):
    _ = _add_security(client, "SSTAGE")

    cases = [
        ("/api/strategic/capital-efficiency", {"components", "total_structural_drag_gbp"}),
        ("/api/strategic/employment-exit", {"rows", "totals", "exit_date"}),
        ("/api/strategic/isa-efficiency", {"active_tax_year", "isa_ratio_pct"}),
        ("/api/strategic/fee-drag", {"totals", "tax_year_rows", "transaction_rows"}),
        ("/api/strategic/data-quality", {"summary", "impact_rows", "tax_plan_freshness"}),
        ("/api/strategic/employment-tax-events", {"tax_year_rows", "event_rows"}),
        ("/api/strategic/reconcile", {"components", "trace_links"}),
        ("/api/strategic/basis-timeline", {"date_rows", "security_rows"}),
    ]

    for path, required_keys in cases:
        resp = client.get(path)
        assert resp.status_code == 200, path
        body = resp.json()
        for key in required_keys:
            assert key in body, f"{path} missing key: {key}"


def test_strategic_pages_render(client):
    pages = [
        ("/insights", "Insights"),
        ("/capital-efficiency", "Capital Efficiency"),
        ("/employment-exit", "Employment Exit"),
        ("/isa-efficiency", "ISA Efficiency"),
        ("/fee-drag", "Fee Drag"),
        ("/data-quality", "Data Quality"),
        ("/employment-tax-events", "Employment Tax Events"),
        ("/reconcile", "Cross-Page Reconciliation"),
        ("/basis-timeline", "Price/FX Basis Timeline"),
    ]

    for path, marker in pages:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert marker in resp.text

    assert "Trend Context" in client.get("/capital-efficiency").text
    assert "Action Links" in client.get("/capital-efficiency").text
    assert "Comparison Context" in client.get("/employment-exit").text
    assert "Action Links" in client.get("/employment-exit").text
    assert "Trend Context" in client.get("/isa-efficiency").text
    assert "Action Links" in client.get("/isa-efficiency").text
    assert "Trend Context" in client.get("/fee-drag").text
    assert "Action Links" in client.get("/fee-drag").text
    assert "Trend Context" in client.get("/data-quality").text
    assert "Action Links" in client.get("/data-quality").text
    assert "Trend Context" in client.get("/employment-tax-events").text
    assert "Action Links" in client.get("/employment-tax-events").text
    assert "Trend Context" in client.get("/basis-timeline").text
    assert "Action Links" in client.get("/basis-timeline").text
    assert "Trend Context" in client.get("/insights").text
    assert "Quick Action" in client.get("/insights").text


def test_basis_timeline_lookback_validation(client):
    assert client.get("/api/strategic/basis-timeline?lookback_days=29").status_code == 422
    assert client.get("/api/strategic/basis-timeline?lookback_days=1826").status_code == 422


def test_reconcile_lookback_validation(client):
    assert client.get("/api/strategic/reconcile?lookback_days=6").status_code == 422
    assert client.get("/api/strategic/reconcile?lookback_days=366").status_code == 422
