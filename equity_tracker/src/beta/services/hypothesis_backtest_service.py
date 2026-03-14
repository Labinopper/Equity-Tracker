"""Historical hypothesis backtesting over the existing beta feature and label stores."""

from __future__ import annotations

import json
from statistics import median

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
from .hypothesis_normalizer import BetaHypothesisNormalizer


class BetaHypothesisBacktestService:
    """Backtest explicit hypothesis definitions against the stored feature/label history."""

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
                        BetaHypothesisDefinition.status.in_(
                            ("CANDIDATE", "PROMISING", "VALIDATED", "DEGRADED")
                        )
                    )
                ).all()
            )
            if not definitions:
                return {"definitions_considered": 0, "test_runs_written": 0}

            feature_names = sorted(
                {
                    feature_name
                    for definition in definitions
                    for feature_name in BetaHypothesisNormalizer.extract_feature_names(
                        BetaHypothesisNormalizer.normalize_conditions(
                            json.loads(definition.entry_conditions_json or "{}")
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
                universe = BetaHypothesisBacktestService._json_object(definition.universe_json)
                target_metric = str(definition.target_metric or "fwd_5d_excess_return_pct")
                label_values = labels_by_metric.get(target_metric, {})
                feature_names_for_definition = sorted(BetaHypothesisNormalizer.extract_feature_names(conditions))
                feature_subset = set(feature_names_for_definition)

                matched_samples: list[tuple[str, object, float]] = []
                eligible_samples: list[float] = []
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
                    eligible_samples.append(label_value)
                    match_result = BetaHypothesisNormalizer.evaluate(conditions, snapshot)
                    if match_result.matched:
                        matched_samples.append((instrument_id, decision_date, label_value))

                summary = BetaHypothesisBacktestService._summarize_samples(
                    matched_samples=matched_samples,
                    eligible_values=eligible_samples,
                    expected_direction=definition.expected_direction,
                    settings=settings,
                )

                test_run = BetaHypothesisTestRun(
                    hypothesis_definition_id=definition.id,
                    dataset_version_id=latest_dataset.id if latest_dataset is not None else None,
                    validation_run_id=latest_validation.id if latest_validation is not None else None,
                    baseline_name="UNCONDITIONAL_UNIVERSE_MEAN",
                    test_start_date=summary["test_start_date"],
                    test_end_date=summary["test_end_date"],
                    sample_size=summary["sample_size"],
                    matched_instruments=summary["matched_instruments"],
                    average_return_pct=summary["average_return_pct"],
                    median_return_pct=summary["median_return_pct"],
                    win_rate_pct=summary["win_rate_pct"],
                    max_drawdown_pct=summary["max_drawdown_pct"],
                    baseline_return_pct=summary["baseline_return_pct"],
                    baseline_sign_accuracy_pct=summary["baseline_sign_accuracy_pct"],
                    transaction_cost_bps=summary["transaction_cost_bps"],
                    transaction_cost_adjusted_return_pct=summary["transaction_cost_adjusted_return_pct"],
                    walk_forward_score=summary["walk_forward_score"],
                    out_of_sample_score=summary["out_of_sample_score"],
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
    def _summarize_samples(
        *,
        matched_samples: list[tuple[str, object, float]],
        eligible_values: list[float],
        expected_direction: str,
        settings: BetaSettings,
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
        sample_size = len(sample_values)
        matched_instruments = len({instrument_id for instrument_id, _decision_date, _value in matched_samples})
        aligned_eligible_values = [
            BetaHypothesisBacktestService._align_return(
                expected_direction=expected_direction,
                raw_return_pct=value,
            )
            for value in eligible_values
        ]
        baseline_return_pct = round(sum(aligned_eligible_values) / len(aligned_eligible_values), 4) if aligned_eligible_values else 0.0
        baseline_sign_accuracy_pct = round(
            (len([value for value in aligned_eligible_values if value > 0]) / len(aligned_eligible_values)) * 100.0,
            2,
        ) if aligned_eligible_values else 0.0
        win_rate_pct = round(
            (len([value for value in sample_values if value > 0]) / len(sample_values)) * 100.0,
            2,
        ) if sample_values else 0.0

        average_return_pct = round(sum(sample_values) / len(sample_values), 4) if sample_values else None
        median_return_pct = round(float(median(sample_values)), 4) if sample_values else None
        transaction_cost_bps = float(max(settings.uk_equity_friction_bps, settings.us_equity_friction_bps) * BetaHypothesisBacktestService._TRANSACTION_COST_MULTIPLIER)
        transaction_cost_adjusted_return_pct = (
            round((average_return_pct or 0.0) - (transaction_cost_bps / 100.0), 4)
            if average_return_pct is not None
            else None
        )
        max_drawdown_pct = BetaHypothesisBacktestService._max_drawdown(sample_values)
        walk_forward_score, out_of_sample_score, walk_windows = BetaHypothesisBacktestService._walk_forward_scores(
            matched_samples,
            expected_direction=expected_direction,
            baseline_return_pct=baseline_return_pct,
            transaction_cost_bps=transaction_cost_bps,
        )
        regime_slice = {
            "markets": sorted({"UNKNOWN"}),
            "matched_instruments": matched_instruments,
            "sample_size": sample_size,
        }
        notes = {
            "minimum_sample_size": BetaHypothesisBacktestService._MIN_SAMPLE_SIZE,
            "walk_forward_windows": walk_windows,
            "baseline_policy": "UNCONDITIONAL_UNIVERSE_MEAN",
            "sample_size_sufficient": sample_size >= BetaHypothesisBacktestService._MIN_SAMPLE_SIZE,
            "evaluation_perspective": "direction_aligned",
            "raw_average_return_pct": round(sum(raw_sample_values) / len(raw_sample_values), 4) if raw_sample_values else None,
            "raw_median_return_pct": round(float(median(raw_sample_values)), 4) if raw_sample_values else None,
        }
        return {
            "sample_size": sample_size,
            "matched_instruments": matched_instruments,
            "test_start_date": min(sample_dates) if sample_dates else None,
            "test_end_date": max(sample_dates) if sample_dates else None,
            "average_return_pct": average_return_pct,
            "median_return_pct": median_return_pct,
            "win_rate_pct": win_rate_pct if sample_values else None,
            "max_drawdown_pct": max_drawdown_pct,
            "baseline_return_pct": baseline_return_pct,
            "baseline_sign_accuracy_pct": baseline_sign_accuracy_pct,
            "transaction_cost_bps": transaction_cost_bps,
            "transaction_cost_adjusted_return_pct": transaction_cost_adjusted_return_pct,
            "walk_forward_score": walk_forward_score,
            "out_of_sample_score": out_of_sample_score,
            "regime_slice": regime_slice,
            "notes": notes,
        }

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
    ) -> tuple[float | None, float | None, list[dict[str, object]]]:
        if not matched_samples:
            return None, None, []
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
            return adjusted - baseline_return_pct, adjusted, []
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
            windows.append(
                {
                    "start_date": str(min(chunk)),
                    "end_date": str(max(chunk)),
                    "sample_size": len(chunk_values),
                    "avg_return_pct": round(avg_return, 4),
                    "adjusted_return_pct": round(adjusted, 4),
                }
            )
        if not windows:
            return None, None, []
        walk_forward_score = round(
            sum(window["adjusted_return_pct"] - baseline_return_pct for window in windows) / len(windows),
            4,
        )
        tail_windows = windows[-max(1, len(windows) // 3):]
        out_of_sample_score = round(
            sum(window["adjusted_return_pct"] for window in tail_windows) / len(tail_windows),
            4,
        )
        return walk_forward_score, out_of_sample_score, windows

    @staticmethod
    def _split_dates(dates: list[object], bucket_count: int) -> list[set[object]]:
        if bucket_count <= 0:
            return []
        buckets: list[set[object]] = []
        size = max(1, len(dates) // bucket_count)
        for start in range(0, len(dates), size):
            buckets.append(set(dates[start : start + size]))
        return buckets
