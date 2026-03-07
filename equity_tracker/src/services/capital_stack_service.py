"""
CapitalStackService - deterministic capital-stack and dual-cost metrics.

Scope:
- Build a reconciled holdings-capital stack for /capital-stack.
- Keep acquisition true cost immutable.
- Add dividend-adjusted capital-at-risk as a separate metric.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from ..app_context import AppContext
from ..core.tax_engine import get_bands, marginal_cgt_rate, tax_year_for_date
from ..core.tax_engine.income_tax import personal_allowance
from ..db.models import Transaction
from ..settings import AppSettings
from .cash_ledger_service import CONTAINER_BANK, CONTAINER_BROKER, CashLedgerService
from .dividend_service import DividendService
from .fx_service import FxService
from .portfolio_service import PortfolioService

_MONEY_Q = Decimal("0.01")
_SHARE_Q = Decimal("0.0001")
_RATE_Q = Decimal("0.0001")

_CGT_RATE_FALLBACK = Decimal("0.20")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_shares(value: Decimal) -> Decimal:
    return value.quantize(_SHARE_Q, rounding=ROUND_HALF_UP)


def _q_rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE_Q, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _deployable_cash_gbp_with_fx(cash_dashboard: dict[str, Any]) -> tuple[Decimal, list[str]]:
    total = Decimal("0")
    fx_rates: dict[str, Decimal] = {"GBP": Decimal("1")}
    fx_converted: set[str] = set()
    fx_missing: set[str] = set()

    for row in cash_dashboard.get("balances", []):
        if row.get("container") not in {CONTAINER_BROKER, CONTAINER_BANK}:
            continue
        currency = str(row.get("currency") or "").strip().upper()
        if not currency:
            continue
        amount = _to_decimal(row.get("balance"))
        if amount is None:
            continue
        if currency == "GBP":
            total += amount
            continue

        fx_rate = fx_rates.get(currency)
        if fx_rate is None:
            try:
                quote = FxService.get_rate(currency, "GBP")
            except Exception:
                fx_missing.add(currency)
                continue
            fx_rate = quote.rate
            fx_rates[currency] = fx_rate
            fx_converted.add(currency)
        total += amount * fx_rate

    notes: list[str] = []
    if fx_converted:
        notes.append(
            "Deployable non-GBP cash converted to GBP using live FX: "
            + ", ".join(sorted(fx_converted))
            + "."
        )
    if fx_missing:
        notes.append(
            "Deployable non-GBP cash excluded due to unavailable FX to GBP: "
            + ", ".join(sorted(fx_missing))
            + "."
        )
    return _q_money(total), notes


def _marginal_cgt_rate_for_settings(
    *,
    settings: AppSettings | None,
    as_of: date_type,
) -> tuple[Decimal, str]:
    if settings is None:
        return _CGT_RATE_FALLBACK, "fallback_no_settings"

    tax_year = settings.default_tax_year or tax_year_for_date(as_of)
    try:
        bands = get_bands(tax_year)
    except Exception:
        return _CGT_RATE_FALLBACK, "fallback_unknown_tax_year"

    adjusted_net_income = (
        settings.default_gross_income
        - settings.default_pension_sacrifice
        + settings.default_other_income
    )
    allowance = personal_allowance(bands, adjusted_net_income)
    taxable_income = max(Decimal("0"), adjusted_net_income - allowance)
    rate = marginal_cgt_rate(bands, taxable_income)
    return _q_rate(rate), f"settings_{tax_year}"


def _estimated_fee_model(hypothetical_liquid_quantity: Decimal) -> dict[str, Decimal | str]:
    total_fees = Decimal("0")
    total_qty = Decimal("0")
    with AppContext.read_session() as sess:
        txs = list(
            sess.execute(
                select(Transaction).where(
                    Transaction.transaction_type == "DISPOSAL",
                    Transaction.is_reversal.is_(False),
                )
            ).scalars()
        )
    for tx in txs:
        if tx.broker_fees_gbp is None:
            continue
        fee = _to_decimal(tx.broker_fees_gbp)
        qty = _to_decimal(tx.quantity)
        if fee is None or qty is None or qty <= Decimal("0"):
            continue
        if fee < Decimal("0"):
            continue
        total_fees += fee
        total_qty += qty

    if total_qty <= Decimal("0") or hypothetical_liquid_quantity <= Decimal("0"):
        return {
            "estimated_fees_gbp": Decimal("0.00"),
            "avg_fee_per_share_gbp": Decimal("0.00"),
            "historical_fee_sample_qty": Decimal("0.0000"),
            "historical_fee_sample_total_gbp": Decimal("0.00"),
            "method": "no_historical_fee_sample",
        }

    avg_fee_per_share = total_fees / total_qty
    estimated_fees = hypothetical_liquid_quantity * avg_fee_per_share
    return {
        "estimated_fees_gbp": _q_money(estimated_fees),
        "avg_fee_per_share_gbp": _q_money(avg_fee_per_share),
        "historical_fee_sample_qty": _q_shares(total_qty),
        "historical_fee_sample_total_gbp": _q_money(total_fees),
        "method": "historical_avg_fee_per_share",
    }


class CapitalStackService:
    @staticmethod
    def get_snapshot(
        *,
        settings: AppSettings | None = None,
        db_path=None,
        summary=None,
        as_of: date_type | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date_type.today()
        portfolio = summary or PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
            as_of=as_of_date,
        )

        gross_market = Decimal("0")
        locked_capital = Decimal("0")
        forfeitable_capital = Decimal("0")
        hypothetical_liquid = Decimal("0")
        taxable_liquid_gain = Decimal("0")
        liquid_quantity = Decimal("0")

        employment_tax = Decimal("0")
        employment_tax_complete = True
        missing_price_lot_count = 0

        for sec in portfolio.securities:
            for ls in sec.active_lots:
                mv = ls.market_value_gbp
                if mv is None:
                    missing_price_lot_count += 1
                    continue

                gross_market += mv
                is_forfeitable_match = (
                    ls.forfeiture_risk is not None
                    and ls.forfeiture_risk.in_window
                    and ls.lot.matching_lot_id is not None
                )
                if is_forfeitable_match:
                    forfeitable_capital += mv
                    continue

                if ls.sellability_status == "LOCKED":
                    locked_capital += mv
                    continue

                hypothetical_liquid += mv
                liquid_quantity += ls.quantity_remaining

                if ls.est_employment_tax_on_lot_gbp is None:
                    employment_tax_complete = False
                else:
                    employment_tax += ls.est_employment_tax_on_lot_gbp

                if ls.lot.scheme_type != "ISA":
                    gain = mv - ls.cost_basis_total_gbp
                    if gain > Decimal("0"):
                        taxable_liquid_gain += gain

        cgt_rate, cgt_rate_source = _marginal_cgt_rate_for_settings(
            settings=settings,
            as_of=as_of_date,
        )
        estimated_cgt = _q_money(taxable_liquid_gain * cgt_rate)

        fee_model = _estimated_fee_model(liquid_quantity)
        estimated_fees = fee_model["estimated_fees_gbp"]
        if not isinstance(estimated_fees, Decimal):
            estimated_fees = Decimal("0.00")

        employment_tax_value = _q_money(employment_tax) if employment_tax_complete else None
        net_deployable = (
            _q_money(hypothetical_liquid - employment_tax_value - estimated_cgt - estimated_fees)
            if employment_tax_value is not None
            else None
        )

        dividends_payload = DividendService.get_summary(settings=settings, as_of=as_of_date)
        estimated_net_dividends = None
        if not dividends_payload.get("hide_values"):
            estimated_net_dividends = _to_decimal(
                dividends_payload.get("summary", {}).get("estimated_net_dividends_gbp")
            )
            if estimated_net_dividends is not None:
                estimated_net_dividends = _q_money(estimated_net_dividends)

        true_cost_acquisition = _q_money(portfolio.total_true_cost_gbp)
        dividend_adjusted_capital_at_risk = (
            _q_money(max(Decimal("0"), true_cost_acquisition - estimated_net_dividends))
            if estimated_net_dividends is not None
            else None
        )

        cash_dashboard = CashLedgerService.dashboard(db_path=db_path)
        gbp_deployable_cash, deployable_cash_notes = _deployable_cash_gbp_with_fx(
            cash_dashboard
        )
        combined_deployable_with_cash = (
            _q_money(net_deployable + gbp_deployable_cash)
            if net_deployable is not None
            else None
        )

        notes: list[str] = []
        if missing_price_lot_count > 0:
            notes.append(
                f"{missing_price_lot_count} lot(s) missing live prices were excluded from stack totals."
            )
        if employment_tax_value is None:
            notes.append(
                "Employment-tax estimate unavailable for at least one liquid lot; net deployable is withheld."
            )
        if estimated_net_dividends is None:
            notes.append(
                "Dividend-adjusted capital at risk unavailable while dividend values are hidden."
            )
        else:
            notes.append(
                "Dividend adjustment uses portfolio-level net dividends (post estimated dividend tax) "
                "without lot-level allocation; allocation engine is tracked separately."
            )
        if fee_model.get("method") == "no_historical_fee_sample":
            notes.append(
                "Estimated fees are zero until disposal history includes broker fees."
            )
        else:
            notes.append(
                "Estimated fees use historical average fee per disposed share."
            )
        notes.extend(deployable_cash_notes)

        return {
            "as_of_date": as_of_date.isoformat(),
            "gross_market_value_gbp": _q_money(gross_market),
            "locked_capital_gbp": _q_money(locked_capital),
            "forfeitable_capital_gbp": _q_money(forfeitable_capital),
            "hypothetical_liquid_gbp": _q_money(hypothetical_liquid),
            "hypothetical_liquid_quantity": _q_shares(liquid_quantity),
            "estimated_employment_tax_gbp": employment_tax_value,
            "employment_tax_complete": employment_tax_complete,
            "estimated_cgt_gbp": estimated_cgt,
            "cgt_marginal_rate": cgt_rate,
            "cgt_rate_source": cgt_rate_source,
            "taxable_liquid_gain_gbp": _q_money(taxable_liquid_gain),
            "estimated_fees_gbp": _q_money(estimated_fees),
            "fee_model": {
                "method": fee_model["method"],
                "avg_fee_per_share_gbp": str(fee_model["avg_fee_per_share_gbp"]),
                "historical_fee_sample_qty": str(fee_model["historical_fee_sample_qty"]),
                "historical_fee_sample_total_gbp": str(fee_model["historical_fee_sample_total_gbp"]),
            },
            "net_deployable_today_gbp": net_deployable,
            "holdings_net_deployable_today_gbp": net_deployable,
            "true_cost_acquisition_gbp": true_cost_acquisition,
            "estimated_net_dividends_gbp": estimated_net_dividends,
            "dividend_adjusted_capital_at_risk_gbp": dividend_adjusted_capital_at_risk,
            "cash_totals_by_currency": cash_dashboard.get("totals_by_currency", []),
            "gbp_deployable_cash_gbp": gbp_deployable_cash,
            "combined_deployable_with_cash_gbp": combined_deployable_with_cash,
            "notes": notes,
        }
