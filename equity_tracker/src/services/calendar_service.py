"""
CalendarService - additive timeline payloads for /calendar.

Scope:
- Upcoming RSU vest events
- Upcoming ESPP+ forfeiture-window end events
- Optional sell-plan tranche events supplied by caller
- UK tax-year boundary countdown markers

No writes are performed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from ..settings import AppSettings
from .calendar_event_state_service import CalendarEventStateService
from .dividend_service import DividendService
from .pension_service import PensionService
from .portfolio_service import PortfolioService

_GBP_Q = Decimal("0.01")
_DEFAULT_HORIZON_DAYS = 400
_MAX_HORIZON_DAYS = 1460

_EVENT_VEST_DATE = "VEST_DATE"
_EVENT_FORFEITURE_END = "FORFEITURE_END"
_EVENT_SELL_TRANCHE = "SELL_TRANCHE"
_EVENT_TAX_YEAR_END = "TAX_YEAR_END"
_EVENT_TAX_YEAR_START = "TAX_YEAR_START"
_EVENT_DIVIDEND_REMINDER = "DIVIDEND_REMINDER"
_EVENT_MONTHLY_INPUT_REMINDER = "MONTHLY_INPUT_REMINDER"
_EVENT_PENSION_CONTRIBUTION_CHECK = "PENSION_CONTRIBUTION_CHECK"
_EVENT_ESPP_TRANSFER_GUARDRAIL = "ESPP_TRANSFER_GUARDRAIL"
_EVENT_ESPP_PLUS_LONG_HOLD_GUARDRAIL = "ESPP_PLUS_LONG_HOLD_GUARDRAIL"
_EVENT_DIVIDEND_CONFIRMATION = "DIVIDEND_CONFIRMATION"

_EVENT_REMINDERS = frozenset(
    {
        _EVENT_DIVIDEND_REMINDER,
        _EVENT_MONTHLY_INPUT_REMINDER,
        _EVENT_PENSION_CONTRIBUTION_CHECK,
        _EVENT_ESPP_TRANSFER_GUARDRAIL,
        _EVENT_ESPP_PLUS_LONG_HOLD_GUARDRAIL,
        _EVENT_DIVIDEND_CONFIRMATION,
    }
)

_EVENT_PRIORITY: dict[str, int] = {
    _EVENT_FORFEITURE_END: 0,
    _EVENT_ESPP_TRANSFER_GUARDRAIL: 1,
    _EVENT_ESPP_PLUS_LONG_HOLD_GUARDRAIL: 2,
    _EVENT_VEST_DATE: 3,
    _EVENT_DIVIDEND_REMINDER: 4,
    _EVENT_MONTHLY_INPUT_REMINDER: 5,
    _EVENT_PENSION_CONTRIBUTION_CHECK: 6,
    _EVENT_DIVIDEND_CONFIRMATION: 7,
    _EVENT_SELL_TRANCHE: 8,
    _EVENT_TAX_YEAR_END: 9,
    _EVENT_TAX_YEAR_START: 10,
}


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(_q_money(value))


def _days_until(event_date: date, as_of: date) -> int:
    return (event_date - as_of).days


def _in_horizon(event_date: date, *, as_of: date, horizon_days: int) -> bool:
    return as_of <= event_date <= (as_of + timedelta(days=horizon_days))


def _in_horizon_or_recent(
    event_date: date,
    *,
    as_of: date,
    horizon_days: int,
    keep_past_days: int = 30,
) -> bool:
    if _in_horizon(event_date, as_of=as_of, horizon_days=horizon_days):
        return True
    if event_date < as_of:
        return (as_of - event_date).days <= keep_past_days
    return False


def _next_tax_year_end(as_of: date) -> date:
    # UK tax year ends on 5 April.
    if as_of.month > 4 or (as_of.month == 4 and as_of.day >= 6):
        return date(as_of.year + 1, 4, 5)
    return date(as_of.year, 4, 5)


def _next_annual_occurrence(*, anchor: date, as_of: date) -> date:
    try:
        candidate = date(as_of.year, anchor.month, anchor.day)
    except ValueError:
        candidate = date(as_of.year, 2, 28)
    if candidate < as_of:
        try:
            candidate = date(as_of.year + 1, anchor.month, anchor.day)
        except ValueError:
            candidate = date(as_of.year + 1, 2, 28)
    return candidate


def _next_monthly_occurrence(*, day: int, as_of: date) -> date:
    clamped_day = max(1, min(28, day))
    candidate = date(as_of.year, as_of.month, clamped_day)
    if candidate >= as_of:
        return candidate
    if as_of.month == 12:
        return date(as_of.year + 1, 1, clamped_day)
    return date(as_of.year, as_of.month + 1, clamped_day)


def _add_years_safe(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # 29 Feb -> 28 Feb on non-leap years.
        return value.replace(month=2, day=28, year=value.year + years)


def _quantity_str(value: Decimal) -> str:
    if value == value.to_integral_value(rounding=ROUND_FLOOR):
        return str(int(value))
    text = format(value.normalize(), "f")
    if "." in text:
        return text.rstrip("0").rstrip(".")
    return text


def _countdown_from_event(*, label: str, event: dict[str, Any] | None) -> dict[str, Any]:
    if event is None:
        return {
            "label": label,
            "event_date": None,
            "days_until": None,
            "title": f"No upcoming {label.lower()} in selected horizon.",
        }
    return {
        "label": label,
        "event_date": event["event_date"],
        "days_until": event["days_until"],
        "title": event["title"],
    }


def _event_type_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    vest = 0
    forfeiture = 0
    sell_tranches = 0
    tax = 0
    reminders = 0
    for event in events:
        event_type = event["event_type"]
        if event_type == _EVENT_VEST_DATE:
            vest += 1
        elif event_type == _EVENT_FORFEITURE_END:
            forfeiture += 1
        elif event_type == _EVENT_SELL_TRANCHE:
            sell_tranches += 1
        elif event_type in (_EVENT_TAX_YEAR_END, _EVENT_TAX_YEAR_START):
            tax += 1
        elif event_type in _EVENT_REMINDERS:
            reminders += 1
    return {
        "total": len(events),
        "vest_dates": vest,
        "forfeiture_windows": forfeiture,
        "sell_tranches": sell_tranches,
        "tax_markers": tax,
        "reminders": reminders,
    }


class CalendarService:
    """
    Build timeline events and countdown summaries from current lot state.
    """

    @staticmethod
    def get_events_payload(
        *,
        settings: AppSettings | None = None,
        db_path = None,
        horizon_days: int = _DEFAULT_HORIZON_DAYS,
        as_of: date | None = None,
        sell_plan_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if horizon_days < 1:
            raise ValueError("horizon_days must be >= 1.")
        if horizon_days > _MAX_HORIZON_DAYS:
            raise ValueError(f"horizon_days must be <= {_MAX_HORIZON_DAYS}.")

        as_of_date = as_of or date.today()
        generated_at_utc = datetime.now(timezone.utc).isoformat()
        state_by_event = CalendarEventStateService.load_states(db_path)

        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
            as_of=as_of_date,
        )

        events: list[dict[str, Any]] = []
        unpriced_value_events = 0

        for security_summary in summary.securities:
            ticker = security_summary.security.ticker
            security_id = security_summary.security.id
            price_as_of = (
                security_summary.price_as_of.isoformat()
                if security_summary.price_as_of is not None
                else None
            )
            price_is_stale = bool(security_summary.price_is_stale)
            fx_as_of = security_summary.fx_as_of
            fx_is_stale = bool(security_summary.fx_is_stale)
            fx_basis_note = (
                "GBP security (no FX conversion)"
                if str(security_summary.security.currency or "").upper() == "GBP"
                else (
                    "FX basis unavailable"
                    if not fx_as_of
                    else None
                )
            )

            for lot_summary in security_summary.active_lots:
                lot = lot_summary.lot
                event_date: date | None = None
                event_type: str | None = None
                title: str | None = None
                subtitle: str | None = None

                if (
                    lot.scheme_type == "RSU"
                    and lot_summary.sellability_status == "LOCKED"
                    and lot_summary.sellability_unlock_date is not None
                ):
                    event_date = lot_summary.sellability_unlock_date
                    event_type = _EVENT_VEST_DATE
                    title = f"{ticker}: RSU vest date"
                    subtitle = "Shares unlock for disposal once vested."
                elif (
                    lot.scheme_type == "ESPP_PLUS"
                    and lot.matching_lot_id is not None
                    and lot_summary.forfeiture_risk is not None
                    and lot_summary.forfeiture_risk.in_window
                ):
                    event_date = lot_summary.forfeiture_risk.end_date
                    event_type = _EVENT_FORFEITURE_END
                    title = f"{ticker}: ESPP+ forfeiture window ends"
                    subtitle = "Matched shares stop being forfeitable and become transferable."

                if event_date is None or event_type is None or title is None or subtitle is None:
                    continue
                if not _in_horizon(event_date, as_of=as_of_date, horizon_days=horizon_days):
                    continue

                has_live_value = lot_summary.market_value_gbp is not None
                if not has_live_value:
                    unpriced_value_events += 1

                events.append(
                    {
                        "event_id": f"lot:{lot.id}:{event_type.lower()}",
                        "event_type": event_type,
                        "event_date": event_date.isoformat(),
                        "days_until": _days_until(event_date, as_of_date),
                        "title": title,
                        "subtitle": subtitle,
                        "security_id": security_id,
                        "ticker": ticker,
                        "scheme_type": lot.scheme_type,
                        "lot_id": lot.id,
                        "quantity": _quantity_str(lot_summary.quantity_remaining),
                        "value_at_stake_gbp": _money_str(lot_summary.market_value_gbp),
                        "has_live_value": has_live_value,
                        "price_as_of": price_as_of,
                        "price_is_stale": price_is_stale,
                        "fx_as_of": fx_as_of,
                        "fx_is_stale": fx_is_stale,
                        "fx_basis_note": fx_basis_note,
                        "deep_link": None,
                        "action_label": None,
                    }
                )

            dividend_anchor = security_summary.security.dividend_reminder_date
            if dividend_anchor is not None:
                dividend_event_date = _next_annual_occurrence(
                    anchor=dividend_anchor,
                    as_of=as_of_date,
                )
                if _in_horizon(dividend_event_date, as_of=as_of_date, horizon_days=horizon_days):
                    events.append(
                        {
                            "event_id": (
                                f"security:{security_id}:"
                                f"{_EVENT_DIVIDEND_REMINDER.lower()}:"
                                f"{dividend_event_date.isoformat()}"
                            ),
                            "event_type": _EVENT_DIVIDEND_REMINDER,
                            "event_date": dividend_event_date.isoformat(),
                            "days_until": _days_until(dividend_event_date, as_of_date),
                            "title": f"{ticker}: Dividend reminder",
                            "subtitle": "Check broker payout and log via Dividends.",
                            "security_id": security_id,
                            "ticker": ticker,
                            "scheme_type": None,
                            "lot_id": None,
                            "quantity": None,
                            "value_at_stake_gbp": None,
                            "has_live_value": False,
                            "price_as_of": None,
                            "price_is_stale": False,
                            "fx_as_of": None,
                            "fx_is_stale": False,
                            "fx_basis_note": None,
                            "deep_link": "/dividends#add-dividend",
                            "action_label": "Open Dividends",
                        }
                    )

            espp_total_qty = sum(
                (
                    lot_summary.quantity_remaining
                    for lot_summary in security_summary.active_lots
                    if lot_summary.lot.scheme_type == "ESPP"
                ),
                Decimal("0"),
            )
            espp_whole_qty = espp_total_qty.to_integral_value(rounding=ROUND_FLOOR)
            if espp_whole_qty > Decimal("1"):
                espp_value = (
                    security_summary.current_price_gbp * espp_whole_qty
                    if security_summary.current_price_gbp is not None
                    else None
                )
                has_live_value = espp_value is not None
                if not has_live_value:
                    unpriced_value_events += 1
                events.append(
                    {
                        "event_id": f"security:{security_id}:{_EVENT_ESPP_TRANSFER_GUARDRAIL.lower()}",
                        "event_type": _EVENT_ESPP_TRANSFER_GUARDRAIL,
                        "event_date": as_of_date.isoformat(),
                        "days_until": 0,
                        "title": f"{ticker}: ESPP transfer guardrail",
                        "subtitle": (
                            "More than 1 whole ESPP share available; "
                            "review transfer to BROKERAGE."
                        ),
                        "security_id": security_id,
                        "ticker": ticker,
                        "scheme_type": "ESPP",
                        "lot_id": None,
                        "quantity": _quantity_str(espp_whole_qty),
                        "value_at_stake_gbp": _money_str(espp_value),
                        "has_live_value": has_live_value,
                        "price_as_of": price_as_of,
                        "price_is_stale": price_is_stale,
                        "fx_as_of": fx_as_of,
                        "fx_is_stale": fx_is_stale,
                        "fx_basis_note": fx_basis_note,
                        "deep_link": "/portfolio/transfer-lot",
                        "action_label": "Open Transfer",
                    }
                )

            aged_espp_plus_qty = Decimal("0")
            aged_lot_count = 0
            first_five_year_date: date | None = None
            for lot_summary in security_summary.active_lots:
                if lot_summary.lot.scheme_type != "ESPP_PLUS":
                    continue
                five_year_date = _add_years_safe(lot_summary.lot.acquisition_date, 5)
                if as_of_date < five_year_date:
                    continue
                aged_espp_plus_qty += lot_summary.quantity_remaining
                aged_lot_count += 1
                if first_five_year_date is None or five_year_date < first_five_year_date:
                    first_five_year_date = five_year_date

            if aged_espp_plus_qty > Decimal("0"):
                aged_espp_plus_value = (
                    security_summary.current_price_gbp * aged_espp_plus_qty
                    if security_summary.current_price_gbp is not None
                    else None
                )
                has_live_value = aged_espp_plus_value is not None
                if not has_live_value:
                    unpriced_value_events += 1
                age_note = (
                    f" (first crossed 5y on {first_five_year_date.isoformat()})."
                    if first_five_year_date is not None
                    else "."
                )
                events.append(
                    {
                        "event_id": (
                            f"security:{security_id}:"
                            f"{_EVENT_ESPP_PLUS_LONG_HOLD_GUARDRAIL.lower()}"
                        ),
                        "event_type": _EVENT_ESPP_PLUS_LONG_HOLD_GUARDRAIL,
                        "event_date": as_of_date.isoformat(),
                        "days_until": 0,
                        "title": f"{ticker}: ESPP+ 5-year guardrail",
                        "subtitle": (
                            f"{aged_lot_count} ESPP+ lot(s) are older than 5 years; "
                            f"review transfer to BROKERAGE{age_note}"
                        ),
                        "security_id": security_id,
                        "ticker": ticker,
                        "scheme_type": "ESPP_PLUS",
                        "lot_id": None,
                        "quantity": _quantity_str(aged_espp_plus_qty),
                        "value_at_stake_gbp": _money_str(aged_espp_plus_value),
                        "has_live_value": has_live_value,
                        "price_as_of": price_as_of,
                        "price_is_stale": price_is_stale,
                        "fx_as_of": fx_as_of,
                        "fx_is_stale": fx_is_stale,
                        "fx_basis_note": fx_basis_note,
                        "deep_link": "/portfolio/transfer-lot",
                        "action_label": "Open Transfer",
                    }
                )

        dividend_payload = DividendService.get_summary(settings=settings, as_of=as_of_date)
        for row in dividend_payload.get("reference_events") or []:
            if str(row.get("status") or "") == "Recorded":
                continue
            ex_date_raw = str(row.get("ex_dividend_date") or "").strip()
            if not ex_date_raw:
                continue
            try:
                ex_date = date.fromisoformat(ex_date_raw)
            except ValueError:
                continue
            payment_date_raw = str(row.get("payment_date") or "").strip()
            if payment_date_raw:
                try:
                    event_date = date.fromisoformat(payment_date_raw)
                    date_basis = "pay date"
                except ValueError:
                    event_date = ex_date + timedelta(days=28)
                    date_basis = "estimated pay date"
            elif ex_date <= as_of_date:
                event_date = ex_date + timedelta(days=28)
                date_basis = "estimated pay date"
            else:
                event_date = ex_date
                date_basis = "ex-date"

            event_id = (
                f"dividend-confirm:{row.get('security_id')}:{row.get('holding_scope')}:"
                f"{ex_date.isoformat()}"
            )
            state_row = state_by_event.get(event_id, {})
            if not _in_horizon_or_recent(event_date, as_of=as_of_date, horizon_days=horizon_days):
                continue

            expected_value_gbp = row.get("expected_total_gbp")
            events.append(
                {
                    "event_id": event_id,
                    "event_type": _EVENT_DIVIDEND_CONFIRMATION,
                    "event_date": event_date.isoformat(),
                    "days_until": _days_until(event_date, as_of_date),
                    "title": f"{row.get('ticker')}: Dividend confirmation",
                    "subtitle": (
                        f"Confirm {row.get('holding_scope_label')} receipt for ex-date {ex_date.isoformat()} "
                        f"using {date_basis}."
                    ),
                    "security_id": row.get("security_id"),
                    "ticker": row.get("ticker"),
                    "scheme_type": row.get("holding_scope_label"),
                    "lot_id": None,
                    "quantity": row.get("expected_quantity"),
                    "value_at_stake_gbp": expected_value_gbp,
                    "has_live_value": expected_value_gbp is not None,
                    "price_as_of": None,
                    "price_is_stale": False,
                    "fx_as_of": None,
                    "fx_is_stale": False,
                    "fx_basis_note": None,
                    "deep_link": "/dividends",
                    "action_label": "Open Dividends",
                    "completed": bool(state_row.get("completed")),
                    "completed_at_utc": state_row.get("completed_at_utc"),
                }
            )

        tax_year_end = _next_tax_year_end(as_of_date)
        tax_year_start = tax_year_end + timedelta(days=1)

        if settings is not None and bool(
            getattr(settings, "monthly_espp_input_reminder_enabled", False)
        ):
            reminder_day = int(getattr(settings, "monthly_espp_input_reminder_day", 1))
            monthly_event_date = _next_monthly_occurrence(day=reminder_day, as_of=as_of_date)
            if _in_horizon(monthly_event_date, as_of=as_of_date, horizon_days=horizon_days):
                reminder_variants = (
                    ("ESPP", "Monthly ESPP input reminder", "Record this month's ESPP purchase."),
                    ("ESPP_PLUS", "Monthly ESPP+ input reminder", "Record this month's ESPP+ purchase."),
                )
                for scheme_type, title, subtitle in reminder_variants:
                    events.append(
                        {
                            "event_id": (
                                "global:"
                                f"{_EVENT_MONTHLY_INPUT_REMINDER.lower()}:"
                                f"{scheme_type.lower()}:"
                                f"{monthly_event_date.isoformat()}"
                            ),
                            "event_type": _EVENT_MONTHLY_INPUT_REMINDER,
                            "event_date": monthly_event_date.isoformat(),
                            "days_until": _days_until(monthly_event_date, as_of_date),
                            "title": title,
                            "subtitle": subtitle,
                            "security_id": None,
                            "ticker": None,
                            "scheme_type": scheme_type,
                            "lot_id": None,
                            "quantity": None,
                            "value_at_stake_gbp": None,
                            "has_live_value": False,
                            "price_as_of": None,
                            "price_is_stale": False,
                            "fx_as_of": None,
                            "fx_is_stale": False,
                            "fx_basis_note": None,
                            "deep_link": "/portfolio/add-lot",
                            "action_label": "Open Add Lot",
                        }
                    )

        pension_assumptions = PensionService.load_assumptions(db_path)
        pension_monthly_total = _q_money(
            Decimal(str(pension_assumptions.get("monthly_employee_contribution_gbp") or "0"))
            + Decimal(str(pension_assumptions.get("monthly_employer_contribution_gbp") or "0"))
        )
        if pension_monthly_total > Decimal("0"):
            pension_event_date = _next_monthly_occurrence(day=6, as_of=as_of_date)
            if _in_horizon(pension_event_date, as_of=as_of_date, horizon_days=horizon_days):
                last_valuation_date = str(pension_assumptions.get("last_valuation_date") or "").strip()
                subtitle = (
                    "Confirm this month's pension contributions. Optional: validate the current pot value to record growth separately."
                )
                if last_valuation_date:
                    subtitle = (
                        f"{subtitle} Last validated on {last_valuation_date}."
                    )
                events.append(
                    {
                        "event_id": (
                            "global:"
                            f"{_EVENT_PENSION_CONTRIBUTION_CHECK.lower()}:"
                            f"{pension_event_date.isoformat()}"
                        ),
                        "event_type": _EVENT_PENSION_CONTRIBUTION_CHECK,
                        "event_date": pension_event_date.isoformat(),
                        "days_until": _days_until(pension_event_date, as_of_date),
                        "title": "Monthly pension contribution check",
                        "subtitle": subtitle,
                        "security_id": None,
                        "ticker": None,
                        "scheme_type": "PENSION",
                        "lot_id": None,
                        "quantity": None,
                        "value_at_stake_gbp": _money_str(pension_monthly_total),
                        "has_live_value": False,
                        "price_as_of": None,
                        "price_is_stale": False,
                        "fx_as_of": None,
                        "fx_is_stale": False,
                        "fx_basis_note": None,
                        "deep_link": "/pension#pension-validation",
                        "action_label": "Open Pension",
                    }
                )

        if _in_horizon(tax_year_end, as_of=as_of_date, horizon_days=horizon_days):
            events.append(
                {
                    "event_id": f"tax-year-end:{tax_year_end.isoformat()}",
                    "event_type": _EVENT_TAX_YEAR_END,
                    "event_date": tax_year_end.isoformat(),
                    "days_until": _days_until(tax_year_end, as_of_date),
                    "title": "UK tax year end",
                    "subtitle": "Current UK tax year closes on 5 April.",
                    "security_id": None,
                    "ticker": None,
                    "scheme_type": None,
                    "lot_id": None,
                    "quantity": None,
                    "value_at_stake_gbp": None,
                    "has_live_value": False,
                    "price_as_of": None,
                    "price_is_stale": False,
                    "fx_as_of": None,
                    "fx_is_stale": False,
                    "fx_basis_note": None,
                    "deep_link": None,
                    "action_label": None,
                }
            )

        if _in_horizon(tax_year_start, as_of=as_of_date, horizon_days=horizon_days):
            events.append(
                {
                    "event_id": f"tax-year-start:{tax_year_start.isoformat()}",
                    "event_type": _EVENT_TAX_YEAR_START,
                    "event_date": tax_year_start.isoformat(),
                    "days_until": _days_until(tax_year_start, as_of_date),
                    "title": "UK tax year start",
                    "subtitle": "New UK tax year starts on 6 April.",
                    "security_id": None,
                    "ticker": None,
                    "scheme_type": None,
                    "lot_id": None,
                    "quantity": None,
                    "value_at_stake_gbp": None,
                    "has_live_value": False,
                    "price_as_of": None,
                    "price_is_stale": False,
                    "fx_as_of": None,
                    "fx_is_stale": False,
                    "fx_basis_note": None,
                    "deep_link": None,
                    "action_label": None,
                }
            )

        if sell_plan_events:
            for raw_event in sell_plan_events:
                event = dict(raw_event)
                event.setdefault("price_as_of", None)
                event.setdefault("price_is_stale", False)
                event.setdefault("fx_as_of", None)
                event.setdefault("fx_is_stale", False)
                event.setdefault("fx_basis_note", None)
                event.setdefault("deep_link", None)
                event.setdefault("action_label", None)
                state_row = state_by_event.get(str(event.get("event_id") or ""), {})
                event.setdefault("completed", bool(state_row.get("completed")))
                event.setdefault("completed_at_utc", state_row.get("completed_at_utc"))
                events.append(event)

        for event in events:
            event.setdefault("completed", False)
            event.setdefault("completed_at_utc", None)

        events.sort(
            key=lambda event: (
                event["event_date"],
                _EVENT_PRIORITY.get(event["event_type"], 99),
                event["ticker"] or "",
                event["title"],
            )
        )

        next_vest = next(
            (event for event in events if event["event_type"] == _EVENT_VEST_DATE and not event.get("completed")),
            None,
        )
        next_forfeiture = next(
            (event for event in events if event["event_type"] == _EVENT_FORFEITURE_END and not event.get("completed")),
            None,
        )
        next_sell_tranche = next(
            (event for event in events if event["event_type"] == _EVENT_SELL_TRANCHE and not event.get("completed")),
            None,
        )
        next_reminder = next(
            (event for event in events if event["event_type"] in _EVENT_REMINDERS and not event.get("completed")),
            None,
        )

        countdowns = {
            "next_vest": _countdown_from_event(label="Vest Date", event=next_vest),
            "next_forfeiture_end": _countdown_from_event(
                label="Forfeiture Window End",
                event=next_forfeiture,
            ),
            "next_sell_tranche": _countdown_from_event(
                label="Sell Tranche",
                event=next_sell_tranche,
            ),
            "next_reminder": _countdown_from_event(
                label="Reminder",
                event=next_reminder,
            ),
            "next_tax_year_end": {
                "label": "UK Tax-Year End",
                "event_date": tax_year_end.isoformat(),
                "days_until": _days_until(tax_year_end, as_of_date),
                "title": "Current UK tax year closes on 5 April.",
            },
        }

        notes: list[str] = []
        if not events:
            notes.append(f"No upcoming events in the next {horizon_days} day(s).")
        if unpriced_value_events > 0:
            notes.append(
                f"{unpriced_value_events} event(s) have no live price; value-at-stake is unavailable."
            )

        return {
            "generated_at_utc": generated_at_utc,
            "as_of_date": as_of_date.isoformat(),
            "horizon_days": horizon_days,
            "event_counts": _event_type_counts(events),
            "countdowns": countdowns,
            "events": events,
            "notes": notes,
        }
