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
    BetaLabelDefinition,
    BetaLabelValue,
    BetaModelVersion,
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

    _MODEL_NAME = "daily_linear_v1"
    _ALGORITHM = "numpy_lstsq"
    _MIN_ROWS = 20
    _MIN_VALIDATION_ROWS = 5
    _MIN_WALKFORWARD_WINDOWS = 2

    @staticmethod
    def _date_bounds(rows: list[_DatasetRow]) -> tuple[object | None, object | None]:
        if not rows:
            return None, None
        return rows[0].decision_date, rows[-1].decision_date

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
        coeffs, *_ = _np.linalg.lstsq(x_train_design, y_train, rcond=None)
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
    def _walk_forward_validate(dataset: list[_DatasetRow]) -> dict[str, object]:
        if _np is None or len(dataset) < (BetaTrainingService._MIN_ROWS + BetaTrainingService._MIN_VALIDATION_ROWS):
            return {
                "window_count": 0,
                "avg_validation_mae": None,
                "avg_validation_sign_accuracy_pct": None,
                "avg_validation_return_pct": None,
                "windows": [],
            }

        validation_window_size = max(
            BetaTrainingService._MIN_VALIDATION_ROWS,
            min(20, max(5, len(dataset) // 6)),
        )
        final_train_start = max(BetaTrainingService._MIN_ROWS, len(dataset) - (validation_window_size * BetaTrainingService._MIN_WALKFORWARD_WINDOWS))
        candidate_starts = list(
            range(
                final_train_start,
                len(dataset) - validation_window_size + 1,
                validation_window_size,
            )
        )
        if not candidate_starts:
            candidate_starts = [max(BetaTrainingService._MIN_ROWS, len(dataset) - validation_window_size)]

        window_metrics: list[dict[str, object]] = []
        for start_index in candidate_starts:
            train_rows = dataset[:start_index]
            validation_rows = dataset[start_index : start_index + validation_window_size]
            if len(train_rows) < BetaTrainingService._MIN_ROWS or len(validation_rows) < BetaTrainingService._MIN_VALIDATION_ROWS:
                continue
            intercept, weights, means, scales = BetaTrainingService._fit_linear_model(
                [row.features for row in train_rows],
                [row.label for row in train_rows],
            )
            predictions = BetaTrainingService._predict_rows(
                [row.features for row in validation_rows],
                intercept=intercept,
                weights=weights,
                means=means,
                scales=scales,
            )
            actual = _np.array([row.label for row in validation_rows], dtype=float)
            mae = float(_np.mean(_np.abs(predictions - actual)))
            sign_accuracy = float(_np.mean((_np.sign(predictions) == _np.sign(actual)).astype(float)) * 100.0)
            positive_mask = predictions > 0
            if positive_mask.any():
                avg_return = float(_np.mean(actual[positive_mask]))
            else:
                avg_return = float(_np.mean(actual))
            window_metrics.append(
                {
                    "train_rows": len(train_rows),
                    "validation_rows": len(validation_rows),
                    "train_date_to": str(train_rows[-1].decision_date),
                    "validation_date_from": str(validation_rows[0].decision_date),
                    "validation_date_to": str(validation_rows[-1].decision_date),
                    "mae": round(mae, 4),
                    "sign_accuracy_pct": round(sign_accuracy, 2),
                    "avg_return_pct": round(avg_return, 4),
                }
            )

        if not window_metrics:
            return {
                "window_count": 0,
                "avg_validation_mae": None,
                "avg_validation_sign_accuracy_pct": None,
                "avg_validation_return_pct": None,
                "windows": [],
            }

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
            "windows": window_metrics,
            "validation_window_rows": validation_window_size,
        }

    @staticmethod
    def ensure_daily_training() -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"trained": False, "performed": False, "reason": "beta_unavailable"}

        beta_db_path = get_beta_db_path()
        settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()
        if not BetaMarketSessionService.training_window_is_open(settings):
            return {"trained": False, "performed": False, "reason": "outside_training_window"}

        with BetaContext.read_session() as sess:
            existing = sess.scalar(
                select(BetaModelVersion)
                .where(BetaModelVersion.model_name == BetaTrainingService._MODEL_NAME)
                .order_by(desc(BetaModelVersion.created_at))
                .limit(1)
            )
            if existing is not None and existing.created_at.date() == date.today():
                return {
                    "trained": False,
                    "performed": False,
                    "reason": "already_trained_today",
                    "model_id": existing.id,
                }
            if existing is not None and settings.retrain_min_new_observations > 0:
                new_rows = (
                    sess.scalar(
                        select(func.count())
                        .where(
                            BetaLabelValue.value_numeric.is_not(None),
                            BetaLabelValue.decision_date > existing.created_at.date(),
                        )
                    )
                    or 0
                )
                if int(new_rows) < settings.retrain_min_new_observations:
                    return {
                        "trained": False,
                        "performed": False,
                        "reason": "insufficient_new_observations",
                        "new_observations": int(new_rows),
                    }

        result = BetaTrainingService.train_daily_challenger()
        result["performed"] = True
        return result

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
                return {"trained": False, "reason": "no_canonical_label"}

            feature_defs = list(
                sess.scalars(
                    select(BetaFeatureDefinition)
                    .where(BetaFeatureDefinition.is_active.is_(True))
                    .order_by(BetaFeatureDefinition.feature_name.asc())
                ).all()
            )
            if not feature_defs:
                return {"trained": False, "reason": "no_features"}

            labels = list(
                sess.scalars(
                    select(BetaLabelValue)
                    .where(BetaLabelValue.label_definition_id == label_def.id, BetaLabelValue.value_numeric.is_not(None))
                    .order_by(BetaLabelValue.decision_date.asc())
                ).all()
            )
            if len(labels) < BetaTrainingService._MIN_ROWS:
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
                return {
                    "trained": False,
                    "reason": "insufficient_feature_aligned_rows",
                    "dataset_rows": len(dataset),
                }

            dataset.sort(key=lambda row: (row.decision_date, row.instrument_id))
            split_index = max(BetaTrainingService._MIN_VALIDATION_ROWS, int(len(dataset) * 0.2))
            train_rows = dataset[:-split_index]
            validation_rows = dataset[-split_index:]
            if len(train_rows) < BetaTrainingService._MIN_ROWS - BetaTrainingService._MIN_VALIDATION_ROWS:
                return {
                    "trained": False,
                    "reason": "insufficient_train_rows",
                    "dataset_rows": len(dataset),
                }

            x_train = _np.array([row.features for row in train_rows], dtype=float)
            y_train = _np.array([row.label for row in train_rows], dtype=float)
            x_val = _np.array([row.features for row in validation_rows], dtype=float)
            y_val = _np.array([row.label for row in validation_rows], dtype=float)

            version_code = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:6]}"
            train_date_from, train_date_to = BetaTrainingService._date_bounds(train_rows)
            validation_date_from, validation_date_to = BetaTrainingService._date_bounds(validation_rows)
            dataset_version = BetaDatasetVersion(
                dataset_name="daily_training_dataset",
                version_code=version_code,
                label_definition_id=label_def.id,
                feature_names_json=json.dumps([row.feature_name for row in feature_defs], sort_keys=True),
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
                experiment_name="daily_linear_training",
                dataset_version_id=dataset_version.id,
                label_definition_id=label_def.id,
                status="RUNNING",
                summary_text="Training daily linear challenger on canonical beta label.",
            )
            sess.add(experiment_run)
            sess.flush()

            intercept, weights, means, scales = BetaTrainingService._fit_linear_model(
                [row.features for row in train_rows],
                [row.label for row in train_rows],
            )
            y_train_pred = BetaTrainingService._predict_rows(
                [row.features for row in train_rows],
                intercept=intercept,
                weights=weights,
                means=means,
                scales=scales,
            )
            y_val_pred = BetaTrainingService._predict_rows(
                [row.features for row in validation_rows],
                intercept=intercept,
                weights=weights,
                means=means,
                scales=scales,
            )

            train_mae = float(_np.mean(_np.abs(y_train_pred - y_train)))
            val_mae = float(_np.mean(_np.abs(y_val_pred - y_val)))
            sign_accuracy = float(_np.mean((_np.sign(y_val_pred) == _np.sign(y_val)).astype(float)) * 100.0)

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
                feature_names_json=json.dumps([row.feature_name for row in feature_defs], sort_keys=True),
                coefficients_json=json.dumps([float(value) for value in weights], sort_keys=False),
                intercept_value=intercept,
                feature_means_json=json.dumps([float(value) for value in means], sort_keys=False),
                feature_scales_json=json.dumps([float(value) for value in scales], sort_keys=False),
                train_mae=train_mae,
                validation_mae=val_mae,
                validation_sign_accuracy_pct=round(sign_accuracy, 2),
                notes_json=json.dumps(
                    {
                        "label_name": label_def.label_name,
                        "label_version": label_def.version_code,
                    },
                    sort_keys=True,
                ),
            )
            sess.add(model)
            sess.flush()

            validation_metrics = BetaTrainingService._walk_forward_validate(dataset)
            validation_run = BetaValidationRun(
                validation_name="daily_linear_walk_forward",
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

            current_active = sess.scalar(
                select(BetaModelVersion)
                .where(BetaModelVersion.is_active.is_(True))
                .order_by(desc(BetaModelVersion.created_at))
                .limit(1)
            )
            active_metric = (
                float(current_active.validation_sign_accuracy_pct)
                if current_active is not None and current_active.validation_sign_accuracy_pct is not None
                else None
            )
            walkforward_accuracy = float(validation_metrics.get("avg_validation_sign_accuracy_pct") or 0.0)
            walkforward_return = float(validation_metrics.get("avg_validation_return_pct") or 0.0)
            should_activate = (
                current_active is None
                or (
                    active_metric is not None
                    and sign_accuracy >= (active_metric + 1.0)
                    and walkforward_accuracy >= max(50.0, active_metric - 1.0)
                    and walkforward_return >= -0.25
                )
                or (
                    active_metric is None
                    and walkforward_accuracy >= 52.0
                    and walkforward_return >= -0.25
                )
            )
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
                f"Validation sign accuracy {round(sign_accuracy, 2)}%, "
                f"validation MAE {round(val_mae, 4)}, walk-forward {walkforward_accuracy:.2f}%, "
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
                    "validation_sign_accuracy_pct": round(sign_accuracy, 2),
                    "validation_mae": round(val_mae, 4),
                    "walkforward_validation": validation_metrics,
                    "activated": should_activate,
                },
                sort_keys=True,
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
                "validation_sign_accuracy_pct": round(sign_accuracy, 2),
                "validation_mae": round(val_mae, 4),
                "walkforward_validation_sign_accuracy_pct": walkforward_accuracy,
                "walkforward_validation_return_pct": walkforward_return,
                "activated": should_activate,
            }
