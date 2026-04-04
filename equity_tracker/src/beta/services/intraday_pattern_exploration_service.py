"""Wide intraday pattern exploration built from interpretable event and context tags."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from statistics import median

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradayPatternCandidate,
    BetaIntradayPatternDiscoveryRun,
)
from ..settings import BetaSettings
from .intraday_pattern_exploration_learning_service import BetaIntradayPatternExplorationLearningService
from .intraday_pattern_threshold_learning_service import BetaIntradayPatternThresholdLearningService


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


def _normalized_event_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return sorted({part.strip() for part in str(raw).split("|") if part.strip()})


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(median(values)), 6)


def _threshold_value(profile: dict[str, object] | None, key: str, default: float) -> float:
    if profile is None:
        return float(default)
    try:
        value = float(profile.get(key) or default)
    except (TypeError, ValueError):
        return float(default)
    return value


def _family_priority_score(profile: dict[str, object] | None, family_code: str) -> float:
    if profile is None:
        return 0.0
    family_scores = profile.get("family_scores")
    if not isinstance(family_scores, dict):
        return 0.0
    try:
        return float(family_scores.get(family_code) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _family_depth_cap(profile: dict[str, object] | None, family_code: str, default: int) -> int:
    if profile is None:
        return default
    family_depth_caps = profile.get("family_depth_caps")
    if not isinstance(family_depth_caps, dict):
        return default
    try:
        return int(family_depth_caps.get(family_code) if family_code in family_depth_caps else default)
    except (TypeError, ValueError):
        return default


def _int_profile_value(profile: dict[str, object] | None, key: str, default: int) -> int:
    if profile is None:
        return default
    try:
        value = profile.get(key)
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _context_prefix(tag: str) -> str:
    text = str(tag or "").strip().upper()
    if not text or ":" not in text:
        return ""
    return text.split(":", 1)[0].strip()


def _family_context_prefix_scores(profile: dict[str, object] | None, family_code: str) -> dict[str, float]:
    if profile is None:
        return {}
    payload = profile.get("family_context_prefix_scores")
    if not isinstance(payload, dict):
        return {}
    row = payload.get(family_code)
    if not isinstance(row, dict):
        return {}
    result: dict[str, float] = {}
    for prefix, score in row.items():
        try:
            result[str(prefix).strip().upper()] = float(score)
        except (TypeError, ValueError):
            continue
    return result


def _family_context_prefix_allowlist(profile: dict[str, object] | None, family_code: str) -> set[str]:
    if profile is None:
        return set()
    payload = profile.get("family_context_prefix_allowlists")
    if not isinstance(payload, dict):
        return set()
    row = payload.get(family_code)
    if not isinstance(row, list):
        return set()
    return {str(prefix).strip().upper() for prefix in row if str(prefix).strip()}


def _pattern_hash(*, anchor_family_code: str, anchor_code: str, context_tags: tuple[str, ...]) -> str:
    payload = {
        "anchor_family_code": anchor_family_code,
        "anchor_code": anchor_code,
        "context_tags": list(context_tags),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _ExplorationRow:
    observation_id: str
    instrument_id: str
    symbol: str
    observed_at: datetime
    state_code: str | None
    state_family_code: str | None
    event_codes: tuple[str, ...]
    feature_values: dict[str, float | None]
    future_returns_by_horizon: dict[int, float | None]
    max_adverse_move_pct: float | None
    max_favorable_move_pct: float | None


@dataclass(frozen=True)
class _PatternSpec:
    pattern_hash: str
    pattern_code: str
    anchor_family_code: str
    anchor_code: str
    context_tags: tuple[str, ...]
    symbol: str | None
    session_segment: str | None


class BetaIntradayPatternExplorationService:
    """Explore large numbers of intraday pattern variants from observation primitives."""

    _SUPPORTED_HORIZON_MINUTES = (5, 15, 30, 60, 120)

    @staticmethod
    def run_exploration(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_pattern_exploration_enabled:
            return {
                "observations_considered": 0,
                "labeled_observations": 0,
                "patterns_generated": 0,
                "patterns_screened_in": 0,
            }

        lookback_days = max(7, int(settings.intraday_pattern_history_days))
        cutoff = _utcnow() - timedelta(days=lookback_days)
        evaluation_horizons = BetaIntradayPatternExplorationService._evaluation_horizons(settings)

        with BetaContext.write_session() as sess:
            exploration_profile = BetaIntradayPatternExplorationLearningService.resolve_profile_in_session(
                sess,
                settings,
            )
            threshold_profile = BetaIntradayPatternThresholdLearningService.resolve_profile_in_session(
                sess,
                settings,
            )
            observation_rows = list(
                sess.execute(
                    select(BetaIntradayFeatureObservation, BetaIntradayFeatureLabelValue)
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
            rows = [
                BetaIntradayPatternExplorationService._row_from_record(observation, label)
                for observation, label in observation_rows
            ]
            rows = [row for row in rows if row is not None]

            input_fingerprint, fingerprint_details = BetaIntradayPatternExplorationService._input_fingerprint(
                observation_rows=observation_rows,
                rows=rows,
                settings=settings,
                evaluation_horizons=evaluation_horizons,
                exploration_profile=exploration_profile,
                threshold_profile=threshold_profile,
            )
            latest_fingerprint = BetaIntradayPatternExplorationService._latest_input_fingerprint(sess)
            if latest_fingerprint == input_fingerprint:
                return {
                    "job_status": "SKIPPED",
                    "reason": "unchanged_intraday_pattern_inputs",
                    "observations_considered": len(observation_rows),
                    "labeled_observations": len(rows),
                    "patterns_generated": 0,
                    "patterns_screened_in": 0,
                    "window_start": fingerprint_details["window_start"],
                    "window_end": fingerprint_details["window_end"],
                    "input_fingerprint": input_fingerprint,
                }

            run = BetaIntradayPatternDiscoveryRun(
                run_code=_utcnow().strftime("%Y%m%d%H%M%S%f"),
                status="SUCCESS",
                lookback_days=lookback_days,
                observations_considered=len(observation_rows),
                labeled_observations=len(rows),
                window_start=min((row.observed_at for row in rows), default=None),
                window_end=max((row.observed_at for row in rows), default=None),
            )
            sess.add(run)
            sess.flush()

            aggregates: dict[str, dict[str, object]] = {}
            raw_pattern_instances = 0
            for row in rows:
                specs = BetaIntradayPatternExplorationService._pattern_specs_for_row(
                    row,
                    settings=settings,
                    exploration_profile=exploration_profile,
                    threshold_profile=threshold_profile,
                )
                raw_pattern_instances += len(specs)
                for spec in specs:
                    aggregate = aggregates.setdefault(
                        spec.pattern_hash,
                        {
                            "spec": spec,
                            "instrument_ids": set(),
                            "returns_by_horizon": {horizon: [] for horizon in evaluation_horizons},
                            "adverse": [],
                            "favorable": [],
                        },
                    )
                    aggregate["instrument_ids"].add(row.instrument_id)
                    for horizon, value in row.future_returns_by_horizon.items():
                        if horizon not in evaluation_horizons or value is None:
                            continue
                        aggregate["returns_by_horizon"].setdefault(horizon, []).append(float(value))
                    if row.max_adverse_move_pct is not None:
                        aggregate["adverse"].append(float(row.max_adverse_move_pct))
                    if row.max_favorable_move_pct is not None:
                        aggregate["favorable"].append(float(row.max_favorable_move_pct))

            run.patterns_generated = len(aggregates)
            screen_in_count = 0
            top_candidates: list[tuple[float, str]] = []

            for aggregate in sorted(
                aggregates.values(),
                key=lambda item: (
                    item["spec"].anchor_family_code,
                    item["spec"].anchor_code,
                    item["spec"].pattern_code,
                ),
            ):
                metrics = BetaIntradayPatternExplorationService._finalize_aggregate(
                    aggregate=aggregate,
                    settings=settings,
                )
                status = BetaIntradayPatternExplorationService._candidate_status(
                    metrics=metrics,
                    settings=settings,
                )
                if status == "SCREENED_IN":
                    screen_in_count += 1
                    top_candidates.append((float(metrics["reliability_score"] or 0.0), str(metrics["pattern_code"])))
                sess.add(
                    BetaIntradayPatternCandidate(
                        discovery_run_id=run.id,
                        pattern_hash=str(metrics["pattern_hash"]),
                        pattern_code=str(metrics["pattern_code"]),
                        anchor_family_code=str(metrics["anchor_family_code"]),
                        anchor_code=str(metrics["anchor_code"]),
                        symbol=metrics["symbol"],
                        session_segment=metrics["session_segment"],
                        context_tags_json=json.dumps(metrics["context_tags"], sort_keys=True),
                        action_bias=str(metrics["action_bias"]),
                        sample_size=int(metrics["sample_size"]),
                        matched_instruments=int(metrics["matched_instruments"]),
                        average_return_15m_pct=metrics["average_return_15m_pct"],
                        average_return_30m_pct=metrics["average_return_30m_pct"],
                        median_return_15m_pct=metrics["median_return_15m_pct"],
                        win_rate_pct=metrics["win_rate_pct"],
                        mean_max_adverse_move_pct=metrics["mean_max_adverse_move_pct"],
                        mean_max_favorable_move_pct=metrics["mean_max_favorable_move_pct"],
                        post_cost_edge_15m_pct=metrics["post_cost_edge_15m_pct"],
                        reliability_score=metrics["reliability_score"],
                        status=status,
                        notes_json=json.dumps(
                            {
                                "reference_return_pct": metrics["reference_return_pct"],
                                "best_horizon_minutes": metrics["best_horizon_minutes"],
                                "best_horizon_return_pct": metrics["best_horizon_return_pct"],
                                "best_horizon_median_return_pct": metrics["best_horizon_median_return_pct"],
                                "best_horizon_win_rate_pct": metrics["best_horizon_win_rate_pct"],
                                "best_horizon_post_cost_edge_pct": metrics["best_horizon_post_cost_edge_pct"],
                                "horizon_stability_score": metrics["horizon_stability_score"],
                                "horizon_profile": metrics["horizon_profile"],
                                "support_vs_threshold": {
                                    "sample_size": metrics["sample_size"],
                                    "min_sample_size": int(settings.intraday_pattern_min_sample_size),
                                },
                            },
                            sort_keys=True,
                        ),
                    )
                )

            top_candidates.sort(reverse=True)
            run.patterns_screened_in = screen_in_count
            run.notes_json = json.dumps(
                {
                    "raw_pattern_instances": raw_pattern_instances,
                    "unique_patterns": len(aggregates),
                    "top_screened_in": [code for _score, code in top_candidates[:5]],
                    "input_fingerprint": input_fingerprint,
                    "fingerprint_details": fingerprint_details,
                    "threshold_profile": {
                        "source_mode": threshold_profile.get("source_mode"),
                        "source_label": threshold_profile.get("source_label"),
                        "active_for_runtime": threshold_profile.get("active_for_runtime"),
                        "profile_code": threshold_profile.get("profile_code"),
                        "confidence_score": threshold_profile.get("confidence_score"),
                    },
                    "exploration_profile": {
                        "source_mode": exploration_profile.get("source_mode"),
                        "source_label": exploration_profile.get("source_label"),
                        "active_for_runtime": exploration_profile.get("active_for_runtime"),
                        "profile_code": exploration_profile.get("profile_code"),
                        "confidence_score": exploration_profile.get("confidence_score"),
                    },
                },
                sort_keys=True,
            )
            return {
                "observations_considered": len(observation_rows),
                "labeled_observations": len(rows),
                "patterns_generated": len(aggregates),
                "patterns_screened_in": screen_in_count,
                "discovery_run_code": run.run_code,
                "input_fingerprint": input_fingerprint,
            }

    @staticmethod
    def _latest_input_fingerprint(sess) -> str | None:
        latest_run = sess.scalar(
            select(BetaIntradayPatternDiscoveryRun)
            .order_by(BetaIntradayPatternDiscoveryRun.created_at.desc())
            .limit(1)
        )
        if latest_run is None:
            return None
        notes = _json_object(latest_run.notes_json)
        fingerprint = str(notes.get("input_fingerprint") or "").strip()
        return fingerprint or None

    @staticmethod
    def _input_fingerprint(
        *,
        observation_rows: list[tuple[BetaIntradayFeatureObservation, BetaIntradayFeatureLabelValue]],
        rows: list[_ExplorationRow],
        settings: BetaSettings,
        evaluation_horizons: list[int],
        exploration_profile: dict[str, object],
        threshold_profile: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        hasher = hashlib.sha1()
        hasher.update(str(int(settings.intraday_pattern_history_days)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_pattern_min_sample_size)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_pattern_min_matched_instruments)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_pattern_max_context_depth)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_pattern_max_patterns_per_observation)).encode("utf-8"))
        hasher.update(json.dumps(evaluation_horizons, sort_keys=True).encode("utf-8"))
        hasher.update(
            json.dumps(
                {
                    "profile_code": exploration_profile.get("profile_code"),
                    "source_mode": exploration_profile.get("source_mode"),
                    "recommended_max_context_depth": exploration_profile.get("recommended_max_context_depth"),
                    "recommended_max_patterns_per_observation": exploration_profile.get("recommended_max_patterns_per_observation"),
                    "confidence_score": exploration_profile.get("confidence_score"),
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        hasher.update(
            json.dumps(
                {
                    "profile_code": threshold_profile.get("profile_code"),
                    "source_mode": threshold_profile.get("source_mode"),
                    "confidence_score": threshold_profile.get("confidence_score"),
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )

        distinct_symbols: set[str] = set()
        for observation, label in observation_rows:
            distinct_symbols.add(str(observation.symbol))
            observation_payload = {
                "observation_id": observation.id,
                "observed_at": observation.observed_at.isoformat() if observation.observed_at is not None else None,
                "symbol": observation.symbol,
                "state_code": observation.state_code,
                "state_family_code": observation.state_family_code,
                "event_trigger_code": observation.event_trigger_code,
                "feature_snapshot_json": observation.feature_snapshot_json,
                "future_5m_return_pct": label.future_5m_return_pct,
                "future_15m_return_pct": label.future_15m_return_pct,
                "future_30m_return_pct": label.future_30m_return_pct,
                "future_60m_return_pct": label.future_60m_return_pct,
                "future_120m_return_pct": label.future_120m_return_pct,
                "max_adverse_move_pct": label.max_adverse_move_pct,
                "max_favorable_move_pct": label.max_favorable_move_pct,
            }
            hasher.update(json.dumps(observation_payload, sort_keys=True, default=str).encode("utf-8"))

        window_start = min((row.observed_at for row in rows), default=None)
        window_end = max((row.observed_at for row in rows), default=None)
        return hasher.hexdigest(), {
            "observations_considered": len(observation_rows),
            "labeled_observations": len(rows),
            "distinct_symbols": len(distinct_symbols),
            "window_start": window_start.isoformat() if window_start is not None else None,
            "window_end": window_end.isoformat() if window_end is not None else None,
            "evaluation_horizons": evaluation_horizons,
            "exploration_profile_code": exploration_profile.get("profile_code"),
            "threshold_profile_code": threshold_profile.get("profile_code"),
        }

    @staticmethod
    def _row_from_record(
        observation: BetaIntradayFeatureObservation,
        label: BetaIntradayFeatureLabelValue,
    ) -> _ExplorationRow | None:
        feature_values = {
            str(key): _safe_float(value)
            for key, value in _json_object(observation.feature_snapshot_json).items()
        }
        if not feature_values:
            return None
        future_returns_by_horizon = BetaIntradayPatternExplorationService._future_returns_by_horizon(label)
        if not any(value is not None for value in future_returns_by_horizon.values()):
            return None
        return _ExplorationRow(
            observation_id=str(observation.id),
            instrument_id=str(observation.instrument_id),
            symbol=str(observation.symbol),
            observed_at=observation.observed_at,
            state_code=str(observation.state_code) if observation.state_code else None,
            state_family_code=str(observation.state_family_code) if observation.state_family_code else None,
            event_codes=tuple(_normalized_event_codes(observation.event_trigger_code)),
            feature_values=feature_values,
            future_returns_by_horizon=future_returns_by_horizon,
            max_adverse_move_pct=_safe_float(label.max_adverse_move_pct),
            max_favorable_move_pct=_safe_float(label.max_favorable_move_pct),
        )

    @staticmethod
    def pattern_specs_for_observation(
        observation: BetaIntradayFeatureObservation,
        *,
        settings: BetaSettings,
        exploration_profile: dict[str, object] | None = None,
        threshold_profile: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        feature_values = {
            str(key): _safe_float(value)
            for key, value in _json_object(observation.feature_snapshot_json).items()
        }
        if not feature_values:
            return []
        row = _ExplorationRow(
            observation_id=str(observation.id),
            instrument_id=str(observation.instrument_id),
            symbol=str(observation.symbol or ""),
            observed_at=observation.observed_at,
            state_code=str(observation.state_code) if observation.state_code else None,
            state_family_code=str(observation.state_family_code) if observation.state_family_code else None,
            event_codes=tuple(_normalized_event_codes(observation.event_trigger_code)),
            feature_values=feature_values,
            future_returns_by_horizon={},
            max_adverse_move_pct=None,
            max_favorable_move_pct=None,
        )
        return [
            {
                "pattern_hash": spec.pattern_hash,
                "pattern_code": spec.pattern_code,
                "anchor_family_code": spec.anchor_family_code,
                "anchor_code": spec.anchor_code,
                "context_tags": list(spec.context_tags),
                "symbol": spec.symbol,
                "session_segment": spec.session_segment,
            }
            for spec in BetaIntradayPatternExplorationService._pattern_specs_for_row(
                row,
                settings=settings,
                exploration_profile=exploration_profile,
                threshold_profile=(
                    threshold_profile
                    or BetaIntradayPatternThresholdLearningService.resolve_profile(settings)
                ),
            )
        ]

    @staticmethod
    def _pattern_specs_for_row(
        row: _ExplorationRow,
        *,
        settings: BetaSettings,
        exploration_profile: dict[str, object] | None = None,
        threshold_profile: dict[str, object] | None = None,
    ) -> list[_PatternSpec]:
        anchors = BetaIntradayPatternExplorationService._anchor_tags(
            row,
            threshold_profile=threshold_profile,
        )
        if not anchors:
            return []

        contexts = BetaIntradayPatternExplorationService._context_tags(
            row,
            threshold_profile=threshold_profile,
        )
        max_depth = max(
            0,
            min(
                int(settings.intraday_pattern_max_context_depth),
                _int_profile_value(
                    exploration_profile,
                    "recommended_max_context_depth",
                    int(settings.intraday_pattern_max_context_depth),
                ),
            ),
        )
        max_specs = max(
            4,
            min(
                int(settings.intraday_pattern_max_patterns_per_observation),
                _int_profile_value(
                    exploration_profile,
                    "recommended_max_patterns_per_observation",
                    int(settings.intraday_pattern_max_patterns_per_observation),
                ),
            ),
        )
        seen_hashes: set[str] = set()
        specs: list[_PatternSpec] = []
        ordered_anchors = sorted(
            enumerate(anchors),
            key=lambda item: (
                _family_priority_score(
                    exploration_profile,
                    f"{item[1][0]}:{item[1][1]}",
                ),
                -item[0],
            ),
            reverse=True,
        )

        for _index, (anchor_family_code, anchor_code) in ordered_anchors:
            family_code = f"{anchor_family_code}:{anchor_code}"
            family_depth = min(
                max_depth,
                _family_depth_cap(exploration_profile, family_code, max_depth),
            )
            family_contexts = BetaIntradayPatternExplorationService._contexts_for_family(
                contexts,
                family_code=family_code,
                exploration_profile=exploration_profile,
            )
            for depth in range(0, family_depth + 1):
                for combo in combinations(family_contexts, depth):
                    normalized_contexts = tuple(sorted(combo))
                    pattern_hash = _pattern_hash(
                        anchor_family_code=anchor_family_code,
                        anchor_code=anchor_code,
                        context_tags=normalized_contexts,
                    )
                    if pattern_hash in seen_hashes:
                        continue
                    seen_hashes.add(pattern_hash)
                    pattern_parts = [f"{anchor_family_code}:{anchor_code}", *normalized_contexts]
                    pattern_code = "|".join(pattern_parts)
                    symbol = next(
                        (tag.split(":", 1)[1] for tag in normalized_contexts if tag.startswith("SYMBOL:")),
                        None,
                    )
                    session_segment = next(
                        (tag.split(":", 1)[1] for tag in normalized_contexts if tag.startswith("SEGMENT:")),
                        None,
                    )
                    specs.append(
                        _PatternSpec(
                            pattern_hash=pattern_hash,
                            pattern_code=(pattern_code[:220] if len(pattern_code) > 220 else pattern_code),
                            anchor_family_code=anchor_family_code,
                            anchor_code=anchor_code,
                            context_tags=normalized_contexts,
                            symbol=symbol,
                            session_segment=session_segment,
                        )
                    )
                    if len(specs) >= max_specs:
                        return specs
        return specs

    @staticmethod
    def _contexts_for_family(
        contexts: list[str],
        *,
        family_code: str,
        exploration_profile: dict[str, object] | None = None,
    ) -> list[str]:
        if not contexts:
            return []
        prefix_scores = _family_context_prefix_scores(exploration_profile, family_code)
        allowlist = _family_context_prefix_allowlist(exploration_profile, family_code)
        filtered = list(contexts)
        if allowlist:
            allowlisted = [tag for tag in contexts if _context_prefix(tag) in allowlist]
            if allowlisted:
                filtered = allowlisted
        scored_contexts = list(enumerate(filtered))
        scored_contexts.sort(
            key=lambda item: (
                float(prefix_scores.get(_context_prefix(item[1]), 0.0)),
                -item[0],
            ),
            reverse=True,
        )
        return [tag for _index, tag in scored_contexts]

    @staticmethod
    def _anchor_tags(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> list[tuple[str, str]]:
        anchors: list[tuple[str, str]] = []
        if row.state_family_code:
            anchors.append(("STATE_FAMILY", row.state_family_code))
        for event_code in row.event_codes:
            anchors.append(("EVENT", event_code))
        if BetaIntradayPatternExplorationService._is_sell_pressure(row, threshold_profile=threshold_profile):
            anchors.append(("DERIVED", "SELL_PRESSURE"))
        if BetaIntradayPatternExplorationService._is_buy_pressure(row, threshold_profile=threshold_profile):
            anchors.append(("DERIVED", "BUY_PRESSURE"))
        if BetaIntradayPatternExplorationService._is_dry_period(row, threshold_profile=threshold_profile):
            anchors.append(("DERIVED", "DRY_PERIOD"))
        if BetaIntradayPatternExplorationService._is_range_expansion(row, threshold_profile=threshold_profile):
            anchors.append(("DERIVED", "RANGE_EXPANSION"))
        if BetaIntradayPatternExplorationService._is_close_drive(row, threshold_profile=threshold_profile):
            anchors.append(("DERIVED", "CLOSE_DRIVE"))
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for anchor in anchors:
            if anchor in seen:
                continue
            seen.add(anchor)
            deduped.append(anchor)
        return deduped

    @staticmethod
    def _context_tags(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> list[str]:
        feature_values = row.feature_values
        tags: list[str] = []

        session_segment = BetaIntradayPatternExplorationService._session_segment(feature_values)
        if session_segment != "UNKNOWN":
            tags.append(f"SEGMENT:{session_segment}")
        if row.symbol:
            tags.append(f"SYMBOL:{row.symbol}")

        gap = _safe_float(feature_values.get("gap_from_prev_close_pct")) or 0.0
        gap_up_threshold = _threshold_value(threshold_profile, "gap_up_pct", 1.0)
        gap_down_threshold = _threshold_value(threshold_profile, "gap_down_pct", -1.0)
        if gap >= gap_up_threshold:
            tags.append("GAP:UP")
        elif gap <= gap_down_threshold:
            tags.append("GAP:DOWN")

        vwap_distance = _safe_float(feature_values.get("distance_from_vwap_pct")) or 0.0
        vwap_above_threshold = _threshold_value(threshold_profile, "vwap_above_pct", 0.10)
        vwap_below_threshold = _threshold_value(threshold_profile, "vwap_below_pct", -0.10)
        if vwap_distance >= vwap_above_threshold:
            tags.append("VWAP:ABOVE")
        elif vwap_distance <= vwap_below_threshold:
            tags.append("VWAP:BELOW")

        volume_ratio = (
            _safe_float(feature_values.get("volume_last_15m_vs_expected"))
            or _safe_float(feature_values.get("volume_last_30m_vs_expected"))
            or 0.0
        )
        volume_high_threshold = _threshold_value(threshold_profile, "volume_high_ratio", 1.40)
        volume_low_threshold = _threshold_value(threshold_profile, "volume_low_ratio", 0.75)
        if volume_ratio >= volume_high_threshold:
            tags.append("VOLUME:HIGH")
        elif 0.0 < volume_ratio <= volume_low_threshold:
            tags.append("VOLUME:LOW")

        volatility = _safe_float(feature_values.get("rolling_intraday_vol_15m_pct")) or 0.0
        volatility_high_threshold = _threshold_value(threshold_profile, "volatility_high_pct", 1.40)
        volatility_low_threshold = _threshold_value(threshold_profile, "volatility_low_pct", 0.45)
        if volatility >= volatility_high_threshold:
            tags.append("VOLATILITY:HIGH")
        elif 0.0 < volatility <= volatility_low_threshold:
            tags.append("VOLATILITY:LOW")

        momentum = _safe_float(feature_values.get("return_last_15m_pct")) or 0.0
        momentum_up_threshold = _threshold_value(threshold_profile, "momentum_up_pct", 0.80)
        momentum_down_threshold = _threshold_value(threshold_profile, "momentum_down_pct", -0.80)
        if momentum >= momentum_up_threshold:
            tags.append("MOMENTUM:UP")
        elif momentum <= momentum_down_threshold:
            tags.append("MOMENTUM:DOWN")

        intraday_range = _safe_float(feature_values.get("intraday_range_pct")) or 0.0
        range_compressed_threshold = _threshold_value(threshold_profile, "range_compressed_pct", 1.20)
        range_expanded_threshold = _threshold_value(threshold_profile, "range_expanded_pct", 2.50)
        if 0.0 < intraday_range <= range_compressed_threshold:
            tags.append("RANGE:COMPRESSED")
        elif intraday_range >= range_expanded_threshold:
            tags.append("RANGE:EXPANDED")

        deduped: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            if tag in seen:
                continue
            seen.add(tag)
            deduped.append(tag)
        return deduped

    @staticmethod
    def _session_segment(feature_values: dict[str, float | None]) -> str:
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

    @staticmethod
    def _is_sell_pressure(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> bool:
        ret_15 = _safe_float(row.feature_values.get("return_last_15m_pct")) or 0.0
        ret_open = _safe_float(row.feature_values.get("return_since_open_pct")) or 0.0
        ret_15_threshold = _threshold_value(threshold_profile, "sell_pressure_return_15m_pct", -1.0)
        ret_open_threshold = _threshold_value(threshold_profile, "sell_pressure_return_since_open_pct", -1.5)
        return (
            ret_15 <= ret_15_threshold
            or ret_open <= ret_open_threshold
            or "EVENT_LARGE_INTRADAY_MOVE" in row.event_codes and ret_15 < 0.0
        )

    @staticmethod
    def _is_buy_pressure(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> bool:
        ret_15 = _safe_float(row.feature_values.get("return_last_15m_pct")) or 0.0
        ret_open = _safe_float(row.feature_values.get("return_since_open_pct")) or 0.0
        ret_15_threshold = _threshold_value(threshold_profile, "buy_pressure_return_15m_pct", 1.0)
        ret_open_threshold = _threshold_value(threshold_profile, "buy_pressure_return_since_open_pct", 1.5)
        return (
            ret_15 >= ret_15_threshold
            or ret_open >= ret_open_threshold
            or "EVENT_LARGE_INTRADAY_MOVE" in row.event_codes and ret_15 > 0.0
        )

    @staticmethod
    def _is_dry_period(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> bool:
        volume_ratio = (
            _safe_float(row.feature_values.get("volume_last_15m_vs_expected"))
            or _safe_float(row.feature_values.get("volume_last_30m_vs_expected"))
            or 0.0
        )
        volatility = _safe_float(row.feature_values.get("rolling_intraday_vol_15m_pct")) or 0.0
        intraday_range = _safe_float(row.feature_values.get("intraday_range_pct")) or 0.0
        return (
            0.0 < volume_ratio <= _threshold_value(threshold_profile, "volume_low_ratio", 0.75)
            and 0.0 < volatility <= _threshold_value(threshold_profile, "volatility_low_pct", 0.45)
            and 0.0 < intraday_range <= _threshold_value(threshold_profile, "range_compressed_pct", 1.20)
        )

    @staticmethod
    def _is_range_expansion(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> bool:
        volatility = _safe_float(row.feature_values.get("rolling_intraday_vol_15m_pct")) or 0.0
        intraday_range = _safe_float(row.feature_values.get("intraday_range_pct")) or 0.0
        return (
            volatility >= _threshold_value(threshold_profile, "range_expansion_volatility_pct", 1.50)
            or intraday_range >= _threshold_value(threshold_profile, "range_expanded_pct", 2.50)
        )

    @staticmethod
    def _is_close_drive(
        row: _ExplorationRow,
        *,
        threshold_profile: dict[str, object] | None = None,
    ) -> bool:
        session_segment = BetaIntradayPatternExplorationService._session_segment(row.feature_values)
        ret_30 = _safe_float(row.feature_values.get("return_last_30m_pct")) or 0.0
        return (
            session_segment in {"LATE", "CLOSING"}
            and abs(ret_30) >= _threshold_value(threshold_profile, "close_drive_abs_return_30m_pct", 0.75)
        )

    @staticmethod
    def _finalize_aggregate(
        *,
        aggregate: dict[str, object],
        settings: BetaSettings,
    ) -> dict[str, object]:
        spec: _PatternSpec = aggregate["spec"]
        cost_drag_pct = (
            float(settings.intraday_execution_commission_bps)
            + float(settings.intraday_execution_spread_bps)
            + float(settings.intraday_execution_slippage_bps)
        ) / 100.0
        horizon_metrics: dict[int, dict[str, object]] = {}
        for horizon in BetaIntradayPatternExplorationService._evaluation_horizons(settings):
            returns = [float(value) for value in aggregate["returns_by_horizon"].get(horizon, [])]
            metrics = BetaIntradayPatternExplorationService._horizon_metrics(
                returns=returns,
                cost_drag_pct=cost_drag_pct,
                min_sample_size=int(settings.intraday_pattern_min_sample_size),
            )
            if metrics is None:
                continue
            horizon_metrics[horizon] = metrics

        best_horizon_minutes = BetaIntradayPatternExplorationService._best_horizon_minutes(horizon_metrics)
        best_metrics = horizon_metrics.get(best_horizon_minutes or -1, {})
        action_bias = str(best_metrics.get("action_bias") or "NEUTRAL")
        sample_size = int(best_metrics.get("sample_size") or 0)
        matched_instruments = len(aggregate["instrument_ids"])
        best_post_cost_edge = float(best_metrics.get("post_cost_edge_pct") or 0.0)
        best_win_rate = _safe_float(best_metrics.get("win_rate_pct"))
        horizon_stability_score = BetaIntradayPatternExplorationService._horizon_stability_score(
            horizon_metrics=horizon_metrics,
            best_horizon_minutes=best_horizon_minutes,
            settings=settings,
        )
        support_factor = min(1.0, sample_size / max(1.0, float(settings.intraday_pattern_min_sample_size)))
        breadth_factor = min(
            1.0,
            matched_instruments / max(1.0, float(settings.intraday_pattern_min_matched_instruments)),
        )
        win_factor = max(0.0, float(best_win_rate or 0.0) / 100.0)
        edge_factor = min(1.0, best_post_cost_edge / 0.35) if best_post_cost_edge > 0.0 else 0.0
        reliability_score = round(
            support_factor * breadth_factor * win_factor * edge_factor * (0.5 + (0.5 * horizon_stability_score)),
            6,
        )
        mean_15 = _safe_float(horizon_metrics.get(15, {}).get("mean_return_pct"))
        mean_30 = _safe_float(horizon_metrics.get(30, {}).get("mean_return_pct"))
        median_15 = _safe_float(horizon_metrics.get(15, {}).get("median_return_pct"))
        post_cost_edge_15m_pct = _safe_float(horizon_metrics.get(15, {}).get("post_cost_edge_pct"))
        reference_return = _safe_float(best_metrics.get("mean_return_pct")) or 0.0
        horizon_profile = {
            str(horizon): {
                "sample_size": int(metrics["sample_size"]),
                "action_bias": metrics["action_bias"],
                "mean_return_pct": metrics["mean_return_pct"],
                "median_return_pct": metrics["median_return_pct"],
                "win_rate_pct": metrics["win_rate_pct"],
                "post_cost_edge_pct": metrics["post_cost_edge_pct"],
                "direction_consistency_score": metrics["direction_consistency_score"],
            }
            for horizon, metrics in sorted(horizon_metrics.items())
        }

        return {
            "pattern_hash": spec.pattern_hash,
            "pattern_code": spec.pattern_code,
            "anchor_family_code": spec.anchor_family_code,
            "anchor_code": spec.anchor_code,
            "context_tags": list(spec.context_tags),
            "symbol": spec.symbol,
            "session_segment": spec.session_segment,
            "action_bias": action_bias,
            "sample_size": sample_size,
            "matched_instruments": matched_instruments,
            "average_return_15m_pct": mean_15,
            "average_return_30m_pct": mean_30,
            "median_return_15m_pct": median_15,
            "win_rate_pct": round(float(best_win_rate), 6) if best_win_rate is not None else None,
            "mean_max_adverse_move_pct": _mean([float(value) for value in aggregate["adverse"]]),
            "mean_max_favorable_move_pct": _mean([float(value) for value in aggregate["favorable"]]),
            "post_cost_edge_15m_pct": post_cost_edge_15m_pct,
            "reference_return_pct": round(float(reference_return), 6),
            "reliability_score": reliability_score,
            "best_horizon_minutes": best_horizon_minutes,
            "best_horizon_return_pct": _safe_float(best_metrics.get("mean_return_pct")),
            "best_horizon_median_return_pct": _safe_float(best_metrics.get("median_return_pct")),
            "best_horizon_win_rate_pct": best_win_rate,
            "best_horizon_post_cost_edge_pct": best_post_cost_edge,
            "horizon_stability_score": horizon_stability_score,
            "horizon_profile": horizon_profile,
        }

    @staticmethod
    def _candidate_status(
        *,
        metrics: dict[str, object],
        settings: BetaSettings,
    ) -> str:
        sample_size = int(metrics["sample_size"])
        matched_instruments = int(metrics["matched_instruments"])
        post_cost_edge_15m_pct = float(metrics["best_horizon_post_cost_edge_pct"] or 0.0)
        win_rate_pct = float(metrics["best_horizon_win_rate_pct"] or 0.0)
        reliability_score = float(metrics["reliability_score"] or 0.0)
        if sample_size < max(3, int(settings.intraday_pattern_min_sample_size)):
            return "REJECTED"
        if matched_instruments < max(1, int(settings.intraday_pattern_min_matched_instruments)):
            return "REJECTED"
        if post_cost_edge_15m_pct <= 0.0:
            return "REJECTED"
        if win_rate_pct < 52.0:
            return "REJECTED"
        if reliability_score < 0.12:
            return "REJECTED"
        return "SCREENED_IN"

    @staticmethod
    def _evaluation_horizons(settings: BetaSettings) -> list[int]:
        raw_horizons = list(getattr(settings, "intraday_pattern_evaluation_horizons_minutes", []) or [])
        normalized: list[int] = []
        seen: set[int] = set()
        for value in raw_horizons:
            try:
                candidate = int(value)
            except (TypeError, ValueError):
                continue
            if candidate not in BetaIntradayPatternExplorationService._SUPPORTED_HORIZON_MINUTES:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        if normalized:
            return normalized
        return list(BetaIntradayPatternExplorationService._SUPPORTED_HORIZON_MINUTES)

    @staticmethod
    def _future_returns_by_horizon(
        label: BetaIntradayFeatureLabelValue,
    ) -> dict[int, float | None]:
        return {
            5: _safe_float(label.future_5m_return_pct),
            15: _safe_float(label.future_15m_return_pct),
            30: _safe_float(label.future_30m_return_pct),
            60: _safe_float(label.future_60m_return_pct),
            120: _safe_float(label.future_120m_return_pct),
        }

    @staticmethod
    def _horizon_metrics(
        *,
        returns: list[float],
        cost_drag_pct: float,
        min_sample_size: int,
    ) -> dict[str, object] | None:
        if not returns:
            return None
        mean_return = _mean(returns)
        median_return = _median(returns)
        if mean_return is None:
            return None

        action_bias = "NEUTRAL"
        if mean_return > 0.0:
            action_bias = "LONG"
        elif mean_return < 0.0:
            action_bias = "SHORT"

        if action_bias == "LONG":
            win_rate = (sum(1 for value in returns if value > 0.0) / len(returns)) * 100.0
            edge_reference = mean_return
        elif action_bias == "SHORT":
            win_rate = (sum(1 for value in returns if value < 0.0) / len(returns)) * 100.0
            edge_reference = abs(mean_return)
        else:
            win_rate = None
            edge_reference = 0.0

        aligned_mean = abs(float(mean_return))
        aligned_median = abs(float(median_return or 0.0))
        if mean_return == 0.0 or median_return is None:
            direction_consistency_score = 0.0
        else:
            same_sign = (mean_return > 0.0 and median_return >= 0.0) or (mean_return < 0.0 and median_return <= 0.0)
            spread_denominator = max(aligned_mean, 0.10)
            spread_penalty = abs(aligned_mean - aligned_median) / spread_denominator
            direction_consistency_score = max(0.0, (1.0 if same_sign else 0.35) - min(0.75, spread_penalty))

        sample_factor = min(1.0, len(returns) / max(1.0, float(min_sample_size)))
        win_factor = max(0.0, float(win_rate or 0.0) / 100.0)
        edge_factor = min(1.0, max(0.0, edge_reference - cost_drag_pct) / 0.35) if edge_reference > 0.0 else 0.0
        horizon_quality_score = round(sample_factor * win_factor * edge_factor * max(0.0, direction_consistency_score), 6)

        return {
            "sample_size": len(returns),
            "action_bias": action_bias,
            "mean_return_pct": round(float(mean_return), 6),
            "median_return_pct": round(float(median_return), 6) if median_return is not None else None,
            "win_rate_pct": round(float(win_rate), 6) if win_rate is not None else None,
            "post_cost_edge_pct": round(max(0.0, edge_reference - cost_drag_pct), 6),
            "direction_consistency_score": round(direction_consistency_score, 6),
            "horizon_quality_score": horizon_quality_score,
        }

    @staticmethod
    def _best_horizon_minutes(horizon_metrics: dict[int, dict[str, object]]) -> int | None:
        if not horizon_metrics:
            return None
        return max(
            horizon_metrics,
            key=lambda horizon: (
                float(horizon_metrics[horizon].get("horizon_quality_score") or 0.0),
                float(horizon_metrics[horizon].get("post_cost_edge_pct") or 0.0),
                float(horizon_metrics[horizon].get("win_rate_pct") or 0.0),
                int(horizon_metrics[horizon].get("sample_size") or 0),
                -abs(horizon - 30),
            ),
        )

    @staticmethod
    def _horizon_stability_score(
        *,
        horizon_metrics: dict[int, dict[str, object]],
        best_horizon_minutes: int | None,
        settings: BetaSettings,
    ) -> float:
        if best_horizon_minutes is None:
            return 0.0
        best_metrics = horizon_metrics.get(best_horizon_minutes)
        if best_metrics is None:
            return 0.0
        best_bias = str(best_metrics.get("action_bias") or "NEUTRAL")
        evaluable = [
            metrics
            for metrics in horizon_metrics.values()
            if int(metrics.get("sample_size") or 0) >= max(3, int(settings.intraday_pattern_min_sample_size))
            and str(metrics.get("action_bias") or "NEUTRAL") in {"LONG", "SHORT"}
        ]
        if not evaluable:
            return 0.0
        aligned = [
            metrics
            for metrics in evaluable
            if str(metrics.get("action_bias") or "NEUTRAL") == best_bias
            and float(metrics.get("post_cost_edge_pct") or 0.0) > 0.0
        ]
        consistency_ratio = len(aligned) / float(len(evaluable))
        span_factor = len(evaluable) / float(len(BetaIntradayPatternExplorationService._SUPPORTED_HORIZON_MINUTES))
        best_consistency = float(best_metrics.get("direction_consistency_score") or 0.0)
        return round(consistency_ratio * max(0.25, span_factor) * max(0.25, best_consistency), 6)
