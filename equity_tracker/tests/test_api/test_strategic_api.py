"""API coverage for strategic Stage-10 surfaces."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


@pytest.mark.parametrize(
    ("path", "required_keys"),
    [
        ("/api/strategic/capital-efficiency", {"components", "total_structural_drag_gbp", "model_scope", "notes"}),
        ("/api/strategic/employment-exit", {"rows", "totals", "exit_date", "model_scope", "notes"}),
        ("/api/strategic/isa-efficiency", {"active_tax_year", "isa_ratio_pct", "potential_shelterable_today_gbp", "notes"}),
        ("/api/strategic/fee-drag", {"totals", "tax_year_rows", "transaction_rows", "notes"}),
        ("/api/strategic/data-quality", {"summary", "impact_rows", "tax_plan_freshness", "notes"}),
        ("/api/strategic/employment-tax-events", {"tax_year_rows", "event_rows", "notes"}),
        ("/api/strategic/reconcile", {"components", "trace_links", "drift_panel", "notes"}),
        ("/api/strategic/basis-timeline", {"date_rows", "security_rows", "lookback_days", "notes"}),
        ("/api/strategic/pension", {"current_pension_value_gbp", "recorded_inputs_gbp", "scenario_rows", "ledger_rows", "trace_links", "model_scope", "notes"}),
        ("/api/strategic/weekly-review", {"active_review", "step_rows", "recent_reviews", "summary", "model_scope", "notes"}),
        ("/api/strategic/notification-digest", {"summary", "entries", "trace_links", "model_scope", "notes"}),
        ("/api/strategic/allocation-planner", {"planner_config", "security_rows", "trim_plan", "candidate_rows", "model_scope", "notes"}),
    ],
)
def test_strategic_api_endpoints_semantic_matrix(client, path, required_keys):
    if path == "/api/strategic/basis-timeline":
        sec = _add_security(client, "SBASIS", currency="USD")
        _add_lot(client, sec["id"])
        _upsert_price(
            sec["id"],
            price_date=date.today() - timedelta(days=1),
            close_original_ccy="100.00",
            close_gbp="80.00",
            currency="USD",
        )
        _upsert_price(
            sec["id"],
            price_date=date.today(),
            close_original_ccy="110.00",
            close_gbp="92.00",
            currency="USD",
        )

    resp = client.get(path)
    assert resp.status_code == 200, path

    body = resp.json()
    for key in required_keys:
        assert key in body, f"{path} missing key: {key}"

    if path == "/api/strategic/capital-efficiency":
        labels = {row["label"] for row in body["components"]}
        assert "Employment Tax (Hypothetical)" in labels
        assert "CGT (Hypothetical)" in labels
        assert body["model_scope"]["assumptions"]
        assert "Structural drag is deterministic" in body["notes"][0]
    elif path == "/api/strategic/employment-exit":
        assert body["price_shock_pct"] == "0.00"
        assert body["model_scope"]["inputs"]
        assert "No market forecast" in body["notes"][-1]
    elif path == "/api/strategic/isa-efficiency":
        assert body["notes"][0].startswith("Headroom uses ISA-lot acquisition values")
        assert body["tax_year_start"] <= body["tax_year_end"]
    elif path == "/api/strategic/fee-drag":
        assert body["totals"]["broker_fees_gbp"] is not None
        assert "committed disposal transactions only" in body["notes"][0]
    elif path == "/api/strategic/data-quality":
        surfaces = {row["surface"] for row in body["impact_rows"]}
        assert surfaces == {
            "Portfolio / Net Value",
            "Risk / Analytics",
            "Tax Plan",
            "Scenario / Simulate",
        }
        assert "No inferred backfill" in body["notes"][-1]
    elif path == "/api/strategic/employment-tax-events":
        assert "explicit source tags" in body["notes"][0]
        assert isinstance(body["event_rows"], list)
    elif path == "/api/strategic/reconcile":
        steps = [row["step"] for row in body["components"]]
        assert steps[0] == "Portfolio Gross Market Value"
        assert steps[-1] == "Reconciled Deployable Capital"
        assert body["trace_links"]["contributing_lots"].endswith("#trace-contributing-lots")
    elif path == "/api/strategic/basis-timeline":
        assert body["lookback_days"] == 365
        assert "native-price and FX contribution components" in body["notes"][-1]
    elif path == "/api/strategic/pension":
        assert body["trace_links"]["ledger"].endswith("#pension-ledger")
        assert body["trace_links"]["assumptions"].endswith("#pension-assumptions")
        assert body["model_scope"]["assumptions"]
        assert body["notes"][0].startswith("Projections use fixed monthly contributions")
    elif path == "/api/strategic/weekly-review":
        assert body["active_review"]["status"] == "ACTIVE"
        assert len(body["step_rows"]) == 4
        assert body["summary"]["total_steps"] == 4
    elif path == "/api/strategic/notification-digest":
        assert body["trace_links"]["alert_center"].startswith("/risk")
        assert body["model_scope"]["inputs"]
        assert body["notes"][0].startswith("Digest entries are generated exclusively")
    elif path == "/api/strategic/allocation-planner":
        assert body["planner_config"]["target_max_pct"]
        assert "consumed_lot_rows" in body["trim_plan"]
        assert body["notes"][0].startswith("Planner outputs are non-advisory")


@pytest.mark.parametrize(
    ("path", "marker", "semantic_markers"),
    [
        ("/insights", "Insights", ("Strategic Pages", "Quick Action", "Trend Context")),
        ("/capital-efficiency", "Capital Efficiency", ("Drag Components", "Fee Realization Context", 'href="/fee-drag"')),
        ("/employment-exit", "Employment Exit", ("Scenario Inputs", "Per-Security Exit View", 'href="/scenario-lab"')),
        ("/isa-efficiency", "ISA Efficiency", ("Headroom", "Tax-Year Context", 'href="/cash"')),
        ("/fee-drag", "Fee Drag", ("By Tax Year", "Transaction Ledger", 'href="/sell-plan"')),
        ("/data-quality", "Data Quality", ("Impact by Surface", "Tax Plan Freshness Cross-Check", 'href="/settings"')),
        ("/employment-tax-events", "Employment Tax Events", ("Tax-Year Totals", "Event Ledger", 'href="/tax-plan"')),
        ("/reconcile", "Cross-Page Reconciliation", ("Reconciliation Path", "Trace: Contributing Lots", "Trace: Recent Audit Mutations")),
        ("/basis-timeline", "Price/FX Basis Timeline", ("Aggregate By Date", "Security Basis Rows", "Native Move", "FX Move")),
        ("/pension", "Pension", ("Pension Assumptions", "Scenario Timeline", "Contribution Ledger")),
        ("/weekly-review", "Weekly Review", ("Review Steps", "Recent Reviews", "Review Progress")),
        ("/notification-digest", "Notification Digest", ("Digest Entries", "Threshold Breaches", "Trace Links")),
        ("/allocation-planner", "Allocation Planner", ("Planner Settings", "Candidate Universe", "Before vs After")),
    ],
)
def test_strategic_pages_render_semantic_matrix(client, path, marker, semantic_markers):
    resp = client.get(path)
    assert resp.status_code == 200, path

    text = resp.text
    assert marker in text
    for semantic_marker in semantic_markers:
        assert semantic_marker in text, f"{path} missing semantic marker: {semantic_marker}"


def _add_security(client, ticker: str, currency: str = "GBP") -> dict:
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


def _upsert_price(
    security_id: str,
    *,
    price_date: date,
    close_original_ccy: str,
    close_gbp: str,
    currency: str,
) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_original_ccy,
            close_price_gbp=close_gbp,
            currency=currency,
            source="test-strategic-api",
        )


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


def test_basis_timeline_lookback_validation(client):
    assert client.get("/api/strategic/basis-timeline?lookback_days=29").status_code == 422
    assert client.get("/api/strategic/basis-timeline?lookback_days=1826").status_code == 422


def test_reconcile_lookback_validation(client):
    assert client.get("/api/strategic/reconcile?lookback_days=6").status_code == 422
    assert client.get("/api/strategic/reconcile?lookback_days=366").status_code == 422
