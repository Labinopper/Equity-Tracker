"""Market-session and quiet-hours helpers for beta runtime decisions."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ...services.twelve_data_price_service import market_window_for_exchange
from ..settings import BetaSettings

_LONDON_TZ = ZoneInfo("Europe/London")
_NEW_YORK_TZ = ZoneInfo("America/New_York")
_USD_EXCHANGES = {"NASDAQ", "NYSE", "NYSE ARCA", "AMEX", "BATS", "IEX"}
_LSE_EXCHANGES = {"LSE", "XLON", "LON"}


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
    def session_state(exchange: str | None, *, now_utc: datetime | None = None) -> str:
        now = _coerce_utc(now_utc)
        session = BetaMarketSessionService._session_bounds(exchange, now)
        if session is None:
            return "CLOSED"
        if session["weekday_closed"]:
            return "CLOSED"
        local_now = session["local_now"]
        pre_open_start = session["open_dt"] - session["pre_open_buffer"]
        post_close_end = session["close_dt"] + session["post_close_buffer"]
        if pre_open_start <= local_now < session["open_dt"]:
            return "PRE_OPEN"
        if session["open_dt"] <= local_now < session["close_dt"]:
            return "REGULAR_OPEN"
        if session["close_dt"] <= local_now < post_close_end:
            return "POST_CLOSE"
        return "CLOSED"

    @staticmethod
    def market_is_tradeable(exchange: str | None, *, now_utc: datetime | None = None) -> bool:
        now = _coerce_utc(now_utc)
        return bool(market_window_for_exchange(exchange, now_utc=now, extended_hours=False).is_open)

    @staticmethod
    def market_status(exchange: str | None, *, now_utc: datetime | None = None) -> dict[str, object]:
        now = _coerce_utc(now_utc)
        window = market_window_for_exchange(exchange, now_utc=now, extended_hours=False)
        session_state = BetaMarketSessionService.session_state(exchange, now_utc=now)
        return {
            "is_open": bool(window.is_open),
            "minutes_until_close": int(window.minutes_until_close or 0),
            "session_state": session_state,
        }

    @staticmethod
    def session_clock(exchange: str | None, *, now_utc: datetime | None = None) -> dict[str, object]:
        now = _coerce_utc(now_utc)
        session = BetaMarketSessionService._session_bounds(exchange, now)
        if session is None or session["weekday_closed"]:
            return {
                "session_state": "CLOSED",
                "minutes_since_open": None,
                "minutes_until_close": None,
                "session_progress_pct": None,
                "regular_session_minutes": None,
            }
        regular_session_minutes = int(
            max(0, (session["close_dt"] - session["open_dt"]).total_seconds() // 60)
        )
        local_now = session["local_now"]
        if local_now <= session["open_dt"]:
            minutes_since_open = 0
            minutes_until_close = regular_session_minutes
        elif local_now >= session["close_dt"]:
            minutes_since_open = regular_session_minutes
            minutes_until_close = 0
        else:
            minutes_since_open = int(
                max(0, (local_now - session["open_dt"]).total_seconds() // 60)
            )
            minutes_until_close = int(
                max(0, (session["close_dt"] - local_now).total_seconds() // 60)
            )
        session_progress_pct = (
            round((minutes_since_open / regular_session_minutes) * 100.0, 4)
            if regular_session_minutes > 0
            else None
        )
        return {
            "session_state": BetaMarketSessionService.session_state(exchange, now_utc=now),
            "minutes_since_open": minutes_since_open,
            "minutes_until_close": minutes_until_close,
            "session_progress_pct": session_progress_pct,
            "regular_session_minutes": regular_session_minutes,
        }

    @staticmethod
    def core_markets_closed(*, now_utc: datetime | None = None) -> bool:
        now = _coerce_utc(now_utc)
        return not BetaMarketSessionService.market_is_tradeable("LSE", now_utc=now) and not BetaMarketSessionService.market_is_tradeable("NASDAQ", now_utc=now)

    @staticmethod
    def live_market_priority_window(settings: BetaSettings, *, now_utc: datetime | None = None) -> bool:
        if not bool(getattr(settings, "market_hours_live_data_priority_enabled", True)):
            return False
        return not BetaMarketSessionService.core_markets_closed(now_utc=now_utc)

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

    @staticmethod
    def _session_bounds(exchange: str | None, now_utc: datetime) -> dict[str, object] | None:
        cleaned_exchange = str(exchange or "").strip().upper()
        if cleaned_exchange in _USD_EXCHANGES:
            tz = _NEW_YORK_TZ
            open_time = time(9, 30)
            close_time = time(16, 0)
            pre_open_buffer = timedelta(hours=2, minutes=30)
            post_close_buffer = timedelta(hours=4)
        elif cleaned_exchange in _LSE_EXCHANGES:
            tz = _LONDON_TZ
            open_time = time(8, 0)
            close_time = time(16, 30)
            pre_open_buffer = timedelta(hours=1)
            post_close_buffer = timedelta(minutes=90)
        else:
            return None
        local_now = now_utc.astimezone(tz)
        open_dt = datetime.combine(local_now.date(), open_time, tzinfo=tz)
        close_dt = datetime.combine(local_now.date(), close_time, tzinfo=tz)
        return {
            "local_now": local_now,
            "open_dt": open_dt,
            "close_dt": close_dt,
            "pre_open_buffer": pre_open_buffer,
            "post_close_buffer": post_close_buffer,
            "weekday_closed": local_now.weekday() >= 5,
        }
