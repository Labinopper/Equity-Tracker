"""Deterministic alert-center and concentration guardrail service."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..settings import AppSettings
from .exposure_service import ExposureService
from .portfolio_service import PortfolioService

_MONEY_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_DEFAULT_TOP_HOLDING_ALERT_PCT = Decimal("50")
_DEFAULT_EMPLOYER_ALERT_PCT = Decimal("40")
_FORFEITURE_SOON_DAYS = 45
_VEST_SOON_DAYS = 30


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _safe_decimal(value: object, fallback: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _threshold(settings: AppSettings | None, attr: str, fallback: Decimal) -> Decimal:
    if settings is None:
        return fallback
    value = _safe_decimal(getattr(settings, attr, fallback), fallback)
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("100"):
        return Decimal("100")
    return _q_pct(value)


def _severity_rank(severity: str) -> int:
    order = {"critical": 0, "warning": 1, "info": 2}
    return order.get((severity or "info").lower(), 9)


class AlertService:
    @staticmethod
    def concentration_thresholds(settings: AppSettings | None) -> dict[str, Decimal]:
        return {
            "top_holding_pct": _threshold(
                settings,
                "concentration_top_holding_alert_pct",
                _DEFAULT_TOP_HOLDING_ALERT_PCT,
            ),
            "employer_pct": _threshold(
                settings,
                "concentration_employer_alert_pct",
                _DEFAULT_EMPLOYER_ALERT_PCT,
            ),
        }

    @staticmethod
    def get_alert_center(
        *,
        settings: AppSettings | None,
        db_path,
    ) -> dict[str, Any]:
        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )
        exposure = ExposureService.get_snapshot(
            settings=settings,
            db_path=db_path,
            summary=summary,
        )

        thresholds = AlertService.concentration_thresholds(settings)
        top_threshold = thresholds["top_holding_pct"]
        employer_threshold = thresholds["employer_pct"]

        today = date.today()
        alerts: list[dict[str, Any]] = []

        top_holding_pct = _q_pct(_safe_decimal(exposure.get("top_holding_pct_gross")))
        if top_holding_pct > top_threshold:
            alerts.append(
                {
                    "id": "concentration_top_holding",
                    "severity": "warning",
                    "title": "Top-Holding Concentration Breach",
                    "message": (
                        f"Top holding is {top_holding_pct}% vs threshold {top_threshold}%."
                    ),
                    "href": "/risk#concentration-guardrails",
                    "event_date": today.isoformat(),
                }
            )

        employer_ticker = str(exposure.get("employer_ticker") or "").strip().upper()
        employer_pct = _q_pct(_safe_decimal(exposure.get("employer_pct_of_gross")))
        if employer_ticker and employer_pct > employer_threshold:
            alerts.append(
                {
                    "id": "concentration_employer",
                    "severity": "warning",
                    "title": "Employer Exposure Breach",
                    "message": (
                        f"{employer_ticker} is {employer_pct}% of gross vs threshold {employer_threshold}%."
                    ),
                    "href": "/risk#concentration-guardrails",
                    "event_date": today.isoformat(),
                }
            )

        forfeiture_count = 0
        forfeiture_value = Decimal("0")
        soonest_forfeiture_days: int | None = None

        vest_count = 0
        vest_value = Decimal("0")
        soonest_vest_days: int | None = None

        stale_price_count = 0
        stale_fx_count = 0

        for security_summary in summary.securities:
            if security_summary.price_is_stale:
                stale_price_count += 1
            if security_summary.fx_is_stale:
                stale_fx_count += 1

            for lot_summary in security_summary.active_lots:
                lot = lot_summary.lot
                risk = lot_summary.forfeiture_risk
                market_value = (
                    _q_money(Decimal(lot_summary.market_value_gbp))
                    if lot_summary.market_value_gbp is not None
                    else Decimal("0")
                )

                if (
                    risk is not None
                    and risk.in_window
                    and lot.scheme_type == "ESPP_PLUS"
                    and lot.matching_lot_id is not None
                    and risk.days_remaining <= _FORFEITURE_SOON_DAYS
                ):
                    forfeiture_count += 1
                    forfeiture_value += market_value
                    if soonest_forfeiture_days is None or risk.days_remaining < soonest_forfeiture_days:
                        soonest_forfeiture_days = risk.days_remaining

                if lot.scheme_type == "RSU" and lot.acquisition_date >= today:
                    days_until_vest = (lot.acquisition_date - today).days
                    if days_until_vest <= _VEST_SOON_DAYS:
                        vest_count += 1
                        vest_value += market_value
                        if soonest_vest_days is None or days_until_vest < soonest_vest_days:
                            soonest_vest_days = days_until_vest

        if forfeiture_count > 0:
            value_text = (
                ""
                if bool(settings and settings.hide_values)
                else f" Value at risk: GBP {_q_money(forfeiture_value)}."
            )
            alerts.append(
                {
                    "id": "forfeiture_ending_soon",
                    "severity": "warning",
                    "title": "Forfeiture Window Nearing End",
                    "message": (
                        f"{forfeiture_count} matched lot(s) are within {_FORFEITURE_SOON_DAYS} days "
                        f"of release (nearest in {soonest_forfeiture_days}d).{value_text}"
                    ),
                    "href": "/calendar",
                    "event_date": today.isoformat(),
                }
            )

        if vest_count > 0:
            value_text = (
                ""
                if bool(settings and settings.hide_values)
                else f" Value entering sellable pool: GBP {_q_money(vest_value)}."
            )
            alerts.append(
                {
                    "id": "vesting_soon",
                    "severity": "info",
                    "title": "RSU Vesting Soon",
                    "message": (
                        f"{vest_count} RSU lot(s) vest within {_VEST_SOON_DAYS} days "
                        f"(nearest in {soonest_vest_days}d).{value_text}"
                    ),
                    "href": "/calendar",
                    "event_date": today.isoformat(),
                }
            )

        gross_income = _safe_decimal(getattr(settings, "default_gross_income", Decimal("0"))) if settings else Decimal("0")
        other_income = _safe_decimal(getattr(settings, "default_other_income", Decimal("0"))) if settings else Decimal("0")
        if gross_income <= 0 and other_income <= 0:
            alerts.append(
                {
                    "id": "tax_inputs_stale",
                    "severity": "warning",
                    "title": "Tax Inputs Incomplete",
                    "message": "Income inputs are zero or missing; employment-tax outputs may be understated.",
                    "href": "/settings",
                    "event_date": today.isoformat(),
                }
            )

        if stale_price_count > 0 or stale_fx_count > 0:
            alerts.append(
                {
                    "id": "market_data_stale",
                    "severity": "info",
                    "title": "Market Data Freshness",
                    "message": (
                        f"{stale_price_count} security(ies) have stale price data and "
                        f"{stale_fx_count} security(ies) have stale FX basis."
                    ),
                    "href": "/data-quality",
                    "event_date": today.isoformat(),
                }
            )

        alerts.sort(key=lambda row: (_severity_rank(str(row.get("severity"))), str(row.get("event_date") or ""), str(row.get("id") or "")))

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "thresholds": {
                "top_holding_pct": str(top_threshold),
                "employer_pct": str(employer_threshold),
            },
            "total": len(alerts),
            "alerts": alerts,
        }
