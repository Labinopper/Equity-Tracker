from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.services.twelve_data_price_service import (
    RefreshCandidate,
    build_refresh_plan,
    market_window_for_exchange,
)


def test_market_window_for_lse_open_hours() -> None:
    now_utc = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
    window = market_window_for_exchange("LSE", now_utc=now_utc)

    assert window.is_open is True
    assert window.minutes_until_close > 300


def test_market_window_for_nasdaq_before_open() -> None:
    now_utc = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    window = market_window_for_exchange("NASDAQ", now_utc=now_utc)

    assert window.is_open is False
    assert window.minutes_until_close == 0


def test_refresh_plan_prioritises_unrefreshed_and_heavier_positions() -> None:
    now_utc = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc)
    candidates = [
        RefreshCandidate(
            security_id="large-unseen",
            ticker="AAA",
            exchange="NASDAQ",
            weight=Decimal("1000"),
            minutes_until_close=120,
            last_refreshed_at=None,
        ),
        RefreshCandidate(
            security_id="small-due",
            ticker="BBB",
            exchange="NASDAQ",
            weight=Decimal("20"),
            minutes_until_close=120,
            last_refreshed_at=datetime(2026, 3, 9, 13, 0, tzinfo=timezone.utc),
        ),
        RefreshCandidate(
            security_id="fresh",
            ticker="CCC",
            exchange="NASDAQ",
            weight=Decimal("500"),
            minutes_until_close=120,
            last_refreshed_at=datetime(2026, 3, 9, 14, 58, tzinfo=timezone.utc),
        ),
    ]

    plan = build_refresh_plan(
        candidates,
        remaining_credits=10,
        min_refresh_minutes=5,
        max_refresh_minutes=120,
        now_utc=now_utc,
    )

    assert len(plan) >= 1
    assert plan[0].security_id == "large-unseen"
    assert plan[0].exchange == "NASDAQ"
    assert all(item.security_id != "fresh" for item in plan)
