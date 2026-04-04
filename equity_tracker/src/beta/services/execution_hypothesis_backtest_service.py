"""Historical backtesting over intraday execution hypothesis definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median, pstdev
from typing import Any

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionHypothesisDefinition,
    BetaExecutionHypothesisTestRun,
    BetaExecutionLabelValue,
    BetaExecutionSignal,
    BetaMinuteBar,
)
from ..settings import BetaSettings
from ..state import get_beta_db_path
from .execution_economic_annotation_service import BetaExecutionEconomicAnnotationService
from .execution_hypothesis_service import BetaExecutionHypothesisService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class _ExecutionDatasetRow:
    execution_signal_id: str
    instrument_id: str | None
    symbol: str
    session_date: date
    signal_time: datetime
    session_state: str
    source_signal_type: str
    event_trigger_code: str
    event_codes: tuple[str, ...]
    feature_values: dict[str, float | None]
    stored_action_aligned_return_pct: float | None
    stored_time_to_peak_minutes: int | None


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BetaExecutionHypothesisBacktestService:
    """Backtest execution hypothesis definitions over stored intraday evaluation points."""

    _DEFINITION_STATUSES = ("ACTIVE", "PAUSED")

    @staticmethod
    def refresh_backtests(settings: BetaSettings | None = None) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"definitions_considered": 0, "test_runs_written": 0}

        if settings is None:
            beta_db_path = get_beta_db_path()
            settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()

        with BetaContext.write_session() as sess:
            BetaExecutionHypothesisService.ensure_default_definitions()
            definitions = list(
                sess.scalars(
                    select(BetaExecutionHypothesisDefinition).where(
                        BetaExecutionHypothesisDefinition.status.in_(BetaExecutionHypothesisBacktestService._DEFINITION_STATUSES)
                    )
                ).all()
            )
            if not definitions:
                return {"definitions_considered": 0, "test_runs_written": 0}

            dataset_rows = BetaExecutionHypothesisBacktestService.load_dataset(sess, settings=settings)
            if not dataset_rows:
                return {"definitions_considered": len(definitions), "test_runs_written": 0}

            minute_cache = BetaExecutionHypothesisBacktestService._load_minute_cache(sess, dataset_rows)
            outcome_cache: dict[tuple[str, str], dict[str, float | int | None]] = {}
            test_runs_written = 0
            for definition in definitions:
                summary = BetaExecutionHypothesisBacktestService.evaluate_definition_summary(
                    sess=sess,
                    definition=definition,
                    dataset_rows=dataset_rows,
                    settings=settings,
                    minute_cache=minute_cache,
                    outcome_cache=outcome_cache,
                )
                sess.add(
                    BetaExecutionHypothesisTestRun(
                        execution_hypothesis_definition_id=definition.id,
                        test_start_at=summary.get("test_start_at"),
                        test_end_at=summary.get("test_end_at"),
                        sample_size=int(summary.get("sample_size") or 0),
                        support_count=int(summary.get("support_count") or 0),
                        matched_instruments=int(summary.get("matched_instruments") or 0),
                        average_return_pct=summary.get("average_return_pct"),
                        median_return_pct=summary.get("median_return_pct"),
                        win_rate_pct=summary.get("win_rate_pct"),
                        outcome_volatility_pct=summary.get("outcome_volatility_pct"),
                        baseline_return_pct=summary.get("baseline_return_pct"),
                        baseline_edge_pct=summary.get("baseline_edge_pct"),
                        transaction_cost_bps=float(summary.get("transaction_cost_bps") or 0.0),
                        transaction_cost_adjusted_return_pct=summary.get("transaction_cost_adjusted_return_pct"),
                        expected_hold_min_minutes=summary.get("expected_hold_min_minutes"),
                        expected_hold_max_minutes=summary.get("expected_hold_max_minutes"),
                        stability_score=summary.get("stability_score"),
                        regime_slice_json=json.dumps(summary.get("regime_slices") or {}, sort_keys=True),
                        notes_json=json.dumps(summary.get("notes") or {}, sort_keys=True),
                    )
                )
                test_runs_written += 1
            return {"definitions_considered": len(definitions), "test_runs_written": test_runs_written}

    @staticmethod
    def load_dataset(sess, *, settings: BetaSettings) -> list[_ExecutionDatasetRow]:
        history_cutoff = _utcnow() - timedelta(
            days=max(1, int(settings.intraday_execution_hypothesis_history_days))
        )
        rows: list[_ExecutionDatasetRow] = []
        query = (
            select(BetaExecutionSignal, BetaExecutionLabelValue)
            .join(BetaExecutionLabelValue, BetaExecutionLabelValue.execution_signal_id == BetaExecutionSignal.id)
            .where(
                BetaExecutionLabelValue.evaluation_complete.is_(True),
                BetaExecutionSignal.signal_time >= history_cutoff,
            )
            .order_by(BetaExecutionSignal.signal_time.asc())
        )
        for signal, label in sess.execute(query).all():
            feature_values = {
                str(key): _safe_float(value)
                for key, value in _json_object(signal.feature_snapshot_json).items()
            }
            if not feature_values:
                continue
            normalized_trigger = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(signal.event_trigger_code)
            rows.append(
                _ExecutionDatasetRow(
                    execution_signal_id=signal.id,
                    instrument_id=signal.instrument_id,
                    symbol=signal.symbol,
                    session_date=signal.session_date,
                    signal_time=signal.signal_time,
                    session_state=signal.session_state,
                    source_signal_type=signal.signal_type,
                    event_trigger_code=normalized_trigger,
                    event_codes=tuple(code for code in normalized_trigger.split("|") if code),
                    feature_values=feature_values,
                    stored_action_aligned_return_pct=_safe_float(label.action_aligned_return_pct),
                    stored_time_to_peak_minutes=int(label.time_to_peak_minutes) if label.time_to_peak_minutes is not None else None,
                )
            )
        return rows

    @staticmethod
    def evaluate_definition_summary(
        *,
        sess,
        definition: BetaExecutionHypothesisDefinition,
        dataset_rows: list[_ExecutionDatasetRow],
        settings: BetaSettings,
        minute_cache: dict[tuple[str, date], list[BetaMinuteBar]] | None = None,
        outcome_cache: dict[tuple[str, str], dict[str, float | int | None]] | None = None,
    ) -> dict[str, object]:
        minute_cache = minute_cache or {}
        outcome_cache = outcome_cache or {}
        configured_events = [
            str(code).strip().upper()
            for code in list(_json_object(definition.metadata_json).get("event_codes") or [])
            if str(code).strip()
        ]
        matched_returns: list[float] = []
        matched_hold_minutes: list[int] = []
        matched_instruments: set[str] = set()
        matched_signal_times: list[datetime] = []
        eligible_returns: list[float] = []
        regime_slices: dict[str, dict[str, float]] = {}

        for row in dataset_rows:
            outcome = BetaExecutionHypothesisBacktestService._outcome_for_row(
                sess=sess,
                row=row,
                signal_type=definition.signal_type,
                minute_cache=minute_cache,
                outcome_cache=outcome_cache,
            )
            aligned_return = _safe_float(outcome.get("action_aligned_return_pct"))
            if aligned_return is None:
                continue

            if not configured_events or any(code in row.event_codes for code in configured_events):
                eligible_returns.append(aligned_return)

            if BetaExecutionHypothesisService.evaluate_definition(
                definition=definition,
                feature_values=row.feature_values,
                event_codes=list(row.event_codes),
            ) is None:
                continue

            matched_returns.append(aligned_return)
            matched_signal_times.append(row.signal_time)
            if row.instrument_id:
                matched_instruments.add(row.instrument_id)
            if outcome.get("time_to_peak_minutes") is not None:
                matched_hold_minutes.append(int(outcome["time_to_peak_minutes"]))
            bucket = BetaExecutionHypothesisBacktestService._regime_bucket(row.feature_values)
            bucket_row = regime_slices.setdefault(bucket, {"count": 0.0, "avg_return_pct": 0.0})
            bucket_row["count"] += 1.0
            bucket_row["avg_return_pct"] += aligned_return

        for bucket_row in regime_slices.values():
            count = int(bucket_row["count"] or 0)
            bucket_row["count"] = count
            bucket_row["avg_return_pct"] = round(bucket_row["avg_return_pct"] / count, 4) if count else 0.0

        default_hold_min, default_hold_max = BetaExecutionEconomicAnnotationService.default_hold_window(
            definition=definition,
            signal_type=definition.signal_type,
        )
        support_count = len(matched_returns)
        cost_drag_pct = float(BetaExecutionEconomicAnnotationService.estimated_cost_drag_pct(settings))
        average_return_pct = round(sum(matched_returns) / support_count, 4) if support_count else None
        median_return_pct = round(float(median(matched_returns)), 4) if matched_returns else None
        win_rate_pct = round((sum(1 for value in matched_returns if value > 0.0) / support_count) * 100.0, 4) if support_count else None
        outcome_volatility_pct = (
            round(float(pstdev(matched_returns)), 4) if len(matched_returns) > 1 else (0.0 if matched_returns else None)
        )
        baseline_pool = eligible_returns if eligible_returns else matched_returns
        baseline_return_pct = round(sum(baseline_pool) / len(baseline_pool), 4) if baseline_pool else None
        baseline_edge_pct = (
            round(float(average_return_pct or 0.0) - float(baseline_return_pct or 0.0), 4)
            if average_return_pct is not None and baseline_return_pct is not None
            else None
        )
        adjusted_return_pct = (
            round(float(average_return_pct or 0.0) - cost_drag_pct, 4)
            if average_return_pct is not None
            else None
        )
        hold_q1 = BetaExecutionHypothesisBacktestService._percentile(matched_hold_minutes, 0.25)
        hold_q3 = BetaExecutionHypothesisBacktestService._percentile(matched_hold_minutes, 0.75)
        stability_score = BetaExecutionHypothesisBacktestService._stability_score(
            support_count=support_count,
            matched_instruments=len(matched_instruments),
            win_rate_pct=win_rate_pct,
            adjusted_return_pct=adjusted_return_pct,
            outcome_volatility_pct=outcome_volatility_pct,
        )
        notes = {
            "configured_event_codes": configured_events,
            "dataset_rows": len(dataset_rows),
            "matched_event_rows": len(eligible_returns),
            "default_hold_window_minutes": [default_hold_min, default_hold_max],
            "matched_signal_type": definition.signal_type,
            "source_type": definition.source_type,
        }
        return {
            "test_start_at": matched_signal_times[0] if matched_signal_times else None,
            "test_end_at": matched_signal_times[-1] if matched_signal_times else None,
            "sample_size": len(dataset_rows),
            "support_count": support_count,
            "matched_instruments": len(matched_instruments),
            "average_return_pct": average_return_pct,
            "median_return_pct": median_return_pct,
            "win_rate_pct": win_rate_pct,
            "outcome_volatility_pct": outcome_volatility_pct,
            "baseline_return_pct": baseline_return_pct,
            "baseline_edge_pct": baseline_edge_pct,
            "transaction_cost_bps": cost_drag_pct * 100.0,
            "transaction_cost_adjusted_return_pct": adjusted_return_pct,
            "expected_hold_min_minutes": hold_q1 if hold_q1 is not None else default_hold_min,
            "expected_hold_max_minutes": hold_q3 if hold_q3 is not None else default_hold_max,
            "stability_score": stability_score,
            "regime_slices": regime_slices,
            "notes": notes,
        }

    @staticmethod
    def _load_minute_cache(sess, dataset_rows: list[_ExecutionDatasetRow]) -> dict[tuple[str, date], list[BetaMinuteBar]]:
        requested_keys = {
            (row.instrument_id, row.session_date)
            for row in dataset_rows
            if row.instrument_id is not None
        }
        cache: dict[tuple[str, date], list[BetaMinuteBar]] = {key: [] for key in requested_keys}
        if not requested_keys:
            return cache
        instrument_ids = sorted({instrument_id for instrument_id, _ in requested_keys if instrument_id})
        session_dates = sorted({session_date for _, session_date in requested_keys})
        for row in sess.scalars(
            select(BetaMinuteBar)
            .where(
                BetaMinuteBar.instrument_id.in_(instrument_ids),
                BetaMinuteBar.session_date.in_(session_dates),
            )
            .order_by(BetaMinuteBar.instrument_id.asc(), BetaMinuteBar.session_date.asc(), BetaMinuteBar.minute_ts.asc())
        ).all():
            key = (row.instrument_id, row.session_date)
            if key in cache:
                cache[key].append(row)
        return cache

    @staticmethod
    def _outcome_for_row(
        *,
        sess,
        row: _ExecutionDatasetRow,
        signal_type: str,
        minute_cache: dict[tuple[str, date], list[BetaMinuteBar]],
        outcome_cache: dict[tuple[str, str], dict[str, float | int | None]],
    ) -> dict[str, float | int | None]:
        cache_key = (row.execution_signal_id, signal_type)
        cached = outcome_cache.get(cache_key)
        if cached is not None:
            return cached

        if row.source_signal_type == signal_type and row.stored_action_aligned_return_pct is not None:
            cached = {
                "action_aligned_return_pct": round(float(row.stored_action_aligned_return_pct), 6),
                "time_to_peak_minutes": row.stored_time_to_peak_minutes,
            }
            outcome_cache[cache_key] = cached
            return cached

        if row.instrument_id is None:
            cached = {"action_aligned_return_pct": None, "time_to_peak_minutes": None}
            outcome_cache[cache_key] = cached
            return cached

        session_rows = minute_cache.get((row.instrument_id, row.session_date), [])
        base_time = row.signal_time.replace(second=0, microsecond=0)
        relevant_rows = [minute_row for minute_row in session_rows if minute_row.minute_ts >= base_time]
        if not relevant_rows:
            cached = {"action_aligned_return_pct": None, "time_to_peak_minutes": None}
            outcome_cache[cache_key] = cached
            return cached

        base_price = _safe_float(relevant_rows[0].close_price_gbp)
        if base_price is None or abs(base_price) < 1e-9:
            cached = {"action_aligned_return_pct": None, "time_to_peak_minutes": None}
            outcome_cache[cache_key] = cached
            return cached

        best_return: float | None = None
        best_minutes: int | None = None
        for minute_row in relevant_rows:
            close_price = _safe_float(minute_row.close_price_gbp)
            if close_price is None:
                continue
            raw_return_pct = ((close_price / base_price) - 1.0) * 100.0
            aligned_return = BetaExecutionEconomicAnnotationService.action_aligned_return(
                raw_return_pct,
                signal_type,
            )
            if aligned_return is None:
                continue
            elapsed_minutes = max(
                0,
                int((minute_row.minute_ts - base_time).total_seconds() // 60),
            )
            if best_return is None or aligned_return > best_return:
                best_return = aligned_return
                best_minutes = elapsed_minutes

        cached = {
            "action_aligned_return_pct": round(best_return, 6) if best_return is not None else None,
            "time_to_peak_minutes": best_minutes,
        }
        outcome_cache[cache_key] = cached
        return cached

    @staticmethod
    def _regime_bucket(feature_values: dict[str, float | None]) -> str:
        session_progress = _safe_float(feature_values.get("session_progress_pct"))
        if session_progress is None:
            return "UNKNOWN"
        if session_progress < 20.0:
            return "OPEN"
        if session_progress < 55.0:
            return "MIDDAY"
        if session_progress < 85.0:
            return "AFTERNOON"
        return "CLOSE"

    @staticmethod
    def _percentile(values: list[int], pct: float) -> int | None:
        if not values:
            return None
        ordered = sorted(int(value) for value in values)
        if len(ordered) == 1:
            return ordered[0]
        rank = (len(ordered) - 1) * max(0.0, min(1.0, pct))
        lower = int(rank)
        upper = min(len(ordered) - 1, lower + 1)
        weight = rank - lower
        return int(round(ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)))

    @staticmethod
    def _stability_score(
        *,
        support_count: int,
        matched_instruments: int,
        win_rate_pct: float | None,
        adjusted_return_pct: float | None,
        outcome_volatility_pct: float | None,
    ) -> float:
        support_factor = min(1.0, support_count / 60.0)
        instrument_factor = min(1.0, matched_instruments / 4.0)
        win_factor = 0.0 if win_rate_pct is None else min(1.0, max(0.0, (win_rate_pct - 50.0) / 15.0))
        return_factor = 0.0 if adjusted_return_pct is None else min(1.0, max(0.0, adjusted_return_pct / 0.25))
        volatility_penalty = 0.0 if outcome_volatility_pct is None else min(0.35, max(0.0, outcome_volatility_pct / 6.0) * 0.2)
        return round(
            max(
                0.0,
                min(
                    1.0,
                    0.15 + (support_factor * 0.25) + (instrument_factor * 0.2) + (win_factor * 0.2) + (return_factor * 0.2)
                    - volatility_penalty,
                ),
            ),
            4,
        )
