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
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..settings import AppSettings
from .portfolio_service import PortfolioService

_GBP_Q = Decimal("0.01")
_DEFAULT_HORIZON_DAYS = 400
_MAX_HORIZON_DAYS = 1460

_EVENT_VEST_DATE = "VEST_DATE"
_EVENT_FORFEITURE_END = "FORFEITURE_END"
_EVENT_SELL_TRANCHE = "SELL_TRANCHE"
_EVENT_TAX_YEAR_END = "TAX_YEAR_END"
_EVENT_TAX_YEAR_START = "TAX_YEAR_START"

_EVENT_PRIORITY: dict[str, int] = {
    _EVENT_FORFEITURE_END: 0,
    _EVENT_VEST_DATE: 1,
    _EVENT_SELL_TRANCHE: 2,
    _EVENT_TAX_YEAR_END: 3,
    _EVENT_TAX_YEAR_START: 4,
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


def _next_tax_year_end(as_of: date) -> date:
    # UK tax year ends on 5 April.
    if as_of.month > 4 or (as_of.month == 4 and as_of.day >= 6):
        return date(as_of.year + 1, 4, 5)
    return date(as_of.year, 4, 5)


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
    return {
        "total": len(events),
        "vest_dates": vest,
        "forfeiture_windows": forfeiture,
        "sell_tranches": sell_tranches,
        "tax_markers": tax,
    }


class CalendarService:
    """
    Build timeline events and countdown summaries from current lot state.
    """

    @staticmethod
    def get_events_payload(
        *,
        settings: AppSettings | None = None,
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

        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )

        events: list[dict[str, Any]] = []
        unpriced_value_events = 0

        for security_summary in summary.securities:
            ticker = security_summary.security.ticker
            security_id = security_summary.security.id

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
                        "quantity": str(lot_summary.quantity_remaining),
                        "value_at_stake_gbp": _money_str(lot_summary.market_value_gbp),
                        "has_live_value": has_live_value,
                    }
                )

        tax_year_end = _next_tax_year_end(as_of_date)
        tax_year_start = tax_year_end + timedelta(days=1)

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
                }
            )

        if sell_plan_events:
            events.extend(sell_plan_events)

        events.sort(
            key=lambda event: (
                event["event_date"],
                _EVENT_PRIORITY.get(event["event_type"], 99),
                event["ticker"] or "",
                event["title"],
            )
        )

        next_vest = next(
            (event for event in events if event["event_type"] == _EVENT_VEST_DATE),
            None,
        )
        next_forfeiture = next(
            (event for event in events if event["event_type"] == _EVENT_FORFEITURE_END),
            None,
        )
        next_sell_tranche = next(
            (event for event in events if event["event_type"] == _EVENT_SELL_TRANCHE),
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
