"""Learn pattern-trade execution parameters from recent closed intraday trades."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, or_, select

from ..context import BetaContext
from ..db.models import BetaIntradayPatternExecutionProfile, BetaIntradaySimulatedTrade
from ..settings import BetaSettings

_LIVE_FORWARD_WEIGHT = 1.0
_HISTORICAL_BACKFILL_WEIGHT = 0.35


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


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


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
    return round(ordered[lower] + ((ordered[upper] - ordered[lower]) * weight), 6)


def _weighted_mean(rows: list[dict[str, object]], key: str) -> float | None:
    weighted_sum = 0.0
    weight_total = 0.0
    for row in rows:
        value = _safe_float(row.get(key))
        weight = _safe_float(row.get("weight"))
        if value is None or weight is None or weight <= 0.0:
            continue
        weighted_sum += value * weight
        weight_total += weight
    if weight_total <= 0.0:
        return None
    return round(weighted_sum / weight_total, 6)


class BetaIntradayPatternExecutionLearningService:
    """Persist a learned execution profile for future pattern-trade entry planning."""

    @staticmethod
    def static_execution_snapshot(settings: BetaSettings) -> dict[str, object]:
        base_bail_ratio = float(settings.intraday_short_trade_bail_after_minutes) / max(
            1.0,
            float(settings.intraday_short_trade_max_hold_minutes),
        )
        return {
            "available": True,
            "active_for_runtime": True,
            "source_mode": "STATIC_SETTINGS",
            "source_label": "Static execution",
            "evaluation_window_days": int(
                getattr(settings, "intraday_pattern_execution_learning_window_days", 30)
            ),
            "trade_count": 0,
            "live_forward_trade_count": 0,
            "historical_backfill_trade_count": 0,
            "winner_count": 0,
            "distinct_pattern_count": 0,
            "confidence_score": 0.0,
            "recommended_target_capture_ratio": 1.0,
            "recommended_stop_loss_ratio": 1.0,
            "recommended_max_hold_ratio": 1.0,
            "recommended_early_bail_ratio": round(max(0.12, min(0.60, base_bail_ratio)), 4),
            "notes": {},
        }

    @staticmethod
    def latest_profile(settings: BetaSettings, *, require_active: bool = False) -> dict[str, object] | None:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_execution_learning_enabled:
            return None
        with BetaContext.read_session() as sess:
            return BetaIntradayPatternExecutionLearningService.latest_profile_in_session(
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
        if not settings.intraday_pattern_execution_learning_enabled:
            return None
        row = sess.scalar(
            select(BetaIntradayPatternExecutionProfile)
            .order_by(
                desc(BetaIntradayPatternExecutionProfile.created_at),
                desc(BetaIntradayPatternExecutionProfile.id),
            )
            .limit(1)
        )
        if row is None:
            return None
        profile = BetaIntradayPatternExecutionLearningService._profile_to_dict(row, settings=settings)
        if require_active and not bool(profile.get("active_for_runtime")):
            return None
        return profile

    @staticmethod
    def resolve_profile(settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternExecutionLearningService.latest_profile(settings, require_active=True)
        return learned or BetaIntradayPatternExecutionLearningService.static_execution_snapshot(settings)

    @staticmethod
    def resolve_profile_in_session(sess, settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternExecutionLearningService.latest_profile_in_session(
            sess,
            settings,
            require_active=True,
        )
        return learned or BetaIntradayPatternExecutionLearningService.static_execution_snapshot(settings)

    @staticmethod
    def learn_execution_profile(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_execution_learning_enabled:
            return {"profile_created": False, "reason": "disabled"}

        now = _utcnow()
        window_days = max(7, int(settings.intraday_pattern_execution_learning_window_days))
        cutoff = now - timedelta(days=window_days)

        with BetaContext.write_session() as sess:
            evidence_rows = BetaIntradayPatternExecutionLearningService._trade_evidence_rows(
                sess,
                cutoff=cutoff,
            )
            if not evidence_rows:
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "reason": "no_evidence",
                }

            payload = BetaIntradayPatternExecutionLearningService._profile_payload(
                evidence_rows=evidence_rows,
                settings=settings,
                window_days=window_days,
            )
            latest_profile = sess.scalar(
                select(BetaIntradayPatternExecutionProfile)
                .order_by(
                    desc(BetaIntradayPatternExecutionProfile.created_at),
                    desc(BetaIntradayPatternExecutionProfile.id),
                )
                .limit(1)
            )
            if latest_profile is not None and BetaIntradayPatternExecutionLearningService._matches_latest(
                latest_profile,
                payload,
            ):
                existing = BetaIntradayPatternExecutionLearningService._profile_to_dict(
                    latest_profile,
                    settings=settings,
                )
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "unchanged": True,
                    "reason": "unchanged_intraday_pattern_execution_inputs",
                    "profile_id": latest_profile.id,
                    "active_for_runtime": existing.get("active_for_runtime"),
                    "source_mode": existing.get("source_mode"),
                    "input_fingerprint": str((existing.get("notes") or {}).get("input_fingerprint") or ""),
                    "input_summary": dict((existing.get("notes") or {}).get("input_summary") or {}),
                }

            profile = BetaIntradayPatternExecutionProfile(
                profile_code=now.strftime("%Y%m%d%H%M%S%f"),
                source_mode=str(payload["source_mode"]),
                evaluation_window_days=int(payload["evaluation_window_days"]),
                trade_count=int(payload["trade_count"]),
                live_forward_trade_count=int(payload["live_forward_trade_count"]),
                historical_backfill_trade_count=int(payload["historical_backfill_trade_count"]),
                winner_count=int(payload["winner_count"]),
                distinct_pattern_count=int(payload["distinct_pattern_count"]),
                confidence_score=float(payload["confidence_score"]),
                recommended_target_capture_ratio=payload.get("recommended_target_capture_ratio"),
                recommended_stop_loss_ratio=payload.get("recommended_stop_loss_ratio"),
                recommended_max_hold_ratio=payload.get("recommended_max_hold_ratio"),
                recommended_early_bail_ratio=payload.get("recommended_early_bail_ratio"),
                notes_json=json.dumps(payload.get("notes") or {}, sort_keys=True),
            )
            sess.add(profile)
            sess.flush()
            stored = BetaIntradayPatternExecutionLearningService._profile_to_dict(profile, settings=settings)
            return {
                "profile_created": True,
                "profile_id": profile.id,
                "active_for_runtime": stored.get("active_for_runtime"),
                "source_mode": stored.get("source_mode"),
                "recommended_target_capture_ratio": stored.get("recommended_target_capture_ratio"),
                "recommended_stop_loss_ratio": stored.get("recommended_stop_loss_ratio"),
                "recommended_max_hold_ratio": stored.get("recommended_max_hold_ratio"),
                "recommended_early_bail_ratio": stored.get("recommended_early_bail_ratio"),
            }

    @staticmethod
    def _trade_evidence_rows(sess, *, cutoff: datetime) -> list[dict[str, object]]:
        trades = list(
            sess.scalars(
                select(BetaIntradaySimulatedTrade)
                .where(
                    BetaIntradaySimulatedTrade.status == "CLOSED",
                    or_(
                        BetaIntradaySimulatedTrade.exit_observed_at >= cutoff,
                        BetaIntradaySimulatedTrade.updated_at >= cutoff,
                    ),
                )
                .order_by(desc(BetaIntradaySimulatedTrade.updated_at), desc(BetaIntradaySimulatedTrade.created_at))
                .limit(500)
            ).all()
        )
        rows: list[dict[str, object]] = []
        for trade in trades:
            notes = _json_object(trade.notes_json)
            if str(notes.get("entry_source") or "").strip().upper() != "PATTERN":
                continue
            realized = _safe_float(trade.realized_post_cost_return_pct)
            if realized is None:
                realized = _safe_float(trade.realized_return_pct)
            if realized is None:
                continue
            simulation_source = str(trade.simulation_source or "").strip().upper()
            weight = _LIVE_FORWARD_WEIGHT if simulation_source == "LIVE_FORWARD" else _HISTORICAL_BACKFILL_WEIGHT
            rows.append(
                {
                    "pattern_hash": str(notes.get("pattern_hash") or "").strip() or None,
                    "simulation_source": simulation_source,
                    "realized_post_cost_return_pct": realized,
                    "target_return_pct": _safe_float(trade.target_return_pct),
                    "stop_loss_pct": _safe_float(trade.stop_loss_pct),
                    "max_hold_minutes": _safe_float(trade.max_hold_minutes),
                    "hold_minutes": _safe_float(trade.hold_minutes),
                    "max_return_pct": _safe_float(trade.max_return_pct),
                    "max_drawdown_pct": _safe_float(trade.max_drawdown_pct),
                    "exit_reason_code": str(trade.exit_reason_code or "").strip().upper() or None,
                    "weight": weight,
                    "exit_observed_at": trade.exit_observed_at.isoformat() if trade.exit_observed_at else None,
                }
            )
        return rows

    @staticmethod
    def _profile_payload(
        *,
        evidence_rows: list[dict[str, object]],
        settings: BetaSettings,
        window_days: int,
    ) -> dict[str, object]:
        winners = [row for row in evidence_rows if float(row["realized_post_cost_return_pct"]) > 0.0]
        losers = [row for row in evidence_rows if float(row["realized_post_cost_return_pct"]) <= 0.0]
        trade_count = len(evidence_rows)
        live_count = sum(1 for row in evidence_rows if row["simulation_source"] == "LIVE_FORWARD")
        historical_count = sum(1 for row in evidence_rows if row["simulation_source"] == "HISTORICAL_BACKFILL")
        distinct_pattern_count = len({str(row["pattern_hash"]) for row in evidence_rows if row.get("pattern_hash")})
        win_rate_decimal = _weighted_mean(
            [{**row, "is_win": 1.0 if float(row["realized_post_cost_return_pct"]) > 0.0 else 0.0} for row in evidence_rows],
            "is_win",
        ) or 0.0

        target_capture_values: list[float] = []
        winner_hold_ratios: list[float] = []
        loser_hold_ratios: list[float] = []
        stop_loss_ratios: list[float] = []
        target_hit_weights: list[dict[str, object]] = []
        time_exit_weights: list[dict[str, object]] = []
        early_exit_weights: list[dict[str, object]] = []

        for row in evidence_rows:
            weight = float(row.get("weight") or 0.0)
            target = _safe_float(row.get("target_return_pct"))
            stop = _safe_float(row.get("stop_loss_pct"))
            hold = _safe_float(row.get("hold_minutes"))
            max_hold = _safe_float(row.get("max_hold_minutes"))
            realized = float(row["realized_post_cost_return_pct"])
            exit_reason = str(row.get("exit_reason_code") or "")

            target_hit_weights.append(
                {"flag": 1.0 if exit_reason == "TARGET_HIT" else 0.0, "weight": weight}
            )
            time_exit_weights.append(
                {"flag": 1.0 if exit_reason in {"TIME_EXIT", "SESSION_END"} else 0.0, "weight": weight}
            )
            early_exit_weights.append(
                {"flag": 1.0 if exit_reason in {"EARLY_BAIL", "EVIDENCE_FADED", "WEAKENING_EXIT"} else 0.0, "weight": weight}
            )

            if realized > 0.0 and target is not None and target > 0.0:
                attainable = max(realized, _safe_float(row.get("max_return_pct")) or realized)
                target_capture_values.append(max(0.40, min(1.40, attainable / target)))
            if hold is not None and max_hold is not None and hold > 0.0 and max_hold > 0.0:
                ratio = max(0.05, min(1.50, hold / max_hold))
                if realized > 0.0:
                    winner_hold_ratios.append(ratio)
                else:
                    loser_hold_ratios.append(ratio)
            if realized <= 0.0 and stop is not None and stop > 0.0:
                ratio = abs(realized) / stop
                stop_loss_ratios.append(max(0.35, min(1.30, ratio)))

        target_capture_ratio = _percentile(target_capture_values, 0.55) or 0.90
        target_hit_rate = _weighted_mean(target_hit_weights, "flag") or 0.0
        time_exit_rate = _weighted_mean(time_exit_weights, "flag") or 0.0
        early_exit_rate = _weighted_mean(early_exit_weights, "flag") or 0.0
        target_capture_ratio = max(0.65, min(1.10, target_capture_ratio))
        if target_hit_rate >= 0.50:
            target_capture_ratio = min(1.10, max(target_capture_ratio, 0.97))
        elif time_exit_rate >= 0.35:
            target_capture_ratio = max(0.70, min(target_capture_ratio, 0.88))

        stop_loss_ratio = _percentile(stop_loss_ratios, 0.65) or 0.95
        stop_loss_ratio = max(0.70, min(1.15, stop_loss_ratio))
        if early_exit_rate >= 0.40:
            stop_loss_ratio = max(0.70, min(stop_loss_ratio, 0.90))

        max_hold_ratio = _percentile(winner_hold_ratios, 0.70) or 0.90
        max_hold_ratio = max(0.55, min(1.10, max_hold_ratio))
        early_bail_ratio = _percentile(loser_hold_ratios, 0.45) or 0.30
        early_bail_ratio = max(0.15, min(0.70, early_bail_ratio))

        confidence_score = BetaIntradayPatternExecutionLearningService._confidence_score(
            trade_count=trade_count,
            live_count=live_count,
            distinct_pattern_count=distinct_pattern_count,
            settings=settings,
        )
        normalized_rows = sorted(
            [
                {
                    "pattern_hash": str(row.get("pattern_hash") or ""),
                    "simulation_source": str(row.get("simulation_source") or ""),
                    "realized_post_cost_return_pct": _safe_float(row.get("realized_post_cost_return_pct")),
                    "target_return_pct": _safe_float(row.get("target_return_pct")),
                    "stop_loss_pct": _safe_float(row.get("stop_loss_pct")),
                    "max_hold_minutes": _safe_float(row.get("max_hold_minutes")),
                    "hold_minutes": _safe_float(row.get("hold_minutes")),
                    "max_return_pct": _safe_float(row.get("max_return_pct")),
                    "max_drawdown_pct": _safe_float(row.get("max_drawdown_pct")),
                    "exit_reason_code": str(row.get("exit_reason_code") or ""),
                    "exit_observed_at": str(row.get("exit_observed_at") or ""),
                }
                for row in evidence_rows
            ],
            key=lambda row: (
                row["pattern_hash"],
                row["simulation_source"],
                row["exit_observed_at"],
                row["exit_reason_code"],
            ),
        )
        input_summary = {
            "window_days": window_days,
            "trade_count": trade_count,
            "live_forward_trade_count": live_count,
            "historical_backfill_trade_count": historical_count,
            "distinct_pattern_count": distinct_pattern_count,
            "first_exit_observed_at": normalized_rows[0]["exit_observed_at"] if normalized_rows else None,
            "last_exit_observed_at": normalized_rows[-1]["exit_observed_at"] if normalized_rows else None,
        }

        return {
            "source_mode": "OUTCOME_DRIVEN"
            if live_count >= int(settings.intraday_pattern_execution_learning_min_closed_trades)
            else "OUTCOME_MIXED",
            "evaluation_window_days": window_days,
            "trade_count": trade_count,
            "live_forward_trade_count": live_count,
            "historical_backfill_trade_count": historical_count,
            "winner_count": len(winners),
            "distinct_pattern_count": distinct_pattern_count,
            "confidence_score": confidence_score,
            "recommended_target_capture_ratio": round(target_capture_ratio, 4),
            "recommended_stop_loss_ratio": round(stop_loss_ratio, 4),
            "recommended_max_hold_ratio": round(max_hold_ratio, 4),
            "recommended_early_bail_ratio": round(early_bail_ratio, 4),
            "notes": {
                "input_summary": input_summary,
                "input_fingerprint": _fingerprint_payload(
                    {
                        "window_days": window_days,
                        "rows": normalized_rows,
                    }
                ),
                "weighted_win_rate_decimal": round(win_rate_decimal, 6),
                "target_hit_rate": round(target_hit_rate, 6),
                "time_exit_rate": round(time_exit_rate, 6),
                "early_exit_rate": round(early_exit_rate, 6),
                "median_target_capture_ratio": _percentile(target_capture_values, 0.50),
                "median_winner_hold_ratio": _percentile(winner_hold_ratios, 0.50),
                "median_loser_hold_ratio": _percentile(loser_hold_ratios, 0.50),
                "median_stop_loss_ratio": _percentile(stop_loss_ratios, 0.50),
            },
        }

    @staticmethod
    def _confidence_score(
        *,
        trade_count: int,
        live_count: int,
        distinct_pattern_count: int,
        settings: BetaSettings,
    ) -> float:
        minimum = max(1, int(settings.intraday_pattern_execution_learning_min_closed_trades))
        support_factor = min(1.0, float(trade_count) / float(minimum))
        live_factor = min(1.0, float(live_count) / max(1.0, float(minimum) * 0.5))
        diversity_factor = min(1.0, float(distinct_pattern_count) / 3.0)
        return round(
            max(
                0.0,
                min(
                    1.0,
                    support_factor * ((0.50 + (0.30 * live_factor)) + (0.20 * diversity_factor)),
                ),
            ),
            6,
        )

    @staticmethod
    def _matches_latest(
        latest_profile: BetaIntradayPatternExecutionProfile,
        payload: dict[str, object],
    ) -> bool:
        latest_notes = _json_object(latest_profile.notes_json)
        payload_notes = payload.get("notes") or {}
        return (
            str(latest_profile.source_mode or "") == str(payload["source_mode"])
            and int(latest_profile.trade_count or 0) == int(payload["trade_count"])
            and int(latest_profile.live_forward_trade_count or 0) == int(payload["live_forward_trade_count"])
            and int(latest_profile.historical_backfill_trade_count or 0)
            == int(payload["historical_backfill_trade_count"])
            and int(latest_profile.winner_count or 0) == int(payload["winner_count"])
            and int(latest_profile.distinct_pattern_count or 0) == int(payload["distinct_pattern_count"])
            and round(float(latest_profile.confidence_score or 0.0), 4)
            == round(float(payload["confidence_score"] or 0.0), 4)
            and round(float(latest_profile.recommended_target_capture_ratio or 0.0), 4)
            == round(float(payload.get("recommended_target_capture_ratio") or 0.0), 4)
            and round(float(latest_profile.recommended_stop_loss_ratio or 0.0), 4)
            == round(float(payload.get("recommended_stop_loss_ratio") or 0.0), 4)
            and round(float(latest_profile.recommended_max_hold_ratio or 0.0), 4)
            == round(float(payload.get("recommended_max_hold_ratio") or 0.0), 4)
            and round(float(latest_profile.recommended_early_bail_ratio or 0.0), 4)
            == round(float(payload.get("recommended_early_bail_ratio") or 0.0), 4)
            and latest_notes == payload_notes
        )

    @staticmethod
    def _profile_to_dict(
        row: BetaIntradayPatternExecutionProfile,
        *,
        settings: BetaSettings,
    ) -> dict[str, object]:
        notes = _json_object(row.notes_json)
        confidence_score = float(row.confidence_score or 0.0)
        active_for_runtime = bool(
            settings.intraday_pattern_execution_learning_enabled
            and (
                confidence_score >= 0.25
                or (
                    int(row.trade_count or 0) >= int(settings.intraday_pattern_execution_learning_min_closed_trades)
                    and confidence_score >= 0.18
                )
            )
        )
        source_mode = str(row.source_mode or "OUTCOME_DRIVEN")
        source_label = {
            "OUTCOME_DRIVEN": "Outcome-driven execution",
            "OUTCOME_MIXED": "Mixed execution evidence",
        }.get(source_mode, source_mode.replace("_", " ").title())
        return {
            "available": True,
            "id": row.id,
            "profile_code": row.profile_code,
            "source_mode": source_mode,
            "source_label": source_label,
            "active_for_runtime": active_for_runtime,
            "evaluation_window_days": int(row.evaluation_window_days or 0),
            "trade_count": int(row.trade_count or 0),
            "live_forward_trade_count": int(row.live_forward_trade_count or 0),
            "historical_backfill_trade_count": int(row.historical_backfill_trade_count or 0),
            "winner_count": int(row.winner_count or 0),
            "distinct_pattern_count": int(row.distinct_pattern_count or 0),
            "confidence_score": confidence_score,
            "recommended_target_capture_ratio": _safe_float(row.recommended_target_capture_ratio),
            "recommended_stop_loss_ratio": _safe_float(row.recommended_stop_loss_ratio),
            "recommended_max_hold_ratio": _safe_float(row.recommended_max_hold_ratio),
            "recommended_early_bail_ratio": _safe_float(row.recommended_early_bail_ratio),
            "notes": notes,
            "created_at": row.created_at,
        }
