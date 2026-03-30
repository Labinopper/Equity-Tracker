"""Economic annotations for execution signals based on historical labelled outcomes."""

from __future__ import annotations

import json
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionHypothesisDefinition,
    BetaExecutionLabelValue,
    BetaExecutionSignal,
)
from ..settings import BetaSettings

_DECIMAL_QUANT = Decimal("0.0001")


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _quantize_4dp(value: float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    clipped_pct = min(1.0, max(0.0, pct))
    index = (len(ordered) - 1) * clipped_pct
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _winsorize(values: list[float], *, tail_pct: float) -> list[float]:
    if not values:
        return []
    clipped_tail = min(0.20, max(0.0, float(tail_pct)))
    if clipped_tail <= 0.0 or len(values) < 2:
        return [float(value) for value in values]
    lower = _percentile(values, clipped_tail)
    upper = _percentile(values, 1.0 - clipped_tail)
    if lower is None or upper is None:
        return [float(value) for value in values]
    return [min(max(float(value), lower), upper) for value in values]


class BetaExecutionEconomicAnnotationService:
    """Derive deterministic economic annotations from historical execution labels."""

    ACTIONABLE = "ACTIONABLE"
    NON_ACTIONABLE = "NON_ACTIONABLE"
    _POSITIVE_ACTION_SIGNAL_TYPES = {
        "HOLD_THROUGH_NOISE",
        "AVOID_SELLING_INTO_PANIC",
        "WAIT_FOR_CLOSE_CONFIRMATION",
    }
    _NEGATIVE_ACTION_SIGNAL_TYPES = {
        "TRIM_ON_STRENGTH",
        "SELL_INTO_REBOUND",
    }
    _DEFAULT_HOLD_WINDOWS_BY_SIGNAL_TYPE: dict[str, tuple[int, int]] = {
        "HOLD_THROUGH_NOISE": (15, 90),
        "AVOID_SELLING_INTO_PANIC": (15, 60),
        "WAIT_FOR_CLOSE_CONFIRMATION": (30, 120),
        "TRIM_ON_STRENGTH": (5, 45),
        "SELL_INTO_REBOUND": (5, 45),
        "NO_ACTION": (15, 45),
    }

    @staticmethod
    def action_aligned_return(raw_return_pct: float | None, signal_type: str | None) -> float | None:
        if raw_return_pct is None:
            return None
        signal = str(signal_type or "NO_ACTION").strip().upper()
        raw = float(raw_return_pct)
        if signal in BetaExecutionEconomicAnnotationService._NEGATIVE_ACTION_SIGNAL_TYPES:
            return round(-raw, 6)
        return round(raw, 6)

    @staticmethod
    def normalize_event_trigger_code(value: str | list[str] | None) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            tokens = [str(item or "").strip().upper() for item in value]
        else:
            tokens = [token.strip().upper() for token in str(value).split("|")]
        cleaned = sorted({token for token in tokens if token})
        return "|".join(cleaned)

    @staticmethod
    def default_hold_window(
        *,
        definition: BetaExecutionHypothesisDefinition | None,
        signal_type: str | None,
    ) -> tuple[int, int]:
        metadata = _json_object(definition.metadata_json) if definition is not None else {}
        raw_window = metadata.get("default_hold_window_minutes")
        if isinstance(raw_window, (list, tuple)) and len(raw_window) == 2:
            try:
                low = max(1, int(raw_window[0]))
                high = max(low, int(raw_window[1]))
                return low, high
            except (TypeError, ValueError):
                pass
        normalized_signal = str(signal_type or "NO_ACTION").strip().upper()
        return BetaExecutionEconomicAnnotationService._DEFAULT_HOLD_WINDOWS_BY_SIGNAL_TYPE.get(
            normalized_signal,
            (15, 45),
        )

    @staticmethod
    def annotation_for_match(
        *,
        sess,
        definition: BetaExecutionHypothesisDefinition | None,
        signal_time,
        signal_type: str | None,
        event_trigger_code: str | list[str] | None,
        settings: BetaSettings,
    ) -> dict[str, Any]:
        default_hold_min, default_hold_max = BetaExecutionEconomicAnnotationService.default_hold_window(
            definition=definition,
            signal_type=signal_type,
        )
        cost_drag_pct = BetaExecutionEconomicAnnotationService.estimated_cost_drag_pct(settings)
        definition_id = definition.id if definition is not None else None
        if definition_id is None:
            return {
                "expected_edge_pct": None,
                "expected_hold_minutes": json.dumps([default_hold_min, default_hold_max]),
                "expected_hold_min_minutes": default_hold_min,
                "expected_hold_max_minutes": default_hold_max,
                "historical_win_rate": None,
                "post_cost_edge_pct": None,
                "estimated_cost_drag_pct": cost_drag_pct,
                "economic_annotation_sample_size": 0,
                "economic_opportunity_status": BetaExecutionEconomicAnnotationService.NON_ACTIONABLE,
                "economic_non_actionable_reason": "no_hypothesis_match",
            }

        trigger_key = BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(event_trigger_code)
        sample_rows = list(
            sess.execute(
                select(
                    BetaExecutionSignal.event_trigger_code,
                    BetaExecutionLabelValue.action_aligned_return_pct,
                    BetaExecutionLabelValue.time_to_peak_minutes,
                )
                .join(
                    BetaExecutionLabelValue,
                    BetaExecutionLabelValue.execution_signal_id == BetaExecutionSignal.id,
                )
                .where(
                    BetaExecutionSignal.execution_hypothesis_definition_id == definition_id,
                    BetaExecutionSignal.signal_time < signal_time,
                    BetaExecutionLabelValue.evaluation_complete.is_(True),
                    BetaExecutionLabelValue.action_aligned_return_pct.is_not(None),
                )
                .order_by(BetaExecutionSignal.signal_time.asc())
            ).all()
        )
        matching_rows = [
            row
            for row in sample_rows
            if BetaExecutionEconomicAnnotationService.normalize_event_trigger_code(row.event_trigger_code) == trigger_key
        ]
        sample_size = len(matching_rows)
        min_sample_size = int(settings.intraday_execution_annotation_min_sample_size)
        if sample_size < min_sample_size:
            return {
                "expected_edge_pct": None,
                "expected_hold_minutes": json.dumps([default_hold_min, default_hold_max]),
                "expected_hold_min_minutes": default_hold_min,
                "expected_hold_max_minutes": default_hold_max,
                "historical_win_rate": None,
                "post_cost_edge_pct": None,
                "estimated_cost_drag_pct": cost_drag_pct,
                "economic_annotation_sample_size": sample_size,
                "economic_opportunity_status": BetaExecutionEconomicAnnotationService.NON_ACTIONABLE,
                "economic_non_actionable_reason": f"insufficient_sample_lt_{min_sample_size}",
            }

        aligned_returns = [float(row.action_aligned_return_pct) for row in matching_rows if row.action_aligned_return_pct is not None]
        winsorized_returns = _winsorize(
            aligned_returns,
            tail_pct=float(settings.intraday_execution_annotation_winsorize_tail_pct),
        )
        if not winsorized_returns:
            return {
                "expected_edge_pct": None,
                "expected_hold_minutes": json.dumps([default_hold_min, default_hold_max]),
                "expected_hold_min_minutes": default_hold_min,
                "expected_hold_max_minutes": default_hold_max,
                "historical_win_rate": None,
                "post_cost_edge_pct": None,
                "estimated_cost_drag_pct": cost_drag_pct,
                "economic_annotation_sample_size": sample_size,
                "economic_opportunity_status": BetaExecutionEconomicAnnotationService.NON_ACTIONABLE,
                "economic_non_actionable_reason": "no_valid_historical_returns",
            }

        expected_edge = _quantize_4dp(sum(winsorized_returns) / len(winsorized_returns))
        historical_win_rate = _quantize_4dp(
            sum(1 for value in winsorized_returns if value > 0.0) / len(winsorized_returns)
        )
        post_cost_edge = (
            _quantize_4dp(expected_edge - cost_drag_pct)
            if expected_edge is not None
            else None
        )
        peak_times = [
            int(row.time_to_peak_minutes)
            for row in matching_rows
            if row.time_to_peak_minutes is not None and int(row.time_to_peak_minutes) >= 0
        ]
        hold_min = default_hold_min
        hold_max = default_hold_max
        if peak_times:
            q1 = _percentile([float(value) for value in peak_times], 0.25)
            q3 = _percentile([float(value) for value in peak_times], 0.75)
            if q1 is not None and q3 is not None:
                hold_min = max(1, int(round(q1)))
                hold_max = max(hold_min, int(round(q3)))

        actionable = bool(post_cost_edge is not None and post_cost_edge > Decimal("0.0000"))
        return {
            "expected_edge_pct": expected_edge,
            "expected_hold_minutes": json.dumps([hold_min, hold_max]),
            "expected_hold_min_minutes": hold_min,
            "expected_hold_max_minutes": hold_max,
            "historical_win_rate": historical_win_rate,
            "post_cost_edge_pct": post_cost_edge,
            "estimated_cost_drag_pct": cost_drag_pct,
            "economic_annotation_sample_size": sample_size,
            "economic_opportunity_status": (
                BetaExecutionEconomicAnnotationService.ACTIONABLE
                if actionable
                else BetaExecutionEconomicAnnotationService.NON_ACTIONABLE
            ),
            "economic_non_actionable_reason": None if actionable else "post_cost_edge_non_positive",
        }

    @staticmethod
    def estimated_cost_drag_pct(settings: BetaSettings) -> Decimal:
        total_bps = (
            Decimal(str(settings.intraday_execution_commission_bps))
            + Decimal(str(settings.intraday_execution_spread_bps))
            + Decimal(str(settings.intraday_execution_slippage_bps))
        )
        return (total_bps / Decimal("100")).quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP)

    @staticmethod
    def apply_annotation(signal: BetaExecutionSignal, annotation: dict[str, Any]) -> None:
        signal.expected_edge_pct = annotation.get("expected_edge_pct")
        signal.expected_hold_minutes = (
            str(annotation.get("expected_hold_minutes"))
            if annotation.get("expected_hold_minutes") is not None
            else None
        )
        signal.expected_hold_min_minutes = annotation.get("expected_hold_min_minutes")
        signal.expected_hold_max_minutes = annotation.get("expected_hold_max_minutes")
        signal.historical_win_rate = annotation.get("historical_win_rate")
        signal.post_cost_edge_pct = annotation.get("post_cost_edge_pct")
        signal.estimated_cost_drag_pct = annotation.get("estimated_cost_drag_pct")
        signal.economic_annotation_sample_size = annotation.get("economic_annotation_sample_size")
        signal.economic_opportunity_status = str(
            annotation.get("economic_opportunity_status")
            or BetaExecutionEconomicAnnotationService.NON_ACTIONABLE
        )
        signal.economic_non_actionable_reason = (
            str(annotation.get("economic_non_actionable_reason"))
            if annotation.get("economic_non_actionable_reason")
            else None
        )

    @staticmethod
    def refresh_historical_signal_annotations(
        settings: BetaSettings,
        *,
        signal_ids: list[str] | None = None,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"signals_updated": 0}

        with BetaContext.write_session() as sess:
            definition_rows = {
                row.id: row
                for row in sess.scalars(select(BetaExecutionHypothesisDefinition)).all()
            }
            query = select(BetaExecutionSignal).order_by(BetaExecutionSignal.signal_time.asc(), BetaExecutionSignal.created_at.asc())
            if signal_ids:
                query = query.where(BetaExecutionSignal.id.in_(signal_ids))
            signals = list(sess.scalars(query).all())
            for signal in signals:
                definition = definition_rows.get(signal.execution_hypothesis_definition_id or "")
                annotation = BetaExecutionEconomicAnnotationService.annotation_for_match(
                    sess=sess,
                    definition=definition,
                    signal_time=signal.signal_time,
                    signal_type=signal.signal_type,
                    event_trigger_code=signal.event_trigger_code,
                    settings=settings,
                )
                BetaExecutionEconomicAnnotationService.apply_annotation(signal, annotation)
            return {"signals_updated": len(signals)}
