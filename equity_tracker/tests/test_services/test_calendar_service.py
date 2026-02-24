from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.calendar_service import CalendarService
from src.services.portfolio_service import PortfolioService


def _add_security(ticker: str):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Calendar Co",
        currency="GBP",
        is_manual_override=True,
    )


def _add_price(security_id: str, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-calendar-service",
        )


def test_calendar_payload_empty_portfolio_still_returns_tax_countdown(app_context):
    as_of = date(2026, 2, 24)

    payload = CalendarService.get_events_payload(as_of=as_of, horizon_days=60)

    assert payload["as_of_date"] == "2026-02-24"
    assert payload["event_counts"]["total"] == 2
    assert payload["event_counts"]["tax_markers"] == 2
    assert payload["countdowns"]["next_tax_year_end"]["event_date"] == "2026-04-05"
    assert payload["countdowns"]["next_tax_year_end"]["days_until"] == 40


def test_calendar_payload_includes_vest_and_forfeiture_events_with_values(app_context):
    as_of = date(2026, 2, 24)

    sec_rsu = _add_security("CALRSU")
    sec_espp = _add_security("CALESPP")

    PortfolioService.add_lot(
        security_id=sec_rsu.id,
        scheme_type="RSU",
        acquisition_date=as_of + timedelta(days=10),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("12.00"),
        true_cost_per_share_gbp=Decimal("12.00"),
    )

    employee = PortfolioService.add_lot(
        security_id=sec_espp.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=as_of - timedelta(days=40),
        quantity=Decimal("8"),
        acquisition_price_gbp=Decimal("9.00"),
        true_cost_per_share_gbp=Decimal("7.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
    )
    PortfolioService.add_lot(
        security_id=sec_espp.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=as_of - timedelta(days=40),
        quantity=Decimal("2"),
        acquisition_price_gbp=Decimal("0.00"),
        true_cost_per_share_gbp=Decimal("0.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
        matching_lot_id=employee.id,
        forfeiture_period_end=as_of + timedelta(days=7),
    )

    _add_price(sec_rsu.id, as_of, "20.00")
    _add_price(sec_espp.id, as_of, "15.00")

    payload = CalendarService.get_events_payload(as_of=as_of, horizon_days=60)

    vest_events = [e for e in payload["events"] if e["event_type"] == "VEST_DATE"]
    forfeiture_events = [e for e in payload["events"] if e["event_type"] == "FORFEITURE_END"]

    assert len(vest_events) == 1
    assert vest_events[0]["ticker"] == "CALRSU"
    assert vest_events[0]["event_date"] == "2026-03-06"
    assert vest_events[0]["value_at_stake_gbp"] == "200.00"

    assert len(forfeiture_events) == 1
    assert forfeiture_events[0]["ticker"] == "CALESPP"
    assert forfeiture_events[0]["event_date"] == "2026-03-03"
    assert forfeiture_events[0]["value_at_stake_gbp"] == "30.00"

    assert payload["event_counts"]["vest_dates"] == 1
    assert payload["event_counts"]["forfeiture_windows"] == 1
    assert payload["countdowns"]["next_vest"]["days_until"] == 10
    assert payload["countdowns"]["next_forfeiture_end"]["days_until"] == 7


def test_calendar_payload_flags_unpriced_events(app_context):
    as_of = date(2026, 2, 24)
    sec = _add_security("CALNOPX")

    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="RSU",
        acquisition_date=as_of + timedelta(days=5),
        quantity=Decimal("4"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    payload = CalendarService.get_events_payload(as_of=as_of, horizon_days=30)

    vest_events = [e for e in payload["events"] if e["event_type"] == "VEST_DATE"]
    assert len(vest_events) == 1
    assert vest_events[0]["has_live_value"] is False
    assert vest_events[0]["value_at_stake_gbp"] is None
    assert any("value-at-stake is unavailable" in note for note in payload["notes"])


@pytest.mark.parametrize("horizon_days", [0, 1461])
def test_calendar_payload_rejects_invalid_horizon(app_context, horizon_days):
    with pytest.raises(ValueError):
        CalendarService.get_events_payload(as_of=date(2026, 2, 24), horizon_days=horizon_days)
