from __future__ import annotations

import json
import math
from datetime import date, timedelta

from sqlalchemy import select

from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import (
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaInstrument,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaModelVersion,
    BetaValidationRun,
)
from src.beta.services.training_service import BetaTrainingService


def _seed_training_dataset() -> None:
    with BetaContext.write_session() as sess:
        instrument = BetaInstrument(
            symbol="IBM",
            name="IBM",
            market="US",
            exchange="NASDAQ",
            currency="USD",
        )
        sess.add(instrument)
        sess.flush()

        feature_defs: dict[str, BetaFeatureDefinition] = {}
        for feature_name in ("market_excess_5d_pct", "ret_5d_pct", "realized_vol_20d_pct"):
            feature_def = BetaFeatureDefinition(
                feature_name=feature_name,
                version_code="test_v1",
                feature_family="training",
                timeframe="1D",
                is_active=True,
            )
            sess.add(feature_def)
            sess.flush()
            feature_defs[feature_name] = feature_def

        label_def = BetaLabelDefinition(
            label_name="fwd_5d_excess_return_pct",
            version_code="test_v1",
            horizon_days=5,
            definition_text="synthetic test label",
            is_canonical=True,
        )
        sess.add(label_def)
        sess.flush()

        start_date = date(2025, 9, 1)
        for offset in range(140):
            decision_date = start_date + timedelta(days=offset)
            market_excess = math.sin(offset / 9.0) * 1.4
            ret_5d = math.cos(offset / 13.0) * 1.1
            realized_vol = 3.0 + ((offset % 12) * 0.18)
            label_value = round((0.65 * market_excess) - (0.35 * ret_5d) - (0.04 * realized_vol), 4)

            sess.add_all(
                [
                    BetaFeatureValue(
                        feature_definition_id=feature_defs["market_excess_5d_pct"].id,
                        instrument_id=instrument.id,
                        feature_date=decision_date,
                        value_numeric=market_excess,
                    ),
                    BetaFeatureValue(
                        feature_definition_id=feature_defs["ret_5d_pct"].id,
                        instrument_id=instrument.id,
                        feature_date=decision_date,
                        value_numeric=ret_5d,
                    ),
                    BetaFeatureValue(
                        feature_definition_id=feature_defs["realized_vol_20d_pct"].id,
                        instrument_id=instrument.id,
                        feature_date=decision_date,
                        value_numeric=realized_vol,
                    ),
                    BetaLabelValue(
                        label_definition_id=label_def.id,
                        instrument_id=instrument.id,
                        decision_date=decision_date,
                        horizon_end_date=decision_date + timedelta(days=5),
                        value_numeric=label_value,
                    ),
                ]
            )


def test_daily_training_records_sklearn_xgboost_and_statsmodels_metadata() -> None:
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    try:
        _seed_training_dataset()

        result = BetaTrainingService.train_daily_challenger()

        with BetaContext.read_session() as sess:
            model = sess.scalar(
                select(BetaModelVersion).order_by(BetaModelVersion.created_at.desc()).limit(1)
            )
            validation_run = sess.scalar(
                select(BetaValidationRun).order_by(BetaValidationRun.created_at.desc()).limit(1)
            )

        assert result["trained"] is True
        assert model is not None
        assert validation_run is not None
        assert model.algorithm == BetaTrainingService._ALGORITHM

        model_notes = json.loads(model.notes_json or "{}")
        validation_notes = json.loads(validation_run.notes_json or "{}")

        benchmark_challengers = model_notes.get("benchmark_challengers") or {}
        statsmodels_checks = model_notes.get("statsmodels_sanity_checks") or {}
        walkforward_benchmarks = validation_notes.get("benchmark_summaries") or {}

        assert model_notes["modeling_stack"]["primary_model"]["uses_sklearn_pipeline"] is True
        assert "xgboost_regressor" in benchmark_challengers
        assert benchmark_challengers["xgboost_regressor"]["available"] is True
        assert benchmark_challengers["xgboost_regressor"]["trained"] is True
        assert statsmodels_checks["available"] is True
        assert statsmodels_checks["label_stationarity"]["available"] is True
        assert "ols_inference" in statsmodels_checks
        assert "market_regime" in statsmodels_checks["regime_slices"]
        assert "xgboost_regressor" in walkforward_benchmarks
    finally:
        BetaContext.lock()
        engine.dispose()
