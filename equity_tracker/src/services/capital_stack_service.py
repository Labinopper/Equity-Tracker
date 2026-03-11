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

from ..settings import AppSettings
from .cash_ledger_service import CONTAINER_BANK, CONTAINER_BROKER, CashLedgerService
from .dividend_service import DividendService
from .fx_service import FxService
from .liquidation_tax_service import LiquidationTaxService
from .portfolio_service import PortfolioService

_MONEY_Q = Decimal("0.01")
_SHARE_Q = Decimal("0.0001")
_RATE_Q = Decimal("0.0001")

_CGT_RATE_FALLBACK = Decimal("0.20")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_shares(value: Decimal) -> Decimal:
    return value.quantize(_SHARE_Q, rounding=ROUND_HALF_UP)


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

        tax_projection = LiquidationTaxService.project_sell_now(
            summary=portfolio,
            settings=settings,
            as_of=as_of_date,
        )
        estimated_cgt = _q_money(
            _to_decimal(tax_projection.get("incremental_cgt_gbp")) or Decimal("0")
        )
        estimated_fees = _q_money(
            _to_decimal(tax_projection.get("estimated_fees_total_gbp")) or Decimal("0")
        )
        cgt_rate = _to_decimal(tax_projection.get("next_pound_cgt_rate")) or Decimal("0")
        cgt_rate = cgt_rate.quantize(_RATE_Q, rounding=ROUND_HALF_UP)
        cgt_rate_source = str(tax_projection.get("cgt_rate_source") or "tax_year_projection")
        fee_details = tax_projection.get("fee_details") or []
        fee_model = fee_details[0] if fee_details else {
            "method": "no_supported_broker_schedule",
            "pricing_model": getattr(settings, "broker_fee_model", "IBKR_UK_US_STOCK_FIXED"),
            "basis_note": "No supported broker fee schedule could be applied to the current sellable holdings.",
        }

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
        if estimated_cgt == Decimal("0.00"):
            notes.append(
                "Hypothetical CGT uses tax-year netting, annual exemption, and share CGT bands; "
                "the current projection produces no incremental CGT."
            )
        else:
            notes.append(
                "Hypothetical CGT uses tax-year netting, annual exemption, and share CGT bands."
            )
        if str(fee_model.get("method")) in {
            "fee_estimation_disabled_or_unpriced",
            "unsupported_market_schedule",
            "no_supported_broker_schedule",
        }:
            notes.append(
                "Estimated fees are zero because no supported broker fee schedule could be applied."
            )
        else:
            notes.append(
                "Estimated fees use a broker order schedule, not historical fee-per-share averages."
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
            "taxable_liquid_gain_gbp": _q_money(
                _to_decimal(tax_projection.get("taxable_gain_gbp")) or Decimal("0")
            ),
            "estimated_fees_gbp": _q_money(estimated_fees),
            "fee_model": {
                "method": str(fee_model.get("method") or "unknown"),
                "pricing_model": str(fee_model.get("pricing_model") or ""),
                "basis_note": str(fee_model.get("basis_note") or ""),
                "avg_fee_per_share_gbp": "0.00",
                "historical_fee_sample_qty": "0.0000",
                "historical_fee_sample_total_gbp": "0.00",
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
