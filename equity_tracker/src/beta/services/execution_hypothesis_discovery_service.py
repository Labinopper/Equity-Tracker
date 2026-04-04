"""Template mutation discovery for intraday execution hypotheses."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaExecutionHypothesisDefinition,
    BetaExecutionHypothesisDiscoveryCandidate,
    BetaExecutionHypothesisDiscoveryRun,
)
from ..settings import BetaSettings
from .execution_hypothesis_backtest_service import BetaExecutionHypothesisBacktestService
from .execution_hypothesis_service import BetaExecutionHypothesisService
from .hypothesis_normalizer import BetaHypothesisNormalizer


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class BetaExecutionHypothesisDiscoveryService:
    """Discover execution hypothesis variants from seeded manual intraday templates."""

    @staticmethod
    def run_discovery(settings: BetaSettings) -> dict[str, object]:
        if not BetaContext.is_initialized() or not settings.intraday_execution_hypothesis_research_enabled:
            return {
                "templates_considered": 0,
                "candidates_generated": 0,
                "candidates_screened_in": 0,
                "candidates_promoted": 0,
            }

        with BetaContext.write_session() as sess:
            BetaExecutionHypothesisService.ensure_default_definitions()
            templates = list(
                sess.scalars(
                    select(BetaExecutionHypothesisDefinition)
                    .where(
                        BetaExecutionHypothesisDefinition.status.in_(("ACTIVE", "PAUSED")),
                        BetaExecutionHypothesisDefinition.source_type == "MANUAL",
                    )
                    .order_by(BetaExecutionHypothesisDefinition.created_at.asc())
                    .limit(max(1, int(settings.intraday_execution_hypothesis_template_limit)))
                ).all()
            )
            if not templates:
                return {
                    "templates_considered": 0,
                    "candidates_generated": 0,
                    "candidates_screened_in": 0,
                    "candidates_promoted": 0,
                }

            dataset_rows = BetaExecutionHypothesisBacktestService.load_dataset(sess, settings=settings)
            if not dataset_rows:
                return {
                    "templates_considered": len(templates),
                    "candidates_generated": 0,
                    "candidates_screened_in": 0,
                    "candidates_promoted": 0,
                }

            input_fingerprint, fingerprint_details = BetaExecutionHypothesisDiscoveryService._input_fingerprint(
                templates=templates,
                dataset_rows=dataset_rows,
                settings=settings,
            )
            latest_fingerprint = BetaExecutionHypothesisDiscoveryService._latest_input_fingerprint(sess)
            if latest_fingerprint == input_fingerprint:
                return {
                    "job_status": "SKIPPED",
                    "reason": "unchanged_execution_discovery_inputs",
                    "templates_considered": len(templates),
                    "candidates_generated": 0,
                    "candidates_screened_in": 0,
                    "candidates_promoted": 0,
                    "discovery_window_start": fingerprint_details["window_start"],
                    "discovery_window_end": fingerprint_details["window_end"],
                    "dataset_rows": fingerprint_details["dataset_rows"],
                    "input_fingerprint": input_fingerprint,
                }

            discovery_run = BetaExecutionHypothesisDiscoveryRun(
                run_code=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
                status="SUCCESS",
                templates_considered=len(templates),
                discovery_window_start=min((row.signal_time for row in dataset_rows), default=None),
                discovery_window_end=max((row.signal_time for row in dataset_rows), default=None),
            )
            sess.add(discovery_run)
            sess.flush()

            minute_cache = BetaExecutionHypothesisBacktestService._load_minute_cache(sess, dataset_rows)
            outcome_cache: dict[tuple[str, str], dict[str, float | int | None]] = {}
            existing_hashes = BetaExecutionHypothesisDiscoveryService._existing_candidate_hashes(sess)
            candidates_generated = 0
            candidates_screened_in = 0
            promoted = 0
            persisted: list[tuple[dict[str, object], BetaExecutionHypothesisDiscoveryCandidate]] = []

            for template in templates:
                specs = BetaExecutionHypothesisDiscoveryService._generate_candidate_specs(
                    template=template,
                    settings=settings,
                )
                candidates_generated += len(specs)
                for spec in specs:
                    definition = BetaExecutionHypothesisDefinition(
                        hypothesis_code=str(spec["hypothesis_code"]),
                        name=str(spec["hypothesis_name"]),
                        signal_type=str(spec["signal_type"]),
                        entry_conditions_json=json.dumps(spec["entry_conditions"], sort_keys=True),
                        regime_filters_json=json.dumps(spec["regime_filters"], sort_keys=True),
                        feature_subset_json=json.dumps(spec["feature_subset"], sort_keys=True),
                        rationale_text=str(spec["rationale_text"]),
                        source_type="GENERATED",
                        metadata_json=json.dumps(spec["metadata"], sort_keys=True),
                        provenance_json=json.dumps(spec["provenance"], sort_keys=True),
                        status="ACTIVE",
                    )
                    summary = BetaExecutionHypothesisBacktestService.evaluate_definition_summary(
                        sess=sess,
                        definition=definition,
                        dataset_rows=dataset_rows,
                        settings=settings,
                        minute_cache=minute_cache,
                        outcome_cache=outcome_cache,
                    )
                    status, reason = BetaExecutionHypothesisDiscoveryService._candidate_status(summary, settings=settings)
                    if status == "SCREENED_IN":
                        candidates_screened_in += 1
                    candidate_row = BetaExecutionHypothesisDiscoveryCandidate(
                        discovery_run_id=discovery_run.id,
                        template_execution_hypothesis_definition_id=template.id,
                        candidate_hash=str(spec["candidate_hash"]),
                        hypothesis_code=str(spec["hypothesis_code"]),
                        hypothesis_name=str(spec["hypothesis_name"]),
                        signal_type=str(spec["signal_type"]),
                        entry_conditions_json=json.dumps(spec["entry_conditions"], sort_keys=True),
                        regime_filters_json=json.dumps(spec["regime_filters"], sort_keys=True),
                        feature_subset_json=json.dumps(spec["feature_subset"], sort_keys=True),
                        metadata_json=json.dumps(spec["metadata"], sort_keys=True),
                        status=("PRUNED" if spec["candidate_hash"] in existing_hashes else status),
                        support_count=int(summary.get("support_count") or 0),
                        matched_instruments=int(summary.get("matched_instruments") or 0),
                        average_return_pct=summary.get("average_return_pct"),
                        median_return_pct=summary.get("median_return_pct"),
                        win_rate_pct=summary.get("win_rate_pct"),
                        outcome_volatility_pct=summary.get("outcome_volatility_pct"),
                        baseline_edge_pct=summary.get("baseline_edge_pct"),
                        transaction_cost_adjusted_return_pct=summary.get("transaction_cost_adjusted_return_pct"),
                        stability_score=summary.get("stability_score"),
                        notes_json=json.dumps(
                            {
                                "reason": ("already_promoted_or_seeded" if spec["candidate_hash"] in existing_hashes else reason),
                                "summary": summary,
                            },
                            sort_keys=True,
                            default=str,
                        ),
                    )
                    sess.add(candidate_row)
                    persisted.append((spec, candidate_row))

            promoted_rows = [
                item for item in persisted
                if item[1].status == "SCREENED_IN"
            ]
            promoted_rows.sort(
                key=lambda item: (
                    float(item[1].transaction_cost_adjusted_return_pct or 0.0),
                    float(item[1].stability_score or 0.0),
                    int(item[1].support_count or 0),
                ),
                reverse=True,
            )
            existing_codes = {
                row.hypothesis_code: row
                for row in sess.scalars(select(BetaExecutionHypothesisDefinition)).all()
            }
            for spec, candidate_row in promoted_rows[: max(1, int(settings.intraday_execution_hypothesis_max_promotions_per_run))]:
                if spec["candidate_hash"] in existing_hashes:
                    continue
                promoted_definition = BetaExecutionHypothesisDefinition(
                    hypothesis_code=str(spec["hypothesis_code"]),
                    name=str(spec["hypothesis_name"]),
                    signal_type=str(spec["signal_type"]),
                    entry_conditions_json=json.dumps(spec["entry_conditions"], sort_keys=True),
                    regime_filters_json=json.dumps(spec["regime_filters"], sort_keys=True),
                    feature_subset_json=json.dumps(spec["feature_subset"], sort_keys=True),
                    rationale_text=str(spec["rationale_text"]),
                    source_type="GENERATED",
                    metadata_json=json.dumps(spec["metadata"], sort_keys=True),
                    provenance_json=json.dumps(spec["provenance"], sort_keys=True),
                    status="ACTIVE",
                )
                if promoted_definition.hypothesis_code in existing_codes:
                    candidate_row.status = "PRUNED"
                    continue
                sess.add(promoted_definition)
                sess.flush()
                candidate_row.status = "PROMOTED"
                candidate_row.promoted_execution_hypothesis_definition_id = promoted_definition.id
                existing_hashes.add(spec["candidate_hash"])
                existing_codes[promoted_definition.hypothesis_code] = promoted_definition
                promoted += 1

            discovery_run.candidates_generated = candidates_generated
            discovery_run.candidates_screened_in = candidates_screened_in
            discovery_run.candidates_promoted = promoted
            discovery_run.notes_json = json.dumps(
                {
                    "dataset_rows": len(dataset_rows),
                    "template_codes": [template.hypothesis_code for template in templates],
                    "input_fingerprint": input_fingerprint,
                    "fingerprint_details": fingerprint_details,
                },
                sort_keys=True,
            )
            return {
                "templates_considered": len(templates),
                "candidates_generated": candidates_generated,
                "candidates_screened_in": candidates_screened_in,
                "candidates_promoted": promoted,
                "discovery_run_code": discovery_run.run_code,
                "input_fingerprint": input_fingerprint,
            }

    @staticmethod
    def _existing_candidate_hashes(sess) -> set[str]:
        hashes: set[str] = set()
        for definition in sess.scalars(select(BetaExecutionHypothesisDefinition)).all():
            provenance = _json_object(definition.provenance_json)
            candidate_hash = str(provenance.get("candidate_hash") or "").strip()
            if candidate_hash:
                hashes.add(candidate_hash)
        return hashes

    @staticmethod
    def _latest_input_fingerprint(sess) -> str | None:
        latest_run = sess.scalar(
            select(BetaExecutionHypothesisDiscoveryRun)
            .order_by(BetaExecutionHypothesisDiscoveryRun.created_at.desc())
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
        templates: list[BetaExecutionHypothesisDefinition],
        dataset_rows: list[object],
        settings: BetaSettings,
    ) -> tuple[str, dict[str, object]]:
        hasher = hashlib.sha1()
        hasher.update(str(int(settings.intraday_execution_hypothesis_history_days)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_execution_hypothesis_template_limit)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_execution_hypothesis_variant_cap)).encode("utf-8"))
        hasher.update(str(int(settings.intraday_execution_hypothesis_max_promotions_per_run)).encode("utf-8"))

        template_codes: list[str] = []
        for template in templates:
            template_payload = {
                "hypothesis_code": template.hypothesis_code,
                "signal_type": template.signal_type,
                "entry_conditions_json": template.entry_conditions_json,
                "regime_filters_json": template.regime_filters_json,
                "feature_subset_json": template.feature_subset_json,
                "metadata_json": template.metadata_json,
                "status": template.status,
            }
            template_codes.append(str(template.hypothesis_code))
            hasher.update(json.dumps(template_payload, sort_keys=True).encode("utf-8"))

        window_start = min((row.signal_time for row in dataset_rows), default=None)
        window_end = max((row.signal_time for row in dataset_rows), default=None)
        distinct_signals: set[str] = set()
        distinct_symbols: set[str] = set()
        for row in dataset_rows:
            distinct_signals.add(str(row.execution_signal_id))
            distinct_symbols.add(str(row.symbol))
            row_payload = {
                "execution_signal_id": row.execution_signal_id,
                "signal_time": row.signal_time.isoformat(),
                "symbol": row.symbol,
                "source_signal_type": row.source_signal_type,
                "event_trigger_code": row.event_trigger_code,
                "event_codes": list(row.event_codes),
                "feature_values": row.feature_values,
                "stored_action_aligned_return_pct": row.stored_action_aligned_return_pct,
                "stored_time_to_peak_minutes": row.stored_time_to_peak_minutes,
            }
            hasher.update(json.dumps(row_payload, sort_keys=True, default=str).encode("utf-8"))

        return hasher.hexdigest(), {
            "dataset_rows": len(dataset_rows),
            "distinct_signals": len(distinct_signals),
            "distinct_symbols": len(distinct_symbols),
            "template_codes": template_codes,
            "window_start": window_start.isoformat() if window_start is not None else None,
            "window_end": window_end.isoformat() if window_end is not None else None,
        }

    @staticmethod
    def _generate_candidate_specs(
        *,
        template: BetaExecutionHypothesisDefinition,
        settings: BetaSettings,
    ) -> list[dict[str, object]]:
        entry_conditions = _json_object(template.entry_conditions_json)
        regime_filters = _json_object(template.regime_filters_json)
        metadata = _json_object(template.metadata_json)
        mutable_terms = BetaExecutionHypothesisDiscoveryService._mutable_term_paths(entry_conditions)
        if not mutable_terms:
            return []

        variants: list[dict[str, object]] = []
        seen_hashes: set[str] = set()
        for path in mutable_terms:
            for mutated_leaf in BetaExecutionHypothesisDiscoveryService._mutated_leaf_variants(
                BetaExecutionHypothesisDiscoveryService._leaf_at_path(entry_conditions, path)
            ):
                mutated_conditions = deepcopy(entry_conditions)
                BetaExecutionHypothesisDiscoveryService._set_leaf_at_path(mutated_conditions, path, mutated_leaf)
                normalized_conditions = BetaHypothesisNormalizer.normalize_conditions(mutated_conditions)
                normalized_regimes = BetaHypothesisNormalizer.normalize_regime_filters(regime_filters)
                feature_subset = sorted(
                    BetaHypothesisNormalizer.extract_feature_names(normalized_conditions)
                    | BetaHypothesisNormalizer.extract_feature_names(normalized_regimes)
                )
                candidate_hash = hashlib.sha1(
                    json.dumps(
                        {
                            "signal_type": template.signal_type,
                            "entry_conditions": normalized_conditions,
                            "regime_filters": normalized_regimes,
                            "event_codes": sorted(str(code) for code in list(metadata.get("event_codes") or [])),
                            "relative_checks": list(metadata.get("relative_checks") or []),
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                if candidate_hash in seen_hashes:
                    continue
                seen_hashes.add(candidate_hash)
                suffix = candidate_hash[:10].upper()
                variants.append(
                    {
                        "candidate_hash": candidate_hash,
                        "hypothesis_code": f"{template.hypothesis_code}__GEN__{suffix}",
                        "hypothesis_name": f"{template.name} generated {suffix[:4]}",
                        "signal_type": template.signal_type,
                        "entry_conditions": normalized_conditions,
                        "regime_filters": normalized_regimes,
                        "feature_subset": feature_subset,
                        "metadata": {
                            **metadata,
                            "template_hypothesis_code": template.hypothesis_code,
                            "generated_variant": True,
                        },
                        "provenance": {
                            "source": "GENERATED",
                            "template_hypothesis_code": template.hypothesis_code,
                            "candidate_hash": candidate_hash,
                        },
                        "rationale_text": f"{template.rationale_text or template.name} Generated intraday variant.",
                    }
                )
                if len(variants) >= max(2, int(settings.intraday_execution_hypothesis_variant_cap)):
                    return variants
        return variants

    @staticmethod
    def _candidate_status(summary: dict[str, object], *, settings: BetaSettings) -> tuple[str, str]:
        support_count = int(summary.get("support_count") or 0)
        matched_instruments = int(summary.get("matched_instruments") or 0)
        adjusted_return = float(summary.get("transaction_cost_adjusted_return_pct") or 0.0)
        median_return = float(summary.get("median_return_pct") or 0.0)
        win_rate = float(summary.get("win_rate_pct") or 0.0)
        stability = float(summary.get("stability_score") or 0.0)
        baseline_edge = float(summary.get("baseline_edge_pct") or 0.0)
        if support_count < max(5, int(settings.intraday_execution_hypothesis_min_support)):
            return "REJECTED", "insufficient_support"
        if matched_instruments < max(1, int(settings.intraday_execution_hypothesis_min_matched_instruments)):
            return "REJECTED", "insufficient_instrument_breadth"
        if adjusted_return <= 0.0:
            return "REJECTED", "post_cost_edge_non_positive"
        if median_return <= 0.0:
            return "REJECTED", "median_non_positive"
        if baseline_edge < 0.0:
            return "REJECTED", "not_beating_baseline"
        if win_rate < 52.0:
            return "REJECTED", "win_rate_below_floor"
        if stability < 0.3:
            return "REJECTED", "stability_below_floor"
        return "SCREENED_IN", "screened_in"

    @staticmethod
    def _mutable_term_paths(node: dict[str, object], *, path: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        paths: list[tuple[object, ...]] = []
        for logical_key in ("all", "any"):
            raw_items = node.get(logical_key)
            if not isinstance(raw_items, list):
                continue
            for index, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    continue
                if "feature" in item and BetaExecutionHypothesisDiscoveryService._term_is_mutable(item):
                    paths.append(path + (logical_key, index))
                else:
                    paths.extend(BetaExecutionHypothesisDiscoveryService._mutable_term_paths(item, path=path + (logical_key, index)))
        return paths

    @staticmethod
    def _term_is_mutable(term: dict[str, object]) -> bool:
        op = str(term.get("op") or "")
        if op in {"gt", "lt"}:
            return isinstance(term.get("value"), (int, float))
        if op == "between":
            return isinstance(term.get("min"), (int, float)) and isinstance(term.get("max"), (int, float))
        return False

    @staticmethod
    def _leaf_at_path(node: dict[str, object], path: tuple[object, ...]) -> dict[str, object]:
        current: object = node
        cursor = 0
        while cursor < len(path):
            key = path[cursor]
            index = path[cursor + 1]
            current = (current.get(str(key)) or [])[int(index)] if isinstance(current, dict) else {}
            cursor += 2
        return dict(current) if isinstance(current, dict) else {}

    @staticmethod
    def _set_leaf_at_path(node: dict[str, object], path: tuple[object, ...], new_leaf: dict[str, object]) -> None:
        current: object = node
        cursor = 0
        while cursor < len(path) - 2:
            key = path[cursor]
            index = path[cursor + 1]
            current = (current.get(str(key)) or [])[int(index)] if isinstance(current, dict) else {}
            cursor += 2
        if not isinstance(current, dict):
            return
        key = str(path[-2])
        index = int(path[-1])
        if isinstance(current.get(key), list):
            current[key][index] = new_leaf

    @staticmethod
    def _mutated_leaf_variants(leaf: dict[str, object]) -> list[dict[str, object]]:
        op = str(leaf.get("op") or "")
        variants: list[dict[str, object]] = []
        if op in {"gt", "lt"}:
            base = float(leaf.get("value") or 0.0)
            if abs(base) < 1e-9:
                candidates = [-0.25, 0.25]
            else:
                candidates = [base * 0.75, base * 1.25]
            for value in candidates:
                mutated = dict(leaf)
                mutated["value"] = round(float(value), 4)
                variants.append(mutated)
            return variants
        if op == "between":
            lower = float(leaf.get("min") or 0.0)
            upper = float(leaf.get("max") or 0.0)
            width = max(0.2, abs(upper - lower))
            tighter = dict(leaf)
            tighter["min"] = round(lower + (width * 0.2), 4)
            tighter["max"] = round(upper - (width * 0.2), 4)
            wider = dict(leaf)
            wider["min"] = round(lower - (width * 0.2), 4)
            wider["max"] = round(upper + (width * 0.2), 4)
            return [tighter, wider]
        return variants
