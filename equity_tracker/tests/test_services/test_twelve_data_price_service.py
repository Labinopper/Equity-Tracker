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
            is_market_open=True,
            minutes_until_close=120,
            last_refreshed_at=None,
        ),
        RefreshCandidate(
            security_id="small-due",
            ticker="BBB",
            exchange="NASDAQ",
            weight=Decimal("20"),
            is_market_open=True,
            minutes_until_close=120,
            last_refreshed_at=datetime(2026, 3, 9, 13, 0, tzinfo=timezone.utc),
        ),
        RefreshCandidate(
            security_id="fresh",
            ticker="CCC",
            exchange="NASDAQ",
            weight=Decimal("500"),
            is_market_open=True,
            minutes_until_close=120,
            last_refreshed_at=datetime(2026, 3, 9, 14, 58, tzinfo=timezone.utc),
        ),
    ]

    plan = build_refresh_plan(
        candidates,
        minute_capacity_remaining=10,
        tracked_instrument_count=2,
        max_calls_per_minute=40,
        now_utc=now_utc,
    )

    assert len(plan) >= 1
    assert plan[0].security_id == "large-unseen"
    assert plan[0].exchange == "NASDAQ"
    assert all(item.security_id != "fresh" for item in plan)
    assert plan[0].interval_seconds == 60


def test_refresh_plan_never_schedules_open_market_stock_more_than_once_per_minute() -> None:
    now_utc = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc)
    candidates = [
        RefreshCandidate(
            security_id="too-fresh",
            ticker="AAA",
            exchange="NASDAQ",
            weight=Decimal("1000"),
            is_market_open=True,
            minutes_until_close=120,
            last_refreshed_at=datetime(2026, 3, 9, 14, 59, 30, tzinfo=timezone.utc),
        ),
        RefreshCandidate(
            security_id="due",
            ticker="BBB",
            exchange="NASDAQ",
            weight=Decimal("100"),
            is_market_open=True,
            minutes_until_close=120,
            last_refreshed_at=datetime(2026, 3, 9, 14, 58, 59, tzinfo=timezone.utc),
        ),
    ]

    plan = build_refresh_plan(
        candidates,
        minute_capacity_remaining=10,
        tracked_instrument_count=1,
        max_calls_per_minute=120,
        now_utc=now_utc,
    )

    assert [item.security_id for item in plan] == ["due"]
    assert plan[0].interval_seconds == 60
