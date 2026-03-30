"""Historical hypothesis backtesting over the existing beta feature and label stores."""

from __future__ import annotations

import json
from random import Random
from statistics import median, pstdev

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaDatasetVersion,
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaHypothesisDefinition,
    BetaHypothesisTestRun,
    BetaInstrument,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaValidationRun,
)
from ..settings import BetaSettings
from ..state import get_beta_db_path
from .hypothesis_governance_service import BetaHypothesisGovernanceService
from .hypothesis_normalizer import BetaHypothesisNormalizer


class BetaHypothesisBacktestService:
    """Backtest explicit hypothesis definitions against the stored feature/label history."""

    _ACTIVE_STATUSES = (
        "DISCOVERED",
        "SCREENED_IN",
        "CANDIDATE",
        "PROMISING",
        "VALIDATED",
        "DEGRADED",
        "REJECTED",
    )
    _MIN_SAMPLE_SIZE = 25
    _TRANSACTION_COST_MULTIPLIER = 2.0

    @staticmethod
    def _align_return(*, expected_direction: str, raw_return_pct: float) -> float:
        if expected_direction in {"BEARISH", "RISK_OFF"}:
            return raw_return_pct * -1.0
        return raw_return_pct

    @staticmethod
    def refresh_backtests() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"definitions_considered": 0, "test_runs_written": 0}

        with BetaContext.write_session() as sess:
            beta_db_path = get_beta_db_path()
            settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()
            definitions = list(
                sess.scalars(
                    select(BetaHypothesisDefinition).where(
                        BetaHypothesisDefinition.status.in_(BetaHypothesisBacktestService._ACTIVE_STATUSES)
                    )
                ).all()
            )
            if not definitions:
                return {"definitions_considered": 0, "test_runs_written": 0}

            feature_names = sorted(
                {
                    feature_name
                    for definition in definitions
                    for feature_name in (
                        BetaHypothesisNormalizer.extract_feature_names(
                            BetaHypothesisNormalizer.normalize_conditions(
                                json.loads(definition.entry_conditions_json or "{}")
                            )
                        )
                        | BetaHypothesisNormalizer.extract_feature_names(
                            BetaHypothesisNormalizer.normalize_regime_filters(
                                json.loads(definition.regime_filters_json or "{}")
                            )
                        )
                    )
                }
            )
            feature_defs = {
                row.feature_name: row
                for row in sess.scalars(
                    select(BetaFeatureDefinition).where(BetaFeatureDefinition.feature_name.in_(feature_names or [""]))
                ).all()
            }
            label_defs = {
                row.label_name: row
                for row in sess.scalars(select(BetaLabelDefinition)).all()
            }
            latest_dataset = sess.scalar(
                select(BetaDatasetVersion).order_by(desc(BetaDatasetVersion.created_at)).limit(1)
            )
            latest_validation = sess.scalar(
                select(BetaValidationRun).order_by(desc(BetaValidationRun.created_at)).limit(1)
            )
            instruments = {
                row.id: row
                for row in sess.scalars(select(BetaInstrument)).all()
            }

            feature_snapshots: dict[tuple[str, object], dict[str, float]] = {}
            feature_name_by_id = {row.id: row.feature_name for row in feature_defs.values()}
            for instrument_id, feature_date, feature_definition_id, value_numeric in sess.execute(
                select(
                    BetaFeatureValue.instrument_id,
                    BetaFeatureValue.feature_date,
                    BetaFeatureValue.feature_definition_id,
                    BetaFeatureValue.value_numeric,
                ).where(
                    BetaFeatureValue.feature_definition_id.in_(list(feature_name_by_id.keys()) or [""]),
                    BetaFeatureValue.value_numeric.is_not(None),
                )
            ):
                feature_name = feature_name_by_id.get(feature_definition_id)
                if feature_name is None or value_numeric is None:
                    continue
                feature_snapshots.setdefault((instrument_id, feature_date), {})[feature_name] = float(value_numeric)

            labels_by_metric: dict[str, dict[tuple[str, object], float]] = {}
            label_name_by_id = {row.id: label_name for label_name, row in label_defs.items()}
            for instrument_id, decision_date, label_definition_id, value_numeric in sess.execute(
                select(
                    BetaLabelValue.instrument_id,
                    BetaLabelValue.decision_date,
                    BetaLabelValue.label_definition_id,
                    BetaLabelValue.value_numeric,
                ).where(
                    BetaLabelValue.label_definition_id.in_(list(label_name_by_id.keys()) or [""]),
                    BetaLabelValue.value_numeric.is_not(None),
                )
            ):
                label_name = label_name_by_id.get(label_definition_id)
                if label_name is None or value_numeric is None:
                    continue
                labels_by_metric.setdefault(label_name, {})[(instrument_id, decision_date)] = float(value_numeric)

            test_runs_written = 0
            for definition in definitions:
                conditions = BetaHypothesisNormalizer.normalize_conditions(
                    json.loads(definition.entry_conditions_json or "{}")
                )
                regime_filters = BetaHypothesisNormalizer.normalize_regime_filters(
                    json.loads(definition.regime_filters_json or "{}")
                )
                universe = BetaHypothesisBacktestService._json_object(definition.universe_json)
                target_metric = str(definition.target_metric or "fwd_5d_excess_return_pct")
                label_values = labels_by_metric.get(target_metric, {})
                feature_subset = set(
                    BetaHypothesisNormalizer.extract_feature_names(conditions)
                    | BetaHypothesisNormalizer.extract_feature_names(regime_filters)
                )

                matched_samples: list[tuple[str, object, float]] = []
                eligible_samples: list[tuple[str, object, float]] = []
                matched_feature_rows: list[dict[str, float]] = []
                for (instrument_id, decision_date), snapshot in feature_snapshots.items():
                    instrument = instruments.get(instrument_id)
                    if instrument is None:
                        continue
                    if not BetaHypothesisBacktestService._universe_match(universe, instrument):
                        continue
                    if not feature_subset.issubset(snapshot.keys()):
                        continue
                    label_value = label_values.get((instrument_id, decision_date))
                    if label_value is None:
                        continue
                    if regime_filters and not BetaHypothesisNormalizer.evaluate(regime_filters, snapshot).matched:
                        continue
                    eligible_samples.append((instrument_id, decision_date, label_value))
                    if BetaHypothesisNormalizer.evaluate(conditions, snapshot).matched:
                        matched_samples.append((instrument_id, decision_date, label_value))
                        matched_feature_rows.append(dict(snapshot))

                summary = BetaHypothesisBacktestService._summarize_samples(
                    matched_samples=matched_samples,
                    eligible_samples=eligible_samples,
                    expected_direction=str(definition.expected_direction or "BULLISH"),
                    settings=settings,
                    matched_feature_rows=matched_feature_rows,
                )

                test_run = BetaHypothesisTestRun(
                    hypothesis_definition_id=definition.id,
                    dataset_version_id=latest_dataset.id if latest_dataset is not None else None,
                    validation_run_id=latest_validation.id if latest_validation is not None else None,
                    baseline_name="UNCONDITIONAL_UNIVERSE_MEAN",
                    test_start_date=summary["test_start_date"],
                    test_end_date=summary["test_end_date"],
                    sample_size=summary["sample_size"],
                    support_count=summary["support_count"],
                    matched_instruments=summary["matched_instruments"],
                    average_target_return_pct=summary["average_target_return_pct"],
                    average_excess_return_pct=summary["average_excess_return_pct"],
                    median_excess_return_pct=summary["median_excess_return_pct"],
                    average_return_pct=summary["average_return_pct"],
                    median_return_pct=summary["median_return_pct"],
                    win_rate_pct=summary["win_rate_pct"],
                    outcome_volatility_pct=summary["outcome_volatility_pct"],
                    max_drawdown_pct=summary["max_drawdown_pct"],
                    baseline_return_pct=summary["baseline_return_pct"],
                    baseline_edge_pct=summary["baseline_edge_pct"],
                    baseline_sign_accuracy_pct=summary["baseline_sign_accuracy_pct"],
                    transaction_cost_bps=summary["transaction_cost_bps"],
                    transaction_cost_adjusted_return_pct=summary["transaction_cost_adjusted_return_pct"],
                    walk_forward_score=summary["walk_forward_score"],
                    out_of_sample_score=summary["out_of_sample_score"],
                    stability_score=summary["stability_score"],
                    regime_slice_json=json.dumps(summary["regime_slice"], sort_keys=True),
                    notes_json=json.dumps(summary["notes"], sort_keys=True),
                )
                sess.add(test_run)
                test_runs_written += 1

            return {
                "definitions_considered": len(definitions),
                "test_runs_written": test_runs_written,
                "feature_pool_size": len(feature_snapshots),
            }

    @staticmethod
    def _json_object(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _universe_match(universe: dict[str, object], instrument: BetaInstrument) -> bool:
        markets = universe.get("markets")
        if isinstance(markets, list) and markets:
            if str(instrument.market or "OTHER") not in {str(item) for item in markets}:
                return False
        if bool(universe.get("core_only")) and instrument.core_security_id is None:
            return False
        return True

    @staticmethod
    def summarize_candidate_samples(
        *,
        matched_samples: list[tuple[str, object, float]],
        eligible_samples: list[tuple[str, object, float]],
        expected_direction: str,
        settings: BetaSettings,
        matched_feature_rows: list[dict[str, float]] | None = None,
    ) -> dict[str, object]:
        return BetaHypothesisBacktestService._summarize_samples(
            matched_samples=matched_samples,
            eligible_samples=eligible_samples,
            expected_direction=expected_direction,
            settings=settings,
            matched_feature_rows=matched_feature_rows,
        )

    @staticmethod
    def _summarize_samples(
        *,
        matched_samples: list[tuple[str, object, float]],
        eligible_samples: list[tuple[str, object, float]],
        expected_direction: str,
        settings: BetaSettings,
        matched_feature_rows: list[dict[str, float]] | None = None,
    ) -> dict[str, object]:
        raw_sample_values = [value for _instrument_id, _decision_date, value in matched_samples]
        sample_values = [
            BetaHypothesisBacktestService._align_return(
                expected_direction=expected_direction,
                raw_return_pct=value,
            )
            for value in raw_sample_values
        ]
        sample_dates = [decision_date for _instrument_id, decision_date, _value in matched_samples]
        support_count = len(sample_values)
        matched_instruments = len({instrument_id for instrument_id, _decision_date, _value in matched_samples})
        sample_span_days = (
            int((max(sample_dates) - min(sample_dates)).days)
            if len(sample_dates) >= 2
            else 0
        )

        raw_eligible_values = [value for _instrument_id, _decision_date, value in eligible_samples]
        aligned_eligible_values = [
            BetaHypothesisBacktestService._align_return(
                expected_direction=expected_direction,
                raw_return_pct=value,
            )
            for value in raw_eligible_values
        ]
        baseline_return_pct = round(
            sum(aligned_eligible_values) / len(aligned_eligible_values),
            4,
        ) if aligned_eligible_values else 0.0
        baseline_sign_accuracy_pct = round(
            (len([value for value in aligned_eligible_values if value > 0]) / len(aligned_eligible_values)) * 100.0,
            2,
        ) if aligned_eligible_values else 0.0
        win_rate_pct = round(
            (len([value for value in sample_values if value > 0]) / len(sample_values)) * 100.0,
            2,
        ) if sample_values else None

        average_target_return_pct = round(sum(raw_sample_values) / len(raw_sample_values), 4) if raw_sample_values else None
        average_excess_return_pct = round(sum(sample_values) / len(sample_values), 4) if sample_values else None
        median_excess_return_pct = round(float(median(sample_values)), 4) if sample_values else None
        average_return_pct = average_excess_return_pct
        median_return_pct = median_excess_return_pct
        median_ci_low_pct, median_ci_high_pct = BetaHypothesisBacktestService._bootstrap_median_interval(sample_values)

        # Parallel return profiles for robustness comparison
        winsorize_cap = 200.0
        winsorized_values = [max(-winsorize_cap, min(winsorize_cap, v)) for v in sample_values] if sample_values else []
        winsorized_avg = round(sum(winsorized_values) / len(winsorized_values), 4) if winsorized_values else 0.0
        sorted_for_trim = sorted(sample_values)
        trim_count = max(1, int(len(sorted_for_trim) * 0.025))
        trimmed_values = (
            sorted_for_trim[trim_count:-trim_count]
            if len(sorted_for_trim) > trim_count * 2
            else sorted_for_trim
        )
        trimmed_avg = round(sum(trimmed_values) / len(trimmed_values), 4) if trimmed_values else 0.0
        raw_avg = average_excess_return_pct or 0.0
        robustness_collapse_pct = round(
            ((raw_avg - winsorized_avg) / raw_avg * 100.0) if abs(raw_avg) > 0.01 else 0.0,
            4,
        )
        _abs_median = abs(median_excess_return_pct or 0.0)
        mean_median_ratio = round(abs(raw_avg) / max(_abs_median, 0.5), 4)
        positive_values = sorted([value for value in sample_values if value > 0.0], reverse=True)
        total_positive_return = sum(positive_values)
        top_two_positive_return_share = round(
            (sum(positive_values[:2]) / total_positive_return) if total_positive_return > 0.01 else 0.0,
            4,
        )

        outcome_volatility_pct = round(pstdev(sample_values), 4) if len(sample_values) > 1 else (0.0 if sample_values else None)
        transaction_cost_bps = float(
            max(settings.uk_equity_friction_bps, settings.us_equity_friction_bps)
            * BetaHypothesisBacktestService._TRANSACTION_COST_MULTIPLIER
        )
        transaction_cost_adjusted_return_pct = (
            round((average_excess_return_pct or 0.0) - (transaction_cost_bps / 100.0), 4)
            if average_excess_return_pct is not None
            else None
        )
        winsorized_adjusted_return_pct = round(winsorized_avg - (transaction_cost_bps / 100.0), 4) if winsorized_values else None
        trimmed_adjusted_return_pct = round(trimmed_avg - (transaction_cost_bps / 100.0), 4) if trimmed_values else None
        baseline_edge_pct = (
            round((transaction_cost_adjusted_return_pct or 0.0) - baseline_return_pct, 4)
            if transaction_cost_adjusted_return_pct is not None
            else None
        )
        max_drawdown_pct = BetaHypothesisBacktestService._max_drawdown(sample_values)
        walk_forward_score, out_of_sample_score, stability_score, walk_windows = (
            BetaHypothesisBacktestService._walk_forward_scores(
                matched_samples,
                expected_direction=expected_direction,
                baseline_return_pct=baseline_return_pct,
                transaction_cost_bps=transaction_cost_bps,
            )
        )
        # Window concentration metric
        if walk_windows and len(walk_windows) > 1:
            _total_pos_edge = sum(max(0.0, float(w["edge_pct"])) for w in walk_windows)
            _max_edge = max(float(w["edge_pct"]) for w in walk_windows)
            max_window_edge_share = round(
                (_max_edge / _total_pos_edge) if _total_pos_edge > 0.01 else 0.0,
                4,
            )
            positive_window_count = len([w for w in walk_windows if float(w["edge_pct"]) > 0.0])
        else:
            max_window_edge_share = 0.0
            positive_window_count = len(walk_windows) if walk_windows else 0

        regime_slice = {
            "matched_instruments": matched_instruments,
            "support_count": support_count,
            "eligible_support_count": len(eligible_samples),
        }
        regime_slice.update(
            BetaHypothesisBacktestService._regime_slice_summary(
                matched_feature_rows=matched_feature_rows or [],
                aligned_returns=sample_values,
            )
        )
        notes = {
            "minimum_sample_size": BetaHypothesisBacktestService._MIN_SAMPLE_SIZE,
            "walk_forward_windows": walk_windows,
            "baseline_policy": "UNCONDITIONAL_UNIVERSE_MEAN",
            "sample_size_sufficient": support_count >= BetaHypothesisBacktestService._MIN_SAMPLE_SIZE,
            "sample_span_days": sample_span_days,
            "evaluation_perspective": "direction_aligned",
            "raw_average_return_pct": average_target_return_pct,
            "raw_median_return_pct": round(float(median(raw_sample_values)), 4) if raw_sample_values else None,
            "median_ci_low_pct": median_ci_low_pct,
            "median_ci_high_pct": median_ci_high_pct,
            "winsorized_avg_return_pct": winsorized_avg,
            "trimmed_avg_return_pct": trimmed_avg,
            "winsorized_adjusted_return_pct": winsorized_adjusted_return_pct,
            "trimmed_adjusted_return_pct": trimmed_adjusted_return_pct,
            "robustness_collapse_pct": robustness_collapse_pct,
            "mean_median_ratio": mean_median_ratio,
            "top_two_positive_return_share": top_two_positive_return_share,
            "max_window_edge_share": max_window_edge_share,
            "positive_window_count": positive_window_count,
            "total_walk_forward_windows": len(walk_windows) if walk_windows else 0,
        }
        summary = {
            "sample_size": support_count,
            "support_count": support_count,
            "matched_instruments": matched_instruments,
            "test_start_date": min(sample_dates) if sample_dates else None,
            "test_end_date": max(sample_dates) if sample_dates else None,
            "average_target_return_pct": average_target_return_pct,
            "average_excess_return_pct": average_excess_return_pct,
            "median_excess_return_pct": median_excess_return_pct,
            "average_return_pct": average_return_pct,
            "median_return_pct": median_return_pct,
            "win_rate_pct": win_rate_pct,
            "outcome_volatility_pct": outcome_volatility_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "baseline_return_pct": baseline_return_pct,
            "baseline_edge_pct": baseline_edge_pct,
            "baseline_sign_accuracy_pct": baseline_sign_accuracy_pct,
            "transaction_cost_bps": transaction_cost_bps,
            "transaction_cost_adjusted_return_pct": transaction_cost_adjusted_return_pct,
            "walk_forward_score": walk_forward_score,
            "out_of_sample_score": out_of_sample_score,
            "stability_score": stability_score,
            "regime_slice": regime_slice,
            "notes": notes,
        }
        summary["notes"]["governance"] = BetaHypothesisGovernanceService.evaluate_summary(summary)
        return summary

    @staticmethod
    def _bootstrap_median_interval(sample_values: list[float], *, iterations: int = 128) -> tuple[float | None, float | None]:
        if len(sample_values) < 5:
            return None, None
        seed = len(sample_values) * 997
        for index, value in enumerate(sorted(sample_values)):
            seed += int(round(value * 100.0)) * (index + 1)
        rng = Random(seed)
        bootstrapped: list[float] = []
        for _ in range(iterations):
            resample = [sample_values[rng.randrange(len(sample_values))] for _idx in range(len(sample_values))]
            bootstrapped.append(float(median(resample)))
        bootstrapped.sort()
        low_index = max(0, min(len(bootstrapped) - 1, int(len(bootstrapped) * 0.1)))
        high_index = max(0, min(len(bootstrapped) - 1, int(len(bootstrapped) * 0.9)))
        return round(bootstrapped[low_index], 4), round(bootstrapped[high_index], 4)

    @staticmethod
    def _regime_slice_summary(
        *,
        matched_feature_rows: list[dict[str, float]],
        aligned_returns: list[float],
    ) -> dict[str, object]:
        if not matched_feature_rows or len(matched_feature_rows) != len(aligned_returns):
            return {
                "evaluated_bucket_count": 0,
                "regime_consistency_score": None,
                "failure_modes": [],
            }

        dimensions = {
            "market_regime": BetaHypothesisBacktestService._market_regime_bucket,
            "volatility_regime": BetaHypothesisBacktestService._volatility_regime_bucket,
            "sector_regime": BetaHypothesisBacktestService._sector_regime_bucket,
        }
        min_bucket_support = 5
        evaluated_stats: list[dict[str, object]] = []
        regime_summary: dict[str, object] = {}
        failure_modes: list[dict[str, object]] = []

        for dimension_name, resolver in dimensions.items():
            grouped: dict[str, list[float]] = {}
            for feature_row, aligned_return in zip(matched_feature_rows, aligned_returns):
                bucket = resolver(feature_row)
                if bucket is None:
                    continue
                grouped.setdefault(bucket, []).append(aligned_return)

            stats: list[dict[str, object]] = []
            for bucket, values in sorted(grouped.items()):
                avg_return_pct = round(sum(values) / len(values), 4) if values else 0.0
                win_rate_pct = round(
                    (len([value for value in values if value > 0]) / len(values)) * 100.0,
                    2,
                ) if values else 0.0
                row = {
                    "bucket": bucket,
                    "sample_size": len(values),
                    "avg_return_pct": avg_return_pct,
                    "median_return_pct": round(float(median(values)), 4) if values else None,
                    "win_rate_pct": win_rate_pct,
                }
                stats.append(row)
                if len(values) >= min_bucket_support:
                    evaluated_stats.append({"dimension": dimension_name, **row})
                    if avg_return_pct <= 0.0 or win_rate_pct < 50.0:
                        failure_modes.append(
                            {
                                "dimension": dimension_name,
                                "bucket": bucket,
                                "avg_return_pct": avg_return_pct,
                                "win_rate_pct": win_rate_pct,
                                "sample_size": len(values),
                            }
                        )
            regime_summary[dimension_name] = stats

        if len(evaluated_stats) < 2:
            regime_summary["evaluated_bucket_count"] = len(evaluated_stats)
            regime_summary["regime_consistency_score"] = None
            regime_summary["failure_modes"] = failure_modes
            return regime_summary

        positive_bucket_ratio = len(
            [
                row
                for row in evaluated_stats
                if float(row["avg_return_pct"]) > 0.0 and float(row["win_rate_pct"]) >= 50.0
            ]
        ) / len(evaluated_stats)
        bucket_returns = [float(row["avg_return_pct"]) for row in evaluated_stats]
        dispersion = pstdev(bucket_returns) if len(bucket_returns) > 1 else 0.0
        minimum_bucket_return = min(bucket_returns)
        regime_consistency_score = round(
            max(
                0.0,
                min(
                    1.0,
                    (positive_bucket_ratio * 0.75)
                    + min(0.15, max(0.0, minimum_bucket_return) / 4.0)
                    - min(0.25, dispersion / 5.0),
                ),
            ),
            4,
        )
        regime_summary["evaluated_bucket_count"] = len(evaluated_stats)
        regime_summary["regime_consistency_score"] = regime_consistency_score
        regime_summary["failure_modes"] = failure_modes
        return regime_summary

    @staticmethod
    def _market_regime_bucket(feature_row: dict[str, float]) -> str | None:
        value = feature_row.get("market_ret_10d_pct")
        if value is None:
            return None
        if value <= -1.5:
            return "weak_market"
        if value >= 1.5:
            return "strong_market"
        return "neutral_market"

    @staticmethod
    def _volatility_regime_bucket(feature_row: dict[str, float]) -> str | None:
        value = feature_row.get("realized_vol_20d_pct")
        if value is None:
            return None
        if value >= 8.0:
            return "high_volatility"
        if value <= 4.0:
            return "low_volatility"
        return "medium_volatility"

    @staticmethod
    def _sector_regime_bucket(feature_row: dict[str, float]) -> str | None:
        value = feature_row.get("sector_ret_10d_pct")
        if value is None:
            return None
        if value <= -1.5:
            return "weak_sector"
        if value >= 1.5:
            return "strong_sector"
        return "neutral_sector"

    @staticmethod
    def _max_drawdown(sample_values: list[float]) -> float | None:
        if not sample_values:
            return None
        equity = 100.0
        peak = equity
        max_drawdown = 0.0
        for value in sample_values:
            equity *= max(0.0001, 1.0 + (value / 100.0))
            if equity > peak:
                peak = equity
            if peak > 0:
                drawdown = ((equity / peak) - 1.0) * 100.0
                max_drawdown = min(max_drawdown, drawdown)
        return round(max_drawdown, 4)

    @staticmethod
    def _walk_forward_scores(
        matched_samples: list[tuple[str, object, float]],
        *,
        expected_direction: str,
        baseline_return_pct: float,
        transaction_cost_bps: float,
    ) -> tuple[float | None, float | None, float | None, list[dict[str, object]]]:
        if not matched_samples:
            return None, None, None, []

        ordered = sorted(matched_samples, key=lambda row: row[1])
        dates = sorted({decision_date for _instrument_id, decision_date, _value in ordered})
        if len(dates) < 4:
            sample_values = [
                BetaHypothesisBacktestService._align_return(
                    expected_direction=expected_direction,
                    raw_return_pct=value,
                )
                for _instrument_id, _decision_date, value in ordered
            ]
            adjusted = round((sum(sample_values) / len(sample_values)) - (transaction_cost_bps / 100.0), 4)
            return adjusted - baseline_return_pct, adjusted, 0.0, []

        bucket_count = min(6, max(3, len(dates) // 20))
        windows: list[dict[str, object]] = []
        for chunk in BetaHypothesisBacktestService._split_dates(dates, bucket_count):
            chunk_values = [
                BetaHypothesisBacktestService._align_return(
                    expected_direction=expected_direction,
                    raw_return_pct=value,
                )
                for _instrument_id, decision_date, value in ordered
                if decision_date in chunk
            ]
            if not chunk_values:
                continue
            avg_return = sum(chunk_values) / len(chunk_values)
            adjusted = avg_return - (transaction_cost_bps / 100.0)
            edge = adjusted - baseline_return_pct
            windows.append(
                {
                    "start_date": str(min(chunk)),
                    "end_date": str(max(chunk)),
                    "sample_size": len(chunk_values),
                    "avg_return_pct": round(avg_return, 4),
                    "adjusted_return_pct": round(adjusted, 4),
                    "edge_pct": round(edge, 4),
                }
            )
        if not windows:
            return None, None, None, []

        walk_forward_score = round(
            sum(window["edge_pct"] for window in windows) / len(windows),
            4,
        )
        tail_windows = windows[-max(1, len(windows) // 3) :]
        out_of_sample_score = round(
            sum(window["adjusted_return_pct"] for window in tail_windows) / len(tail_windows),
            4,
        )
        positive_window_ratio = len([window for window in windows if float(window["edge_pct"]) > 0.0]) / len(windows)
        edge_volatility = pstdev([float(window["edge_pct"]) for window in windows]) if len(windows) > 1 else 0.0
        recency_edge = sum(float(window["edge_pct"]) for window in tail_windows) / len(tail_windows)
        total_positive_edge = sum(max(0.0, float(w["edge_pct"])) for w in windows)
        max_window_edge = max(float(w["edge_pct"]) for w in windows)
        max_window_edge_share = (max_window_edge / total_positive_edge) if total_positive_edge > 0.01 else 0.0
        window_concentration_penalty = max(0.0, min(0.2, (max_window_edge_share - 0.5) * 0.4))
        stability_score = round(
            max(
                0.0,
                min(
                    1.0,
                    (positive_window_ratio * 0.55)
                    + min(0.2, max(0.0, walk_forward_score) / 4.0)
                    + min(0.15, max(0.0, recency_edge) / 3.0)
                    - min(0.25, edge_volatility / 6.0)
                    - window_concentration_penalty,
                ),
            ),
            4,
        )
        return walk_forward_score, out_of_sample_score, stability_score, windows

    @staticmethod
    def _split_dates(dates: list[object], bucket_count: int) -> list[set[object]]:
        if bucket_count <= 0:
            return []
        buckets: list[set[object]] = []
        size = max(1, len(dates) // bucket_count)
        for start in range(0, len(dates), size):
            buckets.append(set(dates[start : start + size]))
        return buckets
