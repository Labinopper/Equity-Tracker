"""Learn where the intraday explorer should spend its search budget."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, or_, select

from ..context import BetaContext
from ..db.models import (
    BetaIntradayPatternCandidate,
    BetaIntradayPatternDiscoveryRun,
    BetaIntradayPatternExplorationProfile,
    BetaIntradaySimulatedTrade,
)
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


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


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


def _context_prefixes(tags: list[str]) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        text = str(tag or "").strip().upper()
        if not text or ":" not in text:
            continue
        prefix = text.split(":", 1)[0].strip()
        if not prefix or prefix in seen:
            continue
        seen.add(prefix)
        prefixes.append(prefix)
    return prefixes


class BetaIntradayPatternExplorationLearningService:
    """Persist a learned exploration profile from recent pattern outcomes and candidates."""

    @staticmethod
    def static_exploration_snapshot(settings: BetaSettings) -> dict[str, object]:
        return {
            "available": True,
            "active_for_runtime": True,
            "source_mode": "STATIC_SETTINGS",
            "source_label": "Static exploration",
            "evaluation_window_days": int(
                getattr(settings, "intraday_pattern_exploration_learning_window_days", 30)
            ),
            "trade_count": 0,
            "live_forward_trade_count": 0,
            "historical_backfill_trade_count": 0,
            "candidate_count": 0,
            "distinct_family_count": 0,
            "confidence_score": 0.0,
            "recommended_max_context_depth": int(settings.intraday_pattern_max_context_depth),
            "recommended_max_patterns_per_observation": int(settings.intraday_pattern_max_patterns_per_observation),
            "family_scores": {},
            "family_depth_caps": {},
            "family_rankings": [],
            "notes": {},
        }

    @staticmethod
    def latest_profile(settings: BetaSettings, *, require_active: bool = False) -> dict[str, object] | None:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_exploration_learning_enabled:
            return None
        with BetaContext.read_session() as sess:
            return BetaIntradayPatternExplorationLearningService.latest_profile_in_session(
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
        if not settings.intraday_pattern_exploration_learning_enabled:
            return None
        row = sess.scalar(
            select(BetaIntradayPatternExplorationProfile)
            .order_by(
                desc(BetaIntradayPatternExplorationProfile.created_at),
                desc(BetaIntradayPatternExplorationProfile.id),
            )
            .limit(1)
        )
        if row is None:
            return None
        profile = BetaIntradayPatternExplorationLearningService._profile_to_dict(row, settings=settings)
        if require_active and not bool(profile.get("active_for_runtime")):
            return None
        return profile

    @staticmethod
    def resolve_profile(settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternExplorationLearningService.latest_profile(settings, require_active=True)
        return learned or BetaIntradayPatternExplorationLearningService.static_exploration_snapshot(settings)

    @staticmethod
    def resolve_profile_in_session(sess, settings: BetaSettings) -> dict[str, object]:
        learned = BetaIntradayPatternExplorationLearningService.latest_profile_in_session(
            sess,
            settings,
            require_active=True,
        )
        return learned or BetaIntradayPatternExplorationLearningService.static_exploration_snapshot(settings)

    @staticmethod
    def learn_exploration_profile(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_exploration_learning_enabled:
            return {"profile_created": False, "reason": "disabled"}

        now = _utcnow()
        window_days = max(7, int(settings.intraday_pattern_exploration_learning_window_days))
        cutoff = now - timedelta(days=window_days)

        with BetaContext.write_session() as sess:
            latest_run = sess.scalar(
                select(BetaIntradayPatternDiscoveryRun)
                .order_by(desc(BetaIntradayPatternDiscoveryRun.created_at), desc(BetaIntradayPatternDiscoveryRun.id))
                .limit(1)
            )
            trade_rows = BetaIntradayPatternExplorationLearningService._trade_evidence_rows(sess, cutoff=cutoff)
            candidate_rows = BetaIntradayPatternExplorationLearningService._candidate_evidence_rows(
                sess,
                latest_run=latest_run,
            )
            payload = BetaIntradayPatternExplorationLearningService._profile_payload(
                trade_rows=trade_rows,
                candidate_rows=candidate_rows,
                settings=settings,
                window_days=window_days,
            )
            if payload is None:
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "reason": "no_evidence",
                }

            latest_profile = sess.scalar(
                select(BetaIntradayPatternExplorationProfile)
                .order_by(
                    desc(BetaIntradayPatternExplorationProfile.created_at),
                    desc(BetaIntradayPatternExplorationProfile.id),
                )
                .limit(1)
            )
            if latest_profile is not None and BetaIntradayPatternExplorationLearningService._matches_latest(
                latest_profile,
                payload,
            ):
                existing = BetaIntradayPatternExplorationLearningService._profile_to_dict(
                    latest_profile,
                    settings=settings,
                )
                return {
                    "profile_created": False,
                    "job_status": "SKIPPED",
                    "unchanged": True,
                    "reason": "unchanged_intraday_pattern_exploration_profile_inputs",
                    "profile_id": latest_profile.id,
                    "active_for_runtime": existing.get("active_for_runtime"),
                    "source_mode": existing.get("source_mode"),
                    "input_fingerprint": str((existing.get("notes") or {}).get("input_fingerprint") or ""),
                    "input_summary": dict((existing.get("notes") or {}).get("input_summary") or {}),
                }

            profile = BetaIntradayPatternExplorationProfile(
                profile_code=now.strftime("%Y%m%d%H%M%S%f"),
                source_mode=str(payload["source_mode"]),
                evaluation_window_days=int(payload["evaluation_window_days"]),
                trade_count=int(payload["trade_count"]),
                live_forward_trade_count=int(payload["live_forward_trade_count"]),
                historical_backfill_trade_count=int(payload["historical_backfill_trade_count"]),
                candidate_count=int(payload["candidate_count"]),
                distinct_family_count=int(payload["distinct_family_count"]),
                confidence_score=float(payload["confidence_score"]),
                recommended_max_context_depth=int(payload["recommended_max_context_depth"]),
                recommended_max_patterns_per_observation=int(
                    payload["recommended_max_patterns_per_observation"]
                ),
                notes_json=json.dumps(payload.get("notes") or {}, sort_keys=True),
            )
            sess.add(profile)
            sess.flush()
            stored = BetaIntradayPatternExplorationLearningService._profile_to_dict(profile, settings=settings)
            return {
                "profile_created": True,
                "profile_id": profile.id,
                "active_for_runtime": stored.get("active_for_runtime"),
                "source_mode": stored.get("source_mode"),
                "distinct_family_count": stored.get("distinct_family_count"),
                "recommended_max_context_depth": stored.get("recommended_max_context_depth"),
                "recommended_max_patterns_per_observation": stored.get(
                    "recommended_max_patterns_per_observation"
                ),
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
            family_code = str(notes.get("pattern_family_code") or "").strip().upper()
            if not family_code:
                continue
            realized = _safe_float(trade.realized_post_cost_return_pct)
            if realized is None:
                realized = _safe_float(trade.realized_return_pct)
            if realized is None:
                continue
            simulation_source = str(trade.simulation_source or "").strip().upper()
            weight = _LIVE_FORWARD_WEIGHT if simulation_source == "LIVE_FORWARD" else _HISTORICAL_BACKFILL_WEIGHT
            context_tags = [
                str(value).strip()
                for value in list(notes.get("pattern_context_tags") or []) + list(notes.get("matched_context_tags") or [])
                if str(value).strip()
            ]
            rows.append(
                {
                    "family_code": family_code,
                    "realized_post_cost_return_pct": realized,
                    "simulation_source": simulation_source,
                    "weight": weight,
                    "context_prefixes": _context_prefixes(context_tags),
                    "exit_observed_at": trade.exit_observed_at.isoformat() if trade.exit_observed_at else None,
                }
            )
        return rows

    @staticmethod
    def _candidate_evidence_rows(
        sess,
        *,
        latest_run: BetaIntradayPatternDiscoveryRun | None,
    ) -> list[dict[str, object]]:
        if latest_run is None:
            return []
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
                .limit(120)
            ).all()
        )
        rows: list[dict[str, object]] = []
        for candidate in candidates:
            family_code = f"{candidate.anchor_family_code}:{candidate.anchor_code}".strip().upper()
            rows.append(
                {
                    "family_code": family_code,
                    "reliability_score": _safe_float(candidate.reliability_score) or 0.0,
                    "post_cost_edge_pct": _safe_float(candidate.post_cost_edge_15m_pct) or 0.0,
                    "sample_size": int(candidate.sample_size or 0),
                    "matched_instruments": int(candidate.matched_instruments or 0),
                    "context_prefixes": _context_prefixes(_json_array(candidate.context_tags_json)),
                }
            )
        return rows

    @staticmethod
    def _profile_payload(
        *,
        trade_rows: list[dict[str, object]],
        candidate_rows: list[dict[str, object]],
        settings: BetaSettings,
        window_days: int,
    ) -> dict[str, object] | None:
        if not trade_rows and not candidate_rows:
            return None

        family_scores: dict[str, float] = {}
        family_depth_caps: dict[str, int] = {}
        family_context_prefix_scores: dict[str, dict[str, float]] = {}
        family_context_prefix_allowlists: dict[str, list[str]] = {}
        source_mode = "DISCOVERY_FALLBACK"

        if trade_rows:
            grouped_trades: dict[str, list[dict[str, object]]] = {}
            for row in trade_rows:
                grouped_trades.setdefault(str(row["family_code"]), []).append(row)
            trade_scores: dict[str, float] = {}
            for family_code, rows in grouped_trades.items():
                avg_return = _weighted_mean(rows, "realized_post_cost_return_pct") or 0.0
                win_rate = _weighted_mean(
                    [{**row, "is_win": 1.0 if float(row["realized_post_cost_return_pct"]) > 0.0 else 0.0} for row in rows],
                    "is_win",
                ) or 0.0
                support_factor = min(1.0, len(rows) / 4.0)
                edge_factor = min(1.0, max(0.0, avg_return) / 0.16)
                win_factor = min(1.0, max(0.0, win_rate - 0.45) / 0.20)
                trade_scores[family_code] = round(
                    max(0.0, min(1.0, (edge_factor * 0.45) + (win_factor * 0.35) + (support_factor * 0.20))),
                    6,
                )
            family_scores.update(trade_scores)
            source_mode = "OUTCOME_DRIVEN"

        if candidate_rows:
            grouped_candidates: dict[str, list[dict[str, object]]] = {}
            for row in candidate_rows:
                grouped_candidates.setdefault(str(row["family_code"]), []).append(row)
            candidate_scores: dict[str, float] = {}
            for family_code, rows in grouped_candidates.items():
                avg_reliability = _mean([float(row["reliability_score"]) for row in rows]) or 0.0
                avg_edge = _mean([float(row["post_cost_edge_pct"]) for row in rows]) or 0.0
                support_factor = min(1.0, len(rows) / 5.0)
                breadth_factor = min(
                    1.0,
                    (_mean([float(row["matched_instruments"]) for row in rows]) or 0.0) / 3.0,
                )
                candidate_scores[family_code] = round(
                    max(
                        0.0,
                        min(
                            1.0,
                            (min(1.0, avg_reliability / 0.40) * 0.50)
                            + (min(1.0, max(0.0, avg_edge) / 0.18) * 0.30)
                            + (support_factor * 0.10)
                            + (breadth_factor * 0.10),
                        ),
                    ),
                    6,
                )
            if family_scores:
                source_mode = "OUTCOME_MIXED"
            for family_code, candidate_score in candidate_scores.items():
                trade_score = family_scores.get(family_code)
                family_scores[family_code] = round(
                    (trade_score * 0.75 + candidate_score * 0.25)
                    if trade_score is not None
                    else candidate_score,
                    6,
                )
            if not trade_rows:
                source_mode = "DISCOVERY_FALLBACK"

        ranked_families = sorted(
            family_scores.items(),
            key=lambda item: (float(item[1]), item[0]),
            reverse=True,
        )
        distinct_family_count = len(ranked_families)
        strong_family_count = sum(1 for _family, score in ranked_families if float(score) >= 0.55)
        medium_family_count = sum(1 for _family, score in ranked_families if float(score) >= 0.30)

        if strong_family_count >= 3:
            recommended_max_context_depth = min(int(settings.intraday_pattern_max_context_depth), 2)
        elif medium_family_count >= 2:
            recommended_max_context_depth = min(int(settings.intraday_pattern_max_context_depth), 1)
        else:
            recommended_max_context_depth = 0

        confidence_score = BetaIntradayPatternExplorationLearningService._confidence_score(
            trade_count=len(trade_rows),
            candidate_count=len(candidate_rows),
            distinct_family_count=distinct_family_count,
            settings=settings,
        )
        base_cap = max(8, int(settings.intraday_pattern_max_patterns_per_observation) // 2)
        recommended_max_patterns_per_observation = base_cap
        if confidence_score >= 0.45 and strong_family_count >= 3:
            recommended_max_patterns_per_observation = int(settings.intraday_pattern_max_patterns_per_observation)
        elif confidence_score >= 0.25 and medium_family_count >= 2:
            recommended_max_patterns_per_observation = min(
                int(settings.intraday_pattern_max_patterns_per_observation),
                max(base_cap, 24),
            )

        for family_code, score in ranked_families:
            if float(score) >= 0.55:
                family_depth_caps[family_code] = recommended_max_context_depth
            elif float(score) >= 0.30:
                family_depth_caps[family_code] = min(1, recommended_max_context_depth)
            else:
                family_depth_caps[family_code] = 0

        trade_prefix_rows: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in trade_rows:
            family_code = str(row.get("family_code") or "")
            for prefix in list(row.get("context_prefixes") or []):
                trade_prefix_rows.setdefault((family_code, str(prefix)), []).append(row)

        candidate_prefix_rows: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in candidate_rows:
            family_code = str(row.get("family_code") or "")
            for prefix in list(row.get("context_prefixes") or []):
                candidate_prefix_rows.setdefault((family_code, str(prefix)), []).append(row)

        for family_code, family_score in ranked_families:
            prefix_scores: dict[str, float] = {}
            known_prefixes = sorted(
                {
                    prefix
                    for fam, prefix in set(trade_prefix_rows) | set(candidate_prefix_rows)
                    if fam == family_code
                }
            )
            for prefix in known_prefixes:
                score_components: list[float] = []
                trade_subset = trade_prefix_rows.get((family_code, prefix), [])
                if trade_subset:
                    avg_return = _weighted_mean(trade_subset, "realized_post_cost_return_pct") or 0.0
                    win_rate = _weighted_mean(
                        [
                            {**row, "is_win": 1.0 if float(row["realized_post_cost_return_pct"]) > 0.0 else 0.0}
                            for row in trade_subset
                        ],
                        "is_win",
                    ) or 0.0
                    support_factor = min(1.0, len(trade_subset) / 3.0)
                    score_components.append(
                        max(
                            0.0,
                            min(
                                1.0,
                                (min(1.0, max(0.0, avg_return) / 0.14) * 0.50)
                                + (min(1.0, max(0.0, win_rate - 0.45) / 0.20) * 0.35)
                                + (support_factor * 0.15),
                            ),
                        )
                    )
                candidate_subset = candidate_prefix_rows.get((family_code, prefix), [])
                if candidate_subset:
                    avg_reliability = _mean([float(row["reliability_score"]) for row in candidate_subset]) or 0.0
                    avg_edge = _mean([float(row["post_cost_edge_pct"]) for row in candidate_subset]) or 0.0
                    score_components.append(
                        max(
                            0.0,
                            min(
                                1.0,
                                (min(1.0, avg_reliability / 0.40) * 0.60)
                                + (min(1.0, max(0.0, avg_edge) / 0.18) * 0.40),
                            ),
                        )
                    )
                if not score_components:
                    continue
                if len(score_components) == 2:
                    prefix_score = (score_components[0] * 0.75) + (score_components[1] * 0.25)
                else:
                    prefix_score = score_components[0]
                prefix_scores[prefix] = round(float(prefix_score), 6)

            if prefix_scores:
                family_context_prefix_scores[family_code] = dict(
                    sorted(prefix_scores.items(), key=lambda item: (float(item[1]), item[0]), reverse=True)
                )
                top_prefix_score = max(prefix_scores.values())
                allowlist = [
                    prefix
                    for prefix, score in family_context_prefix_scores[family_code].items()
                    if float(score) >= max(0.20, top_prefix_score * 0.65)
                ]
                max_prefixes = 3 if float(family_score) >= 0.55 else 2
                family_context_prefix_allowlists[family_code] = allowlist[:max_prefixes]

        top_rankings = [
            {"family_code": family_code, "score": round(float(score), 6)}
            for family_code, score in ranked_families[:10]
        ]
        normalized_trade_rows = sorted(
            [
                {
                    "family_code": str(row.get("family_code") or ""),
                    "realized_post_cost_return_pct": _safe_float(row.get("realized_post_cost_return_pct")),
                    "simulation_source": str(row.get("simulation_source") or ""),
                    "context_prefixes": sorted(str(prefix) for prefix in list(row.get("context_prefixes") or [])),
                    "exit_observed_at": str(row.get("exit_observed_at") or ""),
                }
                for row in trade_rows
            ],
            key=lambda row: (
                row["family_code"],
                row["simulation_source"],
                row["exit_observed_at"],
                row["realized_post_cost_return_pct"] or 0.0,
            ),
        )
        normalized_candidate_rows = sorted(
            [
                {
                    "family_code": str(row.get("family_code") or ""),
                    "reliability_score": _safe_float(row.get("reliability_score")),
                    "post_cost_edge_pct": _safe_float(row.get("post_cost_edge_pct")),
                    "sample_size": int(row.get("sample_size") or 0),
                    "matched_instruments": int(row.get("matched_instruments") or 0),
                    "context_prefixes": sorted(str(prefix) for prefix in list(row.get("context_prefixes") or [])),
                }
                for row in candidate_rows
            ],
            key=lambda row: (
                row["family_code"],
                row["sample_size"],
                row["matched_instruments"],
                row["reliability_score"] or 0.0,
            ),
        )

        return {
            "source_mode": source_mode,
            "evaluation_window_days": window_days,
            "trade_count": len(trade_rows),
            "live_forward_trade_count": sum(
                1 for row in trade_rows if row.get("simulation_source") == "LIVE_FORWARD"
            ),
            "historical_backfill_trade_count": sum(
                1 for row in trade_rows if row.get("simulation_source") == "HISTORICAL_BACKFILL"
            ),
            "candidate_count": len(candidate_rows),
            "distinct_family_count": distinct_family_count,
            "confidence_score": confidence_score,
            "recommended_max_context_depth": recommended_max_context_depth,
            "recommended_max_patterns_per_observation": recommended_max_patterns_per_observation,
            "notes": {
                "family_scores": {family: round(float(score), 6) for family, score in ranked_families[:20]},
                "family_depth_caps": family_depth_caps,
                "family_context_prefix_scores": family_context_prefix_scores,
                "family_context_prefix_allowlists": family_context_prefix_allowlists,
                "family_rankings": top_rankings,
                "strong_family_count": strong_family_count,
                "medium_family_count": medium_family_count,
                "input_summary": {
                    "window_days": window_days,
                    "trade_count": len(trade_rows),
                    "candidate_count": len(candidate_rows),
                    "distinct_family_count": distinct_family_count,
                    "first_trade_exit_observed_at": normalized_trade_rows[0]["exit_observed_at"]
                    if normalized_trade_rows
                    else None,
                    "last_trade_exit_observed_at": normalized_trade_rows[-1]["exit_observed_at"]
                    if normalized_trade_rows
                    else None,
                },
                "input_fingerprint": _fingerprint_payload(
                    {
                        "window_days": window_days,
                        "trade_rows": normalized_trade_rows,
                        "candidate_rows": normalized_candidate_rows,
                    }
                ),
                "source_mix": {
                    "trade_rows": len(trade_rows),
                    "candidate_rows": len(candidate_rows),
                },
            },
        }

    @staticmethod
    def _confidence_score(
        *,
        trade_count: int,
        candidate_count: int,
        distinct_family_count: int,
        settings: BetaSettings,
    ) -> float:
        minimum = max(1, int(settings.intraday_pattern_exploration_learning_min_closed_trades))
        trade_factor = min(1.0, float(trade_count) / float(minimum))
        candidate_factor = min(1.0, float(candidate_count) / 20.0)
        family_factor = min(1.0, float(distinct_family_count) / 5.0)
        return round(
            max(
                0.0,
                min(
                    1.0,
                    (trade_factor * 0.55) + (candidate_factor * 0.25) + (family_factor * 0.20),
                ),
            ),
            6,
        )

    @staticmethod
    def _matches_latest(
        latest_profile: BetaIntradayPatternExplorationProfile,
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
            and int(latest_profile.candidate_count or 0) == int(payload["candidate_count"])
            and int(latest_profile.distinct_family_count or 0) == int(payload["distinct_family_count"])
            and round(float(latest_profile.confidence_score or 0.0), 4)
            == round(float(payload["confidence_score"] or 0.0), 4)
            and int(latest_profile.recommended_max_context_depth or 0)
            == int(payload["recommended_max_context_depth"])
            and int(latest_profile.recommended_max_patterns_per_observation or 0)
            == int(payload["recommended_max_patterns_per_observation"])
            and latest_notes == payload_notes
        )

    @staticmethod
    def _profile_to_dict(
        row: BetaIntradayPatternExplorationProfile,
        *,
        settings: BetaSettings,
    ) -> dict[str, object]:
        notes = _json_object(row.notes_json)
        confidence_score = float(row.confidence_score or 0.0)
        active_for_runtime = bool(
            settings.intraday_pattern_exploration_learning_enabled
            and (
                confidence_score >= 0.18
                or int(row.candidate_count or 0) >= 10
                or int(row.trade_count or 0) >= int(settings.intraday_pattern_exploration_learning_min_closed_trades)
            )
        )
        source_mode = str(row.source_mode or "OUTCOME_DRIVEN")
        source_label = {
            "OUTCOME_DRIVEN": "Outcome-driven exploration",
            "OUTCOME_MIXED": "Mixed exploration evidence",
            "DISCOVERY_FALLBACK": "Discovery-ranked exploration",
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
            "candidate_count": int(row.candidate_count or 0),
            "distinct_family_count": int(row.distinct_family_count or 0),
            "confidence_score": confidence_score,
            "recommended_max_context_depth": int(row.recommended_max_context_depth or 0),
            "recommended_max_patterns_per_observation": int(
                row.recommended_max_patterns_per_observation or 0
            ),
            "family_scores": {
                str(key): float(value)
                for key, value in (notes.get("family_scores") or {}).items()
                if str(key).strip() and _safe_float(value) is not None
            },
            "family_depth_caps": {
                str(key): int(value)
                for key, value in (notes.get("family_depth_caps") or {}).items()
                if str(key).strip()
            },
            "family_context_prefix_scores": {
                str(family_code): {
                    str(prefix): float(score)
                    for prefix, score in (prefix_scores or {}).items()
                    if str(prefix).strip() and _safe_float(score) is not None
                }
                for family_code, prefix_scores in (notes.get("family_context_prefix_scores") or {}).items()
                if str(family_code).strip() and isinstance(prefix_scores, dict)
            },
            "family_context_prefix_allowlists": {
                str(family_code): [
                    str(prefix).strip()
                    for prefix in list(prefixes or [])
                    if str(prefix).strip()
                ]
                for family_code, prefixes in (notes.get("family_context_prefix_allowlists") or {}).items()
                if str(family_code).strip()
            },
            "family_rankings": list(notes.get("family_rankings") or []),
            "notes": notes,
            "created_at": row.created_at,
        }
