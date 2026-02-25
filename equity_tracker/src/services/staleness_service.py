"""
StalenessService - shared freshness/staleness helpers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

_FX_TS_FMT = "%Y-%m-%d %H:%M:%S"


class StalenessService:
    """Utility helpers for price/FX staleness checks."""

    @staticmethod
    def is_price_stale(
        price_as_of: date | None,
        *,
        stale_after_days: int = 1,
        today: date | None = None,
    ) -> bool:
        if price_as_of is None:
            return False
        threshold_days = max(0, int(stale_after_days))
        ref_day = today or date.today()
        return (ref_day - price_as_of).days >= threshold_days

    @staticmethod
    def is_fx_stale(
        fx_as_of: str | None,
        *,
        stale_after_minutes: int = 10,
        now_utc: datetime | None = None,
    ) -> bool:
        """
        Determine whether a provider FX timestamp is stale.

        Returns False for missing/unparseable timestamps to avoid false stale
        warnings from malformed upstream values.
        """
        if not fx_as_of:
            return False
        try:
            ts = datetime.strptime(fx_as_of, _FX_TS_FMT).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False

        threshold_minutes = max(0, int(stale_after_minutes))
        now = now_utc or datetime.now(timezone.utc)
        age_minutes = (now - ts).total_seconds() / 60
        return age_minutes >= threshold_minutes
