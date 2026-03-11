from decimal import Decimal

from src.services.broker_fee_service import (
    BrokerFeeService,
    MODEL_IBKR_UK_US_STOCK_FIXED,
    MODEL_IBKR_UK_US_STOCK_TIERED,
)
from src.settings import AppSettings


def test_ibkr_fixed_uses_one_dollar_minimum():
    settings = AppSettings()
    settings.broker_fee_model = MODEL_IBKR_UK_US_STOCK_FIXED

    result = BrokerFeeService.estimate_order_fee(
        security_currency="USD",
        quantity=Decimal("10.5186"),
        price_native=Decimal("248.21"),
        price_gbp=Decimal("185.1225"),
        settings=settings,
    )

    assert result["estimated_fee_native"] == Decimal("1.00")
    assert result["estimated_fee_gbp"] == Decimal("0.75")


def test_ibkr_tiered_uses_base_minimum():
    settings = AppSettings()
    settings.broker_fee_model = MODEL_IBKR_UK_US_STOCK_TIERED

    result = BrokerFeeService.estimate_order_fee(
        security_currency="USD",
        quantity=Decimal("10.5186"),
        price_native=Decimal("248.21"),
        price_gbp=Decimal("185.1225"),
        settings=settings,
    )

    assert result["estimated_fee_native"] == Decimal("0.35")
    assert result["estimated_fee_gbp"] == Decimal("0.26")
