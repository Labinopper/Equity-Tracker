"""
AnalyticsService - additive analytics payloads for the /analytics dashboard.

Phase 1 scope:
- Portfolio value over time (price history x active quantities)
- Group A summary widgets (scheme/security concentration, liquidity, unrealised P&L)

No write operations are performed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from ..app_context import AppContext
from ..core.tax_engine import available_tax_years, get_bands, tax_year_for_date
from ..core.tax_engine.context import TaxContext
from ..db.models import PriceHistory, Transaction
from ..settings import AppSettings
from .portfolio_service import PortfolioService
from .report_service import ReportService
from .risk_service import RiskService

_GBP_Q = Decimal("0.01")
_TOTAL_LABEL = "Total"


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money_str(value: Decimal) -> str:
    return str(_q_money(value))


def _pct_str(part: Decimal, whole: Decimal) -> str:
    if whole <= Decimal("0"):
        return "0.00"
    return str(_q_money((part / whole) * Decimal("100")))


def _hidden_widget(
    *,
    widget_id: str,
    title: str,
    subtitle: str,
) -> dict[str, Any]:
    return {
        "widget_id": widget_id,
        "title": title,
        "subtitle": subtitle,
        "hidden": True,
        "has_data": False,
        "reason": "Values hidden by privacy mode.",
        "rows": [],
    }


class AnalyticsService:
    """
    Read-only analytics payload assembler.
    """

    @staticmethod
    def get_portfolio_over_time(
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        """
        Build daily portfolio value points from price_history and active quantities.

        Uses current active quantities with historical prices and carries forward
        each security's last known close between observed price dates.
        """
        payload: dict[str, Any] = {
            "widget_id": "portfolio-value-time",
            "title": "Portfolio Value Over Time",
            "subtitle": "Daily GBP portfolio value from stored closes and active quantities.",
            "hidden": False,
            "has_data": False,
            "reason": None,
            "price_as_of": None,
            "labels": [],
            "values_gbp": [],
            "points": [],
            "notes": [],
        }

        if bool(settings and settings.hide_values):
            payload["hidden"] = True
            payload["reason"] = "Values hidden by privacy mode."
            payload["notes"] = ["Monetary series is suppressed while hide-values mode is enabled."]
            return payload

        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )
        quantity_by_security: dict[str, Decimal] = {
            ss.security.id: Decimal(ss.total_quantity)
            for ss in summary.securities
            if Decimal(ss.total_quantity) > Decimal("0")
        }
        total_security_count = len(quantity_by_security)

        if total_security_count == 0:
            payload["reason"] = "No active lots available."
            return payload

        price_by_security = AnalyticsService._price_history_by_security(quantity_by_security)
        all_dates = sorted(
            {
                px_date
                for by_date in price_by_security.values()
                for px_date in by_date
            }
        )

        if not all_dates:
            payload["reason"] = "No GBP price history available for active holdings."
            return payload

        latest_price: dict[str, Decimal] = {}
        points: list[dict[str, Any]] = []
        partial_day_count = 0

        for px_date in all_dates:
            total_value = Decimal("0")
            priced_security_count = 0

            for security_id, qty in quantity_by_security.items():
                by_date = price_by_security.get(security_id, {})
                if px_date in by_date:
                    latest_price[security_id] = by_date[px_date]

                price = latest_price.get(security_id)
                if price is None:
                    continue

                total_value += _q_money(price * qty)
                priced_security_count += 1

            if priced_security_count == 0:
                continue

            if priced_security_count < total_security_count:
                partial_day_count += 1

            points.append(
                {
                    "date": px_date.isoformat(),
                    "total_value_gbp": _money_str(total_value),
                    "priced_security_count": priced_security_count,
                    "total_security_count": total_security_count,
                }
            )

        if not points:
            payload["reason"] = "Insufficient overlapping price history to build a time series."
            return payload

        securities_without_prices = sum(
            1 for security_id in quantity_by_security if not price_by_security.get(security_id)
        )

        notes: list[str] = [
            "Series uses last available close carry-forward between observed price dates.",
        ]
        if securities_without_prices > 0:
            notes.append(
                f"{securities_without_prices} active security(ies) excluded due to missing GBP price history."
            )
        if partial_day_count > 0:
            notes.append(
                f"{partial_day_count} day(s) are partial due to missing prices for one or more securities."
            )

        payload["has_data"] = True
        payload["labels"] = [point["date"] for point in points]
        payload["values_gbp"] = [point["total_value_gbp"] for point in points]
        payload["points"] = points
        payload["price_as_of"] = points[-1]["date"]
        payload["notes"] = notes

        return payload

    @staticmethod
    def get_summary(
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        """
        Build Group A analytics widgets as one JSON payload.
        """
        generated_at_utc = datetime.now(timezone.utc).isoformat()
        hide_values = bool(settings and settings.hide_values)

        portfolio_over_time = AnalyticsService.get_portfolio_over_time(settings=settings)

        tax_position = AnalyticsService.get_tax_position(settings=settings)

        if hide_values:
            return {
                "generated_at_utc": generated_at_utc,
                "hide_values": True,
                "widgets": {
                    "portfolio_value_time": portfolio_over_time,
                    "scheme_concentration": _hidden_widget(
                        widget_id="scheme-concentration",
                        title="Value by Scheme",
                        subtitle="Share of current market value by holding scheme.",
                    ),
                    "security_concentration": _hidden_widget(
                        widget_id="security-concentration",
                        title="Top Holdings",
                        subtitle="Largest holdings by current market value share.",
                    ),
                    "liquidity_breakdown": _hidden_widget(
                        widget_id="liquidity-breakdown",
                        title="Sellable vs Locked vs At-Risk",
                        subtitle="Current liquidity classification split by market value.",
                    ),
                    "unrealised_pnl": _hidden_widget(
                        widget_id="unrealised-pnl",
                        title="Unrealised P&L by Security",
                        subtitle="Cost basis and true cost versus current market value.",
                    ),
                    "cgt_year_position": _hidden_widget(
                        widget_id="cgt-year-position",
                        title="Tax-Year CGT Position",
                        subtitle="Annual exempt amount, realised gains, and remaining allowance.",
                    ),
                    "gain_loss_history": _hidden_widget(
                        widget_id="gain-loss-history",
                        title="Gain/Loss by Tax Year",
                        subtitle="Realised gains and losses split by UK tax year.",
                    ),
                    "economic_vs_tax": _hidden_widget(
                        widget_id="economic-vs-tax",
                        title="Economic vs Tax P&L",
                        subtitle="Net economic gain versus CGT-basis gain by tax year.",
                    ),
                },
                "notes": ["Monetary analytics widgets are hidden while privacy mode is enabled."],
            }

        risk_summary = RiskService.get_risk_summary(settings=settings)
        portfolio_summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )

        scheme_rows = [
            {
                "key": row.key,
                "label": row.label,
                "value_gbp": str(row.value_gbp),
                "pct_of_total": str(row.pct_of_total),
            }
            for row in risk_summary.scheme_concentration
        ]

        security_rows = [
            {
                "key": row.key,
                "label": row.label,
                "value_gbp": str(row.value_gbp),
                "pct_of_total": str(row.pct_of_total),
            }
            for row in risk_summary.security_concentration[:10]
        ]

        liquidity = risk_summary.liquidity
        if liquidity is None:
            liquidity_rows: list[dict[str, str]] = []
            liquidity_reason = "Liquidity data unavailable."
        else:
            liquidity_rows = [
                {
                    "category": "Sellable",
                    "value_gbp": str(liquidity.sellable_gbp),
                    "pct_of_classified": str(liquidity.sellable_pct),
                },
                {
                    "category": "Locked",
                    "value_gbp": str(liquidity.locked_gbp),
                    "pct_of_classified": str(liquidity.locked_pct),
                },
                {
                    "category": "At Risk",
                    "value_gbp": str(liquidity.at_risk_gbp),
                    "pct_of_classified": str(liquidity.at_risk_pct),
                },
                {
                    "category": _TOTAL_LABEL,
                    "value_gbp": str(liquidity.classified_total_gbp),
                    "pct_of_classified": "100.00",
                },
            ]
            liquidity_reason = None

        unrealised_rows: list[dict[str, str]] = []
        sortable_rows: list[tuple[Decimal, dict[str, str]]] = []

        for security_summary in portfolio_summary.securities:
            market_value = security_summary.market_value_gbp
            if market_value is None:
                continue

            market_value_d = _q_money(Decimal(market_value))
            cost_basis = _q_money(Decimal(security_summary.total_cost_basis_gbp))
            true_cost = _q_money(Decimal(security_summary.total_true_cost_gbp))

            unrealised_tax = security_summary.unrealised_gain_cgt_gbp
            if unrealised_tax is None:
                unrealised_tax = _q_money(market_value_d - cost_basis)
            else:
                unrealised_tax = _q_money(Decimal(unrealised_tax))

            unrealised_economic = security_summary.unrealised_gain_economic_gbp
            if unrealised_economic is None:
                unrealised_economic = _q_money(market_value_d - true_cost)
            else:
                unrealised_economic = _q_money(Decimal(unrealised_economic))

            row = {
                "security_id": security_summary.security.id,
                "ticker": security_summary.security.ticker,
                "cost_basis_gbp": _money_str(cost_basis),
                "true_cost_gbp": _money_str(true_cost),
                "market_value_gbp": _money_str(market_value_d),
                "unrealised_gain_tax_basis_gbp": _money_str(unrealised_tax),
                "unrealised_gain_economic_gbp": _money_str(unrealised_economic),
                "market_value_pct": _pct_str(
                    market_value_d,
                    Decimal(risk_summary.total_market_value_gbp),
                ),
            }
            sortable_rows.append((market_value_d, row))

        sortable_rows.sort(key=lambda item: item[0], reverse=True)
        unrealised_rows = [row for _, row in sortable_rows]

        notes = list(risk_summary.notes)
        notes.extend(tax_position.get("notes", []))
        notes.append("Group C and D chart widgets remain placeholders until their source EPICs ship.")

        return {
            "generated_at_utc": generated_at_utc,
            "hide_values": hide_values,
            "widgets": {
                "portfolio_value_time": portfolio_over_time,
                "scheme_concentration": {
                    "widget_id": "scheme-concentration",
                    "title": "Value by Scheme",
                    "subtitle": "Share of current market value by holding scheme.",
                    "hidden": False,
                    "has_data": bool(scheme_rows),
                    "reason": None if scheme_rows else "No priced holdings available.",
                    "rows": scheme_rows,
                },
                "security_concentration": {
                    "widget_id": "security-concentration",
                    "title": "Top Holdings",
                    "subtitle": "Largest holdings by current market value share.",
                    "hidden": False,
                    "has_data": bool(security_rows),
                    "reason": None if security_rows else "No priced holdings available.",
                    "rows": security_rows,
                },
                "liquidity_breakdown": {
                    "widget_id": "liquidity-breakdown",
                    "title": "Sellable vs Locked vs At-Risk",
                    "subtitle": "Current liquidity classification split by market value.",
                    "hidden": False,
                    "has_data": bool(liquidity_rows),
                    "reason": liquidity_reason,
                    "rows": liquidity_rows,
                    "unpriced_lot_count": liquidity.unpriced_lot_count if liquidity else 0,
                },
                "unrealised_pnl": {
                    "widget_id": "unrealised-pnl",
                    "title": "Unrealised P&L by Security",
                    "subtitle": "Cost basis and true cost versus current market value.",
                    "hidden": False,
                    "has_data": bool(unrealised_rows),
                    "reason": None if unrealised_rows else "No priced holdings available.",
                    "rows": unrealised_rows,
                },
                "cgt_year_position": tax_position["widgets"]["cgt_year_position"],
                "gain_loss_history": tax_position["widgets"]["gain_loss_history"],
                "economic_vs_tax": tax_position["widgets"]["economic_vs_tax"],
            },
            "notes": notes,
        }

    @staticmethod
    def get_tax_position(
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        """
        Build Group B analytics widgets (tax and return context).
        """
        active_tax_year = AnalyticsService._active_tax_year(settings=settings)

        if bool(settings and settings.hide_values):
            return {
                "active_tax_year": active_tax_year,
                "widgets": {
                    "cgt_year_position": _hidden_widget(
                        widget_id="cgt-year-position",
                        title="Tax-Year CGT Position",
                        subtitle="Annual exempt amount, realised gains, and remaining allowance.",
                    ),
                    "gain_loss_history": _hidden_widget(
                        widget_id="gain-loss-history",
                        title="Gain/Loss by Tax Year",
                        subtitle="Realised gains and losses split by UK tax year.",
                    ),
                    "economic_vs_tax": _hidden_widget(
                        widget_id="economic-vs-tax",
                        title="Economic vs Tax P&L",
                        subtitle="Net economic gain versus CGT-basis gain by tax year.",
                    ),
                },
                "notes": ["Tax and returns widgets are hidden while privacy mode is enabled."],
            }

        tax_context = AnalyticsService._tax_context_for_year(
            settings=settings,
            tax_year=active_tax_year,
        )
        active_cgt = ReportService.cgt_summary(active_tax_year, tax_context=tax_context)
        active_aea = get_bands(active_tax_year).cgt_annual_exempt_amount
        active_positive_gain = max(active_cgt.net_gain_gbp, Decimal("0"))
        active_remaining_aea = max(active_aea - active_positive_gain, Decimal("0"))
        active_taxable_gain = max(active_positive_gain - active_aea, Decimal("0"))

        cgt_year_rows = [
            {
                "tax_year": active_tax_year,
                "annual_exempt_amount_gbp": _money_str(active_aea),
                "realised_gains_gbp": _money_str(active_cgt.total_gains_gbp),
                "realised_losses_gbp": _money_str(active_cgt.total_losses_gbp),
                "net_gain_gbp": _money_str(active_cgt.net_gain_gbp),
                "remaining_aea_gbp": _money_str(active_remaining_aea),
                "taxable_gain_gbp": _money_str(
                    active_cgt.cgt_result.taxable_gain
                    if active_cgt.cgt_result is not None
                    else active_taxable_gain
                ),
                "total_cgt_gbp": _money_str(
                    active_cgt.cgt_result.total_cgt
                    if active_cgt.cgt_result is not None
                    else Decimal("0")
                ),
            }
        ]

        history_rows: list[dict[str, str]] = []
        economic_vs_tax_rows: list[dict[str, str]] = []
        tax_years_with_disposals = AnalyticsService._tax_years_with_disposals()

        for tax_year in tax_years_with_disposals:
            cgt_report = ReportService.cgt_summary(tax_year)
            eco_report = ReportService.economic_gain_summary(tax_year)

            history_rows.append(
                {
                    "tax_year": tax_year,
                    "gains_gbp": _money_str(cgt_report.total_gains_gbp),
                    "losses_gbp": _money_str(cgt_report.total_losses_gbp),
                    "net_gain_gbp": _money_str(cgt_report.net_gain_gbp),
                }
            )

            economic_vs_tax_rows.append(
                {
                    "tax_year": tax_year,
                    "cgt_net_gain_gbp": _money_str(cgt_report.net_gain_gbp),
                    "economic_net_gain_gbp": _money_str(eco_report.net_economic_gain_gbp),
                    "delta_gbp": _money_str(
                        eco_report.net_economic_gain_gbp - cgt_report.net_gain_gbp
                    ),
                }
            )

        notes = [
            f"CGT position is keyed to tax year {active_tax_year}.",
        ]
        if active_cgt.cgt_result is None:
            notes.append("Total CGT is shown as 0.00 when income settings are unavailable.")
        if not history_rows:
            notes.append("No disposal history found for gain/loss tax-year charts.")

        return {
            "active_tax_year": active_tax_year,
            "widgets": {
                "cgt_year_position": {
                    "widget_id": "cgt-year-position",
                    "title": "Tax-Year CGT Position",
                    "subtitle": "Annual exempt amount, realised gains, and remaining allowance.",
                    "hidden": False,
                    "has_data": bool(cgt_year_rows),
                    "reason": None if cgt_year_rows else "No tax-year position data available.",
                    "rows": cgt_year_rows,
                },
                "gain_loss_history": {
                    "widget_id": "gain-loss-history",
                    "title": "Gain/Loss by Tax Year",
                    "subtitle": "Realised gains and losses split by UK tax year.",
                    "hidden": False,
                    "has_data": bool(history_rows),
                    "reason": (
                        None
                        if history_rows
                        else "No taxable disposal history available."
                    ),
                    "rows": history_rows,
                },
                "economic_vs_tax": {
                    "widget_id": "economic-vs-tax",
                    "title": "Economic vs Tax P&L",
                    "subtitle": "Net economic gain versus CGT-basis gain by tax year.",
                    "hidden": False,
                    "has_data": bool(economic_vs_tax_rows),
                    "reason": (
                        None
                        if economic_vs_tax_rows
                        else "No disposal history available for comparison."
                    ),
                    "rows": economic_vs_tax_rows,
                },
            },
            "notes": notes,
        }

    @staticmethod
    def _price_history_by_security(
        quantity_by_security: dict[str, Decimal],
    ) -> dict[str, dict[date, Decimal]]:
        """
        Return latest GBP close per security per date for active securities.
        """
        security_ids = list(quantity_by_security.keys())
        if not security_ids:
            return {}

        by_security: dict[str, dict[date, Decimal]] = {security_id: {} for security_id in security_ids}

        with AppContext.read_session() as sess:
            rows = list(
                sess.scalars(
                    select(PriceHistory)
                    .where(PriceHistory.security_id.in_(security_ids))
                    .order_by(
                        PriceHistory.security_id.asc(),
                        PriceHistory.price_date.asc(),
                        PriceHistory.fetched_at.asc(),
                        PriceHistory.created_at.asc(),
                    )
                ).all()
            )

        for row in rows:
            value = _to_decimal(row.close_price_gbp)
            if value is None and (row.currency or "").upper() == "GBP":
                value = _to_decimal(row.close_price_original_ccy)
            if value is None:
                continue
            by_security[row.security_id][row.price_date] = value

        return by_security

    @staticmethod
    def _active_tax_year(settings: AppSettings | None) -> str:
        supported = available_tax_years()
        preferred = settings.default_tax_year if settings is not None else None
        if preferred in supported:
            return preferred
        return supported[-1]

    @staticmethod
    def _tax_context_for_year(
        *,
        settings: AppSettings | None,
        tax_year: str,
    ) -> TaxContext | None:
        if settings is None:
            return None
        return TaxContext(
            tax_year=tax_year,
            gross_employment_income=settings.default_gross_income,
            pension_sacrifice=settings.default_pension_sacrifice,
            other_income=settings.default_other_income,
            student_loan_plan=settings.default_student_loan_plan,
        )

    @staticmethod
    def _tax_years_with_disposals() -> list[str]:
        with AppContext.read_session() as sess:
            disposal_dates = list(
                sess.scalars(
                    select(Transaction.transaction_date)
                    .where(Transaction.transaction_type == "DISPOSAL")
                ).all()
            )
        if not disposal_dates:
            return []
        return sorted({tax_year_for_date(d) for d in disposal_dates})
