"""
BrokerFeeService - deterministic broker commission estimates.

Scope:
- Estimate fees per broker order, not per lot.
- Start with IBKR UK client schedules for US stock disposals.
- Return explicit assumptions so UI surfaces can explain the estimate basis.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..settings import AppSettings

_MONEY_Q = Decimal("0.01")
_FX_Q = Decimal("0.0001")
_USD = "USD"

MODEL_IBKR_UK_US_STOCK_FIXED = "IBKR_UK_US_STOCK_FIXED"
MODEL_IBKR_UK_US_STOCK_TIERED = "IBKR_UK_US_STOCK_TIERED"


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_fx(value: Decimal) -> Decimal:
    return value.quantize(_FX_Q, rounding=ROUND_HALF_UP)


def _effective_model(settings: AppSettings | None) -> str:
    raw = getattr(settings, "broker_fee_model", "") if settings is not None else ""
    model = str(raw or MODEL_IBKR_UK_US_STOCK_FIXED).strip().upper()
    if model not in {
        MODEL_IBKR_UK_US_STOCK_FIXED,
        MODEL_IBKR_UK_US_STOCK_TIERED,
    }:
        return MODEL_IBKR_UK_US_STOCK_FIXED
    return model


class BrokerFeeService:
    @staticmethod
    def estimate_order_fee(
        *,
        security_currency: str | None,
        quantity: Decimal,
        price_native: Decimal | None,
        price_gbp: Decimal | None,
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        model = _effective_model(settings)
        fee_enabled = (
            True
            if settings is None
            else bool(getattr(settings, "broker_fee_estimation_enabled", True))
        )
        currency = str(security_currency or "").strip().upper()

        if (
            not fee_enabled
            or quantity <= Decimal("0")
            or price_native is None
            or price_gbp is None
            or price_native <= Decimal("0")
            or price_gbp <= Decimal("0")
        ):
            return {
                "estimated_fee_native": Decimal("0.00"),
                "estimated_fee_gbp": Decimal("0.00"),
                "native_currency": currency or _USD,
                "pricing_model": model,
                "method": "fee_estimation_disabled_or_unpriced",
                "basis_note": "Fee estimate unavailable because pricing or settings are incomplete.",
                "assumptions": [],
            }

        if currency != _USD:
            return {
                "estimated_fee_native": Decimal("0.00"),
                "estimated_fee_gbp": Decimal("0.00"),
                "native_currency": currency or "",
                "pricing_model": model,
                "method": "unsupported_market_schedule",
                "basis_note": f"No broker fee schedule is configured yet for {currency or 'unknown'} securities.",
                "assumptions": [],
            }

        fx_gbp_per_native = _q_fx(price_gbp / price_native)
        trade_value_native = quantity * price_native

        if model == MODEL_IBKR_UK_US_STOCK_TIERED:
            fee_native = Decimal("0.35")
            basis_note = (
                "IBKR UK US stocks tiered base minimum of $0.35 per order; "
                "exchange and regulatory pass-throughs are not yet added."
            )
            method = "ibkr_uk_us_stock_tiered_minimum"
        else:
            fee_native = max(Decimal("0.005") * quantity, Decimal("1.00"))
            basis_note = (
                "IBKR UK US stocks fixed pricing at $0.005/share with $1.00 minimum per order."
            )
            method = "ibkr_uk_us_stock_fixed"

        fee_gbp = _q_money(fee_native * fx_gbp_per_native)
        return {
            "estimated_fee_native": _q_money(fee_native),
            "estimated_fee_gbp": fee_gbp,
            "native_currency": _USD,
            "pricing_model": model,
            "method": method,
            "basis_note": basis_note,
            "assumptions": [
                f"Trade value native: {_q_money(trade_value_native)} {_USD}.",
                f"FX implied from current price snapshot: {_q_fx(fx_gbp_per_native)} GBP/{_USD}.",
            ],
        }
