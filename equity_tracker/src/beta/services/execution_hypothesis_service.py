"""Seed and evaluate explicit intraday execution hypotheses."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from ..context import BetaContext
from ..db.models import BetaExecutionHypothesisDefinition
from ..settings import BetaSettings
from .hypothesis_normalizer import BetaHypothesisNormalizer


class BetaExecutionHypothesisService:
    """Execution-only hypothesis definitions and event trigger evaluation."""

    _ALLOWED_SIGNAL_TYPES = {
        "HOLD_THROUGH_NOISE",
        "TRIM_ON_STRENGTH",
        "SELL_INTO_REBOUND",
        "AVOID_SELLING_INTO_PANIC",
        "WAIT_FOR_CLOSE_CONFIRMATION",
        "NO_ACTION",
    }
    _ACTION_GUIDANCE_BY_SIGNAL_TYPE: dict[str, dict[str, dict[str, str]]] = {
        "HOLD_THROUGH_NOISE": {
            "held": {
                "recommended_action_side": "BUY",
                "recommended_action_code": "ADD",
                "recommended_action_label": "Add on supportive pullback",
            },
            "other": {
                "recommended_action_side": "BUY",
                "recommended_action_code": "ENTER",
                "recommended_action_label": "Buy constructive continuation",
            },
        },
        "AVOID_SELLING_INTO_PANIC": {
            "held": {
                "recommended_action_side": "BUY",
                "recommended_action_code": "ADD",
                "recommended_action_label": "Add into panic recovery",
            },
            "other": {
                "recommended_action_side": "BUY",
                "recommended_action_code": "ENTER",
                "recommended_action_label": "Buy panic recovery",
            },
        },
        "TRIM_ON_STRENGTH": {
            "held": {
                "recommended_action_side": "SELL",
                "recommended_action_code": "TRIM",
                "recommended_action_label": "Trim into strength",
            },
            "other": {
                "recommended_action_side": "WAIT",
                "recommended_action_code": "AVOID_ENTRY",
                "recommended_action_label": "Avoid chasing overextended strength",
            },
        },
        "SELL_INTO_REBOUND": {
            "held": {
                "recommended_action_side": "SELL",
                "recommended_action_code": "EXIT",
                "recommended_action_label": "Sell into rebound",
            },
            "other": {
                "recommended_action_side": "WAIT",
                "recommended_action_code": "AVOID_ENTRY",
                "recommended_action_label": "Avoid weak rebound entry",
            },
        },
        "WAIT_FOR_CLOSE_CONFIRMATION": {
            "held": {
                "recommended_action_side": "WAIT",
                "recommended_action_code": "CONFIRM",
                "recommended_action_label": "Wait for close confirmation",
            },
            "other": {
                "recommended_action_side": "WAIT",
                "recommended_action_code": "CONFIRM",
                "recommended_action_label": "Wait for close confirmation",
            },
        },
        "NO_ACTION": {
            "held": {
                "recommended_action_side": "HOLD",
                "recommended_action_code": "NO_ACTION",
                "recommended_action_label": "No trade action",
            },
            "other": {
                "recommended_action_side": "WAIT",
                "recommended_action_code": "NO_ACTION",
                "recommended_action_label": "No trade action",
            },
        },
    }

    @staticmethod
    def ensure_default_definitions() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"definitions_added": 0, "definitions_updated": 0, "definition_count": 0}

        definitions_added = 0
        definitions_updated = 0
        with BetaContext.write_session() as sess:
            specs = BetaExecutionHypothesisService._load_json_config("execution_hypothesis_definitions.json")
            existing = {
                row.hypothesis_code: row
                for row in sess.scalars(select(BetaExecutionHypothesisDefinition)).all()
            }
            for spec in specs:
                signal_type = str(spec.get("signal_type") or "NO_ACTION")
                if signal_type not in BetaExecutionHypothesisService._ALLOWED_SIGNAL_TYPES:
                    continue
                normalized_conditions = BetaHypothesisNormalizer.normalize_conditions(spec.get("entry_conditions") or {})
                regime_filters = BetaHypothesisNormalizer.normalize_regime_filters(spec.get("regime_filters") or {})
                feature_subset = sorted(
                    BetaHypothesisNormalizer.extract_feature_names(normalized_conditions)
                    | BetaHypothesisNormalizer.extract_feature_names(regime_filters)
                )
                row = existing.get(str(spec["hypothesis_code"]))
                if row is None:
                    row = BetaExecutionHypothesisDefinition(
                        hypothesis_code=str(spec["hypothesis_code"]),
                        name=str(spec["name"]),
                        signal_type=signal_type,
                        entry_conditions_json=json.dumps(normalized_conditions, sort_keys=True),
                        regime_filters_json=json.dumps(regime_filters, sort_keys=True) if regime_filters else json.dumps({}, sort_keys=True),
                        feature_subset_json=json.dumps(feature_subset, sort_keys=True),
                        rationale_text=str(spec.get("rationale") or ""),
                        source_type=str(spec.get("source_type") or "MANUAL"),
                        metadata_json=json.dumps(spec.get("metadata") or {}, sort_keys=True),
                        provenance_json=json.dumps({"source": spec.get("source_type") or "MANUAL"}, sort_keys=True),
                        status="ACTIVE",
                    )
                    sess.add(row)
                    definitions_added += 1
                else:
                    row.name = str(spec["name"])
                    row.signal_type = signal_type
                    row.entry_conditions_json = json.dumps(normalized_conditions, sort_keys=True)
                    row.regime_filters_json = json.dumps(regime_filters, sort_keys=True) if regime_filters else json.dumps({}, sort_keys=True)
                    row.feature_subset_json = json.dumps(feature_subset, sort_keys=True)
                    row.rationale_text = str(spec.get("rationale") or "")
                    row.source_type = str(spec.get("source_type") or row.source_type or "MANUAL")
                    row.metadata_json = json.dumps(spec.get("metadata") or {}, sort_keys=True)
                    row.provenance_json = json.dumps({"source": spec.get("source_type") or row.source_type}, sort_keys=True)
                    row.status = "ACTIVE"
                    definitions_updated += 1
            definition_count = int(
                sess.scalar(select(func.count()).select_from(BetaExecutionHypothesisDefinition)) or 0
            )
        return {
            "definitions_added": definitions_added,
            "definitions_updated": definitions_updated,
            "definition_count": definition_count,
        }

    @staticmethod
    def active_definitions(sess) -> list[BetaExecutionHypothesisDefinition]:
        return list(
            sess.scalars(
                select(BetaExecutionHypothesisDefinition)
                .where(BetaExecutionHypothesisDefinition.status == "ACTIVE")
                .order_by(BetaExecutionHypothesisDefinition.created_at.asc())
            ).all()
        )

    @staticmethod
    def detect_event_triggers(
        *,
        feature_values: dict[str, float | None],
        settings: BetaSettings,
    ) -> list[str]:
        triggers: list[str] = []
        for spec in BetaExecutionHypothesisService._load_json_config("execution_event_triggers.json"):
            conditions = dict(spec.get("conditions") or {})
            event_code = spec.get("event_code")
            if event_code == "EVENT_VOLATILITY_EXPANSION":
                conditions = {
                    "all": [
                        {
                            "feature": "rolling_intraday_vol_15m_pct",
                            "op": "gt",
                            "value": float(settings.intraday_volatility_expansion_threshold_pct),
                        }
                    ]
                }
            elif event_code == "EVENT_OPEN_GAP":
                threshold = float(settings.intraday_gap_event_threshold_pct)
                conditions = {
                    "any": [
                        {"feature": "gap_from_prev_close_pct", "op": "gt", "value": threshold},
                        {"feature": "gap_from_prev_close_pct", "op": "lt", "value": -threshold},
                    ]
                }
            elif event_code == "EVENT_LARGE_INTRADAY_MOVE":
                threshold = float(settings.intraday_large_move_event_threshold_pct)
                conditions = {
                    "any": [
                        {"feature": "return_last_15m_pct", "op": "gt", "value": threshold},
                        {"feature": "return_last_15m_pct", "op": "lt", "value": -threshold},
                    ]
                }
            elif event_code == "EVENT_REVERSAL_SPIKE":
                threshold = float(settings.intraday_reversal_event_threshold_pct)
                conditions = {
                    "any": [
                        {"feature": "reversal_from_low_15m_pct", "op": "gt", "value": threshold},
                        {"feature": "reversal_from_high_15m_pct", "op": "gt", "value": threshold},
                    ]
                }
            normalized = BetaHypothesisNormalizer.normalize_regime_filters(conditions)
            if normalized and BetaHypothesisNormalizer.evaluate(normalized, feature_values).matched:
                triggers.append(str(spec["event_code"]))
        return triggers

    @staticmethod
    def evaluate_definition(
        *,
        definition: BetaExecutionHypothesisDefinition,
        feature_values: dict[str, float | None],
        event_codes: list[str],
    ) -> dict[str, object] | None:
        entry_conditions = BetaExecutionHypothesisService._json_object(definition.entry_conditions_json)
        regime_filters = BetaExecutionHypothesisService._json_object(definition.regime_filters_json)
        condition_match = BetaHypothesisNormalizer.evaluate(entry_conditions, feature_values)
        if not condition_match.matched:
            return None
        if regime_filters and not BetaHypothesisNormalizer.evaluate(regime_filters, feature_values).matched:
            return None
        metadata = BetaExecutionHypothesisService._json_object(definition.metadata_json)
        if not BetaExecutionHypothesisService._relative_checks_pass(
            checks=list(metadata.get("relative_checks") or []),
            feature_values=feature_values,
        ):
            return None
        confidence = BetaExecutionHypothesisService._confidence(
            metadata=metadata,
            event_codes=event_codes,
        )
        return {
            "definition_id": definition.id,
            "hypothesis_code": definition.hypothesis_code,
            "name": definition.name,
            "signal_type": definition.signal_type,
            "confidence_score": confidence,
            "rationale_text": definition.rationale_text or definition.name,
            "matched_conditions": condition_match.matched_terms,
            "event_codes": event_codes,
        }

    @staticmethod
    def action_guidance(
        *,
        definition: BetaExecutionHypothesisDefinition | None,
        signal_type: str | None,
        priority_tier: str | None = None,
        position_source: str | None = None,
    ) -> dict[str, str]:
        normalized_signal = str(signal_type or "NO_ACTION").strip().upper()
        context_key = "held" if BetaExecutionHypothesisService._held_context(
            priority_tier=priority_tier,
            position_source=position_source,
        ) else "other"
        default_guidance = (
            BetaExecutionHypothesisService._ACTION_GUIDANCE_BY_SIGNAL_TYPE.get(normalized_signal)
            or BetaExecutionHypothesisService._ACTION_GUIDANCE_BY_SIGNAL_TYPE["NO_ACTION"]
        )
        selected = dict(default_guidance.get(context_key) or default_guidance.get("other") or {})
        metadata = BetaExecutionHypothesisService._json_object(definition.metadata_json) if definition is not None else {}
        side = str(
            metadata.get(f"action_side_{context_key}")
            or metadata.get("action_side")
            or selected.get("recommended_action_side")
            or "WAIT"
        ).strip().upper()
        code = str(
            metadata.get(f"action_code_{context_key}")
            or metadata.get("action_code")
            or selected.get("recommended_action_code")
            or "NO_ACTION"
        ).strip().upper()
        label = str(
            metadata.get(f"action_label_{context_key}")
            or metadata.get("action_label")
            or selected.get("recommended_action_label")
            or "No trade action"
        ).strip()
        return {
            "recommended_action_side": side,
            "recommended_action_code": code,
            "recommended_action_label": label,
        }

    @staticmethod
    def _confidence(*, metadata: dict[str, object], event_codes: list[str]) -> float:
        base_confidence = float(metadata.get("base_confidence") or 0.5)
        configured_events = [str(code) for code in metadata.get("event_codes", []) if str(code).strip()]
        overlap_bonus = 0.05 if configured_events and any(code in event_codes for code in configured_events) else 0.0
        return min(0.85, max(0.35, round(base_confidence + overlap_bonus, 4)))

    @staticmethod
    def _held_context(*, priority_tier: str | None, position_source: str | None) -> bool:
        if str(priority_tier or "").strip().upper() == "HELD":
            return True
        return str(position_source or "").strip().upper() in {"MANUAL", "DEMO"}

    @staticmethod
    def _relative_checks_pass(
        *,
        checks: list[dict[str, object]],
        feature_values: dict[str, float | None],
    ) -> bool:
        for check in checks:
            left = feature_values.get(str(check.get("left_feature") or ""))
            right = feature_values.get(str(check.get("right_feature") or ""))
            if left is None or right is None:
                return False
            offset = float(check.get("offset") or 0.0)
            op = str(check.get("op") or "")
            if op == "lt_feature" and not (float(left) < float(right)):
                return False
            if op == "gt_feature" and not (float(left) > float(right)):
                return False
            if op == "gt_offset_feature" and not (float(left) > (float(right) + offset)):
                return False
            if op == "lt_offset_feature" and not (float(left) < (float(right) + offset)):
                return False
        return True

    @staticmethod
    def _json_object(payload: str | None) -> dict[str, object]:
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _load_json_config(filename: str) -> list[dict[str, object]]:
        path = Path(__file__).resolve().parent.parent / "config" / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to load execution config '{filename}': {exc}") from exc
        if not isinstance(payload, list):
            raise RuntimeError(f"Execution config '{filename}' must contain a JSON list.")
        return [row for row in payload if isinstance(row, dict)]
