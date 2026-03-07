from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from src.api import _state
from src.app_context import AppContext
from src.db.models import AuditLog, PortfolioGuardrailStateEvent
from src.db.repository.prices import PriceRepository
from src.services.alert_service import AlertService
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Lifecycle Plc",
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
            source="test-alert-lifecycle",
        )


def _save_settings(
    *,
    employer_ticker: str = "",
    gross_income: str = "100000",
    top_threshold: str = "50",
    employer_threshold: str = "40",
) -> AppSettings:
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.employer_ticker = employer_ticker
    settings.default_gross_income = Decimal(gross_income)
    settings.default_other_income = Decimal("0")
    settings.concentration_top_holding_alert_pct = Decimal(top_threshold)
    settings.concentration_employer_alert_pct = Decimal(employer_threshold)
    settings.save()
    return settings


def _get_alert_center() -> dict:
    db_path = _state.get_db_path()
    assert db_path is not None
    settings = AppSettings.load(db_path)
    return AlertService.get_alert_center(settings=settings, db_path=db_path)


def test_risk_alert_snooze_persists_across_pages_and_reactivate_restores(client):
    sec_id = _add_security(client, ticker="T57RISK")
    _add_lot(client, sec_id, quantity="10")
    _add_price(
        sec_id,
        price_date=date.today(),
        close_price_original_ccy="20.00",
        close_price_gbp="20.00",
    )
    _save_settings(
        employer_ticker="T57RISK",
        gross_income="100000",
        top_threshold="50",
        employer_threshold="40",
    )

    risk = client.get("/risk")
    assert risk.status_code == 200
    assert 'action="/risk/alerts/lifecycle"' in risk.text
    assert "Snooze 7d" in risk.text

    alert_center = _get_alert_center()
    top_alert = next(
        alert for alert in alert_center["alerts"] if alert["id"] == "concentration_top_holding"
    )

    snooze = client.post(
        "/risk/alerts/lifecycle",
        data={
            "lifecycle_id": top_alert["lifecycle_id"],
            "condition_hash": top_alert["condition_hash"],
            "action": "snooze",
        },
        follow_redirects=True,
    )
    assert snooze.status_code == 200
    assert "Snoozed alert state saved." in snooze.text
    assert "Reactivate" in snooze.text
    assert "Snoozed" in snooze.text

    net_value = client.get("/net-value")
    assert net_value.status_code == 200
    assert re.search(r'topbar__alert-count">\s*1\s*<', net_value.text)
    assert "Top-Holding Concentration Breach" not in net_value.text

    with AppContext.read_session() as sess:
        latest_state = sess.scalar(
            select(PortfolioGuardrailStateEvent)
            .where(
                PortfolioGuardrailStateEvent.guardrail_id
                == "alert_center:concentration_top_holding"
            )
            .order_by(PortfolioGuardrailStateEvent.changed_at.desc())
            .limit(1)
        )
        audit_rows = list(
            sess.scalars(
                select(AuditLog)
                .where(AuditLog.table_name == "portfolio_guardrail_state_events")
                .order_by(AuditLog.changed_at.asc())
            ).all()
        )
    assert latest_state is not None
    assert latest_state.state == "SNOOZED"
    assert any(
        '"guardrail_id": "alert_center:concentration_top_holding"'
        in (row.new_values_json or "")
        and '"state": "SNOOZED"' in (row.new_values_json or "")
        for row in audit_rows
    )

    reactivate = client.post(
        "/risk/alerts/lifecycle",
        data={
            "lifecycle_id": top_alert["lifecycle_id"],
            "condition_hash": top_alert["condition_hash"],
            "action": "activate",
        },
        follow_redirects=True,
    )
    assert reactivate.status_code == 200
    assert "Active alert state saved." in reactivate.text

    refreshed = client.get("/net-value")
    assert refreshed.status_code == 200
    assert re.search(r'topbar__alert-count">\s*2\s*<', refreshed.text)
    assert "Top-Holding Concentration Breach" in refreshed.text


def test_portfolio_guardrail_snooze_hides_guardrail_and_records_state(client):
    sec_id = _add_security(client, ticker="T57PORT")
    _add_lot(client, sec_id, quantity="1")
    _add_price(
        sec_id,
        price_date=date.today(),
        close_price_original_ccy="12.00",
        close_price_gbp="12.00",
    )

    home = client.get("/")
    assert home.status_code == 200
    assert "Top-holding concentration breach" in home.text
    assert "Snooze 7d" in home.text

    match = re.search(
        r'data-guardrail-id="concentration_top_holding"[^>]*data-guardrail-hash="([^"]+)"',
        home.text,
    )
    assert match is not None
    condition_hash = match.group(1)

    snooze = client.post(
        "/portfolio/guardrails/dismiss",
        json={
            "guardrail_id": "concentration_top_holding",
            "condition_hash": condition_hash,
            "action": "snooze",
        },
    )
    assert snooze.status_code == 200, snooze.text
    payload = snooze.json()
    assert payload["ok"] is True
    assert payload["state"] == "SNOOZED"

    refreshed = client.get("/")
    assert refreshed.status_code == 200
    assert "Top-holding concentration breach" not in refreshed.text
    assert "1 suppressed" in refreshed.text

    with AppContext.read_session() as sess:
        latest_state = sess.scalar(
            select(PortfolioGuardrailStateEvent)
            .where(PortfolioGuardrailStateEvent.guardrail_id == "concentration_top_holding")
            .order_by(PortfolioGuardrailStateEvent.changed_at.desc())
            .limit(1)
        )
    assert latest_state is not None
    assert latest_state.state == "SNOOZED"


def test_suppressed_alert_reappears_when_condition_hash_changes(client):
    sec_id = _add_security(client, ticker="T57HASH")
    _add_lot(client, sec_id, quantity="10")
    _add_price(
        sec_id,
        price_date=date.today(),
        close_price_original_ccy="20.00",
        close_price_gbp="20.00",
    )
    _save_settings(
        employer_ticker="T57HASH",
        gross_income="100000",
        top_threshold="50",
        employer_threshold="40",
    )

    alert_center = _get_alert_center()
    top_alert = next(
        alert for alert in alert_center["alerts"] if alert["id"] == "concentration_top_holding"
    )
    snooze = client.post(
        "/risk/alerts/lifecycle",
        data={
            "lifecycle_id": top_alert["lifecycle_id"],
            "condition_hash": top_alert["condition_hash"],
            "action": "snooze",
        },
        follow_redirects=True,
    )
    assert snooze.status_code == 200

    hidden = client.get("/net-value")
    assert hidden.status_code == 200
    assert re.search(r'topbar__alert-count">\s*1\s*<', hidden.text)

    _save_settings(
        employer_ticker="T57HASH",
        gross_income="100000",
        top_threshold="40",
        employer_threshold="40",
    )

    resurfaced = client.get("/net-value")
    assert resurfaced.status_code == 200
    assert re.search(r'topbar__alert-count">\s*2\s*<', resurfaced.text)
    assert "Top-Holding Concentration Breach" in resurfaced.text

    risk = client.get("/risk")
    assert risk.status_code == 200
    assert "Top-Holding Concentration Breach" in risk.text
    assert "Snoozed" not in risk.text
