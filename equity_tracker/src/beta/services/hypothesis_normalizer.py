"""Normalization and evaluation helpers for machine-testable hypothesis conditions."""

from __future__ import annotations

import json
from dataclasses import dataclass


_OP_ALIASES = {
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
    "==": "eq",
    "!=": "neq",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "eq": "eq",
    "neq": "neq",
    "between": "between",
}
_LEAF_OPS = set(_OP_ALIASES.values())


@dataclass(frozen=True)
class HypothesisMatchResult:
    matched: bool
    matched_terms: list[dict[str, object]]


def _normalize_numeric(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BetaHypothesisNormalizer:
    """Normalize and evaluate explicit hypothesis conditions."""

    @staticmethod
    def normalize_conditions(raw) -> dict[str, object]:
        return BetaHypothesisNormalizer._normalize_rule_tree(raw, allow_empty=False)

    @staticmethod
    def normalize_regime_filters(raw) -> dict[str, object]:
        if raw in (None, {}, []):
            return {}
        if isinstance(raw, list):
            return {"all": [BetaHypothesisNormalizer._normalize_rule_tree(item, allow_empty=False) for item in raw]}
        return BetaHypothesisNormalizer._normalize_rule_tree(raw, allow_empty=False)

    @staticmethod
    def canonical_json(raw) -> str:
        normalized = BetaHypothesisNormalizer._normalize_rule_tree(raw, allow_empty=True)
        return json.dumps(normalized, sort_keys=True)

    @staticmethod
    def extract_feature_names(conditions: dict[str, object]) -> set[str]:
        if not conditions:
            return set()
        if "all" in conditions:
            names: set[str] = set()
            for child in conditions.get("all", []):
                names.update(BetaHypothesisNormalizer.extract_feature_names(child))
            return names
        if "any" in conditions:
            names: set[str] = set()
            for child in conditions.get("any", []):
                names.update(BetaHypothesisNormalizer.extract_feature_names(child))
            return names
        if "not" in conditions:
            return BetaHypothesisNormalizer.extract_feature_names(conditions.get("not") or {})
        return {str(conditions.get("feature") or conditions.get("field") or "")}

    @staticmethod
    def leaf_count(conditions: dict[str, object]) -> int:
        if not conditions:
            return 0
        if "all" in conditions:
            return sum(BetaHypothesisNormalizer.leaf_count(child) for child in conditions.get("all", []))
        if "any" in conditions:
            return sum(BetaHypothesisNormalizer.leaf_count(child) for child in conditions.get("any", []))
        if "not" in conditions:
            return BetaHypothesisNormalizer.leaf_count(conditions.get("not") or {})
        return 1

    @staticmethod
    def evaluate(conditions: dict[str, object], values: dict[str, float | None]) -> HypothesisMatchResult:
        if not conditions:
            return HypothesisMatchResult(matched=False, matched_terms=[])
        matched, terms = BetaHypothesisNormalizer._evaluate_inner(conditions, values)
        return HypothesisMatchResult(matched=matched, matched_terms=terms)

    @staticmethod
    def _normalize_rule_tree(raw, *, allow_empty: bool) -> dict[str, object]:
        if raw in (None, "", [], {}):
            return {} if allow_empty else BetaHypothesisNormalizer._raise_invalid("Rule tree cannot be empty.")
        if not isinstance(raw, dict):
            raise ValueError("Hypothesis rules must be a dictionary.")

        if "all" in raw:
            children = raw.get("all") or []
            if not isinstance(children, list) or not children:
                raise ValueError("'all' conditions must be a non-empty list.")
            return {"all": [BetaHypothesisNormalizer._normalize_rule_tree(child, allow_empty=False) for child in children]}
        if "any" in raw:
            children = raw.get("any") or []
            if not isinstance(children, list) or not children:
                raise ValueError("'any' conditions must be a non-empty list.")
            return {"any": [BetaHypothesisNormalizer._normalize_rule_tree(child, allow_empty=False) for child in children]}
        if "not" in raw:
            return {"not": BetaHypothesisNormalizer._normalize_rule_tree(raw.get("not"), allow_empty=False)}

        field_name = str(raw.get("feature") or raw.get("field") or "").strip()
        raw_op = str(raw.get("op") or "").strip().lower()
        op = _OP_ALIASES.get(raw_op)
        if not field_name:
            raise ValueError("Leaf conditions must define 'feature' or 'field'.")
        if op not in _LEAF_OPS:
            raise ValueError(f"Unsupported hypothesis operator: {raw.get('op')}")

        normalized: dict[str, object] = {"feature": field_name, "op": op}
        if op == "between":
            min_value = _normalize_numeric(raw.get("min"))
            max_value = _normalize_numeric(raw.get("max"))
            if min_value is None or max_value is None:
                raise ValueError("'between' conditions require numeric 'min' and 'max'.")
            if min_value > max_value:
                min_value, max_value = max_value, min_value
            normalized["min"] = min_value
            normalized["max"] = max_value
            return normalized

        value = _normalize_numeric(raw.get("value"))
        if value is None:
            raise ValueError("Leaf conditions require a numeric 'value'.")
        normalized["value"] = value
        return normalized

    @staticmethod
    def _evaluate_inner(conditions: dict[str, object], values: dict[str, float | None]) -> tuple[bool, list[dict[str, object]]]:
        if "all" in conditions:
            matched_terms: list[dict[str, object]] = []
            for child in conditions.get("all", []):
                child_match, child_terms = BetaHypothesisNormalizer._evaluate_inner(child, values)
                matched_terms.extend(child_terms)
                if not child_match:
                    return False, matched_terms
            return True, matched_terms
        if "any" in conditions:
            all_terms: list[dict[str, object]] = []
            for child in conditions.get("any", []):
                child_match, child_terms = BetaHypothesisNormalizer._evaluate_inner(child, values)
                all_terms.extend(child_terms)
                if child_match:
                    return True, all_terms
            return False, all_terms
        if "not" in conditions:
            child_match, child_terms = BetaHypothesisNormalizer._evaluate_inner(conditions.get("not") or {}, values)
            return (not child_match), child_terms

        feature_name = str(conditions.get("feature") or conditions.get("field") or "")
        op = str(conditions.get("op") or "")
        actual_value = _normalize_numeric(values.get(feature_name))
        term_result = {
            "feature": feature_name,
            "op": op,
            "actual": actual_value,
        }
        if actual_value is None:
            term_result["matched"] = False
            return False, [term_result]

        if op == "between":
            min_value = float(conditions.get("min"))
            max_value = float(conditions.get("max"))
            matched = min_value <= actual_value <= max_value
            term_result["min"] = min_value
            term_result["max"] = max_value
            term_result["matched"] = matched
            return matched, [term_result]

        expected_value = float(conditions.get("value"))
        matched = False
        if op == "gt":
            matched = actual_value > expected_value
        elif op == "gte":
            matched = actual_value >= expected_value
        elif op == "lt":
            matched = actual_value < expected_value
        elif op == "lte":
            matched = actual_value <= expected_value
        elif op == "eq":
            matched = actual_value == expected_value
        elif op == "neq":
            matched = actual_value != expected_value

        term_result["value"] = expected_value
        term_result["matched"] = matched
        return matched, [term_result]

    @staticmethod
    def _raise_invalid(message: str) -> dict[str, object]:
        raise ValueError(message)
