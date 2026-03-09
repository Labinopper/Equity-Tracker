from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings
from src.services.calendar_service import CalendarService
from src.services.pension_service import PensionService
from src.services.portfolio_service import PortfolioService


def _add_security(ticker: str, *, dividend_reminder_date: date | None = None):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Calendar Co",
        currency="GBP",
        dividend_reminder_date=dividend_reminder_date,
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
    assert vest_events[0]["price_as_of"] == "2026-02-24"
    assert vest_events[0]["price_is_stale"] is False
    assert vest_events[0]["fx_basis_note"] == "GBP security (no FX conversion)"

    assert len(forfeiture_events) == 1
    assert forfeiture_events[0]["ticker"] == "CALESPP"
    assert forfeiture_events[0]["event_date"] == "2026-03-03"
    assert forfeiture_events[0]["value_at_stake_gbp"] == "30.00"
    assert forfeiture_events[0]["price_as_of"] == "2026-02-24"
    assert forfeiture_events[0]["price_is_stale"] is False

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
    assert vest_events[0]["price_as_of"] is None
    assert any("value-at-stake is unavailable" in note for note in payload["notes"])


def test_calendar_payload_includes_dividend_and_monthly_reminders(app_context):
    as_of = date(2026, 2, 24)
    _add_security("CALDIV", dividend_reminder_date=date(2025, 3, 5))

    settings = AppSettings()
    settings.monthly_espp_input_reminder_enabled = True
    settings.monthly_espp_input_reminder_day = 15

    payload = CalendarService.get_events_payload(
        as_of=as_of,
        horizon_days=60,
        settings=settings,
    )

    dividend_events = [
        e for e in payload["events"] if e["event_type"] == "DIVIDEND_REMINDER"
    ]
    monthly_events = [
        e for e in payload["events"] if e["event_type"] == "MONTHLY_INPUT_REMINDER"
    ]

    assert len(dividend_events) == 1
    assert dividend_events[0]["ticker"] == "CALDIV"
    assert dividend_events[0]["event_date"] == "2026-03-05"
    assert dividend_events[0]["deep_link"] == "/dividends#add-dividend"

    assert len(monthly_events) == 1
    assert monthly_events[0]["event_date"] == "2026-03-15"
    assert monthly_events[0]["deep_link"] == "/portfolio/add-lot"

    assert payload["countdowns"]["next_reminder"]["event_date"] == "2026-03-05"
    assert payload["event_counts"]["reminders"] >= 2


def test_calendar_payload_includes_monthly_pension_check(app_context, tmp_path):
    as_of = date(2026, 2, 24)
    db_path = tmp_path / "calendar-pension.db"
    PensionService.save_assumptions(
        db_path=db_path,
        current_pension_value_gbp="100000",
        monthly_employee_contribution_gbp="500",
        monthly_employer_contribution_gbp="250",
        retirement_date="2045-03-31",
        target_annual_income_gbp="40000",
        target_withdrawal_rate_pct="4",
        conservative_annual_return_pct="3",
        base_annual_return_pct="5",
        aggressive_annual_return_pct="7",
    )

    payload = CalendarService.get_events_payload(
        as_of=as_of,
        horizon_days=60,
        db_path=db_path,
    )

    pension_events = [
        e for e in payload["events"] if e["event_type"] == "PENSION_CONTRIBUTION_CHECK"
    ]

    assert len(pension_events) == 1
    assert pension_events[0]["event_date"] == "2026-03-06"
    assert pension_events[0]["deep_link"] == "/pension#pension-validation"
    assert pension_events[0]["value_at_stake_gbp"] == "750.00"
    assert "validate the current pot value" in pension_events[0]["subtitle"]


def test_calendar_payload_includes_espp_transfer_guardrail_events(app_context):
    as_of = date(2026, 2, 24)
    sec = _add_security("CALESPPGR")
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="ESPP",
        acquisition_date=as_of - timedelta(days=10),
        quantity=Decimal("2.40"),
        acquisition_price_gbp=Decimal("8.00"),
        true_cost_per_share_gbp=Decimal("8.00"),
    )
    _add_price(sec.id, as_of, "11.00")

    payload = CalendarService.get_events_payload(as_of=as_of, horizon_days=30)
    guardrails = [
        e for e in payload["events"] if e["event_type"] == "ESPP_TRANSFER_GUARDRAIL"
    ]

    assert len(guardrails) == 1
    assert guardrails[0]["ticker"] == "CALESPPGR"
    assert guardrails[0]["quantity"] == "2"
    assert guardrails[0]["value_at_stake_gbp"] == "22.00"
    assert guardrails[0]["deep_link"] == "/portfolio/transfer-lot"


def test_calendar_payload_includes_espp_plus_five_year_guardrail_events(app_context):
    as_of = date(2026, 2, 24)
    sec = _add_security("CALES5Y")
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date(2020, 2, 20),
        quantity=Decimal("3"),
        acquisition_price_gbp=Decimal("9.00"),
        true_cost_per_share_gbp=Decimal("7.00"),
    )
    _add_price(sec.id, as_of, "12.00")

    payload = CalendarService.get_events_payload(as_of=as_of, horizon_days=30)
    guardrails = [
        e
        for e in payload["events"]
        if e["event_type"] == "ESPP_PLUS_LONG_HOLD_GUARDRAIL"
    ]

    assert len(guardrails) == 1
    assert guardrails[0]["ticker"] == "CALES5Y"
    assert guardrails[0]["quantity"] == "3"
    assert guardrails[0]["value_at_stake_gbp"] == "36.00"
    assert guardrails[0]["deep_link"] == "/portfolio/transfer-lot"


@pytest.mark.parametrize("horizon_days", [0, 1461])
def test_calendar_payload_rejects_invalid_horizon(app_context, horizon_days):
    with pytest.raises(ValueError):
        CalendarService.get_events_payload(as_of=date(2026, 2, 24), horizon_days=horizon_days)
