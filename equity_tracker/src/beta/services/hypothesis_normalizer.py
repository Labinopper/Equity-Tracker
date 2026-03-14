"""Normalization and evaluation helpers for machine-testable hypothesis conditions."""

from __future__ import annotations

from dataclasses import dataclass


_ALLOWED_COMPARE_OPS = {"<", "<=", ">", ">=", "==", "!=", "between"}


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
    """Normalize JSON-like condition payloads into a compact condition AST."""

    @staticmethod
    def normalize_conditions(raw) -> dict[str, object]:
        if not raw:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("Hypothesis conditions must be a dictionary.")

        if "all" in raw:
            children = raw.get("all") or []
            if not isinstance(children, list):
                raise ValueError("'all' conditions must be a list.")
            return {"all": [BetaHypothesisNormalizer.normalize_conditions(child) for child in children]}
        if "any" in raw:
            children = raw.get("any") or []
            if not isinstance(children, list):
                raise ValueError("'any' conditions must be a list.")
            return {"any": [BetaHypothesisNormalizer.normalize_conditions(child) for child in children]}
        if "not" in raw:
            return {"not": BetaHypothesisNormalizer.normalize_conditions(raw.get("not"))}

        field_name = str(raw.get("feature") or raw.get("field") or "").strip()
        op = str(raw.get("op") or "").strip()
        if not field_name:
            raise ValueError("Leaf conditions must define 'feature' or 'field'.")
        if op not in _ALLOWED_COMPARE_OPS:
            raise ValueError(f"Unsupported hypothesis operator: {op}")

        normalized: dict[str, object] = {"feature": field_name, "op": op}
        if op == "between":
            min_value = _normalize_numeric(raw.get("min"))
            max_value = _normalize_numeric(raw.get("max"))
            if min_value is None or max_value is None:
                raise ValueError("'between' conditions require numeric 'min' and 'max'.")
            normalized["min"] = min_value
            normalized["max"] = max_value
            return normalized

        value = _normalize_numeric(raw.get("value"))
        if value is None:
            raise ValueError("Leaf conditions require a numeric 'value'.")
        normalized["value"] = value
        return normalized

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
    def evaluate(conditions: dict[str, object], values: dict[str, float | None]) -> HypothesisMatchResult:
        if not conditions:
            return HypothesisMatchResult(matched=False, matched_terms=[])
        matched, terms = BetaHypothesisNormalizer._evaluate_inner(conditions, values)
        return HypothesisMatchResult(matched=matched, matched_terms=terms)

    @staticmethod
    def _evaluate_inner(conditions: dict[str, object], values: dict[str, float | None]) -> tuple[bool, list[dict[str, object]]]:
        if "all" in conditions:
            matched_terms: list[dict[str, object]] = []
            for child in conditions.get("all", []):
                child_match, child_terms = BetaHypothesisNormalizer._evaluate_inner(child, values)
                if not child_match:
                    return False, matched_terms + child_terms
                matched_terms.extend(child_terms)
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
        if op == ">":
            matched = actual_value > expected_value
        elif op == ">=":
            matched = actual_value >= expected_value
        elif op == "<":
            matched = actual_value < expected_value
        elif op == "<=":
            matched = actual_value <= expected_value
        elif op == "==":
            matched = actual_value == expected_value
        elif op == "!=":
            matched = actual_value != expected_value

        term_result["value"] = expected_value
        term_result["matched"] = matched
        return matched, [term_result]
