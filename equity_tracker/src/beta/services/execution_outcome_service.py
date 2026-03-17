"""Evaluate execution signal outcomes and position execution quality."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import BetaExecutionLabelValue, BetaExecutionSignal, BetaMinuteBar


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BetaExecutionOutcomeService:
    """Compute intraday execution labels from aggregated minute bars."""

    @staticmethod
    def update_execution_outcomes() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"labels_written": 0, "signals_evaluated": 0}

        with BetaContext.write_session() as sess:
            existing_labels = {
                row.execution_signal_id: row
                for row in sess.scalars(select(BetaExecutionLabelValue)).all()
            }
            signals = list(
                sess.scalars(
                    select(BetaExecutionSignal)
                    .order_by(BetaExecutionSignal.signal_time.asc())
                ).all()
            )
            labels_written = 0
            for signal in signals:
                minute_rows = list(
                    sess.scalars(
                        select(BetaMinuteBar)
                        .where(
                            BetaMinuteBar.instrument_id == signal.instrument_id,
                            BetaMinuteBar.session_date == signal.session_date,
                            BetaMinuteBar.minute_ts >= signal.signal_time.replace(second=0, microsecond=0),
                        )
                        .order_by(BetaMinuteBar.minute_ts.asc())
                    ).all()
                )
                if not minute_rows:
                    continue
                label = existing_labels.get(signal.id)
                if label is None:
                    label = BetaExecutionLabelValue(
                        execution_signal_id=signal.id,
                        position_state_id=signal.position_state_id,
                        instrument_id=signal.instrument_id,
                        symbol=signal.symbol,
                        session_date=signal.session_date,
                        signal_time=signal.signal_time,
                    )
                    sess.add(label)
                    existing_labels[signal.id] = label

                base_price = _safe_float(minute_rows[0].close_price_gbp)
                if base_price is None or abs(base_price) < 1e-9:
                    continue
                label.future_30m_return_pct = BetaExecutionOutcomeService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal_time=signal.signal_time,
                    horizon_minutes=30,
                )
                label.future_60m_return_pct = BetaExecutionOutcomeService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal_time=signal.signal_time,
                    horizon_minutes=60,
                )
                label.future_120m_return_pct = BetaExecutionOutcomeService._future_return_pct(
                    minute_rows=minute_rows,
                    base_price=base_price,
                    signal_time=signal.signal_time,
                    horizon_minutes=120,
                )
                session_close_price = _safe_float(minute_rows[-1].close_price_gbp)
                if session_close_price is not None and abs(base_price) > 1e-9:
                    label.close_return_from_signal_pct = round(
                        ((session_close_price / base_price) - 1.0) * 100.0,
                        6,
                    )
                lows = [_safe_float(row.low_price_gbp) for row in minute_rows if _safe_float(row.low_price_gbp) is not None]
                highs = [_safe_float(row.high_price_gbp) for row in minute_rows if _safe_float(row.high_price_gbp) is not None]
                if lows:
                    label.max_adverse_move_after_signal_pct = round(
                        ((min(lows) / base_price) - 1.0) * 100.0,
                        6,
                    )
                if highs:
                    label.max_favorable_move_after_signal_pct = round(
                        ((max(highs) / base_price) - 1.0) * 100.0,
                        6,
                    )
                last_minute_ts = minute_rows[-1].minute_ts
                label.evaluation_complete = bool(
                    label.close_return_from_signal_pct is not None
                    and (
                        label.future_120m_return_pct is not None
                        or last_minute_ts >= signal.signal_time + timedelta(minutes=120)
                    )
                )
                labels_written += 1

            return {"labels_written": labels_written, "signals_evaluated": len(signals)}

    @staticmethod
    def _future_return_pct(
        *,
        minute_rows: list[BetaMinuteBar],
        base_price: float,
        signal_time,
        horizon_minutes: int,
    ) -> float | None:
        target_time = signal_time + timedelta(minutes=horizon_minutes)
        future_row = next((row for row in minute_rows if row.minute_ts >= target_time), None)
        if future_row is None:
            return None
        future_price = _safe_float(future_row.close_price_gbp)
        if future_price is None or abs(base_price) < 1e-9:
            return None
        return round(((future_price / base_price) - 1.0) * 100.0, 6)
