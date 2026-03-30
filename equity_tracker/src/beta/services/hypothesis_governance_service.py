"""Central hard-gate evaluation for explicit beta hypotheses."""

from __future__ import annotations


class BetaHypothesisGovernanceService:
    """Evaluate whether a hypothesis is robust enough to watch or promote."""

    _MIN_SAMPLE_SIZE = 25
    _MIN_MEDIAN_EXCESS_RETURN_PCT = 0.0
    _MIN_WINSORIZED_ADJUSTED_RETURN_PCT = 0.0
    _MIN_TRIMMED_ADJUSTED_RETURN_PCT = 0.0
    _MIN_TRANSACTION_COST_ADJUSTED_RETURN_PCT = 0.0
    _MIN_BASELINE_EDGE_PCT = 0.0
    _MIN_WIN_RATE_PCT = 50.0
    _MIN_STABILITY_SCORE = 0.18
    _MIN_WALK_FORWARD_SCORE = 0.0
    _MIN_OUT_OF_SAMPLE_SCORE = 0.0
    _MAX_MEAN_MEDIAN_RATIO = 8.0
    _MAX_ROBUSTNESS_COLLAPSE_PCT = 35.0
    _MAX_TOP_TWO_POSITIVE_RETURN_SHARE = 0.6
    _MAX_WINDOW_EDGE_SHARE = 0.7
    _MIN_POSITIVE_WINDOW_RATIO = 0.5
    _MIN_REGIME_CONSISTENCY_SCORE = 0.25

    _PROMOTION_MIN_SAMPLE_SIZE = 80
    _PROMOTION_MIN_MEDIAN_EXCESS_RETURN_PCT = 0.10
    _PROMOTION_MIN_WINSORIZED_ADJUSTED_RETURN_PCT = 0.05
    _PROMOTION_MIN_TRIMMED_ADJUSTED_RETURN_PCT = 0.05
    _PROMOTION_MIN_BASELINE_EDGE_PCT = 0.02
    _PROMOTION_MIN_WIN_RATE_PCT = 52.0
    _PROMOTION_MIN_STABILITY_SCORE = 0.40
    _PROMOTION_MIN_WALK_FORWARD_SCORE = 0.05
    _PROMOTION_MIN_OUT_OF_SAMPLE_SCORE = 0.03
    _PROMOTION_MAX_MEAN_MEDIAN_RATIO = 6.0
    _PROMOTION_MAX_ROBUSTNESS_COLLAPSE_PCT = 25.0
    _PROMOTION_MAX_TOP_TWO_POSITIVE_RETURN_SHARE = 0.5
    _PROMOTION_MIN_POSITIVE_WINDOW_RATIO = 0.6
    _PROMOTION_MIN_REGIME_CONSISTENCY_SCORE = 0.40

    @staticmethod
    def _safe_float(value) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value) -> int:
        try:
            if value is None:
                return 0
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _string_list(values) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(value) for value in values if str(value).strip()]

    @staticmethod
    def evaluate_summary(summary: dict[str, object]) -> dict[str, object]:
        notes = summary.get("notes") if isinstance(summary.get("notes"), dict) else {}
        regime_slice = summary.get("regime_slice") if isinstance(summary.get("regime_slice"), dict) else {}

        sample_size = max(
            BetaHypothesisGovernanceService._safe_int(summary.get("sample_size")),
            BetaHypothesisGovernanceService._safe_int(summary.get("support_count")),
        )
        median_excess_return_pct = BetaHypothesisGovernanceService._safe_float(
            summary.get("median_excess_return_pct")
        )
        winsorized_adjusted_return_pct = BetaHypothesisGovernanceService._safe_float(
            notes.get("winsorized_adjusted_return_pct")
        )
        trimmed_adjusted_return_pct = BetaHypothesisGovernanceService._safe_float(
            notes.get("trimmed_adjusted_return_pct")
        )
        transaction_cost_adjusted_return_pct = BetaHypothesisGovernanceService._safe_float(
            summary.get("transaction_cost_adjusted_return_pct")
        )
        baseline_edge_pct = BetaHypothesisGovernanceService._safe_float(summary.get("baseline_edge_pct"))
        win_rate_pct = BetaHypothesisGovernanceService._safe_float(summary.get("win_rate_pct"))
        stability_score = BetaHypothesisGovernanceService._safe_float(summary.get("stability_score"))
        walk_forward_score = BetaHypothesisGovernanceService._safe_float(summary.get("walk_forward_score"))
        out_of_sample_score = BetaHypothesisGovernanceService._safe_float(summary.get("out_of_sample_score"))
        mean_median_ratio = BetaHypothesisGovernanceService._safe_float(notes.get("mean_median_ratio"))
        robustness_collapse_pct = BetaHypothesisGovernanceService._safe_float(
            notes.get("robustness_collapse_pct")
        )
        top_two_positive_return_share = BetaHypothesisGovernanceService._safe_float(
            notes.get("top_two_positive_return_share")
        )
        max_window_edge_share = BetaHypothesisGovernanceService._safe_float(
            notes.get("max_window_edge_share")
        )
        total_walk_forward_windows = BetaHypothesisGovernanceService._safe_int(
            notes.get("total_walk_forward_windows")
        )
        positive_window_count = BetaHypothesisGovernanceService._safe_int(
            notes.get("positive_window_count")
        )
        positive_window_ratio = (
            round(positive_window_count / total_walk_forward_windows, 4)
            if total_walk_forward_windows > 0
            else None
        )
        regime_consistency_score = BetaHypothesisGovernanceService._safe_float(
            regime_slice.get("regime_consistency_score")
        )
        evaluated_regime_bucket_count = BetaHypothesisGovernanceService._safe_int(
            regime_slice.get("evaluated_bucket_count")
        )
        failure_modes = regime_slice.get("failure_modes") if isinstance(regime_slice.get("failure_modes"), list) else []

        hard_fail_reasons: list[str] = []
        critical_fail_reasons: list[str] = []
        promotion_fail_reasons: list[str] = []

        if sample_size < BetaHypothesisGovernanceService._MIN_SAMPLE_SIZE:
            hard_fail_reasons.append("sample_size_below_floor")
        if (
            median_excess_return_pct is not None
            and median_excess_return_pct < BetaHypothesisGovernanceService._MIN_MEDIAN_EXCESS_RETURN_PCT
        ):
            hard_fail_reasons.append("median_excess_return_non_positive")
            if median_excess_return_pct <= -0.10:
                critical_fail_reasons.append("median_excess_return_strongly_negative")
        if (
            winsorized_adjusted_return_pct is not None
            and winsorized_adjusted_return_pct <= BetaHypothesisGovernanceService._MIN_WINSORIZED_ADJUSTED_RETURN_PCT
        ):
            hard_fail_reasons.append("winsorized_edge_non_positive")
        if (
            trimmed_adjusted_return_pct is not None
            and trimmed_adjusted_return_pct <= BetaHypothesisGovernanceService._MIN_TRIMMED_ADJUSTED_RETURN_PCT
        ):
            hard_fail_reasons.append("trimmed_edge_non_positive")
        if (
            transaction_cost_adjusted_return_pct is not None
            and transaction_cost_adjusted_return_pct <= BetaHypothesisGovernanceService._MIN_TRANSACTION_COST_ADJUSTED_RETURN_PCT
        ):
            hard_fail_reasons.append("cost_adjusted_edge_non_positive")
            if transaction_cost_adjusted_return_pct <= -0.10:
                critical_fail_reasons.append("cost_adjusted_edge_strongly_negative")
        if (
            baseline_edge_pct is not None
            and baseline_edge_pct <= BetaHypothesisGovernanceService._MIN_BASELINE_EDGE_PCT
        ):
            hard_fail_reasons.append("baseline_edge_non_positive")
        if win_rate_pct is not None and win_rate_pct < BetaHypothesisGovernanceService._MIN_WIN_RATE_PCT:
            hard_fail_reasons.append("win_rate_below_floor")
            if win_rate_pct < 45.0:
                critical_fail_reasons.append("win_rate_materially_below_floor")
        if stability_score is not None and stability_score < BetaHypothesisGovernanceService._MIN_STABILITY_SCORE:
            hard_fail_reasons.append("stability_below_floor")
        if (
            walk_forward_score is not None
            and walk_forward_score < BetaHypothesisGovernanceService._MIN_WALK_FORWARD_SCORE
        ):
            hard_fail_reasons.append("walk_forward_non_positive")
            if walk_forward_score <= -0.05:
                critical_fail_reasons.append("walk_forward_strongly_negative")
        if (
            out_of_sample_score is not None
            and out_of_sample_score < BetaHypothesisGovernanceService._MIN_OUT_OF_SAMPLE_SCORE
        ):
            hard_fail_reasons.append("out_of_sample_non_positive")
            if out_of_sample_score <= -0.05:
                critical_fail_reasons.append("out_of_sample_strongly_negative")
        if (
            mean_median_ratio is not None
            and mean_median_ratio > BetaHypothesisGovernanceService._MAX_MEAN_MEDIAN_RATIO
        ):
            hard_fail_reasons.append("mean_median_divergence_too_high")
            if mean_median_ratio > 12.0:
                critical_fail_reasons.append("mean_median_divergence_extreme")
        if (
            robustness_collapse_pct is not None
            and robustness_collapse_pct > BetaHypothesisGovernanceService._MAX_ROBUSTNESS_COLLAPSE_PCT
        ):
            hard_fail_reasons.append("winsorization_collapse_too_high")
            if robustness_collapse_pct > 60.0:
                critical_fail_reasons.append("winsorization_collapse_extreme")
        if (
            top_two_positive_return_share is not None
            and top_two_positive_return_share > BetaHypothesisGovernanceService._MAX_TOP_TWO_POSITIVE_RETURN_SHARE
        ):
            hard_fail_reasons.append("positive_return_concentration_too_high")
            if top_two_positive_return_share > 0.8:
                critical_fail_reasons.append("positive_return_concentration_extreme")
        if (
            total_walk_forward_windows >= 3
            and max_window_edge_share is not None
            and max_window_edge_share > BetaHypothesisGovernanceService._MAX_WINDOW_EDGE_SHARE
        ):
            hard_fail_reasons.append("edge_concentrated_in_single_window")
        if (
            total_walk_forward_windows >= 3
            and positive_window_ratio is not None
            and positive_window_ratio < BetaHypothesisGovernanceService._MIN_POSITIVE_WINDOW_RATIO
        ):
            hard_fail_reasons.append("too_few_positive_walk_forward_windows")
        if (
            evaluated_regime_bucket_count >= 2
            and regime_consistency_score is not None
            and regime_consistency_score < BetaHypothesisGovernanceService._MIN_REGIME_CONSISTENCY_SCORE
        ):
            hard_fail_reasons.append("regime_consistency_too_low")

        if sample_size < BetaHypothesisGovernanceService._PROMOTION_MIN_SAMPLE_SIZE:
            promotion_fail_reasons.append("promotion_sample_size_below_floor")
        if (
            median_excess_return_pct is not None
            and median_excess_return_pct < BetaHypothesisGovernanceService._PROMOTION_MIN_MEDIAN_EXCESS_RETURN_PCT
        ):
            promotion_fail_reasons.append("promotion_median_excess_return_too_low")
        if (
            winsorized_adjusted_return_pct is not None
            and winsorized_adjusted_return_pct < BetaHypothesisGovernanceService._PROMOTION_MIN_WINSORIZED_ADJUSTED_RETURN_PCT
        ):
            promotion_fail_reasons.append("promotion_winsorized_edge_too_low")
        if (
            trimmed_adjusted_return_pct is not None
            and trimmed_adjusted_return_pct < BetaHypothesisGovernanceService._PROMOTION_MIN_TRIMMED_ADJUSTED_RETURN_PCT
        ):
            promotion_fail_reasons.append("promotion_trimmed_edge_too_low")
        if (
            baseline_edge_pct is not None
            and baseline_edge_pct < BetaHypothesisGovernanceService._PROMOTION_MIN_BASELINE_EDGE_PCT
        ):
            promotion_fail_reasons.append("promotion_baseline_edge_too_low")
        if (
            win_rate_pct is not None
            and win_rate_pct < BetaHypothesisGovernanceService._PROMOTION_MIN_WIN_RATE_PCT
        ):
            promotion_fail_reasons.append("promotion_win_rate_too_low")
        if (
            stability_score is not None
            and stability_score < BetaHypothesisGovernanceService._PROMOTION_MIN_STABILITY_SCORE
        ):
            promotion_fail_reasons.append("promotion_stability_too_low")
        if (
            walk_forward_score is not None
            and walk_forward_score < BetaHypothesisGovernanceService._PROMOTION_MIN_WALK_FORWARD_SCORE
        ):
            promotion_fail_reasons.append("promotion_walk_forward_too_low")
        if (
            out_of_sample_score is not None
            and out_of_sample_score < BetaHypothesisGovernanceService._PROMOTION_MIN_OUT_OF_SAMPLE_SCORE
        ):
            promotion_fail_reasons.append("promotion_out_of_sample_too_low")
        if (
            mean_median_ratio is not None
            and mean_median_ratio > BetaHypothesisGovernanceService._PROMOTION_MAX_MEAN_MEDIAN_RATIO
        ):
            promotion_fail_reasons.append("promotion_mean_median_divergence_too_high")
        if (
            robustness_collapse_pct is not None
            and robustness_collapse_pct > BetaHypothesisGovernanceService._PROMOTION_MAX_ROBUSTNESS_COLLAPSE_PCT
        ):
            promotion_fail_reasons.append("promotion_winsorization_collapse_too_high")
        if (
            top_two_positive_return_share is not None
            and top_two_positive_return_share > BetaHypothesisGovernanceService._PROMOTION_MAX_TOP_TWO_POSITIVE_RETURN_SHARE
        ):
            promotion_fail_reasons.append("promotion_positive_return_concentration_too_high")
        if (
            total_walk_forward_windows >= 3
            and positive_window_ratio is not None
            and positive_window_ratio < BetaHypothesisGovernanceService._PROMOTION_MIN_POSITIVE_WINDOW_RATIO
        ):
            promotion_fail_reasons.append("promotion_positive_window_ratio_too_low")
        if (
            evaluated_regime_bucket_count >= 2
            and regime_consistency_score is not None
            and regime_consistency_score < BetaHypothesisGovernanceService._PROMOTION_MIN_REGIME_CONSISTENCY_SCORE
        ):
            promotion_fail_reasons.append("promotion_regime_consistency_too_low")

        watch_eligible = not hard_fail_reasons
        promotion_eligible = watch_eligible and not promotion_fail_reasons
        severity = "PASS"
        if not watch_eligible:
            severity = "REJECTED" if critical_fail_reasons else "DEGRADED"

        return {
            "watch_eligible": watch_eligible,
            "promotion_eligible": promotion_eligible,
            "severity": severity,
            "hard_fail_reasons": hard_fail_reasons,
            "critical_fail_reasons": critical_fail_reasons,
            "promotion_fail_reasons": promotion_fail_reasons,
            "gate_metrics": {
                "sample_size": sample_size,
                "median_excess_return_pct": median_excess_return_pct,
                "winsorized_adjusted_return_pct": winsorized_adjusted_return_pct,
                "trimmed_adjusted_return_pct": trimmed_adjusted_return_pct,
                "transaction_cost_adjusted_return_pct": transaction_cost_adjusted_return_pct,
                "baseline_edge_pct": baseline_edge_pct,
                "win_rate_pct": win_rate_pct,
                "stability_score": stability_score,
                "walk_forward_score": walk_forward_score,
                "out_of_sample_score": out_of_sample_score,
                "mean_median_ratio": mean_median_ratio,
                "robustness_collapse_pct": robustness_collapse_pct,
                "top_two_positive_return_share": top_two_positive_return_share,
                "max_window_edge_share": max_window_edge_share,
                "positive_window_ratio": positive_window_ratio,
                "regime_consistency_score": regime_consistency_score,
                "evaluated_regime_bucket_count": evaluated_regime_bucket_count,
            },
            "failure_modes": failure_modes if isinstance(failure_modes, list) else [],
        }
