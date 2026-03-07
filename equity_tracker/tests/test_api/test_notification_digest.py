from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Digest Plc",
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
    quantity: str = "10",
    acquisition_price_gbp: str = "10.00",
) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": acquisition_price_gbp,
            "true_cost_per_share_gbp": acquisition_price_gbp,
            "tax_year": "2024-25",
        },
    )
    assert resp.status_code == 201, resp.text


def _upsert_price(
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
            source="test-notification-digest",
        )


def _save_settings() -> None:
    db_path = _state.get_db_path()
    assert db_path is not None
    settings = AppSettings.load(db_path)
    settings.employer_ticker = "DIGEST"
    settings.default_gross_income = Decimal("90000")
    settings.default_other_income = Decimal("0")
    settings.concentration_top_holding_alert_pct = Decimal("20")
    settings.concentration_employer_alert_pct = Decimal("20")
    settings.price_stale_after_days = 1
    settings.fx_stale_after_minutes = 10
    settings.save()


def test_notification_digest_groups_thresholds_staleness_and_upcoming_events(client):
    _save_settings()

    employer_security_id = _add_security(client, ticker="DIGEST")
    _add_lot(client, security_id=employer_security_id, quantity="20")
    _upsert_price(
        employer_security_id,
        price_date=date.today(),
        close_price_original_ccy="100.00",
        close_price_gbp="100.00",
    )

    stale_security_id = _add_security(client, ticker="STALE", currency="USD")
    _add_lot(client, security_id=stale_security_id, quantity="5", acquisition_price_gbp="8.00")
    _upsert_price(
        stale_security_id,
        price_date=date.today() - timedelta(days=7),
        close_price_original_ccy="50.00",
        close_price_gbp="40.00",
        currency="USD",
    )

    payload = client.get("/api/strategic/notification-digest?horizon_days=30").json()

    assert payload["summary"]["threshold_breach_count"] >= 1
    assert payload["summary"]["stale_data_count"] >= 1
    assert payload["summary"]["upcoming_event_count"] >= 1

    categories = {row["category"] for row in payload["entries"]}
    assert "THRESHOLD_BREACH" in categories
    assert "STALE_DATA" in categories
    assert "UPCOMING_EVENT" in categories

    page = client.get("/notification-digest?horizon_days=30")
    assert page.status_code == 200
    text = page.text
    assert "Notification Digest" in text
    assert "Digest Entries" in text
    assert "Threshold Breaches" in text
    assert "Trace Links" in text
