"""Learn conservative pattern-approval policy knobs from realized intraday trade evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, or_, select

from ..context import BetaContext
from ..db.models import (
    BetaIntradayPatternCandidate,
    BetaIntradayPatternDiscoveryRun,
    BetaIntradayPatternPolicyProfile,
    BetaIntradaySimulatedTrade,
)
from ..settings import BetaSettings

_MIN_STABILITY_FLOOR = 0.20
_MIN_SAMPLE_QUALITY_FLOOR = 0.25
_MIN_RELIABILITY_FLOOR = 0.10
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


class BetaIntradayPatternParameterLearningService:
    """Persist a learned live-forward approval policy from realized pattern outcomes."""

    @staticmethod
    def static_policy_snapshot(settings: BetaSettings) -> dict[str, object]:
        return {
            "available": True,
            "active_for_runtime": True,
            "source_mode": "STATIC_SETTINGS",
            "source_label": "Static settings",
            "evaluation_window_days": int(getattr(settings, "intraday_pattern_parameter_learning_window_days", 30)),
            "trade_count": 0,
            "live_forward_trade_count": 0,
            "historical_backfill_trade_count": 0,
            "winner_count": 0,
            "distinct_pattern_count": 0,
            "avg_realized_post_cost_return_pct": None,
            "win_rate_pct": None,
            "confidence_score": 0.0,
            "recommended_min_quality_score": float(settings.intraday_pattern_live_forward_min_quality_score),
            "recommended_min_stability_score": _MIN_STABILITY_FLOOR,
            "recommended_min_sample_quality_score": _MIN_SAMPLE_QUALITY_FLOOR,
            "recommended_min_reliability_score": _MIN_RELIABILITY_FLOOR,
            "recommended_top_n": int(settings.intraday_pattern_live_forward_top_n),
            "recommended_max_open_trades": int(settings.intraday_pattern_live_forward_max_open_trades),
            "notes": {},
        }

    @staticmethod
    def latest_profile(settings: BetaSettings, *, require_active: bool = False) -> dict[str, object] | None:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_parameter_learning_enabled:
            return None
        with BetaContext.read_session() as sess:
            return BetaIntradayPatternParameterLearningService.latest_profile_in_session(
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
        if not settings.intraday_pattern_parameter_learning_enabled:
            return None
        row = sess.scalar(
            select(BetaIntradayPatternPolicyProfile)
            .order_by(desc(BetaIntradayPatternPolicyProfile.created_at), desc(BetaIntradayPatternPolicyProfile.id))
            .limit(1)
        )
        if row is None:
            return None
        profile = BetaIntradayPatternParameterLearningService._profile_to_dict(row, settings=settings)
        if require_active and not bool(profile.get("active_for_runtime")):
            return None
        return profile

    @staticmethod
    def resolve_policy(settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternParameterLearningService.latest_profile(settings, require_active=True)
        return learned or BetaIntradayPatternParameterLearningService.static_policy_snapshot(settings)

    @staticmethod
    def resolve_policy_in_session(sess, settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternParameterLearningService.latest_profile_in_session(
            sess,
            settings,
            require_active=True,
        )
        return learned or BetaIntradayPatternParameterLearningService.static_policy_snapshot(settings)

    @staticmethod
    def learn_policy_profile(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_parameter_learning_enabled:
            return {"profile_created": False, "reason": "disabled"}

        now = _utcnow()
        window_days = max(7, int(settings.intraday_pattern_parameter_learning_window_days))
        cutoff = now - timedelta(days=window_days)

        with BetaContext.write_session() as sess:
            latest_run = sess.scalar(
                select(BetaIntradayPatternDiscoveryRun)
                .order_by(desc(BetaIntradayPatternDiscoveryRun.created_at), desc(BetaIntradayPatternDiscoveryRun.id))
                .limit(1)
            )
            evidence_rows = BetaIntradayPatternParameterLearningService._trade_evidence_rows(
                sess,
                cutoff=cutoff,
            )
            profile_payload = None
            if evidence_rows:
                profile_payload = BetaIntradayPatternParameterLearningService._outcome_profile_payload(
                    evidence_rows=evidence_rows,
                    settings=settings,
                    latest_run=latest_run,
                    window_days=window_days,
                )
            elif latest_run is not None:
                profile_payload = BetaIntradayPatternParameterLearningService._candidate_fallback_payload(
                    sess,
                    settings=settings,
                    latest_run=latest_run,
                    window_days=window_days,
                )
            if profile_payload is None:
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "reason": "no_evidence",
                }

            latest_profile = sess.scalar(
                select(BetaIntradayPatternPolicyProfile)
                .order_by(desc(BetaIntradayPatternPolicyProfile.created_at), desc(BetaIntradayPatternPolicyProfile.id))
                .limit(1)
            )
            if latest_profile is not None and BetaIntradayPatternParameterLearningService._matches_latest(
                latest_profile,
                profile_payload,
            ):
                existing = BetaIntradayPatternParameterLearningService._profile_to_dict(
                    latest_profile,
                    settings=settings,
                )
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "unchanged": True,
                    "reason": "unchanged_intraday_pattern_policy_inputs",
                    "profile_id": latest_profile.id,
                    "active_for_runtime": existing.get("active_for_runtime"),
                    "source_mode": existing.get("source_mode"),
                    "input_fingerprint": str((existing.get("notes") or {}).get("input_fingerprint") or ""),
                    "input_summary": dict((existing.get("notes") or {}).get("input_summary") or {}),
                }

            profile = BetaIntradayPatternPolicyProfile(
                profile_code=now.strftime("%Y%m%d%H%M%S%f"),
                discovery_run_id=profile_payload.get("discovery_run_id"),
                source_mode=str(profile_payload["source_mode"]),
                evaluation_window_days=int(profile_payload["evaluation_window_days"]),
                trade_count=int(profile_payload["trade_count"]),
                live_forward_trade_count=int(profile_payload["live_forward_trade_count"]),
                historical_backfill_trade_count=int(profile_payload["historical_backfill_trade_count"]),
                winner_count=int(profile_payload["winner_count"]),
                distinct_pattern_count=int(profile_payload["distinct_pattern_count"]),
                avg_realized_post_cost_return_pct=profile_payload.get("avg_realized_post_cost_return_pct"),
                win_rate_pct=profile_payload.get("win_rate_pct"),
                confidence_score=float(profile_payload["confidence_score"]),
                recommended_min_quality_score=profile_payload.get("recommended_min_quality_score"),
                recommended_min_stability_score=profile_payload.get("recommended_min_stability_score"),
                recommended_min_sample_quality_score=profile_payload.get("recommended_min_sample_quality_score"),
                recommended_min_reliability_score=profile_payload.get("recommended_min_reliability_score"),
                recommended_top_n=int(profile_payload["recommended_top_n"]),
                recommended_max_open_trades=int(profile_payload["recommended_max_open_trades"]),
                notes_json=json.dumps(profile_payload.get("notes") or {}, sort_keys=True),
            )
            sess.add(profile)
            sess.flush()
            stored = BetaIntradayPatternParameterLearningService._profile_to_dict(profile, settings=settings)
            return {
                "profile_created": True,
                "profile_id": profile.id,
                "active_for_runtime": stored.get("active_for_runtime"),
                "source_mode": stored.get("source_mode"),
                "recommended_top_n": stored.get("recommended_top_n"),
                "recommended_max_open_trades": stored.get("recommended_max_open_trades"),
                "recommended_min_quality_score": stored.get("recommended_min_quality_score"),
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
                    "weight": weight,
                    "quality_score": _safe_float(notes.get("pattern_quality_score")),
                    "stability_score": _safe_float(notes.get("pattern_stability_score")),
                    "sample_quality_score": _safe_float(notes.get("pattern_sample_quality_score")),
                    "reliability_score": _safe_float(notes.get("pattern_reliability_score")),
                    "best_horizon_edge_pct": _safe_float(
                        notes.get("pattern_best_horizon_post_cost_edge_pct")
                        or notes.get("pattern_post_cost_edge_15m_pct")
                    ),
                    "exit_observed_at": trade.exit_observed_at.isoformat() if trade.exit_observed_at else None,
                }
            )
        return rows

    @staticmethod
    def _outcome_profile_payload(
        *,
        evidence_rows: list[dict[str, object]],
        settings: BetaSettings,
        latest_run: BetaIntradayPatternDiscoveryRun | None,
        window_days: int,
    ) -> dict[str, object]:
        winners = [row for row in evidence_rows if float(row["realized_post_cost_return_pct"]) > 0.0]
        losers = [row for row in evidence_rows if float(row["realized_post_cost_return_pct"]) <= 0.0]
        trade_count = len(evidence_rows)
        live_count = sum(1 for row in evidence_rows if row["simulation_source"] == "LIVE_FORWARD")
        historical_count = sum(1 for row in evidence_rows if row["simulation_source"] == "HISTORICAL_BACKFILL")
        distinct_pattern_count = len({str(row["pattern_hash"]) for row in evidence_rows if row.get("pattern_hash")})
        distinct_winner_patterns = len({str(row["pattern_hash"]) for row in winners if row.get("pattern_hash")})
        avg_realized_return = _weighted_mean(evidence_rows, "realized_post_cost_return_pct")
        win_rate_decimal = _weighted_mean(
            [{**row, "is_win": 1.0 if float(row["realized_post_cost_return_pct"]) > 0.0 else 0.0} for row in evidence_rows],
            "is_win",
        ) or 0.0

        min_quality = BetaIntradayPatternParameterLearningService._recommended_floor(
            winners=winners,
            losers=losers,
            key="quality_score",
            base=float(settings.intraday_pattern_live_forward_min_quality_score),
            floor=max(0.15, float(settings.intraday_pattern_live_forward_min_quality_score) * 0.85),
            ceiling=0.90,
        )
        min_stability = BetaIntradayPatternParameterLearningService._recommended_floor(
            winners=winners,
            losers=losers,
            key="stability_score",
            base=_MIN_STABILITY_FLOOR,
            floor=0.05,
            ceiling=0.95,
        )
        min_sample_quality = BetaIntradayPatternParameterLearningService._recommended_floor(
            winners=winners,
            losers=losers,
            key="sample_quality_score",
            base=_MIN_SAMPLE_QUALITY_FLOOR,
            floor=0.10,
            ceiling=0.95,
        )
        min_reliability = BetaIntradayPatternParameterLearningService._recommended_floor(
            winners=winners,
            losers=losers,
            key="reliability_score",
            base=_MIN_RELIABILITY_FLOOR,
            floor=0.02,
            ceiling=0.95,
        )

        confidence_score = BetaIntradayPatternParameterLearningService._confidence_score(
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
                    "quality_score": _safe_float(row.get("quality_score")),
                    "stability_score": _safe_float(row.get("stability_score")),
                    "sample_quality_score": _safe_float(row.get("sample_quality_score")),
                    "reliability_score": _safe_float(row.get("reliability_score")),
                    "best_horizon_edge_pct": _safe_float(row.get("best_horizon_edge_pct")),
                    "exit_observed_at": str(row.get("exit_observed_at") or ""),
                }
                for row in evidence_rows
            ],
            key=lambda row: (
                row["pattern_hash"],
                row["simulation_source"],
                row["exit_observed_at"],
                row["realized_post_cost_return_pct"] or 0.0,
            ),
        )
        input_summary = {
            "window_days": window_days,
            "evidence_mode": "outcome",
            "trade_count": trade_count,
            "live_forward_trade_count": live_count,
            "historical_backfill_trade_count": historical_count,
            "distinct_pattern_count": distinct_pattern_count,
            "first_exit_observed_at": normalized_rows[0]["exit_observed_at"] if normalized_rows else None,
            "last_exit_observed_at": normalized_rows[-1]["exit_observed_at"] if normalized_rows else None,
        }

        recommended_top_n = 1
        if (
            confidence_score >= 0.25
            and trade_count >= int(settings.intraday_pattern_parameter_learning_min_closed_trades)
            and win_rate_decimal >= 0.55
            and (avg_realized_return or 0.0) > 0.02
            and distinct_winner_patterns >= 2
        ):
            recommended_top_n = 2
        if (
            confidence_score >= 0.45
            and trade_count >= int(settings.intraday_pattern_parameter_learning_min_closed_trades) * 2
            and win_rate_decimal >= 0.60
            and (avg_realized_return or 0.0) > 0.08
            and distinct_winner_patterns >= 3
        ):
            recommended_top_n = 3

        avg_winner = _mean([float(row["realized_post_cost_return_pct"]) for row in winners]) or 0.0
        avg_loser = abs(_mean([float(row["realized_post_cost_return_pct"]) for row in losers]) or 0.0)
        recommended_max_open = 1
        if (
            recommended_top_n >= 2
            and confidence_score >= 0.35
            and avg_winner >= max(avg_loser, 0.03)
        ):
            recommended_max_open = 2

        return {
            "discovery_run_id": latest_run.id if latest_run is not None else None,
            "source_mode": "OUTCOME_DRIVEN" if live_count >= int(settings.intraday_pattern_parameter_learning_min_closed_trades) else "OUTCOME_MIXED",
            "evaluation_window_days": window_days,
            "trade_count": trade_count,
            "live_forward_trade_count": live_count,
            "historical_backfill_trade_count": historical_count,
            "winner_count": len(winners),
            "distinct_pattern_count": distinct_pattern_count,
            "avg_realized_post_cost_return_pct": avg_realized_return,
            "win_rate_pct": round(win_rate_decimal * 100.0, 4),
            "confidence_score": confidence_score,
            "recommended_min_quality_score": min_quality,
            "recommended_min_stability_score": min_stability,
            "recommended_min_sample_quality_score": min_sample_quality,
            "recommended_min_reliability_score": min_reliability,
            "recommended_top_n": recommended_top_n,
            "recommended_max_open_trades": recommended_max_open,
            "notes": {
                "weighted_win_rate_decimal": round(win_rate_decimal, 6),
                "avg_winner_return_pct": avg_winner,
                "avg_loser_return_pct": -avg_loser if losers else None,
                "distinct_winner_patterns": distinct_winner_patterns,
                "input_summary": input_summary,
                "input_fingerprint": _fingerprint_payload(
                    {
                        "window_days": window_days,
                        "evidence_mode": "outcome",
                        "rows": normalized_rows,
                    }
                ),
                "evidence_mix": {
                    "live_forward_weight": _LIVE_FORWARD_WEIGHT,
                    "historical_backfill_weight": _HISTORICAL_BACKFILL_WEIGHT,
                },
            },
        }

    @staticmethod
    def _candidate_fallback_payload(
        sess,
        *,
        settings: BetaSettings,
        latest_run: BetaIntradayPatternDiscoveryRun,
        window_days: int,
    ) -> dict[str, object] | None:
        candidates = list(
            sess.scalars(
                select(BetaIntradayPatternCandidate)
                .where(
                    BetaIntradayPatternCandidate.discovery_run_id == latest_run.id,
                    BetaIntradayPatternCandidate.status == "SCREENED_IN",
                )
                .order_by(
                    desc(BetaIntradayPatternCandidate.reliability_score),
                    desc(BetaIntradayPatternCandidate.post_cost_edge_15m_pct),
                    desc(BetaIntradayPatternCandidate.sample_size),
                )
                .limit(80)
            ).all()
        )
        candidates = [
            row
            for row in candidates
            if str(row.action_bias or "").strip().upper() in {"LONG", "SHORT"}
        ]
        if not candidates:
            return None
        family_count = len({f"{row.anchor_family_code}:{row.anchor_code}" for row in candidates})
        screened_in_count = len(candidates)
        normalized_candidates = sorted(
            [
                {
                    "pattern_hash": str(row.pattern_hash or ""),
                    "family_code": f"{row.anchor_family_code}:{row.anchor_code}".strip().upper(),
                    "action_bias": str(row.action_bias or "").strip().upper(),
                    "sample_size": int(row.sample_size or 0),
                    "matched_instruments": int(row.matched_instruments or 0),
                    "reliability_score": _safe_float(row.reliability_score),
                    "post_cost_edge_15m_pct": _safe_float(row.post_cost_edge_15m_pct),
                }
                for row in candidates
            ],
            key=lambda row: (
                row["family_code"],
                row["pattern_hash"],
                row["action_bias"],
            ),
        )
        recommended_top_n = 1
        if screened_in_count >= 10 and family_count >= 3:
            recommended_top_n = 2
        if screened_in_count >= 20 and family_count >= 5:
            recommended_top_n = 3
        confidence_score = min(
            0.22,
            0.08 + (min(1.0, screened_in_count / 20.0) * 0.08) + (min(1.0, family_count / 5.0) * 0.06),
        )
        return {
            "discovery_run_id": latest_run.id,
            "source_mode": "DISCOVERY_FALLBACK",
            "evaluation_window_days": window_days,
            "trade_count": 0,
            "live_forward_trade_count": 0,
            "historical_backfill_trade_count": 0,
            "winner_count": 0,
            "distinct_pattern_count": screened_in_count,
            "avg_realized_post_cost_return_pct": None,
            "win_rate_pct": None,
            "confidence_score": round(confidence_score, 6),
            "recommended_min_quality_score": float(settings.intraday_pattern_live_forward_min_quality_score),
            "recommended_min_stability_score": _MIN_STABILITY_FLOOR,
            "recommended_min_sample_quality_score": _MIN_SAMPLE_QUALITY_FLOOR,
            "recommended_min_reliability_score": _MIN_RELIABILITY_FLOOR,
            "recommended_top_n": recommended_top_n,
            "recommended_max_open_trades": 1,
            "notes": {
                "screened_in_candidates": screened_in_count,
                "family_count": family_count,
                "fallback_reason": "no_closed_pattern_trade_evidence",
                "input_summary": {
                    "window_days": window_days,
                    "evidence_mode": "candidate_fallback",
                    "screened_in_candidates": screened_in_count,
                    "family_count": family_count,
                },
                "input_fingerprint": _fingerprint_payload(
                    {
                        "window_days": window_days,
                        "evidence_mode": "candidate_fallback",
                        "candidates": normalized_candidates,
                    }
                ),
            },
        }

    @staticmethod
    def _recommended_floor(
        *,
        winners: list[dict[str, object]],
        losers: list[dict[str, object]],
        key: str,
        base: float,
        floor: float,
        ceiling: float,
    ) -> float:
        winner_values = [
            float(value)
            for value in (_safe_float(row.get(key)) for row in winners)
            if value is not None
        ]
        loser_values = [
            float(value)
            for value in (_safe_float(row.get(key)) for row in losers)
            if value is not None
        ]
        candidate = max(floor, min(ceiling, float(base)))
        if winner_values:
            winner_anchor = _percentile(winner_values, 0.20)
            if winner_anchor is not None:
                candidate = max(floor, min(ceiling, winner_anchor))
        if loser_values:
            loser_anchor = _percentile(loser_values, 0.60)
            if loser_anchor is not None:
                if winner_values:
                    candidate = max(floor, min(ceiling, (candidate + float(loser_anchor)) / 2.0))
                else:
                    candidate = max(candidate, min(ceiling, float(loser_anchor)))
        return round(candidate, 4)

    @staticmethod
    def _confidence_score(
        *,
        trade_count: int,
        live_count: int,
        distinct_pattern_count: int,
        settings: BetaSettings,
    ) -> float:
        minimum = max(1, int(settings.intraday_pattern_parameter_learning_min_closed_trades))
        support_factor = min(1.0, float(trade_count) / float(minimum))
        live_factor = min(1.0, float(live_count) / max(1.0, float(minimum) * 0.5))
        diversity_factor = min(1.0, float(distinct_pattern_count) / 3.0)
        return round(
            max(
                0.0,
                min(
                    1.0,
                    support_factor * ((0.55 + (0.25 * live_factor)) + (0.20 * diversity_factor)),
                ),
            ),
            6,
        )

    @staticmethod
    def _matches_latest(
        latest_profile: BetaIntradayPatternPolicyProfile,
        payload: dict[str, object],
    ) -> bool:
        latest_notes = _json_object(latest_profile.notes_json)
        payload_notes = payload.get("notes") or {}
        return (
            str(latest_profile.source_mode or "") == str(payload["source_mode"])
            and int(latest_profile.recommended_top_n or 0) == int(payload["recommended_top_n"])
            and int(latest_profile.recommended_max_open_trades or 0) == int(payload["recommended_max_open_trades"])
            and round(float(latest_profile.confidence_score or 0.0), 4) == round(float(payload["confidence_score"] or 0.0), 4)
            and round(float(latest_profile.recommended_min_quality_score or 0.0), 4)
            == round(float(payload.get("recommended_min_quality_score") or 0.0), 4)
            and round(float(latest_profile.recommended_min_stability_score or 0.0), 4)
            == round(float(payload.get("recommended_min_stability_score") or 0.0), 4)
            and round(float(latest_profile.recommended_min_sample_quality_score or 0.0), 4)
            == round(float(payload.get("recommended_min_sample_quality_score") or 0.0), 4)
            and round(float(latest_profile.recommended_min_reliability_score or 0.0), 4)
            == round(float(payload.get("recommended_min_reliability_score") or 0.0), 4)
            and latest_notes == payload_notes
        )

    @staticmethod
    def _profile_to_dict(
        row: BetaIntradayPatternPolicyProfile,
        *,
        settings: BetaSettings,
    ) -> dict[str, object]:
        notes = _json_object(row.notes_json)
        confidence_score = float(row.confidence_score or 0.0)
        active_for_runtime = bool(
            settings.intraday_pattern_parameter_learning_enabled
            and (
                confidence_score >= 0.25
                or (
                    int(row.trade_count or 0) >= int(settings.intraday_pattern_parameter_learning_min_closed_trades)
                    and confidence_score >= 0.18
                )
            )
        )
        source_mode = str(row.source_mode or "OUTCOME_DRIVEN")
        source_label = {
            "OUTCOME_DRIVEN": "Outcome-driven",
            "OUTCOME_MIXED": "Mixed evidence",
            "DISCOVERY_FALLBACK": "Discovery fallback",
        }.get(source_mode, source_mode.replace("_", " ").title())
        return {
            "available": True,
            "id": row.id,
            "profile_code": row.profile_code,
            "discovery_run_id": row.discovery_run_id,
            "source_mode": source_mode,
            "source_label": source_label,
            "active_for_runtime": active_for_runtime,
            "evaluation_window_days": int(row.evaluation_window_days or 0),
            "trade_count": int(row.trade_count or 0),
            "live_forward_trade_count": int(row.live_forward_trade_count or 0),
            "historical_backfill_trade_count": int(row.historical_backfill_trade_count or 0),
            "winner_count": int(row.winner_count or 0),
            "distinct_pattern_count": int(row.distinct_pattern_count or 0),
            "avg_realized_post_cost_return_pct": _safe_float(row.avg_realized_post_cost_return_pct),
            "win_rate_pct": _safe_float(row.win_rate_pct),
            "confidence_score": confidence_score,
            "recommended_min_quality_score": _safe_float(row.recommended_min_quality_score),
            "recommended_min_stability_score": _safe_float(row.recommended_min_stability_score),
            "recommended_min_sample_quality_score": _safe_float(row.recommended_min_sample_quality_score),
            "recommended_min_reliability_score": _safe_float(row.recommended_min_reliability_score),
            "recommended_top_n": int(row.recommended_top_n or 0),
            "recommended_max_open_trades": int(row.recommended_max_open_trades or 0),
            "notes": notes,
            "created_at": row.created_at,
        }
