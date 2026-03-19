"""Incremental intraday feature aggregation for execution-only signals."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from statistics import pstdev

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import BetaDailyBar, BetaInstrument, BetaIntradayFeatureSnapshot, BetaMinuteBar
from .intraday_priority_service import IntradayPriorityItem
from .session_service import BetaMarketSessionService

_EXPECTED_VOLUME_LOOKBACK_SESSIONS = 20
_MIN_EXPECTED_VOLUME_SESSIONS = 3


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or abs(previous) < 1e-9:
        return None
    return round(((current / previous) - 1.0) * 100.0, 6)


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _expected_profile_value(values: list[float], minute_count: int) -> float | None:
    if minute_count <= 0 or minute_count > len(values):
        return None
    expected = float(values[minute_count - 1])
    if expected <= 0:
        return None
    return expected


class BetaIntradayFeatureService:
    """Maintain per-session intraday execution features incrementally."""

    @staticmethod
    def refresh_feature_snapshots(
        *,
        priority_items: list[IntradayPriorityItem],
        now_utc: datetime | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized() or not priority_items:
            return {"snapshots_updated": 0, "instruments_processed": 0}

        instrument_ids = [item.instrument_id for item in priority_items]
        priority_by_instrument = {item.instrument_id: item for item in priority_items}
        with BetaContext.write_session() as sess:
            latest_session_rows = list(
                sess.execute(
                    select(BetaMinuteBar.instrument_id, BetaMinuteBar.session_date)
                    .where(BetaMinuteBar.instrument_id.in_(instrument_ids))
                    .order_by(BetaMinuteBar.instrument_id.asc(), BetaMinuteBar.session_date.desc())
                ).all()
            )
            latest_session_by_instrument: dict[str, date] = {}
            for instrument_id, session_date in latest_session_rows:
                if instrument_id not in latest_session_by_instrument:
                    latest_session_by_instrument[instrument_id] = session_date

            snapshots_updated = 0
            for instrument_id in instrument_ids:
                session_date = latest_session_by_instrument.get(instrument_id)
                if session_date is None:
                    continue
                priority_item = priority_by_instrument[instrument_id]
                snapshot = sess.scalar(
                    select(BetaIntradayFeatureSnapshot)
                    .where(
                        BetaIntradayFeatureSnapshot.instrument_id == instrument_id,
                        BetaIntradayFeatureSnapshot.session_date == session_date,
                    )
                    .limit(1)
                )
                if snapshot is None:
                    snapshot = BetaIntradayFeatureSnapshot(
                        instrument_id=instrument_id,
                        session_date=session_date,
                        priority_tier=priority_item.tier,
                        session_state=priority_item.session_state,
                    )
                    sess.add(snapshot)
                    sess.flush()
                state = BetaIntradayFeatureService._json_object(snapshot.accumulator_state_json)
                last_minute_ts = snapshot.last_minute_ts
                minute_query = (
                    select(BetaMinuteBar)
                    .where(
                        BetaMinuteBar.instrument_id == instrument_id,
                        BetaMinuteBar.session_date == session_date,
                    )
                    .order_by(BetaMinuteBar.minute_ts.asc())
                )
                if last_minute_ts is not None:
                    minute_query = minute_query.where(BetaMinuteBar.minute_ts > last_minute_ts)
                minute_rows = list(sess.scalars(minute_query).all())
                if state.get("expected_cumulative_volume_by_minute") is None:
                    state.update(
                        BetaIntradayFeatureService._expected_volume_profile(
                            sess,
                            instrument_id=instrument_id,
                            session_date=session_date,
                        )
                    )
                if not minute_rows and snapshot.feature_snapshot_json:
                    snapshot.session_state = BetaMarketSessionService.session_state(
                        priority_item.exchange,
                        now_utc=now_utc,
                    )
                    snapshot.priority_tier = priority_item.tier
                    continue

                previous_close = BetaIntradayFeatureService._previous_close_price(
                    sess,
                    instrument_id=instrument_id,
                    session_date=session_date,
                )
                if state.get("previous_close_price") is None and previous_close is not None:
                    state["previous_close_price"] = previous_close

                for row in minute_rows:
                    state = BetaIntradayFeatureService._consume_minute_bar(state, row)
                    snapshot.last_minute_ts = row.minute_ts

                feature_snapshot = BetaIntradayFeatureService._feature_view(state)
                snapshot.feature_snapshot_json = json.dumps(feature_snapshot, sort_keys=True)
                snapshot.accumulator_state_json = json.dumps(state, sort_keys=True)
                snapshot.priority_tier = priority_item.tier
                snapshot.session_state = BetaMarketSessionService.session_state(
                    priority_item.exchange,
                    now_utc=now_utc,
                )
                snapshots_updated += 1

            return {
                "snapshots_updated": snapshots_updated,
                "instruments_processed": len(instrument_ids),
            }

    @staticmethod
    def latest_features_by_instrument(
        instrument_ids: list[str],
    ) -> dict[str, dict[str, float | None]]:
        if not BetaContext.is_initialized() or not instrument_ids:
            return {}
        with BetaContext.read_session() as sess:
            rows = list(
                sess.scalars(
                    select(BetaIntradayFeatureSnapshot)
                    .where(BetaIntradayFeatureSnapshot.instrument_id.in_(instrument_ids))
                    .order_by(
                        BetaIntradayFeatureSnapshot.instrument_id.asc(),
                        BetaIntradayFeatureSnapshot.session_date.desc(),
                        desc(BetaIntradayFeatureSnapshot.updated_at),
                    )
                ).all()
            )
        latest: dict[str, dict[str, float | None]] = {}
        for row in rows:
            if row.instrument_id in latest:
                continue
            latest[row.instrument_id] = BetaIntradayFeatureService._json_object(row.feature_snapshot_json)
        return latest

    @staticmethod
    def _consume_minute_bar(state: dict[str, object], row: BetaMinuteBar) -> dict[str, object]:
        next_state = dict(state)
        rolling = list(next_state.get("rolling_window") or [])
        open_price = _safe_float(row.open_price_gbp)
        high_price = _safe_float(row.high_price_gbp)
        low_price = _safe_float(row.low_price_gbp)
        close_price = _safe_float(row.close_price_gbp)
        volume = max(0.0, _safe_float(row.volume_native) or 0.0)
        if open_price is None or high_price is None or low_price is None or close_price is None:
            return next_state

        minute_count = int(next_state.get("minute_count") or 0) + 1
        next_state["minute_count"] = minute_count
        next_state["session_open_price"] = (
            open_price if next_state.get("session_open_price") is None else next_state["session_open_price"]
        )
        next_state["session_high_price"] = max(
            float(next_state.get("session_high_price") or high_price),
            high_price,
        )
        next_state["session_low_price"] = min(
            float(next_state.get("session_low_price") or low_price),
            low_price,
        )
        next_state["last_close_price"] = close_price
        next_state["last_minute_ts"] = row.minute_ts.isoformat()
        next_state["cumulative_volume"] = float(next_state.get("cumulative_volume") or 0.0) + volume
        next_state["vwap_volume_total"] = float(next_state.get("vwap_volume_total") or 0.0) + volume
        typical_price = (high_price + low_price + close_price) / 3.0
        next_state["vwap_price_volume"] = float(next_state.get("vwap_price_volume") or 0.0) + (typical_price * volume)

        if minute_count <= 5:
            next_state["first_5m_close"] = close_price
        if minute_count <= 15:
            next_state["first_15m_close"] = close_price
        if minute_count <= 30:
            next_state["first_30m_close"] = close_price
            next_state["first_30m_high"] = max(
                float(next_state.get("first_30m_high") or high_price),
                high_price,
            )
            next_state["first_30m_low"] = min(
                float(next_state.get("first_30m_low") or low_price),
                low_price,
            )

        rolling.append(
            {
                "minute_ts": row.minute_ts.isoformat(),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )
        next_state["rolling_window"] = rolling[-30:]
        return next_state

    @staticmethod
    def _feature_view(state: dict[str, object]) -> dict[str, float | None]:
        rolling = list(state.get("rolling_window") or [])
        current_close = _safe_float(state.get("last_close_price"))
        session_open = _safe_float(state.get("session_open_price"))
        session_high = _safe_float(state.get("session_high_price"))
        session_low = _safe_float(state.get("session_low_price"))
        previous_close = _safe_float(state.get("previous_close_price"))
        first_5m_close = _safe_float(state.get("first_5m_close"))
        first_15m_close = _safe_float(state.get("first_15m_close"))
        first_30m_close = _safe_float(state.get("first_30m_close"))
        first_30m_high = _safe_float(state.get("first_30m_high"))
        first_30m_low = _safe_float(state.get("first_30m_low"))
        minute_count = int(state.get("minute_count") or 0)

        closes = [float(item["close"]) for item in rolling if _safe_float(item.get("close")) is not None]
        volumes = [float(item["volume"]) for item in rolling if _safe_float(item.get("volume")) is not None]

        def rolling_return(window: int) -> float | None:
            if len(closes) <= window:
                return None
            return _pct_change(closes[-1], closes[-(window + 1)])

        def rolling_vol(window: int) -> float | None:
            if len(closes) <= window:
                return None
            sample = closes[-(window + 1) :]
            returns = [
                ((sample[idx] / sample[idx - 1]) - 1.0) * 100.0
                for idx in range(1, len(sample))
                if abs(sample[idx - 1]) > 1e-9
            ]
            if len(returns) < 2:
                return None
            return round(float(pstdev(returns)), 6)

        low_15 = min((float(item["low"]) for item in rolling[-15:] if _safe_float(item.get("low")) is not None), default=None)
        low_30 = min((float(item["low"]) for item in rolling[-30:] if _safe_float(item.get("low")) is not None), default=None)
        high_15 = max((float(item["high"]) for item in rolling[-15:] if _safe_float(item.get("high")) is not None), default=None)
        high_30 = max((float(item["high"]) for item in rolling[-30:] if _safe_float(item.get("high")) is not None), default=None)

        intraday_range_pct = None
        if session_high is not None and session_low is not None and session_open is not None and abs(session_open) > 1e-9:
            intraday_range_pct = round(((session_high - session_low) / session_open) * 100.0, 6)

        first_30m_range_pct = None
        if (
            first_30m_high is not None
            and first_30m_low is not None
            and session_open is not None
            and abs(session_open) > 1e-9
        ):
            first_30m_range_pct = round(((first_30m_high - first_30m_low) / session_open) * 100.0, 6)

        expected_cumulative = list(state.get("expected_cumulative_volume_by_minute") or [])
        expected_last_15 = list(state.get("expected_last_15m_volume_by_minute") or [])
        cumulative_volume = _safe_float(state.get("cumulative_volume")) or 0.0
        volume_last_15 = sum(volumes[-15:]) if volumes else 0.0
        expected_cumulative_value = _expected_profile_value(expected_cumulative, minute_count)
        expected_last_15_value = _expected_profile_value(expected_last_15, minute_count)
        vwap_volume_total = _safe_float(state.get("vwap_volume_total"))
        vwap_price_volume = _safe_float(state.get("vwap_price_volume"))
        vwap_price = (
            (vwap_price_volume / vwap_volume_total)
            if vwap_price_volume is not None and vwap_volume_total is not None and vwap_volume_total > 0
            else None
        )

        return {
            "gap_from_prev_close_pct": _pct_change(session_open, previous_close),
            "return_since_open_pct": _pct_change(current_close, session_open),
            "return_last_5m_pct": rolling_return(5),
            "return_last_15m_pct": rolling_return(15),
            "first_5m_return_pct": _pct_change(first_5m_close, session_open),
            "first_15m_return_pct": _pct_change(first_15m_close, session_open),
            "first_30m_return_pct": _pct_change(first_30m_close, session_open),
            "first_30m_range_pct": first_30m_range_pct,
            "intraday_range_pct": intraday_range_pct,
            "rolling_intraday_vol_15m_pct": rolling_vol(15),
            "rolling_intraday_vol_30m_pct": rolling_vol(30),
            "distance_from_session_high_pct": (
                round(((session_high - current_close) / session_high) * 100.0, 6)
                if session_high is not None and current_close is not None and abs(session_high) > 1e-9
                else None
            ),
            "distance_from_session_low_pct": (
                round(((current_close - session_low) / session_low) * 100.0, 6)
                if session_low is not None and current_close is not None and abs(session_low) > 1e-9
                else None
            ),
            "reversal_from_low_15m_pct": _pct_change(current_close, low_15),
            "reversal_from_low_30m_pct": _pct_change(current_close, low_30),
            "reversal_from_high_15m_pct": _pct_change(high_15, current_close),
            "reversal_from_high_30m_pct": _pct_change(high_30, current_close),
            "cumulative_volume_vs_expected": (
                round(cumulative_volume / expected_cumulative_value, 6)
                if expected_cumulative_value is not None
                else None
            ),
            "volume_last_15m_vs_expected": (
                round(volume_last_15 / expected_last_15_value, 6)
                if expected_last_15_value is not None
                else None
            ),
            "distance_from_vwap_pct": _pct_change(current_close, vwap_price),
        }

    @staticmethod
    def _expected_volume_profile(sess, *, instrument_id: str, session_date: date) -> dict[str, object]:
        prior_session_dates = [
            row[0]
            for row in sess.execute(
                select(BetaMinuteBar.session_date)
                .where(
                    BetaMinuteBar.instrument_id == instrument_id,
                    BetaMinuteBar.session_date < session_date,
                    BetaMinuteBar.volume_native.is_not(None),
                )
                .group_by(BetaMinuteBar.session_date)
                .order_by(BetaMinuteBar.session_date.desc())
                .limit(_EXPECTED_VOLUME_LOOKBACK_SESSIONS)
            ).all()
        ]
        if not prior_session_dates:
            return {}

        session_volumes: dict[date, list[float]] = defaultdict(list)
        for row in sess.scalars(
            select(BetaMinuteBar)
            .where(
                BetaMinuteBar.instrument_id == instrument_id,
                BetaMinuteBar.session_date.in_(prior_session_dates),
            )
            .order_by(BetaMinuteBar.session_date.asc(), BetaMinuteBar.minute_ts.asc())
        ).all():
            volume = max(0.0, _safe_float(row.volume_native) or 0.0)
            session_volumes[row.session_date].append(volume)

        volume_series = [series for series in session_volumes.values() if any(value > 0 for value in series)]
        if len(volume_series) < _MIN_EXPECTED_VOLUME_SESSIONS:
            return {}

        expected_cumulative: list[float] = []
        expected_last_15: list[float] = []
        max_length = max(len(series) for series in volume_series)
        for minute_index in range(max_length):
            cumulative_samples: list[float] = []
            last_15_samples: list[float] = []
            for series in volume_series:
                if len(series) <= minute_index:
                    continue
                cumulative_samples.append(sum(series[: minute_index + 1]))
                window_start = max(0, minute_index - 14)
                last_15_samples.append(sum(series[window_start : minute_index + 1]))
            if not cumulative_samples:
                continue
            expected_cumulative.append(round(sum(cumulative_samples) / len(cumulative_samples), 6))
            expected_last_15.append(round(sum(last_15_samples) / len(last_15_samples), 6))
        return {
            "expected_cumulative_volume_by_minute": expected_cumulative,
            "expected_last_15m_volume_by_minute": expected_last_15,
            "expected_volume_profile_sessions": len(volume_series),
        }

    @staticmethod
    def _previous_close_price(sess, *, instrument_id: str, session_date: date) -> float | None:
        row = sess.scalar(
            select(BetaDailyBar)
            .where(
                BetaDailyBar.instrument_id == instrument_id,
                BetaDailyBar.bar_date < session_date,
            )
            .order_by(BetaDailyBar.bar_date.desc())
            .limit(1)
        )
        if row is None:
            row = sess.scalar(
                select(BetaDailyBar)
                .where(
                    BetaDailyBar.instrument_id == instrument_id,
                    BetaDailyBar.bar_date <= session_date,
                )
                .order_by(BetaDailyBar.bar_date.desc())
                .limit(1)
            )
        return _safe_float(row.close_price_gbp) if row is not None else None

    @staticmethod
    def _json_object(payload: str | None) -> dict[str, object]:
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
