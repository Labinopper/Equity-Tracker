"""Lightweight challenger-model training over the beta feature and label stores."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaDatasetRow,
    BetaDatasetVersion,
    BetaExperimentRun,
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaInstrument,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaModelVersion,
    BetaTrainingDecision,
    BetaValidationRun,
)
from ..services.session_service import BetaMarketSessionService
from ..services.strategy_service import BetaStrategyService
from ..settings import BetaSettings
from ..state import get_beta_db_path

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None


@dataclass
class _DatasetRow:
    instrument_id: str
    decision_date: object
    features: list[float]
    label: float


class BetaTrainingService:
    """Build a compact linear challenger from stored daily features and labels."""

    _MODEL_NAME = "daily_ridge_v2"
    _ALGORITHM = "numpy_ridge_closed_form"
    _MIN_ROWS = 60
    _MIN_VALIDATION_ROWS = 20
    _MIN_VALIDATION_DATES = 15
    _MIN_WALKFORWARD_WINDOWS = 3
    _MIN_WALKFORWARD_TRAIN_DATES = 60
    _MIN_ACTIVATION_TRAIN_ROWS = 120
    _MIN_ACTIVATION_VALIDATION_ROWS = 25
    _MIN_ACTIVATION_WALKFORWARD_WINDOWS = 3
    _MIN_ACTIVATION_SIGN_ACCURACY = 53.0
    _MIN_ACTIVATION_WALKFORWARD_ACCURACY = 53.0
    _MIN_ACTIVATION_WALKFORWARD_RETURN = 0.05
    _MIN_ACTIVATION_HOLDOUT_BASELINE_LIFT = 0.75
    _MIN_ACTIVATION_WALKFORWARD_BASELINE_LIFT = 0.50
    _MIN_ACTIVATION_WALKFORWARD_RETURN_LIFT = 0.05
    _SUSPICIOUS_SIGN_ACCURACY = 99.5
    _SUSPICIOUS_LOW_RETURN_ABS = 0.10
    _STALE_MODEL_RETRAIN_HOURS = 24
    _RIDGE_ALPHA = 2.0

    @staticmethod
    def _record_training_decision(
        sess,
        *,
        decision_type: str = "DAILY_TRAINING",
        status_code: str,
        reason_code: str | None = None,
        performed: bool,
        trained: bool,
        activated: bool | None = None,
        model_version_id: str | None = None,
        validation_run_id: str | None = None,
        training_rows: int | None = None,
        validation_rows: int | None = None,
        walkforward_window_count: int | None = None,
        validation_sign_accuracy_pct: float | None = None,
        walkforward_validation_sign_accuracy_pct: float | None = None,
        notes: dict[str, object] | None = None,
    ) -> None:
        sess.add(
            BetaTrainingDecision(
                decision_type=decision_type,
                status_code=status_code,
                reason_code=reason_code,
                performed=performed,
                trained=trained,
                activated=activated,
                model_version_id=model_version_id,
                validation_run_id=validation_run_id,
                training_rows=training_rows,
                validation_rows=validation_rows,
                walkforward_window_count=walkforward_window_count,
                validation_sign_accuracy_pct=validation_sign_accuracy_pct,
                walkforward_validation_sign_accuracy_pct=walkforward_validation_sign_accuracy_pct,
                notes_json=json.dumps(notes or {}, sort_keys=True),
            )
        )

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
    def _latest_validation_run(sess, *, model_version_id: str | None):
        if not model_version_id:
            return None
        return sess.scalar(
            select(BetaValidationRun)
            .where(BetaValidationRun.model_version_id == model_version_id)
            .order_by(desc(BetaValidationRun.created_at))
            .limit(1)
        )

    @staticmethod
    def _new_observation_count_since_model(sess, *, model_created_at: datetime) -> int:
        return int(
            sess.scalar(
                select(func.count())
                .select_from(BetaLabelValue)
                .where(
                    BetaLabelValue.value_numeric.is_not(None),
                    BetaLabelValue.created_at > model_created_at,
                )
            )
            or 0
        )

    @staticmethod
    def _active_model_governance_assessment(*, sess, model) -> dict[str, object]:
        if model is None:
            return {"trustworthy": True, "reasons": [], "validation_summary": None, "validation_run_id": None}

        validation_run = BetaTrainingService._latest_validation_run(sess, model_version_id=model.id)
        walkforward_window_count = int(validation_run.window_count or 0) if validation_run is not None else 0
        walkforward_accuracy = (
            float(validation_run.avg_validation_sign_accuracy_pct)
            if validation_run is not None and validation_run.avg_validation_sign_accuracy_pct is not None
            else None
        )
        walkforward_return = (
            float(validation_run.avg_validation_return_pct)
            if validation_run is not None and validation_run.avg_validation_return_pct is not None
            else None
        )
        sign_accuracy = float(model.validation_sign_accuracy_pct or 0.0)

        reasons: list[str] = []
        if model.validation_row_count < BetaTrainingService._MIN_ACTIVATION_VALIDATION_ROWS:
            reasons.append("validation_rows_below_activation_floor")
        if walkforward_window_count < BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_WINDOWS:
            reasons.append("walkforward_windows_below_activation_floor")
        if (
            sign_accuracy >= BetaTrainingService._SUSPICIOUS_SIGN_ACCURACY
            and abs(walkforward_return or 0.0) <= BetaTrainingService._SUSPICIOUS_LOW_RETURN_ABS
            and walkforward_window_count <= BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_WINDOWS
        ):
            reasons.append("validation_profile_suspiciously_perfect")

        validation_summary = {
            "validation_rows": int(model.validation_row_count or 0),
            "validation_sign_accuracy_pct": round(sign_accuracy, 2),
            "walkforward_window_count": walkforward_window_count,
            "walkforward_validation_sign_accuracy_pct": walkforward_accuracy,
            "walkforward_validation_return_pct": walkforward_return,
        }
        return {
            "trustworthy": len(reasons) == 0,
            "reasons": reasons,
            "validation_summary": validation_summary,
            "validation_run_id": validation_run.id if validation_run is not None else None,
        }

    @staticmethod
    def enforce_active_model_governance(sess) -> dict[str, object]:
        active_model = sess.scalar(
            select(BetaModelVersion)
            .where(BetaModelVersion.is_active.is_(True))
            .order_by(desc(BetaModelVersion.activated_at), desc(BetaModelVersion.created_at))
            .limit(1)
        )
        if active_model is None:
            return {"checked": True, "suspended": False, "reason": "no_active_model"}

        assessment = BetaTrainingService._active_model_governance_assessment(sess=sess, model=active_model)
        if assessment["trustworthy"]:
            return {
                "checked": True,
                "suspended": False,
                "reason": "active_model_trusted",
                "active_model_version": active_model.version_code,
                "validation_summary": assessment["validation_summary"],
            }

        previous_status = active_model.status
        active_model.is_active = False
        active_model.is_challenger = False
        active_model.status = "SUSPENDED"
        notes = BetaTrainingService._json_object(active_model.notes_json)
        notes["governance"] = {
            "checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "previous_status": previous_status,
            "suspension_reasons": assessment["reasons"],
            "suspension_source": "automatic_active_model_governance",
            "validation_summary": assessment["validation_summary"],
        }
        active_model.notes_json = json.dumps(notes, sort_keys=True)

        suspended_strategy = BetaStrategyService.suspend_active_strategy(
            sess=sess,
            model_version_id=active_model.id,
            reason_code=str(assessment["reasons"][0]),
            message_text=(
                f"Strategy version {active_model.version_code} suspended because the active model failed governance checks."
            ),
            payload={
                "model_version_code": active_model.version_code,
                "suspension_reasons": assessment["reasons"],
                "validation_summary": assessment["validation_summary"],
            },
        )
        BetaTrainingService._record_training_decision(
            sess,
            decision_type="MODEL_GOVERNANCE",
            status_code="ACTIVE_MODEL_SUSPENDED",
            reason_code=str(assessment["reasons"][0]),
            performed=True,
            trained=False,
            activated=False,
            model_version_id=active_model.id,
            validation_run_id=assessment["validation_run_id"],
            training_rows=int(active_model.training_row_count or 0),
            validation_rows=int(active_model.validation_row_count or 0),
            walkforward_window_count=int(assessment["validation_summary"]["walkforward_window_count"] or 0),
            validation_sign_accuracy_pct=active_model.validation_sign_accuracy_pct,
            walkforward_validation_sign_accuracy_pct=assessment["validation_summary"][
                "walkforward_validation_sign_accuracy_pct"
            ],
            notes={
                "model_version_code": active_model.version_code,
                "suspension_reasons": assessment["reasons"],
                "suspended_strategy_version": suspended_strategy.version_code if suspended_strategy is not None else None,
                "validation_summary": assessment["validation_summary"],
            },
        )
        return {
            "checked": True,
            "suspended": True,
            "active_model_version": active_model.version_code,
            "suspension_reasons": assessment["reasons"],
            "suspended_strategy_version": suspended_strategy.version_code if suspended_strategy is not None else None,
            "validation_summary": assessment["validation_summary"],
        }

    @staticmethod
    def has_tracked_core_equity() -> bool:
        if not BetaContext.is_initialized():
            return False
        with BetaContext.read_session() as sess:
            tracked = sess.scalar(
                select(func.count())
                .select_from(BetaInstrument)
                .where(
                    BetaInstrument.is_active.is_(True),
                    BetaInstrument.core_security_id.is_not(None),
                )
            )
            return bool(tracked or 0)

    @staticmethod
    def _date_bounds(rows: list[_DatasetRow]) -> tuple[object | None, object | None]:
        if not rows:
            return None, None
        return rows[0].decision_date, rows[-1].decision_date

    @staticmethod
    def _split_train_validation_rows(dataset: list[_DatasetRow]) -> tuple[list[_DatasetRow], list[_DatasetRow], int]:
        unique_dates = sorted({row.decision_date for row in dataset})
        if len(unique_dates) <= 1:
            return dataset, [], 0
        validation_date_count = max(
            BetaTrainingService._MIN_VALIDATION_DATES,
            max(1, int(len(unique_dates) * 0.2)),
        )
        validation_date_count = min(validation_date_count, max(1, len(unique_dates) - 1))
        validation_dates = set(unique_dates[-validation_date_count:])
        train_rows = [row for row in dataset if row.decision_date not in validation_dates]
        validation_rows = [row for row in dataset if row.decision_date in validation_dates]
        return train_rows, validation_rows, validation_date_count

    @staticmethod
    def _feature_matrix(rows: list[_DatasetRow]):
        return _np.array([row.features for row in rows], dtype=float)

    @staticmethod
    def _label_vector(rows: list[_DatasetRow]):
        return _np.array([row.label for row in rows], dtype=float)

    @staticmethod
    def _winsorize_feature_matrices(train_matrix, validation_matrix=None):
        low = _np.nanpercentile(train_matrix, 1.0, axis=0)
        high = _np.nanpercentile(train_matrix, 99.0, axis=0)
        clipped_train = _np.clip(train_matrix, low, high)
        clipped_validation = None
        if validation_matrix is not None:
            clipped_validation = _np.clip(validation_matrix, low, high)
        return clipped_train, clipped_validation, low, high

    @staticmethod
    def _winsorize_label_vector(train_vector):
        low = float(_np.nanpercentile(train_vector, 1.0))
        high = float(_np.nanpercentile(train_vector, 99.0))
        return _np.clip(train_vector, low, high), low, high

    @staticmethod
    def _evaluate_predictions(predictions, actual) -> dict[str, float]:
        mae = float(_np.mean(_np.abs(predictions - actual)))
        sign_accuracy = float(_np.mean((_np.sign(predictions) == _np.sign(actual)).astype(float)) * 100.0)
        directional_capture = float(_np.mean(actual * _np.sign(predictions)))
        positive_mask = predictions > 0
        selected_return = float(_np.mean(actual[positive_mask])) if positive_mask.any() else float(_np.mean(actual))
        positive_rate = float(_np.mean(positive_mask.astype(float)) * 100.0)
        return {
            "mae": round(mae, 4),
            "sign_accuracy_pct": round(sign_accuracy, 2),
            "avg_return_pct": round(directional_capture, 4),
            "avg_selected_return_pct": round(selected_return, 4),
            "positive_rate_pct": round(positive_rate, 2),
        }

    @staticmethod
    def _baseline_predictions(feature_names: list[str], rows: list[_DatasetRow]) -> dict[str, object]:
        if not rows:
            return {
                "zero_excess": _np.array([], dtype=float),
                "continuation_excess": _np.array([], dtype=float),
                "mean_reversion_excess": _np.array([], dtype=float),
            }
        feature_index = {name: idx for idx, name in enumerate(feature_names)}
        matrix = BetaTrainingService._feature_matrix(rows)
        continuation_source = None
        for candidate in (
            "benchmark_excess_5d_pct",
            "market_excess_5d_pct",
            "sector_excess_5d_pct",
            "ret_5d_pct",
        ):
            if candidate in feature_index:
                continuation_source = candidate
                break
        if continuation_source is None:
            continuation = _np.zeros(len(rows), dtype=float)
        else:
            continuation = matrix[:, feature_index[continuation_source]]
        return {
            "zero_excess": _np.zeros(len(rows), dtype=float),
            "continuation_excess": continuation,
            "mean_reversion_excess": continuation * -1.0,
        }

    @staticmethod
    def _evaluate_baselines(feature_names: list[str], rows: list[_DatasetRow], actual) -> dict[str, dict[str, float | str | None]]:
        baseline_predictions = BetaTrainingService._baseline_predictions(feature_names, rows)
        metrics: dict[str, dict[str, float | str | None]] = {}
        continuation_source = None
        for candidate in (
            "benchmark_excess_5d_pct",
            "market_excess_5d_pct",
            "sector_excess_5d_pct",
            "ret_5d_pct",
        ):
            if candidate in feature_names:
                continuation_source = candidate
                break
        for baseline_name, predictions in baseline_predictions.items():
            summary = BetaTrainingService._evaluate_predictions(predictions, actual)
            if baseline_name in {"continuation_excess", "mean_reversion_excess"}:
                summary["source_feature"] = continuation_source
            else:
                summary["source_feature"] = None
            metrics[baseline_name] = summary
        return metrics

    @staticmethod
    def _aggregate_baseline_metrics(
        summaries: list[dict[str, dict[str, float | str | None]]],
    ) -> dict[str, dict[str, float | str | None]]:
        if not summaries:
            return {}
        aggregated: dict[str, dict[str, float | str | None]] = {}
        baseline_names = sorted({name for summary in summaries for name in summary.keys()})
        metric_names = ("mae", "sign_accuracy_pct", "avg_return_pct", "avg_selected_return_pct", "positive_rate_pct")
        for baseline_name in baseline_names:
            rows = [summary[baseline_name] for summary in summaries if baseline_name in summary]
            if not rows:
                continue
            aggregated[baseline_name] = {
                metric_name: round(
                    sum(float(row.get(metric_name) or 0.0) for row in rows) / len(rows),
                    4 if metric_name in {"mae", "avg_return_pct", "avg_selected_return_pct"} else 2,
                )
                for metric_name in metric_names
            }
            source_feature = next((row.get("source_feature") for row in rows if row.get("source_feature")), None)
            aggregated[baseline_name]["source_feature"] = source_feature
        return aggregated

    @staticmethod
    def _build_confidence_calibration(predictions, actual) -> dict[str, object]:
        if len(predictions) == 0:
            return {"global_sign_accuracy_pct": None, "bucket_count": 0, "buckets": []}
        abs_predictions = _np.abs(predictions)
        global_accuracy = BetaTrainingService._evaluate_predictions(predictions, actual)["sign_accuracy_pct"]
        bucket_target = 4 if len(predictions) >= 80 else 3
        index_buckets = [bucket for bucket in _np.array_split(_np.argsort(abs_predictions), bucket_target) if len(bucket) > 0]
        buckets: list[dict[str, object]] = []
        for bucket in index_buckets:
            bucket_predictions = predictions[bucket]
            bucket_actual = actual[bucket]
            metrics = BetaTrainingService._evaluate_predictions(bucket_predictions, bucket_actual)
            bucket_abs = abs_predictions[bucket]
            buckets.append(
                {
                    "count": int(len(bucket)),
                    "min_abs_prediction_pct": round(float(bucket_abs.min()), 4),
                    "max_abs_prediction_pct": round(float(bucket_abs.max()), 4),
                    "avg_abs_prediction_pct": round(float(bucket_abs.mean()), 4),
                    "sign_accuracy_pct": metrics["sign_accuracy_pct"],
                    "avg_return_pct": metrics["avg_return_pct"],
                }
            )
        return {
            "global_sign_accuracy_pct": global_accuracy,
            "bucket_count": len(buckets),
            "buckets": buckets,
        }

    @staticmethod
    def _fit_linear_model(
        feature_rows: list[list[float]],
        label_rows: list[float],
    ) -> tuple[float, object, object, object]:
        x_train = _np.array(feature_rows, dtype=float)
        y_train = _np.array(label_rows, dtype=float)
        means = x_train.mean(axis=0)
        scales = x_train.std(axis=0)
        scales = _np.where(scales == 0.0, 1.0, scales)
        x_train_scaled = (x_train - means) / scales
        x_train_design = _np.column_stack([_np.ones(len(x_train_scaled)), x_train_scaled])
        regularizer = _np.eye(x_train_design.shape[1], dtype=float)
        regularizer[0, 0] = 0.0
        lhs = x_train_design.T @ x_train_design
        rhs = x_train_design.T @ y_train
        coeffs = _np.linalg.pinv(lhs + (BetaTrainingService._RIDGE_ALPHA * regularizer)) @ rhs
        intercept = float(coeffs[0])
        weights = coeffs[1:]
        return intercept, weights, means, scales

    @staticmethod
    def _predict_rows(
        feature_rows: list[list[float]],
        *,
        intercept: float,
        weights,
        means,
        scales,
    ):
        matrix = _np.array(feature_rows, dtype=float)
        scaled = (matrix - means) / scales
        return intercept + scaled.dot(weights)

    @staticmethod
    def _walk_forward_validate(dataset: list[_DatasetRow], feature_names: list[str]) -> dict[str, object]:
        if _np is None or len(dataset) < (BetaTrainingService._MIN_ROWS + BetaTrainingService._MIN_VALIDATION_ROWS):
            return {
                "window_count": 0,
                "avg_validation_mae": None,
                "avg_validation_sign_accuracy_pct": None,
                "avg_validation_return_pct": None,
                "baseline_summaries": {},
                "windows": [],
            }

        unique_dates = sorted({row.decision_date for row in dataset})
        if len(unique_dates) < (BetaTrainingService._MIN_VALIDATION_DATES + 2):
            return {
                "window_count": 0,
                "avg_validation_mae": None,
                "avg_validation_sign_accuracy_pct": None,
                "avg_validation_return_pct": None,
                "baseline_summaries": {},
                "windows": [],
            }

        validation_window_dates = max(
            BetaTrainingService._MIN_VALIDATION_DATES,
            min(30, max(10, len(unique_dates) // 6)),
        )
        validation_window_dates = min(validation_window_dates, max(1, len(unique_dates) - 1))
        min_train_dates = min(
            max(BetaTrainingService._MIN_WALKFORWARD_TRAIN_DATES, validation_window_dates * 2),
            max(1, len(unique_dates) - validation_window_dates),
        )
        candidate_starts = list(
            range(
                min_train_dates,
                len(unique_dates) - validation_window_dates + 1,
                validation_window_dates,
            )
        )
        if not candidate_starts:
            candidate_starts = [max(1, len(unique_dates) - validation_window_dates)]

        window_metrics: list[dict[str, object]] = []
        baseline_metrics_by_window: list[dict[str, dict[str, float | str | None]]] = []
        for start_index in candidate_starts:
            train_dates = set(unique_dates[:start_index])
            validation_dates = set(unique_dates[start_index : start_index + validation_window_dates])
            train_rows = [row for row in dataset if row.decision_date in train_dates]
            validation_rows = [row for row in dataset if row.decision_date in validation_dates]
            if len(train_rows) < BetaTrainingService._MIN_ROWS or len(validation_rows) < BetaTrainingService._MIN_VALIDATION_ROWS:
                continue
            train_matrix = BetaTrainingService._feature_matrix(train_rows)
            validation_matrix = BetaTrainingService._feature_matrix(validation_rows)
            train_matrix, validation_matrix, _feature_clip_lows, _feature_clip_highs = BetaTrainingService._winsorize_feature_matrices(
                train_matrix,
                validation_matrix,
            )
            y_train = BetaTrainingService._label_vector(train_rows)
            y_train, _label_clip_low, _label_clip_high = BetaTrainingService._winsorize_label_vector(y_train)
            intercept, weights, means, scales = BetaTrainingService._fit_linear_model(
                train_matrix.tolist(),
                y_train.tolist(),
            )
            predictions = BetaTrainingService._predict_rows(
                validation_matrix.tolist(),
                intercept=intercept,
                weights=weights,
                means=means,
                scales=scales,
            )
            actual = _np.array([row.label for row in validation_rows], dtype=float)
            model_metrics = BetaTrainingService._evaluate_predictions(predictions, actual)
            baseline_metrics = BetaTrainingService._evaluate_baselines(feature_names, validation_rows, actual)
            baseline_metrics_by_window.append(baseline_metrics)
            window_metrics.append(
                {
                    "train_rows": len(train_rows),
                    "validation_rows": len(validation_rows),
                    "train_date_to": str(train_rows[-1].decision_date),
                    "validation_date_from": str(validation_rows[0].decision_date),
                    "validation_date_to": str(validation_rows[-1].decision_date),
                    "mae": model_metrics["mae"],
                    "sign_accuracy_pct": model_metrics["sign_accuracy_pct"],
                    "avg_return_pct": model_metrics["avg_return_pct"],
                    "avg_selected_return_pct": model_metrics["avg_selected_return_pct"],
                    "positive_rate_pct": model_metrics["positive_rate_pct"],
                    "baselines": baseline_metrics,
                }
            )

        if not window_metrics:
            return {
                "window_count": 0,
                "avg_validation_mae": None,
                "avg_validation_sign_accuracy_pct": None,
                "avg_validation_return_pct": None,
                "baseline_summaries": {},
                "windows": [],
            }

        baseline_summaries = BetaTrainingService._aggregate_baseline_metrics(baseline_metrics_by_window)
        best_baseline_name = None
        best_baseline_accuracy = None
        best_baseline_return = None
        if baseline_summaries:
            best_baseline_name, best_baseline_summary = max(
                baseline_summaries.items(),
                key=lambda item: float(item[1].get("sign_accuracy_pct") or 0.0),
            )
            best_baseline_accuracy = float(best_baseline_summary.get("sign_accuracy_pct") or 0.0)
            best_baseline_return = float(best_baseline_summary.get("avg_return_pct") or 0.0)
        return {
            "window_count": len(window_metrics),
            "avg_validation_mae": round(sum(float(row["mae"]) for row in window_metrics) / len(window_metrics), 4),
            "avg_validation_sign_accuracy_pct": round(
                sum(float(row["sign_accuracy_pct"]) for row in window_metrics) / len(window_metrics),
                2,
            ),
            "avg_validation_return_pct": round(
                sum(float(row["avg_return_pct"]) for row in window_metrics) / len(window_metrics),
                4,
            ),
            "baseline_summaries": baseline_summaries,
            "best_baseline_name": best_baseline_name,
            "best_baseline_sign_accuracy_pct": best_baseline_accuracy,
            "best_baseline_return_pct": best_baseline_return,
            "windows": window_metrics,
            "validation_window_rows": int(sum(int(row["validation_rows"]) for row in window_metrics) / len(window_metrics)),
            "validation_window_dates": validation_window_dates,
        }

    @staticmethod
    def ensure_daily_training() -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"trained": False, "performed": False, "reason": "beta_unavailable"}

        beta_db_path = get_beta_db_path()
        settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()
        if not BetaMarketSessionService.training_window_is_open(settings) and not BetaTrainingService.has_tracked_core_equity():
            with BetaContext.write_session() as sess:
                BetaTrainingService._record_training_decision(
                    sess,
                    status_code="SKIPPED",
                    reason_code="outside_training_window",
                    performed=False,
                    trained=False,
                )
            return {"trained": False, "performed": False, "reason": "outside_training_window"}

        with BetaContext.read_session() as sess:
            existing = sess.scalar(
                select(BetaModelVersion)
                .where(BetaModelVersion.model_name == BetaTrainingService._MODEL_NAME)
                .order_by(desc(BetaModelVersion.created_at))
                .limit(1)
            )
            if existing is not None and settings.retrain_min_new_observations > 0:
                new_rows = BetaTrainingService._new_observation_count_since_model(
                    sess,
                    model_created_at=existing.created_at,
                )
                model_age_hours = max(
                    0.0,
                    (datetime.now(timezone.utc).replace(tzinfo=None) - existing.created_at).total_seconds() / 3600.0,
                )
                if int(new_rows) < settings.retrain_min_new_observations and model_age_hours < BetaTrainingService._STALE_MODEL_RETRAIN_HOURS:
                    result = {
                        "trained": False,
                        "performed": False,
                        "reason": "insufficient_new_observations",
                        "new_observations": int(new_rows),
                        "model_age_hours": round(model_age_hours, 2),
                    }
                    with BetaContext.write_session() as write_sess:
                        BetaTrainingService._record_training_decision(
                            write_sess,
                            status_code="SKIPPED",
                            reason_code="insufficient_new_observations",
                            performed=False,
                            trained=False,
                            model_version_id=existing.id if existing is not None else None,
                            notes={
                                "new_observations": int(new_rows),
                                "model_age_hours": round(model_age_hours, 2),
                                "stale_model_retrain_hours": BetaTrainingService._STALE_MODEL_RETRAIN_HOURS,
                            },
                        )
                    return result

        result = BetaTrainingService.train_daily_challenger()
        result["performed"] = True
        return result

    @staticmethod
    def _activation_gate(
        *,
        current_active,
        train_rows: int,
        validation_rows: int,
        sign_accuracy: float,
        walkforward_accuracy: float,
        walkforward_return: float,
        window_count: int,
        best_holdout_baseline_accuracy: float | None,
        best_walkforward_baseline_accuracy: float | None,
        best_walkforward_baseline_return: float | None,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        if train_rows < BetaTrainingService._MIN_ACTIVATION_TRAIN_ROWS:
            reasons.append("train_rows_below_activation_floor")
        if validation_rows < BetaTrainingService._MIN_ACTIVATION_VALIDATION_ROWS:
            reasons.append("validation_rows_below_activation_floor")
        if window_count < BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_WINDOWS:
            reasons.append("walkforward_windows_below_activation_floor")
        if sign_accuracy < BetaTrainingService._MIN_ACTIVATION_SIGN_ACCURACY:
            reasons.append("validation_sign_accuracy_below_activation_floor")
        if walkforward_accuracy < BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_ACCURACY:
            reasons.append("walkforward_accuracy_below_activation_floor")
        if walkforward_return < BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_RETURN:
            reasons.append("walkforward_return_below_activation_floor")
        if (
            best_holdout_baseline_accuracy is not None
            and sign_accuracy < (best_holdout_baseline_accuracy + BetaTrainingService._MIN_ACTIVATION_HOLDOUT_BASELINE_LIFT)
        ):
            reasons.append("holdout_not_beating_baseline")
        if (
            best_walkforward_baseline_accuracy is not None
            and walkforward_accuracy
            < (best_walkforward_baseline_accuracy + BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_BASELINE_LIFT)
        ):
            reasons.append("walkforward_not_beating_baseline")
        if (
            best_walkforward_baseline_return is not None
            and walkforward_return
            < (best_walkforward_baseline_return + BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_RETURN_LIFT)
        ):
            reasons.append("walkforward_return_not_beating_baseline")
        if (
            sign_accuracy >= BetaTrainingService._SUSPICIOUS_SIGN_ACCURACY
            and abs(walkforward_return) <= BetaTrainingService._SUSPICIOUS_LOW_RETURN_ABS
            and window_count <= BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_WINDOWS
        ):
            reasons.append("validation_profile_suspiciously_perfect")

        active_metric = (
            float(current_active.validation_sign_accuracy_pct)
            if current_active is not None and current_active.validation_sign_accuracy_pct is not None
            else None
        )
        if current_active is not None and active_metric is not None:
            if sign_accuracy < (active_metric + 1.0):
                reasons.append("not_materially_better_than_active_model")
            if walkforward_accuracy < max(BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_ACCURACY, active_metric - 1.0):
                reasons.append("walkforward_not_confirming_active_model_replacement")

        return len(reasons) == 0, reasons

    @staticmethod
    def train_daily_challenger() -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"trained": False, "reason": "beta_unavailable"}
        if _np is None:
            return {"trained": False, "reason": "numpy_unavailable"}

        with BetaContext.write_session() as sess:
            label_def = sess.scalar(
                select(BetaLabelDefinition).where(BetaLabelDefinition.is_canonical.is_(True)).limit(1)
            )
            if label_def is None:
                BetaTrainingService._record_training_decision(
                    sess,
                    status_code="FAILED_PRECONDITION",
                    reason_code="no_canonical_label",
                    performed=True,
                    trained=False,
                )
                return {"trained": False, "reason": "no_canonical_label"}

            feature_defs = list(
                sess.scalars(
                    select(BetaFeatureDefinition)
                    .where(BetaFeatureDefinition.is_active.is_(True))
                    .order_by(BetaFeatureDefinition.feature_name.asc())
                ).all()
            )
            if not feature_defs:
                BetaTrainingService._record_training_decision(
                    sess,
                    status_code="FAILED_PRECONDITION",
                    reason_code="no_features",
                    performed=True,
                    trained=False,
                )
                return {"trained": False, "reason": "no_features"}

            labels = list(
                sess.scalars(
                    select(BetaLabelValue)
                    .where(BetaLabelValue.label_definition_id == label_def.id, BetaLabelValue.value_numeric.is_not(None))
                    .order_by(BetaLabelValue.decision_date.asc())
                ).all()
            )
            if len(labels) < BetaTrainingService._MIN_ROWS:
                BetaTrainingService._record_training_decision(
                    sess,
                    status_code="FAILED_DATA",
                    reason_code="insufficient_labels",
                    performed=True,
                    trained=False,
                    notes={"label_rows": len(labels)},
                )
                return {
                    "trained": False,
                    "reason": "insufficient_labels",
                    "label_rows": len(labels),
                }

            feature_ids = [row.id for row in feature_defs]
            feature_map: dict[tuple[str, str, object], float] = {}
            for value in sess.scalars(
                select(BetaFeatureValue).where(
                    BetaFeatureValue.feature_definition_id.in_(feature_ids),
                    BetaFeatureValue.value_numeric.is_not(None),
                )
            ).all():
                feature_map[(value.instrument_id, value.feature_definition_id, value.feature_date)] = float(
                    value.value_numeric
                )

            dataset: list[_DatasetRow] = []
            for label in labels:
                row_features: list[float] = []
                missing = False
                for feature_def in feature_defs:
                    value = feature_map.get((label.instrument_id, feature_def.id, label.decision_date))
                    if value is None:
                        missing = True
                        break
                    row_features.append(value)
                if missing:
                    continue
                dataset.append(
                    _DatasetRow(
                        instrument_id=label.instrument_id,
                        decision_date=label.decision_date,
                        features=row_features,
                        label=float(label.value_numeric),
                    )
                )

            if len(dataset) < BetaTrainingService._MIN_ROWS:
                BetaTrainingService._record_training_decision(
                    sess,
                    status_code="FAILED_DATA",
                    reason_code="insufficient_feature_aligned_rows",
                    performed=True,
                    trained=False,
                    notes={"dataset_rows": len(dataset)},
                )
                return {
                    "trained": False,
                    "reason": "insufficient_feature_aligned_rows",
                    "dataset_rows": len(dataset),
                }

            dataset.sort(key=lambda row: (row.decision_date, row.instrument_id))
            feature_names = [row.feature_name for row in feature_defs]
            train_rows, validation_rows, validation_date_count = BetaTrainingService._split_train_validation_rows(dataset)
            if len(validation_rows) < BetaTrainingService._MIN_VALIDATION_ROWS or len(train_rows) < BetaTrainingService._MIN_ROWS:
                BetaTrainingService._record_training_decision(
                    sess,
                    status_code="FAILED_DATA",
                    reason_code="insufficient_train_validation_rows",
                    performed=True,
                    trained=False,
                    notes={
                        "dataset_rows": len(dataset),
                        "train_rows": len(train_rows),
                        "validation_rows": len(validation_rows),
                        "validation_dates": validation_date_count,
                    },
                )
                return {
                    "trained": False,
                    "reason": "insufficient_train_validation_rows",
                    "dataset_rows": len(dataset),
                }

            x_train = BetaTrainingService._feature_matrix(train_rows)
            x_val = BetaTrainingService._feature_matrix(validation_rows)
            x_train, x_val, feature_clip_lows, feature_clip_highs = BetaTrainingService._winsorize_feature_matrices(
                x_train,
                x_val,
            )
            y_train = BetaTrainingService._label_vector(train_rows)
            y_val = BetaTrainingService._label_vector(validation_rows)
            y_train_clipped, label_clip_low, label_clip_high = BetaTrainingService._winsorize_label_vector(y_train)

            version_code = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:6]}"
            train_date_from, train_date_to = BetaTrainingService._date_bounds(train_rows)
            validation_date_from, validation_date_to = BetaTrainingService._date_bounds(validation_rows)
            dataset_version = BetaDatasetVersion(
                dataset_name="daily_training_dataset",
                version_code=version_code,
                label_definition_id=label_def.id,
                feature_names_json=json.dumps(feature_names, sort_keys=True),
                row_count=len(dataset),
                train_row_count=len(train_rows),
                validation_row_count=len(validation_rows),
                train_date_from=train_date_from,
                train_date_to=train_date_to,
                validation_date_from=validation_date_from,
                validation_date_to=validation_date_to,
                notes_json=json.dumps(
                    {
                        "label_name": label_def.label_name,
                        "label_version": label_def.version_code,
                        "instrument_count": len({row.instrument_id for row in dataset}),
                        "train_instrument_count": len({row.instrument_id for row in train_rows}),
                        "validation_instrument_count": len({row.instrument_id for row in validation_rows}),
                        "validation_date_count": validation_date_count,
                    },
                    sort_keys=True,
                ),
            )
            sess.add(dataset_version)
            sess.flush()

            for row in train_rows:
                sess.add(
                    BetaDatasetRow(
                        dataset_version_id=dataset_version.id,
                        instrument_id=row.instrument_id,
                        decision_date=row.decision_date,
                        split_label="TRAIN",
                        label_value_numeric=row.label,
                    )
                )
            for row in validation_rows:
                sess.add(
                    BetaDatasetRow(
                        dataset_version_id=dataset_version.id,
                        instrument_id=row.instrument_id,
                        decision_date=row.decision_date,
                        split_label="VALIDATION",
                        label_value_numeric=row.label,
                    )
                )

            experiment_run = BetaExperimentRun(
                experiment_name="daily_ridge_training",
                dataset_version_id=dataset_version.id,
                label_definition_id=label_def.id,
                status="RUNNING",
                summary_text="Training daily ridge challenger on canonical beta label with baseline-aware validation.",
            )
            sess.add(experiment_run)
            sess.flush()

            intercept, weights, means, scales = BetaTrainingService._fit_linear_model(
                x_train.tolist(),
                y_train_clipped.tolist(),
            )
            y_train_pred = BetaTrainingService._predict_rows(
                x_train.tolist(),
                intercept=intercept,
                weights=weights,
                means=means,
                scales=scales,
            )
            y_val_pred = BetaTrainingService._predict_rows(
                x_val.tolist(),
                intercept=intercept,
                weights=weights,
                means=means,
                scales=scales,
            )

            train_metrics = BetaTrainingService._evaluate_predictions(y_train_pred, y_train)
            holdout_metrics = BetaTrainingService._evaluate_predictions(y_val_pred, y_val)
            holdout_baselines = BetaTrainingService._evaluate_baselines(feature_names, validation_rows, y_val)
            confidence_calibration = BetaTrainingService._build_confidence_calibration(y_val_pred, y_val)
            best_holdout_baseline_name = None
            best_holdout_baseline_accuracy = None
            best_holdout_baseline_return = None
            validated_baseline_policy = None
            if holdout_baselines:
                best_holdout_baseline_name, best_holdout_baseline_summary = max(
                    holdout_baselines.items(),
                    key=lambda item: float(item[1].get("sign_accuracy_pct") or 0.0),
                )
                best_holdout_baseline_accuracy = float(best_holdout_baseline_summary.get("sign_accuracy_pct") or 0.0)
                best_holdout_baseline_return = float(best_holdout_baseline_summary.get("avg_return_pct") or 0.0)

            model = BetaModelVersion(
                model_name=BetaTrainingService._MODEL_NAME,
                version_code=version_code,
                algorithm=BetaTrainingService._ALGORITHM,
                status="TRAINED",
                is_active=False,
                is_challenger=True,
                dataset_version_id=dataset_version.id,
                training_row_count=len(train_rows),
                validation_row_count=len(validation_rows),
                feature_names_json=json.dumps(feature_names, sort_keys=True),
                coefficients_json=json.dumps([float(value) for value in weights], sort_keys=False),
                intercept_value=intercept,
                feature_means_json=json.dumps([float(value) for value in means], sort_keys=False),
                feature_scales_json=json.dumps([float(value) for value in scales], sort_keys=False),
                train_mae=float(train_metrics["mae"]),
                validation_mae=float(holdout_metrics["mae"]),
                validation_sign_accuracy_pct=float(holdout_metrics["sign_accuracy_pct"]),
                notes_json=json.dumps(
                    {
                        "label_name": label_def.label_name,
                        "label_version": label_def.version_code,
                        "holdout_metrics": holdout_metrics,
                        "holdout_baselines": holdout_baselines,
                        "best_holdout_baseline_name": best_holdout_baseline_name,
                        "best_holdout_baseline_sign_accuracy_pct": best_holdout_baseline_accuracy,
                        "best_holdout_baseline_return_pct": best_holdout_baseline_return,
                        "feature_clip_lows": [round(float(value), 6) for value in feature_clip_lows],
                        "feature_clip_highs": [round(float(value), 6) for value in feature_clip_highs],
                        "label_clip_range_pct": [round(float(label_clip_low), 6), round(float(label_clip_high), 6)],
                        "confidence_calibration": confidence_calibration,
                        "validated_baseline_policy": validated_baseline_policy,
                    },
                    sort_keys=True,
                ),
            )
            sess.add(model)
            sess.flush()

            validation_metrics = BetaTrainingService._walk_forward_validate(dataset, feature_names)
            walkforward_baselines = validation_metrics.get("baseline_summaries", {})
            validated_baseline_name = validation_metrics.get("best_baseline_name")
            if (
                isinstance(validated_baseline_name, str)
                and isinstance(walkforward_baselines, dict)
                and validated_baseline_name in holdout_baselines
                and validated_baseline_name in walkforward_baselines
            ):
                holdout_baseline = holdout_baselines[validated_baseline_name]
                walkforward_baseline = walkforward_baselines[validated_baseline_name]
                if (
                    float(holdout_baseline.get("sign_accuracy_pct") or 0.0) >= 50.0
                    and float(walkforward_baseline.get("sign_accuracy_pct") or 0.0) >= 50.0
                    and float(walkforward_baseline.get("avg_return_pct") or 0.0) > 0.0
                ):
                    baseline_predictions = BetaTrainingService._baseline_predictions(feature_names, validation_rows)[
                        validated_baseline_name
                    ]
                    validated_baseline_policy = {
                        "policy_name": validated_baseline_name,
                        "source_feature": holdout_baseline.get("source_feature"),
                        "holdout_sign_accuracy_pct": holdout_baseline.get("sign_accuracy_pct"),
                        "holdout_return_pct": holdout_baseline.get("avg_return_pct"),
                        "walkforward_sign_accuracy_pct": walkforward_baseline.get("sign_accuracy_pct"),
                        "walkforward_return_pct": walkforward_baseline.get("avg_return_pct"),
                        "confidence_calibration": BetaTrainingService._build_confidence_calibration(
                            baseline_predictions,
                            y_val,
                        ),
                    }
            validation_run = BetaValidationRun(
                validation_name="daily_ridge_walk_forward",
                validation_method="WALK_FORWARD",
                dataset_version_id=dataset_version.id,
                model_version_id=model.id,
                window_count=int(validation_metrics.get("window_count") or 0),
                train_window_rows=len(train_rows),
                validation_window_rows=int(validation_metrics.get("validation_window_rows") or len(validation_rows)),
                avg_validation_mae=validation_metrics.get("avg_validation_mae"),  # type: ignore[arg-type]
                avg_validation_sign_accuracy_pct=validation_metrics.get("avg_validation_sign_accuracy_pct"),  # type: ignore[arg-type]
                avg_validation_return_pct=validation_metrics.get("avg_validation_return_pct"),  # type: ignore[arg-type]
                summary_text="Walk-forward validation over rolling trailing windows.",
                notes_json=json.dumps(validation_metrics, sort_keys=True),
            )
            sess.add(validation_run)
            sess.flush()

            BetaTrainingService.enforce_active_model_governance(sess)
            current_active = sess.scalar(
                select(BetaModelVersion)
                .where(BetaModelVersion.is_active.is_(True))
                .order_by(desc(BetaModelVersion.created_at))
                .limit(1)
            )
            walkforward_accuracy = float(validation_metrics.get("avg_validation_sign_accuracy_pct") or 0.0)
            walkforward_return = float(validation_metrics.get("avg_validation_return_pct") or 0.0)
            window_count = int(validation_metrics.get("window_count") or 0)
            should_activate, activation_gate_reasons = BetaTrainingService._activation_gate(
                current_active=current_active,
                train_rows=len(train_rows),
                validation_rows=len(validation_rows),
                sign_accuracy=float(holdout_metrics["sign_accuracy_pct"]),
                walkforward_accuracy=walkforward_accuracy,
                walkforward_return=walkforward_return,
                window_count=window_count,
                best_holdout_baseline_accuracy=best_holdout_baseline_accuracy,
                best_walkforward_baseline_accuracy=(
                    float(validation_metrics.get("best_baseline_sign_accuracy_pct"))
                    if validation_metrics.get("best_baseline_sign_accuracy_pct") is not None
                    else None
                ),
                best_walkforward_baseline_return=(
                    float(validation_metrics.get("best_baseline_return_pct"))
                    if validation_metrics.get("best_baseline_return_pct") is not None
                    else None
                ),
            )
            activation_gate = {
                "should_activate": should_activate,
                "reasons": activation_gate_reasons,
                "minimums": {
                    "train_rows": BetaTrainingService._MIN_ACTIVATION_TRAIN_ROWS,
                    "validation_rows": BetaTrainingService._MIN_ACTIVATION_VALIDATION_ROWS,
                    "walkforward_windows": BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_WINDOWS,
                    "validation_sign_accuracy_pct": BetaTrainingService._MIN_ACTIVATION_SIGN_ACCURACY,
                    "walkforward_sign_accuracy_pct": BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_ACCURACY,
                    "walkforward_return_pct": BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_RETURN,
                    "holdout_baseline_lift_pct": BetaTrainingService._MIN_ACTIVATION_HOLDOUT_BASELINE_LIFT,
                    "walkforward_baseline_lift_pct": BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_BASELINE_LIFT,
                    "walkforward_return_lift_pct": BetaTrainingService._MIN_ACTIVATION_WALKFORWARD_RETURN_LIFT,
                },
                "holdout_best_baseline_name": best_holdout_baseline_name,
                "holdout_best_baseline_sign_accuracy_pct": best_holdout_baseline_accuracy,
                "holdout_best_baseline_return_pct": best_holdout_baseline_return,
                "walkforward_best_baseline_name": validation_metrics.get("best_baseline_name"),
                "walkforward_best_baseline_sign_accuracy_pct": validation_metrics.get("best_baseline_sign_accuracy_pct"),
                "walkforward_best_baseline_return_pct": validation_metrics.get("best_baseline_return_pct"),
            }
            if should_activate:
                if current_active is not None:
                    current_active.is_active = False
                    current_active.is_challenger = True
                model.is_active = True
                model.is_challenger = False
                model.status = "ACTIVE"
                model.activated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                model.status = "CHALLENGER"
            model.notes_json = json.dumps(
                {
                    "label_name": label_def.label_name,
                    "label_version": label_def.version_code,
                    "activation_gate": activation_gate,
                    "holdout_metrics": holdout_metrics,
                    "holdout_baselines": holdout_baselines,
                    "feature_clip_lows": [round(float(value), 6) for value in feature_clip_lows],
                    "feature_clip_highs": [round(float(value), 6) for value in feature_clip_highs],
                    "label_clip_range_pct": [round(float(label_clip_low), 6), round(float(label_clip_high), 6)],
                    "confidence_calibration": confidence_calibration,
                    "validated_baseline_policy": validated_baseline_policy,
                },
                sort_keys=True,
            )

            strategy = BetaStrategyService.sync_model_strategy(
                sess=sess,
                model=model,
                label_definition=label_def,
                validation_metrics=validation_metrics,
                activate=should_activate,
            )
            validation_run.strategy_version_id = strategy.id

            experiment_run.model_version_id = model.id
            experiment_run.status = model.status
            experiment_run.summary_text = (
                f"Holdout sign accuracy {float(holdout_metrics['sign_accuracy_pct']):.2f}% "
                f"vs baseline {float(best_holdout_baseline_accuracy or 0.0):.2f}%; "
                f"walk-forward {walkforward_accuracy:.2f}% vs baseline "
                f"{float(validation_metrics.get('best_baseline_sign_accuracy_pct') or 0.0):.2f}%; "
                f"activated={should_activate}."
            )
            experiment_run.notes_json = json.dumps(
                {
                    "dataset_version_id": dataset_version.id,
                    "strategy_version_id": strategy.id,
                    "validation_run_id": validation_run.id,
                    "feature_count": len(feature_defs),
                    "training_rows": len(train_rows),
                    "validation_rows": len(validation_rows),
                    "validation_sign_accuracy_pct": float(holdout_metrics["sign_accuracy_pct"]),
                    "validation_mae": float(holdout_metrics["mae"]),
                    "holdout_metrics": holdout_metrics,
                    "holdout_baselines": holdout_baselines,
                    "feature_clip_lows": [round(float(value), 6) for value in feature_clip_lows],
                    "feature_clip_highs": [round(float(value), 6) for value in feature_clip_highs],
                    "label_clip_range_pct": [round(float(label_clip_low), 6), round(float(label_clip_high), 6)],
                    "confidence_calibration": confidence_calibration,
                    "validated_baseline_policy": validated_baseline_policy,
                    "walkforward_validation": validation_metrics,
                    "activated": should_activate,
                    "activation_gate": activation_gate,
                },
                sort_keys=True,
            )
            BetaTrainingService._record_training_decision(
                sess,
                status_code="TRAINED_ACTIVATED" if should_activate else "TRAINED_CHALLENGER",
                reason_code=None if should_activate else (activation_gate_reasons[0] if activation_gate_reasons else "activation_gate_blocked"),
                performed=True,
                trained=True,
                activated=should_activate,
                model_version_id=model.id,
                validation_run_id=validation_run.id,
                training_rows=len(train_rows),
                validation_rows=len(validation_rows),
                walkforward_window_count=window_count,
                validation_sign_accuracy_pct=float(holdout_metrics["sign_accuracy_pct"]),
                walkforward_validation_sign_accuracy_pct=walkforward_accuracy,
                notes={
                    "version_code": version_code,
                    "activation_gate_reasons": activation_gate_reasons,
                    "holdout_baselines": holdout_baselines,
                    "best_holdout_baseline_name": best_holdout_baseline_name,
                    "best_holdout_baseline_sign_accuracy_pct": best_holdout_baseline_accuracy,
                    "walkforward_validation_return_pct": walkforward_return,
                    "walkforward_best_baseline_name": validation_metrics.get("best_baseline_name"),
                    "walkforward_best_baseline_sign_accuracy_pct": validation_metrics.get("best_baseline_sign_accuracy_pct"),
                    "validated_baseline_policy": validated_baseline_policy,
                },
            )

            return {
                "trained": True,
                "model_id": model.id,
                "strategy_version_id": strategy.id,
                "dataset_version_id": dataset_version.id,
                "experiment_run_id": experiment_run.id,
                "validation_run_id": validation_run.id,
                "version_code": version_code,
                "training_rows": len(train_rows),
                "validation_rows": len(validation_rows),
                "validation_sign_accuracy_pct": float(holdout_metrics["sign_accuracy_pct"]),
                "validation_mae": float(holdout_metrics["mae"]),
                "holdout_baselines": holdout_baselines,
                "best_holdout_baseline_name": best_holdout_baseline_name,
                "best_holdout_baseline_sign_accuracy_pct": best_holdout_baseline_accuracy,
                "validated_baseline_policy": validated_baseline_policy,
                "walkforward_validation_sign_accuracy_pct": walkforward_accuracy,
                "walkforward_validation_return_pct": walkforward_return,
                "walkforward_window_count": window_count,
                "activated": should_activate,
                "activation_gate_reasons": activation_gate_reasons,
            }
