from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository import PriceRepository
from src.services.history_service import _dedup_daily_rows
from src.services.portfolio_service import PortfolioService


def test_dedup_daily_rows_includes_twelve_data_rows(app_context) -> None:
    security = PortfolioService.add_security(
        "TDHIST",
        "Twelve Data History Corp",
        "USD",
        is_manual_override=True,
    )

    with AppContext.write_session() as sess:
        repo = PriceRepository(sess)
        repo.upsert(
            security_id=security.id,
            price_date=date(2026, 3, 7),
            close_price_original_ccy="100.00",
            close_price_gbp="79.00",
            currency="USD",
            source="yfinance_history",
        )
        repo.upsert(
            security_id=security.id,
            price_date=date(2026, 3, 7),
            close_price_original_ccy="101.00",
            close_price_gbp="80.00",
            currency="USD",
            source="twelvedata:2026-03-07",
        )

    with AppContext.read_session() as sess:
        rows = PriceRepository(sess).get_history_range(security.id)

    result = _dedup_daily_rows(rows, "USD")
    assert result[date(2026, 3, 7)] == Decimal("80.00")
