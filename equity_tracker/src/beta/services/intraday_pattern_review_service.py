"""Read-side scoring, rollups, and live-forward approval for intraday pattern exploration."""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaIntradayFeatureObservation,
    BetaIntradayPatternCandidate,
    BetaIntradayPatternDiscoveryRun,
)
from ..settings import BetaSettings
from .intraday_pattern_exploration_service import BetaIntradayPatternExplorationService
from .intraday_pattern_parameter_learning_service import BetaIntradayPatternParameterLearningService
from .intraday_pattern_threshold_learning_service import BetaIntradayPatternThresholdLearningService


def _json_array(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [str(value).strip() for value in payload if str(value).strip()]


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


class BetaIntradayPatternReviewService:
    """Build leaderboard and approval views over the latest intraday pattern run."""

    @staticmethod
    def latest_summary(
        settings: BetaSettings,
        *,
        leaderboard_limit: int = 12,
        family_limit: int = 10,
    ) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return BetaIntradayPatternReviewService._empty_summary()
        with BetaContext.read_session() as sess:
            return BetaIntradayPatternReviewService.latest_summary_in_session(
                sess,
                settings,
                leaderboard_limit=leaderboard_limit,
                family_limit=family_limit,
            )

    @staticmethod
    def latest_summary_in_session(
        sess,
        settings: BetaSettings,
        *,
        leaderboard_limit: int = 12,
        family_limit: int = 10,
    ) -> dict[str, object]:
        latest_run = sess.scalar(
            select(BetaIntradayPatternDiscoveryRun)
            .order_by(desc(BetaIntradayPatternDiscoveryRun.created_at), desc(BetaIntradayPatternDiscoveryRun.id))
            .limit(1)
        )
        if latest_run is None:
            return BetaIntradayPatternReviewService._empty_summary()

        raw_candidates = list(
            sess.scalars(
                select(BetaIntradayPatternCandidate)
                .where(BetaIntradayPatternCandidate.discovery_run_id == latest_run.id)
                .order_by(
                    desc(BetaIntradayPatternCandidate.reliability_score),
                    desc(BetaIntradayPatternCandidate.post_cost_edge_15m_pct),
                    desc(BetaIntradayPatternCandidate.sample_size),
                )
            ).all()
        )
        enriched_candidates = [
            BetaIntradayPatternReviewService._enrich_candidate(row)
            for row in raw_candidates
        ]
        screened_in = [
            row
            for row in enriched_candidates
            if row["status"] == "SCREENED_IN" and row["action_bias"] in {"LONG", "SHORT"}
        ]
        sorted_candidates = sorted(
            screened_in or enriched_candidates,
            key=BetaIntradayPatternReviewService._candidate_sort_key,
            reverse=True,
        )
        adaptive_policy = BetaIntradayPatternParameterLearningService.resolve_policy_in_session(
            sess,
            settings,
        )
        threshold_profile = BetaIntradayPatternThresholdLearningService.resolve_profile_in_session(
            sess,
            settings,
        )

        approved_patterns = BetaIntradayPatternReviewService._approved_patterns(
            sorted_candidates,
            settings,
            adaptive_policy=adaptive_policy,
        )
        approved_hashes = {str(row["pattern_hash"]) for row in approved_patterns}
        manual_hashes = BetaIntradayPatternReviewService._manual_hashes(settings)
        for row in sorted_candidates:
            if str(row["pattern_hash"]) in approved_hashes:
                row["approval_status"] = "APPROVED"
            elif row["status"] != "SCREENED_IN":
                row["approval_status"] = "REJECTED"
            elif manual_hashes:
                row["approval_status"] = "WAITLISTED"
            else:
                row["approval_status"] = "STAGED"

        family_rollups = BetaIntradayPatternReviewService._family_rollups(
            sorted_candidates,
            approved_hashes=approved_hashes,
            limit=family_limit,
        )
        latest_run_payload = {
            "id": latest_run.id,
            "run_code": latest_run.run_code,
            "status": latest_run.status,
            "lookback_days": latest_run.lookback_days,
            "observations_considered": latest_run.observations_considered,
            "labeled_observations": latest_run.labeled_observations,
            "patterns_generated": latest_run.patterns_generated,
            "patterns_screened_in": latest_run.patterns_screened_in,
            "window_start": latest_run.window_start,
            "window_end": latest_run.window_end,
            "created_at": latest_run.created_at,
            "notes": BetaIntradayPatternReviewService._notes_json(latest_run.notes_json),
        }
        if manual_hashes:
            approval_mode = "MANUAL"
        elif adaptive_policy and str(adaptive_policy.get("source_mode") or "STATIC_SETTINGS") != "STATIC_SETTINGS":
            approval_mode = "ADAPTIVE_AUTO_TOP_N"
        else:
            approval_mode = "AUTO_TOP_N"
        return {
            "available": True,
            "latest_run": latest_run_payload,
            "leaderboard": sorted_candidates[: max(1, leaderboard_limit)],
            "family_rollups": family_rollups,
            "approved_patterns": approved_patterns,
            "adaptive_policy": adaptive_policy,
            "threshold_profile": threshold_profile,
            "approval_mode": approval_mode,
            "counts": {
                "candidate_count": len(enriched_candidates),
                "screened_in_count": len(screened_in),
                "approved_count": len(approved_patterns),
                "family_count": len({str(row["family_code"]) for row in sorted_candidates}),
            },
        }

    @staticmethod
    def approved_live_forward_candidates_in_session(
        sess,
        settings: BetaSettings,
    ) -> list[dict[str, object]]:
        summary = BetaIntradayPatternReviewService.latest_summary_in_session(
            sess,
            settings,
            leaderboard_limit=max(8, int(settings.intraday_pattern_live_forward_top_n) * 2),
            family_limit=max(6, int(settings.intraday_pattern_live_forward_top_n) * 2),
        )
        return list(summary.get("approved_patterns") or [])

    @staticmethod
    def best_live_forward_match(
        observation: BetaIntradayFeatureObservation,
        approved_patterns: list[dict[str, object]],
        *,
        settings: BetaSettings,
        threshold_profile: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(observation.session_state or "").strip().upper() != "REGULAR_OPEN":
            return None
        if not approved_patterns:
            return None
        approved_by_hash = {
            str(row["pattern_hash"]): row
            for row in approved_patterns
            if str(row.get("pattern_hash") or "").strip()
        }
        if not approved_by_hash:
            return None

        specs = BetaIntradayPatternExplorationService.pattern_specs_for_observation(
            observation,
            settings=settings,
            threshold_profile=threshold_profile,
        )
        matches: list[dict[str, object]] = []
        for spec in specs:
            pattern_hash = str(spec.get("pattern_hash") or "").strip()
            approved = approved_by_hash.get(pattern_hash)
            if approved is None:
                continue
            matches.append(
                {
                    **approved,
                    "matched_pattern_hash": pattern_hash,
                    "matched_pattern_code": spec.get("pattern_code"),
                    "matched_context_depth": len(spec.get("context_tags") or []),
                    "matched_context_tags": list(spec.get("context_tags") or []),
                }
            )
        if not matches:
            return None
        return max(
            matches,
            key=lambda row: (
                int(row.get("matched_context_depth") or 0),
                float(row.get("quality_score") or 0.0),
                float(row.get("stability_score") or 0.0),
                float(row.get("post_cost_edge_15m_pct") or 0.0),
                int(row.get("sample_size") or 0),
            ),
        )

    @staticmethod
    def _empty_summary() -> dict[str, object]:
        return {
            "available": False,
            "latest_run": None,
            "leaderboard": [],
            "family_rollups": [],
            "approved_patterns": [],
            "threshold_profile": None,
            "approval_mode": "AUTO_TOP_N",
            "counts": {
                "candidate_count": 0,
                "screened_in_count": 0,
                "approved_count": 0,
                "family_count": 0,
            },
        }

    @staticmethod
    def _notes_json(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _manual_hashes(settings: BetaSettings) -> set[str]:
        return {
            str(value).strip()
            for value in (settings.intraday_pattern_live_forward_manual_pattern_hashes or [])
            if str(value).strip()
        }

    @staticmethod
    def _aligned_value(value: float | None, action_bias: str) -> float | None:
        if value is None:
            return None
        normalized_bias = str(action_bias or "").strip().upper()
        return -value if normalized_bias == "SHORT" else value

    @staticmethod
    def _sample_quality_score(sample_size: int, matched_instruments: int) -> float:
        support_component = min(1.0, float(sample_size) / 40.0)
        breadth_component = min(1.0, float(matched_instruments) / 4.0)
        return round((support_component * 0.7) + (breadth_component * 0.3), 6)

    @staticmethod
    def _stability_score(
        *,
        aligned_reference_return_pct: float | None,
        aligned_reference_median_return_pct: float | None,
        win_rate_pct: float | None,
        mean_max_adverse_move_pct: float | None,
        horizon_stability_score: float | None,
    ) -> float:
        mean_edge = max(0.0, float(aligned_reference_return_pct or 0.0))
        median_edge = max(0.0, float(aligned_reference_median_return_pct or 0.0))
        win_component = min(1.0, max(0.0, float(win_rate_pct or 0.0) - 50.0) / 15.0)
        consistency_denominator = max(abs(mean_edge), 0.10)
        consistency_component = max(0.0, 1.0 - (abs(mean_edge - median_edge) / consistency_denominator))
        adverse = abs(float(mean_max_adverse_move_pct or 0.0))
        drawdown_component = min(1.0, mean_edge / max(adverse, 0.10)) if mean_edge > 0.0 else 0.0
        horizon_component = min(1.0, max(0.0, float(horizon_stability_score or 0.0)) / 0.45)
        return round(
            max(
                0.0,
                min(
                    1.0,
                    (win_component * 0.30)
                    + (consistency_component * 0.20)
                    + (drawdown_component * 0.20)
                    + (horizon_component * 0.30),
                ),
            ),
            6,
        )

    @staticmethod
    def _quality_score(
        *,
        post_cost_edge_pct: float | None,
        stability_score: float,
        sample_quality_score: float,
    ) -> float:
        edge_component = min(1.0, max(0.0, float(post_cost_edge_pct or 0.0)) / 0.35)
        return round(
            max(
                0.0,
                min(
                    1.0,
                    (edge_component * 0.40) + (stability_score * 0.30) + (sample_quality_score * 0.30),
                ),
            ),
            6,
        )

    @staticmethod
    def _enrich_candidate(row: BetaIntradayPatternCandidate) -> dict[str, object]:
        action_bias = str(row.action_bias or "NEUTRAL").strip().upper()
        notes = BetaIntradayPatternReviewService._notes_json(row.notes_json)
        best_horizon_minutes = _safe_float(notes.get("best_horizon_minutes"))
        best_mean_return = _safe_float(notes.get("best_horizon_return_pct"))
        best_median_return = _safe_float(notes.get("best_horizon_median_return_pct"))
        best_post_cost_edge = _safe_float(notes.get("best_horizon_post_cost_edge_pct"))
        best_win_rate = _safe_float(notes.get("best_horizon_win_rate_pct"))
        horizon_stability_score = _safe_float(notes.get("horizon_stability_score")) or 0.0
        aligned_mean_15 = BetaIntradayPatternReviewService._aligned_value(
            _safe_float(row.average_return_15m_pct),
            action_bias,
        )
        aligned_mean_30 = BetaIntradayPatternReviewService._aligned_value(
            _safe_float(row.average_return_30m_pct),
            action_bias,
        )
        aligned_median_15 = BetaIntradayPatternReviewService._aligned_value(
            _safe_float(row.median_return_15m_pct),
            action_bias,
        )
        aligned_best_mean = BetaIntradayPatternReviewService._aligned_value(best_mean_return, action_bias)
        aligned_best_median = BetaIntradayPatternReviewService._aligned_value(best_median_return, action_bias)
        sample_size = int(row.sample_size or 0)
        matched_instruments = int(row.matched_instruments or 0)
        sample_quality_score = BetaIntradayPatternReviewService._sample_quality_score(
            sample_size,
            matched_instruments,
        )
        stability_score = BetaIntradayPatternReviewService._stability_score(
            aligned_reference_return_pct=(aligned_best_mean if aligned_best_mean is not None else aligned_mean_15),
            aligned_reference_median_return_pct=(aligned_best_median if aligned_best_median is not None else aligned_median_15),
            win_rate_pct=(best_win_rate if best_win_rate is not None else _safe_float(row.win_rate_pct)),
            mean_max_adverse_move_pct=_safe_float(row.mean_max_adverse_move_pct),
            horizon_stability_score=horizon_stability_score,
        )
        quality_score = BetaIntradayPatternReviewService._quality_score(
            post_cost_edge_pct=(best_post_cost_edge if best_post_cost_edge is not None else _safe_float(row.post_cost_edge_15m_pct)),
            stability_score=stability_score,
            sample_quality_score=sample_quality_score,
        )
        family_code = f"{row.anchor_family_code}:{row.anchor_code}"
        context_tags = _json_array(row.context_tags_json)
        risk_adjusted_edge = None
        adverse = abs(float(row.mean_max_adverse_move_pct or 0.0))
        edge = float(row.post_cost_edge_15m_pct or 0.0)
        if edge > 0.0:
            risk_adjusted_edge = round(edge / max(adverse, 0.10), 6)
        return {
            "id": row.id,
            "discovery_run_id": row.discovery_run_id,
            "pattern_hash": row.pattern_hash,
            "pattern_code": row.pattern_code,
            "anchor_family_code": row.anchor_family_code,
            "anchor_code": row.anchor_code,
            "family_code": family_code,
            "symbol": row.symbol,
            "session_segment": row.session_segment,
            "context_tags": context_tags,
            "context_depth": len(context_tags),
            "action_bias": action_bias,
            "sample_size": sample_size,
            "matched_instruments": matched_instruments,
            "average_return_15m_pct": _safe_float(row.average_return_15m_pct),
            "average_return_30m_pct": _safe_float(row.average_return_30m_pct),
            "median_return_15m_pct": _safe_float(row.median_return_15m_pct),
            "aligned_average_return_15m_pct": aligned_mean_15,
            "aligned_average_return_30m_pct": aligned_mean_30,
            "aligned_median_return_15m_pct": aligned_median_15,
            "best_horizon_minutes": int(best_horizon_minutes) if best_horizon_minutes is not None else None,
            "best_horizon_label": (
                f"{int(best_horizon_minutes)}m"
                if best_horizon_minutes is not None
                else None
            ),
            "best_horizon_return_pct": best_mean_return,
            "best_horizon_median_return_pct": best_median_return,
            "aligned_best_horizon_return_pct": aligned_best_mean,
            "aligned_best_horizon_median_return_pct": aligned_best_median,
            "best_horizon_post_cost_edge_pct": best_post_cost_edge,
            "best_horizon_win_rate_pct": best_win_rate,
            "horizon_stability_score": horizon_stability_score,
            "win_rate_pct": _safe_float(row.win_rate_pct),
            "win_rate_decimal": (
                round(float(row.win_rate_pct) / 100.0, 6)
                if row.win_rate_pct is not None
                else None
            ),
            "mean_max_adverse_move_pct": _safe_float(row.mean_max_adverse_move_pct),
            "mean_max_favorable_move_pct": _safe_float(row.mean_max_favorable_move_pct),
            "post_cost_edge_15m_pct": _safe_float(row.post_cost_edge_15m_pct),
            "reliability_score": _safe_float(row.reliability_score),
            "sample_quality_score": sample_quality_score,
            "stability_score": stability_score,
            "quality_score": quality_score,
            "risk_adjusted_edge_score": risk_adjusted_edge,
            "status": row.status,
            "notes": notes,
            "created_at": row.created_at,
        }

    @staticmethod
    def _candidate_sort_key(row: dict[str, object]) -> tuple[float, ...]:
        return (
            float(row.get("quality_score") or 0.0),
            float(row.get("stability_score") or 0.0),
            float(row.get("best_horizon_post_cost_edge_pct") or row.get("post_cost_edge_15m_pct") or 0.0),
            float(row.get("horizon_stability_score") or 0.0),
            float(row.get("reliability_score") or 0.0),
            float(row.get("sample_quality_score") or 0.0),
            float(row.get("sample_size") or 0.0),
            float(row.get("context_depth") or 0.0),
        )

    @staticmethod
    def _approved_patterns(
        sorted_candidates: list[dict[str, object]],
        settings: BetaSettings,
        *,
        adaptive_policy: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        if not settings.intraday_pattern_live_forward_enabled:
            return []
        policy = adaptive_policy or BetaIntradayPatternParameterLearningService.static_policy_snapshot(settings)
        manual_hashes = BetaIntradayPatternReviewService._manual_hashes(settings)
        quality_floor = float(policy.get("recommended_min_quality_score") or settings.intraday_pattern_live_forward_min_quality_score)
        stability_floor = float(policy.get("recommended_min_stability_score") or 0.0)
        sample_quality_floor = float(policy.get("recommended_min_sample_quality_score") or 0.0)
        reliability_floor = float(policy.get("recommended_min_reliability_score") or 0.0)
        eligible = [
            row
            for row in sorted_candidates
            if row["status"] == "SCREENED_IN"
            and row["action_bias"] in {"LONG", "SHORT"}
            and float(row.get("quality_score") or 0.0) >= quality_floor
            and float(row.get("stability_score") or 0.0) >= stability_floor
            and float(row.get("sample_quality_score") or 0.0) >= sample_quality_floor
            and float(row.get("reliability_score") or 0.0) >= reliability_floor
        ]
        if manual_hashes:
            approved = [row for row in eligible if str(row["pattern_hash"]) in manual_hashes]
            return approved[: max(1, int(policy.get("recommended_top_n") or settings.intraday_pattern_live_forward_top_n))]

        top_n = max(1, int(policy.get("recommended_top_n") or settings.intraday_pattern_live_forward_top_n))
        approved: list[dict[str, object]] = []
        seen_families: set[str] = set()
        for row in eligible:
            family_code = str(row.get("family_code") or "")
            if family_code in seen_families:
                continue
            approved.append(row)
            seen_families.add(family_code)
            if len(approved) >= top_n:
                return approved
        for row in eligible:
            if row in approved:
                continue
            approved.append(row)
            if len(approved) >= top_n:
                break
        return approved

    @staticmethod
    def _family_rollups(
        sorted_candidates: list[dict[str, object]],
        *,
        approved_hashes: set[str],
        limit: int,
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in sorted_candidates:
            grouped[str(row["family_code"])].append(row)

        rollups: list[dict[str, object]] = []
        for family_code, rows in grouped.items():
            best_row = max(rows, key=BetaIntradayPatternReviewService._candidate_sort_key)
            screened_in_count = sum(1 for row in rows if row["status"] == "SCREENED_IN")
            approved_count = sum(1 for row in rows if str(row["pattern_hash"]) in approved_hashes)
            average_quality = _mean([float(row["quality_score"] or 0.0) for row in rows]) or 0.0
            average_stability = _mean([float(row["stability_score"] or 0.0) for row in rows]) or 0.0
            average_edge = _mean(
                [
                    float(row.get("best_horizon_post_cost_edge_pct") or row.get("post_cost_edge_15m_pct") or 0.0)
                    for row in rows
                ]
            ) or 0.0
            average_adverse = _mean([abs(float(row["mean_max_adverse_move_pct"] or 0.0)) for row in rows]) or 0.0
            average_sample = _mean([float(row["sample_size"] or 0.0) for row in rows]) or 0.0
            verdict = "NOISY"
            if approved_count > 0 and average_quality >= 0.30:
                verdict = "WORKING"
            elif screened_in_count > 0 and average_quality >= 0.20:
                verdict = "PROMISING"
            rollups.append(
                {
                    "family_code": family_code,
                    "anchor_family_code": best_row["anchor_family_code"],
                    "anchor_code": best_row["anchor_code"],
                    "patterns_total": len(rows),
                    "screened_in_count": screened_in_count,
                    "approved_count": approved_count,
                    "average_quality_score": average_quality,
                    "average_stability_score": average_stability,
                    "average_post_cost_edge_15m_pct": average_edge,
                    "average_adverse_move_pct": average_adverse,
                    "average_sample_size": average_sample,
                    "best_pattern_code": best_row["pattern_code"],
                    "best_pattern_hash": best_row["pattern_hash"],
                    "best_action_bias": best_row["action_bias"],
                    "best_horizon_label": best_row.get("best_horizon_label"),
                    "best_quality_score": best_row["quality_score"],
                    "verdict": verdict,
                }
            )
        rollups.sort(
            key=lambda row: (
                int(row["approved_count"]),
                float(row["average_quality_score"]),
                float(row["average_post_cost_edge_15m_pct"]),
                float(row["average_sample_size"]),
            ),
            reverse=True,
        )
        return rollups[: max(1, limit)]
