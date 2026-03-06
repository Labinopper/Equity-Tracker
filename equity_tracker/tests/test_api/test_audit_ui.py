"""UI-level checks for /audit trace filters."""

from __future__ import annotations


def test_audit_ui_accepts_table_and_record_filters(client):
    sec_resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": "AUDUI",
            "name": "Audit UI Corp",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert sec_resp.status_code == 201
    security_id = sec_resp.json()["id"]

    resp = client.get(f"/audit?table_name=securities&record_id={security_id}")
    assert resp.status_code == 200
    text = resp.text
    assert "Audit Log" in text
    assert security_id[:8] in text
    assert "Record ID:" in text
