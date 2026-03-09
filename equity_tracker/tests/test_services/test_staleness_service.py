from __future__ import annotations

from datetime import date, datetime, timezone

from src.services.staleness_service import StalenessService


def test_price_is_not_stale_when_market_is_closed() -> None:
    now_utc = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)  # 08:00 New York

    assert StalenessService.is_price_stale(
        date(2026, 3, 6),
        exchange="NASDAQ",
        stale_after_days=1,
        today=date(2026, 3, 9),
        now_utc=now_utc,
    ) is False


def test_price_is_stale_when_market_is_open_and_price_date_is_old() -> None:
    now_utc = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc)  # 11:00 New York

    assert StalenessService.is_price_stale(
        date(2026, 3, 6),
        exchange="NASDAQ",
        stale_after_days=1,
        today=date(2026, 3, 9),
        now_utc=now_utc,
    ) is True
