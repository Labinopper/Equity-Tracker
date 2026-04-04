"""Track and analyze prediction accuracy across all signals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, stdev

from sqlalchemy import select

from ..context import BetaContext
from ..db.models import BetaCalibrationMetrics, BetaPredictionAccuracyLog


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaPredictionAccuracyService:
    """Track and analyze prediction accuracy across all signals."""

    # Confidence band thresholds
    HIGH_CONFIDENCE_THRESHOLD = 0.7
    MEDIUM_CONFIDENCE_THRESHOLD = 0.5

    @staticmethod
    def log_prediction(
        *,
        hypothesis_definition_id: str,
        signal_observation_id: str | None = None,
        execution_signal_id: str | None = None,
        predicted_return_pct: float,
        confidence_score: float,
        prediction_time: datetime | None = None,
        horizon_days: int,
    ) -> str:
        """
        Log a prediction for later accuracy tracking.
        
        Args:
            hypothesis_definition_id: ID of hypothesis that generated prediction
            signal_observation_id: Optional ID of signal observation (daily signals)
            execution_signal_id: Optional ID of execution signal (intraday signals)
            predicted_return_pct: Predicted return percentage
            confidence_score: Confidence score (0-1)
            prediction_time: When prediction was made (defaults to now)
            horizon_days: Prediction horizon in days
            
        Returns:
            ID of created prediction log entry
        """
        if not BetaContext.is_initialized():
            raise RuntimeError("BetaContext not initialized")

        if prediction_time is None:
            prediction_time = _utcnow()

        confidence_band = BetaPredictionAccuracyService._confidence_band(confidence_score)

        with BetaContext.write_session() as sess:
            log_entry = BetaPredictionAccuracyService._existing_prediction_log(
                sess,
                signal_observation_id=signal_observation_id,
                execution_signal_id=execution_signal_id,
            )
            if log_entry is None:
                log_entry = BetaPredictionAccuracyLog(
                    hypothesis_definition_id=hypothesis_definition_id,
                    signal_observation_id=signal_observation_id,
                    execution_signal_id=execution_signal_id,
                    predicted_return_pct=predicted_return_pct,
                    confidence_score=confidence_score,
                    confidence_band=confidence_band,
                    prediction_time=prediction_time,
                    horizon_days=horizon_days,
                )
                sess.add(log_entry)
            else:
                log_entry.hypothesis_definition_id = hypothesis_definition_id
                log_entry.signal_observation_id = signal_observation_id
                log_entry.execution_signal_id = execution_signal_id
                log_entry.predicted_return_pct = predicted_return_pct
                log_entry.confidence_score = confidence_score
                log_entry.confidence_band = confidence_band
                log_entry.prediction_time = prediction_time
                log_entry.horizon_days = horizon_days
            sess.flush()
            return log_entry.id

    @staticmethod
    def update_realized_outcome(
        *,
        prediction_log_id: str,
        realized_return_pct: float,
        realization_time: datetime | None = None,
    ) -> None:
        """
        Update prediction log with realized outcome.
        
        Args:
            prediction_log_id: ID of prediction log entry
            realized_return_pct: Actual realized return percentage
            realization_time: When outcome was realized (defaults to now)
        """
        if not BetaContext.is_initialized():
            return

        if realization_time is None:
            realization_time = _utcnow()

        with BetaContext.write_session() as sess:
            log_entry = sess.get(BetaPredictionAccuracyLog, prediction_log_id)
            if log_entry is None:
                return

            log_entry.realized_return_pct = realized_return_pct
            log_entry.realization_time = realization_time
            log_entry.prediction_error_pct = realized_return_pct - log_entry.predicted_return_pct

            # Check directional match
            predicted_direction = BetaPredictionAccuracyService._direction(log_entry.predicted_return_pct)
            realized_direction = BetaPredictionAccuracyService._direction(realized_return_pct)
            log_entry.directional_match = 1 if predicted_direction == realized_direction else 0

    @staticmethod
    def compute_calibration_metrics(
        *,
        hypothesis_definition_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        lookback_days: int = 30,
    ) -> dict[str, object]:
        """
        Compute calibration metrics for a hypothesis or all hypotheses.
        
        Args:
            hypothesis_definition_id: Optional specific hypothesis to analyze
            period_start: Start of evaluation period (defaults to lookback_days ago)
            period_end: End of evaluation period (defaults to now)
            lookback_days: Number of days to look back if period_start not provided
            
        Returns:
            Dictionary with calibration metrics by confidence band
        """
        if not BetaContext.is_initialized():
            return {"error": "BetaContext not initialized"}

        if period_end is None:
            period_end = _utcnow()

        if period_start is None:
            period_start = period_end - timedelta(days=lookback_days)

        with BetaContext.read_session() as sess:
            query = select(BetaPredictionAccuracyLog).where(
                BetaPredictionAccuracyLog.prediction_time >= period_start,
                BetaPredictionAccuracyLog.prediction_time <= period_end,
                BetaPredictionAccuracyLog.realized_return_pct.is_not(None),
            )
            
            if hypothesis_definition_id:
                query = query.where(
                    BetaPredictionAccuracyLog.hypothesis_definition_id == hypothesis_definition_id
                )

            logs = list(sess.scalars(query).all())

        by_band_logs: dict[str, list[BetaPredictionAccuracyLog]] = {}
        for log in logs:
            by_band_logs.setdefault(log.confidence_band, []).append(log)

        by_confidence_band = {
            band: BetaPredictionAccuracyService._compute_band_metrics(band_logs)
            for band, band_logs in sorted(by_band_logs.items())
        }
        overall = BetaPredictionAccuracyService._compute_band_metrics(logs)

        if overall["count"] > 0:
            BetaPredictionAccuracyService._persist_calibration_metrics(
                hypothesis_definition_id=hypothesis_definition_id,
                period_start=period_start,
                period_end=period_end,
                by_confidence_band=by_confidence_band,
            )

        return {
            "hypothesis_definition_id": hypothesis_definition_id,
            "period_start": period_start,
            "period_end": period_end,
            "overall": overall,
            "by_confidence_band": by_confidence_band,
        }

    @staticmethod
    def _compute_band_metrics(logs: list[BetaPredictionAccuracyLog]) -> dict[str, object]:
        """Compute metrics for a single confidence band."""
        # Filter out None values (should not happen since we query for realized_return_pct.is_not(None))
        predicted_returns = [log.predicted_return_pct for log in logs]
        realized_returns = [log.realized_return_pct for log in logs if log.realized_return_pct is not None]
        errors = [log.prediction_error_pct for log in logs if log.prediction_error_pct is not None]
        directional_matches = [log.directional_match for log in logs if log.directional_match is not None]
        
        mae = mean([abs(e) for e in errors]) if errors else 0.0
        rmse = (mean([e**2 for e in errors])) ** 0.5 if errors else 0.0
        directional_accuracy = mean(directional_matches) * 100 if directional_matches else 0.0
        win_rate = len([r for r in realized_returns if r > 0]) / len(realized_returns) * 100 if realized_returns else 0.0
        
        # Calibration error: difference between predicted and observed probabilities
        mean_predicted = mean(predicted_returns) if predicted_returns else 0.0
        mean_realized = mean(realized_returns) if realized_returns else 0.0
        calibration_error = abs(mean_predicted - mean_realized)
        
        # Sharpe ratio (if enough data)
        sharpe = None
        if len(realized_returns) >= 10:
            mean_return = mean(realized_returns)
            std_return = stdev(realized_returns) if len(realized_returns) > 1 else 0.0
            sharpe = (mean_return / std_return) if std_return > 0 else 0.0
        
        # Information ratio (excess return over prediction error)
        information_ratio = None
        if mae > 0:
            information_ratio = mean_realized / mae

        return {
            "count": len(logs),
            "prediction_count": len(logs),
            "mean_predicted_return_pct": round(mean_predicted, 4),
            "mean_realized_return_pct": round(mean_realized, 4),
            "mae": round(mae, 4),
            "mean_absolute_error_pct": round(mae, 4),
            "rmse": round(rmse, 4),
            "root_mean_squared_error_pct": round(rmse, 4),
            "directional_accuracy_pct": round(directional_accuracy, 2),
            "win_rate_pct": round(win_rate, 2),
            "calibration_error_pct": round(calibration_error, 4),
            "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
            "information_ratio": round(information_ratio, 3) if information_ratio is not None else None,
        }

    @staticmethod
    def get_hypothesis_accuracy(
        *,
        hypothesis_definition_id: str,
        lookback_days: int = 90,
    ) -> dict[str, object]:
        """
        Get accuracy metrics for a specific hypothesis.
        
        Args:
            hypothesis_definition_id: ID of hypothesis to analyze
            lookback_days: Number of days to look back
            
        Returns:
            Dictionary with accuracy metrics
        """
        period_end = _utcnow()
        period_start = period_end - timedelta(days=lookback_days)

        return BetaPredictionAccuracyService.compute_calibration_metrics(
            hypothesis_definition_id=hypothesis_definition_id,
            period_start=period_start,
            period_end=period_end,
        )

    @staticmethod
    def get_overall_accuracy(
        *,
        lookback_days: int = 30,
    ) -> dict[str, object]:
        """
        Get system-wide accuracy metrics.
        
        Args:
            lookback_days: Number of days to look back
            
        Returns:
            Dictionary with overall accuracy metrics
        """
        period_end = _utcnow()
        period_start = period_end - timedelta(days=lookback_days)

        return BetaPredictionAccuracyService.compute_calibration_metrics(
            hypothesis_definition_id=None,
            period_start=period_start,
            period_end=period_end,
        )

    @staticmethod
    def get_accuracy_trends(
        *,
        hypothesis_definition_id: str | None = None,
        lookback_days: int = 90,
        window_days: int = 7,
    ) -> list[dict[str, object]]:
        """
        Get accuracy trends over time using rolling windows.
        
        Args:
            hypothesis_definition_id: Optional specific hypothesis
            lookback_days: Total period to analyze
            window_days: Size of rolling window
            
        Returns:
            List of metrics for each window
        """
        if not BetaContext.is_initialized():
            return []

        period_end = _utcnow()
        period_start = period_end - timedelta(days=lookback_days)

        trends = []
        current_end = period_end

        while current_end > period_start:
            current_start = current_end - timedelta(days=window_days)

            if current_start < period_start:
                current_start = period_start

            metrics = BetaPredictionAccuracyService.compute_calibration_metrics(
                hypothesis_definition_id=hypothesis_definition_id,
                period_start=current_start,
                period_end=current_end,
            )

            if metrics.get("overall", {}).get("count", 0) > 0:
                trends.append(
                    {
                        "period_start": current_start.isoformat(),
                        "period_end": current_end.isoformat(),
                        "metrics": metrics,
                    }
                )

            current_end = current_start

        return list(reversed(trends))

    @staticmethod
    def _confidence_band(score: float) -> str:
        """Map confidence score to band."""
        if score >= BetaPredictionAccuracyService.HIGH_CONFIDENCE_THRESHOLD:
            return "HIGH"
        if score >= BetaPredictionAccuracyService.MEDIUM_CONFIDENCE_THRESHOLD:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _direction(value: float) -> int:
        if value > 0:
            return 1
        if value < 0:
            return -1
        return 0

    @staticmethod
    def _existing_prediction_log(
        sess,
        *,
        signal_observation_id: str | None,
        execution_signal_id: str | None,
    ) -> BetaPredictionAccuracyLog | None:
        if execution_signal_id:
            return sess.scalar(
                select(BetaPredictionAccuracyLog)
                .where(BetaPredictionAccuracyLog.execution_signal_id == execution_signal_id)
                .limit(1)
            )
        if signal_observation_id:
            return sess.scalar(
                select(BetaPredictionAccuracyLog)
                .where(BetaPredictionAccuracyLog.signal_observation_id == signal_observation_id)
                .limit(1)
            )
        return None

    @staticmethod
    def _persist_calibration_metrics(
        *,
        hypothesis_definition_id: str | None,
        period_start: datetime,
        period_end: datetime,
        by_confidence_band: dict[str, dict[str, object]],
    ) -> None:
        if not by_confidence_band:
            return

        evaluation_period_start = period_start.date()
        evaluation_period_end = period_end.date()
        with BetaContext.write_session() as sess:
            for band, metrics in by_confidence_band.items():
                existing = sess.scalar(
                    select(BetaCalibrationMetrics)
                    .where(
                        BetaCalibrationMetrics.hypothesis_definition_id == hypothesis_definition_id,
                        BetaCalibrationMetrics.confidence_band == band,
                        BetaCalibrationMetrics.evaluation_period_start == evaluation_period_start,
                        BetaCalibrationMetrics.evaluation_period_end == evaluation_period_end,
                    )
                    .limit(1)
                )
                if existing is None:
                    existing = BetaCalibrationMetrics(
                        hypothesis_definition_id=hypothesis_definition_id,
                        confidence_band=band,
                        evaluation_period_start=evaluation_period_start,
                        evaluation_period_end=evaluation_period_end,
                    )
                    sess.add(existing)
                existing.prediction_count = int(metrics["prediction_count"])
                existing.realized_count = int(metrics["prediction_count"])
                existing.mean_predicted_return_pct = float(metrics["mean_predicted_return_pct"])
                existing.mean_realized_return_pct = float(metrics["mean_realized_return_pct"])
                existing.mean_absolute_error_pct = float(metrics["mean_absolute_error_pct"])
                existing.root_mean_squared_error_pct = float(metrics["root_mean_squared_error_pct"])
                existing.directional_accuracy_pct = float(metrics["directional_accuracy_pct"])
                existing.win_rate_pct = float(metrics["win_rate_pct"])
                existing.calibration_error_pct = float(metrics["calibration_error_pct"])
                existing.sharpe_ratio = (
                    float(metrics["sharpe_ratio"]) if metrics.get("sharpe_ratio") is not None else None
                )
                existing.information_ratio = (
                    float(metrics["information_ratio"]) if metrics.get("information_ratio") is not None else None
                )
