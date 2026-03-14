"""Market-session and quiet-hours helpers for beta runtime decisions."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from ...services.twelve_data_price_service import market_window_for_exchange
from ..settings import BetaSettings

_LONDON_TZ = ZoneInfo("Europe/London")


def _parse_hhmm(value: str, fallback: time) -> time:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return time(int(hour_text), int(minute_text))
    except (TypeError, ValueError):
        return fallback


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: datetime | None) -> datetime:
    now = value or _utcnow()
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


class BetaMarketSessionService:
    """Thin adapter around market windows and local quiet-hour policy."""

    @staticmethod
    def market_is_tradeable(exchange: str | None, *, now_utc: datetime | None = None) -> bool:
        now = _coerce_utc(now_utc)
        return bool(market_window_for_exchange(exchange, now_utc=now, extended_hours=False).is_open)

    @staticmethod
    def market_status(exchange: str | None, *, now_utc: datetime | None = None) -> dict[str, object]:
        now = _coerce_utc(now_utc)
        window = market_window_for_exchange(exchange, now_utc=now, extended_hours=False)
        return {
            "is_open": bool(window.is_open),
            "minutes_until_close": int(window.minutes_until_close or 0),
        }

    @staticmethod
    def core_markets_closed(*, now_utc: datetime | None = None) -> bool:
        now = _coerce_utc(now_utc)
        return not BetaMarketSessionService.market_is_tradeable("LSE", now_utc=now) and not BetaMarketSessionService.market_is_tradeable("NASDAQ", now_utc=now)

    @staticmethod
    def training_window_is_open(settings: BetaSettings, *, now_utc: datetime | None = None) -> bool:
        if BetaMarketSessionService.core_markets_closed(now_utc=now_utc):
            return True
        if not settings.research_quiet_hours_only:
            return True
        now = _coerce_utc(now_utc)
        local_now = now.astimezone(_LONDON_TZ).time()
        start = _parse_hhmm(settings.training_window_start_local, time(22, 0))
        end = _parse_hhmm(settings.training_window_end_local, time(6, 0))
        if start == end:
            return True
        if start < end:
            return start <= local_now < end
        return local_now >= start or local_now < end
