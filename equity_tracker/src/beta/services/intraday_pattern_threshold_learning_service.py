"""Learn the thresholds that define intraday pattern tags from the recent observation corpus."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradayPatternThresholdProfile,
)
from ..settings import BetaSettings

_DEFAULT_THRESHOLDS = {
    "gap_up_pct": 1.0,
    "gap_down_pct": -1.0,
    "vwap_above_pct": 0.10,
    "vwap_below_pct": -0.10,
    "volume_high_ratio": 1.40,
    "volume_low_ratio": 0.75,
    "volatility_high_pct": 1.40,
    "volatility_low_pct": 0.45,
    "range_expansion_volatility_pct": 1.50,
    "momentum_up_pct": 0.80,
    "momentum_down_pct": -0.80,
    "range_compressed_pct": 1.20,
    "range_expanded_pct": 2.50,
    "sell_pressure_return_15m_pct": -1.0,
    "sell_pressure_return_since_open_pct": -1.5,
    "buy_pressure_return_15m_pct": 1.0,
    "buy_pressure_return_since_open_pct": 1.5,
    "close_drive_abs_return_30m_pct": 0.75,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    clipped_pct = min(1.0, max(0.0, float(pct)))
    index = (len(ordered) - 1) * clipped_pct
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    weight = index - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _clamp(value: float, *, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, float(value)))


def _session_segment_from_features(feature_values: dict[str, float | None]) -> str:
    minutes_since_open = _safe_float(feature_values.get("minutes_since_open"))
    minutes_until_close = _safe_float(feature_values.get("minutes_until_close"))
    session_progress = _safe_float(feature_values.get("session_progress_pct"))
    if minutes_since_open is not None and minutes_since_open <= 60:
        return "OPENING"
    if minutes_until_close is not None and minutes_until_close <= 45:
        return "CLOSING"
    if session_progress is not None and session_progress >= 70.0:
        return "LATE"
    if session_progress is not None:
        return "MIDDAY"
    return "UNKNOWN"


class BetaIntradayPatternThresholdLearningService:
    """Persist adaptive thresholds for the intraday pattern explorer."""

    @staticmethod
    def static_threshold_snapshot(settings: BetaSettings) -> dict[str, object]:
        return {
            "available": True,
            "active_for_runtime": True,
            "source_mode": "STATIC_SETTINGS",
            "source_label": "Static thresholds",
            "evaluation_window_days": int(
                getattr(settings, "intraday_pattern_threshold_learning_window_days", 45)
            ),
            "observation_count": 0,
            "distinct_instrument_count": 0,
            "confidence_score": 0.0,
            "thresholds": dict(_DEFAULT_THRESHOLDS),
            "notes": {},
            **dict(_DEFAULT_THRESHOLDS),
        }

    @staticmethod
    def latest_profile(settings: BetaSettings, *, require_active: bool = False) -> dict[str, object] | None:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_threshold_learning_enabled:
            return None
        with BetaContext.read_session() as sess:
            return BetaIntradayPatternThresholdLearningService.latest_profile_in_session(
                sess,
                settings,
                require_active=require_active,
            )

    @staticmethod
    def latest_profile_in_session(
        sess,
        settings: BetaSettings,
        *,
        require_active: bool = False,
    ) -> dict[str, object] | None:
        if not settings.intraday_pattern_threshold_learning_enabled:
            return None
        row = sess.scalar(
            select(BetaIntradayPatternThresholdProfile)
            .order_by(
                desc(BetaIntradayPatternThresholdProfile.created_at),
                desc(BetaIntradayPatternThresholdProfile.id),
            )
            .limit(1)
        )
        if row is None:
            return None
        profile = BetaIntradayPatternThresholdLearningService._profile_to_dict(row, settings=settings)
        if require_active and not bool(profile.get("active_for_runtime")):
            return None
        return profile

    @staticmethod
    def resolve_profile(settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternThresholdLearningService.latest_profile(settings, require_active=True)
        return learned or BetaIntradayPatternThresholdLearningService.static_threshold_snapshot(settings)

    @staticmethod
    def resolve_profile_in_session(sess, settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternThresholdLearningService.latest_profile_in_session(
            sess,
            settings,
            require_active=True,
        )
        return learned or BetaIntradayPatternThresholdLearningService.static_threshold_snapshot(settings)

    @staticmethod
    def learn_threshold_profile(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_threshold_learning_enabled:
            return {"profile_created": False, "reason": "disabled"}

        now = _utcnow()
        window_days = max(14, int(settings.intraday_pattern_threshold_learning_window_days))
        cutoff = now - timedelta(days=window_days)

        with BetaContext.write_session() as sess:
            observation_rows = list(
                sess.scalars(
                    select(BetaIntradayFeatureObservation)
                    .join(
                        BetaIntradayFeatureLabelValue,
                        BetaIntradayFeatureLabelValue.observation_id == BetaIntradayFeatureObservation.id,
                    )
                    .where(
                        BetaIntradayFeatureObservation.session_state == "REGULAR_OPEN",
                        BetaIntradayFeatureObservation.observed_at >= cutoff,
                        BetaIntradayFeatureLabelValue.evaluation_complete.is_(True),
                    )
                    .order_by(BetaIntradayFeatureObservation.observed_at.asc())
                ).all()
            )
            feature_rows = []
            input_rows: list[dict[str, object]] = []
            distinct_instruments: set[str] = set()
            for observation in observation_rows:
                feature_values = {
                    str(key): _safe_float(value)
                    for key, value in _json_object(observation.feature_snapshot_json).items()
                }
                if not feature_values:
                    continue
                feature_rows.append(feature_values)
                input_rows.append(
                    {
                        "observation_id": str(observation.id or ""),
                        "instrument_id": str(observation.instrument_id or ""),
                        "observed_at": observation.observed_at.isoformat() if observation.observed_at else None,
                        "feature_snapshot_json": str(observation.feature_snapshot_json or ""),
                    }
                )
                instrument_id = str(observation.instrument_id or "").strip()
                if instrument_id:
                    distinct_instruments.add(instrument_id)

            if not feature_rows:
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "reason": "no_observations",
                }

            thresholds, notes = BetaIntradayPatternThresholdLearningService._learn_thresholds(feature_rows)
            observation_count = len(feature_rows)
            distinct_instrument_count = len(distinct_instruments)
            input_summary = {
                "window_days": window_days,
                "observation_count": observation_count,
                "distinct_instrument_count": distinct_instrument_count,
                "first_observed_at": input_rows[0]["observed_at"] if input_rows else None,
                "last_observed_at": input_rows[-1]["observed_at"] if input_rows else None,
                "feature_counts": dict(notes.get("feature_counts") or {}),
            }
            notes = {
                **notes,
                "input_summary": input_summary,
                "input_fingerprint": _fingerprint_payload(
                    {
                        "window_days": window_days,
                        "observations": input_rows,
                    }
                ),
            }
            confidence_score = BetaIntradayPatternThresholdLearningService._confidence_score(
                observation_count=observation_count,
                distinct_instrument_count=distinct_instrument_count,
                feature_counts=notes.get("feature_counts") or {},
                settings=settings,
            )
            payload = {
                "source_mode": "OBSERVATION_DISTRIBUTION",
                "evaluation_window_days": window_days,
                "observation_count": observation_count,
                "distinct_instrument_count": distinct_instrument_count,
                "confidence_score": confidence_score,
                "thresholds": thresholds,
                "notes": notes,
            }

            latest_profile = sess.scalar(
                select(BetaIntradayPatternThresholdProfile)
                .order_by(
                    desc(BetaIntradayPatternThresholdProfile.created_at),
                    desc(BetaIntradayPatternThresholdProfile.id),
                )
                .limit(1)
            )
            if latest_profile is not None and BetaIntradayPatternThresholdLearningService._matches_latest(
                latest_profile,
                payload,
            ):
                existing = BetaIntradayPatternThresholdLearningService._profile_to_dict(
                    latest_profile,
                    settings=settings,
                )
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "unchanged": True,
                    "reason": "unchanged_intraday_pattern_threshold_inputs",
                    "profile_id": latest_profile.id,
                    "active_for_runtime": existing.get("active_for_runtime"),
                    "source_mode": existing.get("source_mode"),
                    "input_fingerprint": str((existing.get("notes") or {}).get("input_fingerprint") or ""),
                    "input_summary": dict((existing.get("notes") or {}).get("input_summary") or {}),
                }

            profile = BetaIntradayPatternThresholdProfile(
                profile_code=now.strftime("%Y%m%d%H%M%S%f"),
                source_mode=str(payload["source_mode"]),
                evaluation_window_days=int(payload["evaluation_window_days"]),
                observation_count=int(payload["observation_count"]),
                distinct_instrument_count=int(payload["distinct_instrument_count"]),
                confidence_score=float(payload["confidence_score"]),
                thresholds_json=json.dumps(payload["thresholds"], sort_keys=True),
                notes_json=json.dumps(payload["notes"], sort_keys=True),
            )
            sess.add(profile)
            sess.flush()
            stored = BetaIntradayPatternThresholdLearningService._profile_to_dict(profile, settings=settings)
            return {
                "profile_created": True,
                "profile_id": profile.id,
                "active_for_runtime": stored.get("active_for_runtime"),
                "source_mode": stored.get("source_mode"),
                "observation_count": stored.get("observation_count"),
                "distinct_instrument_count": stored.get("distinct_instrument_count"),
                "confidence_score": stored.get("confidence_score"),
            }

    @staticmethod
    def _learn_thresholds(
        feature_rows: list[dict[str, float | None]],
    ) -> tuple[dict[str, float], dict[str, object]]:
        def _values(key: str, *, positive_only: bool = False) -> list[float]:
            collected = [
                float(value)
                for value in (_safe_float(row.get(key)) for row in feature_rows)
                if value is not None and (not positive_only or float(value) > 0.0)
            ]
            return collected

        def _positive_threshold(
            values: list[float],
            *,
            pct: float,
            floor: float,
            ceiling: float,
            default: float,
        ) -> float:
            candidate = _percentile([value for value in values if value > 0.0], pct)
            return round(_clamp(candidate if candidate is not None else default, floor=floor, ceiling=ceiling), 4)

        def _negative_threshold(
            values: list[float],
            *,
            pct: float,
            min_abs: float,
            max_abs: float,
            default: float,
        ) -> float:
            negatives = [value for value in values if value < 0.0]
            candidate = _percentile(negatives, pct)
            magnitude = abs(candidate) if candidate is not None else abs(default)
            return round(-_clamp(magnitude, floor=min_abs, ceiling=max_abs), 4)

        gap_values = _values("gap_from_prev_close_pct")
        vwap_values = _values("distance_from_vwap_pct")
        volume_values = (
            _values("volume_last_15m_vs_expected", positive_only=True)
            or _values("volume_last_30m_vs_expected", positive_only=True)
        )
        volatility_values = _values("rolling_intraday_vol_15m_pct", positive_only=True)
        momentum_values = _values("return_last_15m_pct")
        ret_30_values = _values("return_last_30m_pct")
        ret_open_values = _values("return_since_open_pct")
        range_values = _values("intraday_range_pct", positive_only=True)
        close_drive_values = [
            abs(float(value))
            for row in feature_rows
            for value in [_safe_float(row.get("return_last_30m_pct"))]
            if value is not None and _session_segment_from_features(row) in {"LATE", "CLOSING"}
        ]

        thresholds = dict(_DEFAULT_THRESHOLDS)
        thresholds["gap_up_pct"] = _positive_threshold(
            gap_values,
            pct=0.75,
            floor=0.40,
            ceiling=4.50,
            default=_DEFAULT_THRESHOLDS["gap_up_pct"],
        )
        thresholds["gap_down_pct"] = _negative_threshold(
            gap_values,
            pct=0.25,
            min_abs=0.40,
            max_abs=4.50,
            default=_DEFAULT_THRESHOLDS["gap_down_pct"],
        )
        thresholds["vwap_above_pct"] = _positive_threshold(
            vwap_values,
            pct=0.75,
            floor=0.02,
            ceiling=0.80,
            default=_DEFAULT_THRESHOLDS["vwap_above_pct"],
        )
        thresholds["vwap_below_pct"] = _negative_threshold(
            vwap_values,
            pct=0.25,
            min_abs=0.02,
            max_abs=0.80,
            default=_DEFAULT_THRESHOLDS["vwap_below_pct"],
        )
        thresholds["volume_low_ratio"] = _positive_threshold(
            volume_values,
            pct=0.25,
            floor=0.20,
            ceiling=1.10,
            default=_DEFAULT_THRESHOLDS["volume_low_ratio"],
        )
        thresholds["volume_high_ratio"] = _positive_threshold(
            volume_values,
            pct=0.75,
            floor=0.90,
            ceiling=3.50,
            default=_DEFAULT_THRESHOLDS["volume_high_ratio"],
        )
        thresholds["volatility_low_pct"] = _positive_threshold(
            volatility_values,
            pct=0.25,
            floor=0.08,
            ceiling=0.90,
            default=_DEFAULT_THRESHOLDS["volatility_low_pct"],
        )
        thresholds["volatility_high_pct"] = _positive_threshold(
            volatility_values,
            pct=0.75,
            floor=0.35,
            ceiling=4.50,
            default=_DEFAULT_THRESHOLDS["volatility_high_pct"],
        )
        thresholds["momentum_up_pct"] = _positive_threshold(
            momentum_values,
            pct=0.75,
            floor=0.20,
            ceiling=4.50,
            default=_DEFAULT_THRESHOLDS["momentum_up_pct"],
        )
        thresholds["momentum_down_pct"] = _negative_threshold(
            momentum_values,
            pct=0.25,
            min_abs=0.20,
            max_abs=4.50,
            default=_DEFAULT_THRESHOLDS["momentum_down_pct"],
        )
        thresholds["range_compressed_pct"] = _positive_threshold(
            range_values,
            pct=0.25,
            floor=0.20,
            ceiling=2.00,
            default=_DEFAULT_THRESHOLDS["range_compressed_pct"],
        )
        thresholds["range_expansion_volatility_pct"] = _positive_threshold(
            volatility_values,
            pct=0.80,
            floor=0.45,
            ceiling=5.00,
            default=_DEFAULT_THRESHOLDS["range_expansion_volatility_pct"],
        )
        thresholds["range_expanded_pct"] = _positive_threshold(
            range_values,
            pct=0.75,
            floor=1.00,
            ceiling=6.00,
            default=_DEFAULT_THRESHOLDS["range_expanded_pct"],
        )
        thresholds["sell_pressure_return_since_open_pct"] = _negative_threshold(
            ret_open_values,
            pct=0.20,
            min_abs=0.60,
            max_abs=6.00,
            default=_DEFAULT_THRESHOLDS["sell_pressure_return_since_open_pct"],
        )
        thresholds["buy_pressure_return_since_open_pct"] = _positive_threshold(
            ret_open_values,
            pct=0.80,
            floor=0.60,
            ceiling=6.00,
            default=_DEFAULT_THRESHOLDS["buy_pressure_return_since_open_pct"],
        )
        thresholds["close_drive_abs_return_30m_pct"] = _positive_threshold(
            close_drive_values or [abs(value) for value in ret_30_values if abs(value) > 0.0],
            pct=0.75,
            floor=0.30,
            ceiling=4.50,
            default=_DEFAULT_THRESHOLDS["close_drive_abs_return_30m_pct"],
        )

        thresholds["volume_low_ratio"] = round(
            min(thresholds["volume_low_ratio"], max(0.25, thresholds["volume_high_ratio"] * 0.80)),
            4,
        )
        thresholds["volatility_low_pct"] = round(
            min(thresholds["volatility_low_pct"], max(0.10, thresholds["volatility_high_pct"] * 0.70)),
            4,
        )
        thresholds["range_compressed_pct"] = round(
            min(thresholds["range_compressed_pct"], max(0.25, thresholds["range_expanded_pct"] * 0.70)),
            4,
        )

        thresholds["sell_pressure_return_15m_pct"] = round(
            min(
                -0.25,
                thresholds["momentum_down_pct"] * 1.15,
            ),
            4,
        )
        thresholds["buy_pressure_return_15m_pct"] = round(
            max(
                0.25,
                thresholds["momentum_up_pct"] * 1.15,
            ),
            4,
        )

        feature_counts = {
            "gap": len(gap_values),
            "vwap_distance": len(vwap_values),
            "volume": len(volume_values),
            "volatility": len(volatility_values),
            "momentum": len(momentum_values),
            "return_30m": len(ret_30_values),
            "return_since_open": len(ret_open_values),
            "range": len(range_values),
            "close_drive": len(close_drive_values),
        }
        notes = {
            "feature_counts": feature_counts,
            "coverage_ratio": round(
                sum(1 for count in feature_counts.values() if int(count) > 0) / max(1, len(feature_counts)),
                6,
            ),
        }
        return thresholds, notes

    @staticmethod
    def _confidence_score(
        *,
        observation_count: int,
        distinct_instrument_count: int,
        feature_counts: dict[str, object],
        settings: BetaSettings,
    ) -> float:
        minimum = max(1, int(settings.intraday_pattern_threshold_learning_min_observations))
        support_factor = min(1.0, float(observation_count) / float(minimum))
        diversity_factor = min(1.0, float(distinct_instrument_count) / 8.0)
        populated_features = sum(1 for value in feature_counts.values() if int(value or 0) > 0)
        coverage_factor = min(1.0, float(populated_features) / max(1.0, float(len(feature_counts) or 1)))
        return round(
            max(
                0.0,
                min(
                    1.0,
                    (support_factor * 0.60) + (diversity_factor * 0.25) + (coverage_factor * 0.15),
                ),
            ),
            6,
        )

    @staticmethod
    def _matches_latest(
        latest_profile: BetaIntradayPatternThresholdProfile,
        payload: dict[str, object],
    ) -> bool:
        latest_thresholds = _json_object(latest_profile.thresholds_json)
        latest_notes = _json_object(latest_profile.notes_json)
        return (
            str(latest_profile.source_mode or "") == str(payload["source_mode"])
            and int(latest_profile.evaluation_window_days or 0) == int(payload["evaluation_window_days"])
            and int(latest_profile.observation_count or 0) == int(payload["observation_count"])
            and int(latest_profile.distinct_instrument_count or 0) == int(payload["distinct_instrument_count"])
            and round(float(latest_profile.confidence_score or 0.0), 4)
            == round(float(payload["confidence_score"] or 0.0), 4)
            and latest_thresholds == dict(payload.get("thresholds") or {})
            and latest_notes == dict(payload.get("notes") or {})
        )

    @staticmethod
    def _profile_to_dict(
        row: BetaIntradayPatternThresholdProfile,
        *,
        settings: BetaSettings,
    ) -> dict[str, object]:
        thresholds = {
            **dict(_DEFAULT_THRESHOLDS),
            **{
                str(key): float(value)
                for key, value in _json_object(row.thresholds_json).items()
                if _safe_float(value) is not None
            },
        }
        notes = _json_object(row.notes_json)
        confidence_score = float(row.confidence_score or 0.0)
        active_for_runtime = bool(
            settings.intraday_pattern_threshold_learning_enabled
            and int(row.observation_count or 0) >= int(settings.intraday_pattern_threshold_learning_min_observations)
            and confidence_score >= 0.25
        )
        source_mode = str(row.source_mode or "OBSERVATION_DISTRIBUTION")
        source_label = {
            "OBSERVATION_DISTRIBUTION": "Learned thresholds",
            "STATIC_FALLBACK": "Static fallback",
        }.get(source_mode, source_mode.replace("_", " ").title())
        return {
            "available": True,
            "id": row.id,
            "profile_code": row.profile_code,
            "source_mode": source_mode,
            "source_label": source_label,
            "active_for_runtime": active_for_runtime,
            "evaluation_window_days": int(row.evaluation_window_days or 0),
            "observation_count": int(row.observation_count or 0),
            "distinct_instrument_count": int(row.distinct_instrument_count or 0),
            "confidence_score": confidence_score,
            "thresholds": thresholds,
            "notes": notes,
            "created_at": row.created_at,
            **thresholds,
        }
