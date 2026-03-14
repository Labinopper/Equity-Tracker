"""Simple heuristic scoring loop for the beta shadow/demo lane."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from statistics import pstdev

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaDailyBar,
    BetaDemoPosition,
    BetaDemoPositionEvent,
    BetaFeatureDefinition,
    BetaFeatureValue,
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaHypothesis,
    BetaIntradaySnapshot,
    BetaInstrument,
    BetaModelVersion,
    BetaNewsArticle,
    BetaNewsArticleLink,
    BetaScoreRun,
    BetaScoreTape,
    BetaSignalCandidate,
    BetaSignalCandidateEvent,
    BetaStrategyVersion,
)
from .allocation_service import BetaAllocationService
from .hypothesis_signal_service import BetaHypothesisSignalService
from .risk_control_service import BetaRiskControlService
from .session_service import BetaMarketSessionService
from ..settings import BetaSettings
from .hypothesis_service import BetaHypothesisService
from .strategy_service import BetaStrategyService
from .training_service import BetaTrainingService


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _size_gbp(confidence: float, edge: float) -> str:
    base = Decimal("250")
    scaled = Decimal(str(max(0.0, confidence) * 1000 + max(0.0, edge) * 600))
    size = min(Decimal("2000"), base + scaled)
    return str(size.quantize(Decimal("0.01")))


def _quant_money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _quant_price(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001")))


def _friction_bps(settings: BetaSettings, market: str | None) -> Decimal:
    normalized = str(market or "").upper()
    if normalized == "UK":
        return Decimal(str(settings.uk_equity_friction_bps))
    if normalized == "US":
        return Decimal(str(settings.us_equity_friction_bps))
    if normalized == "FX":
        return Decimal(str(settings.fx_trade_friction_bps))
    return Decimal(str(max(settings.uk_equity_friction_bps, settings.us_equity_friction_bps)))


def _entry_fill_price(mark_price: Decimal, settings: BetaSettings, market: str | None) -> Decimal:
    bps = _friction_bps(settings, market)
    return mark_price * (Decimal("1") + (bps / Decimal("10000")))


def _exit_fill_price(mark_price: Decimal, settings: BetaSettings, market: str | None) -> Decimal:
    bps = _friction_bps(settings, market)
    adjusted = mark_price * (Decimal("1") - (bps / Decimal("10000")))
    return adjusted if adjusted > 0 else Decimal("0.0001")


def _position_units(position: BetaDemoPosition) -> Decimal | None:
    units = _as_decimal(position.units)
    if units is not None and units > 0:
        return units
    size = _as_decimal(position.size_gbp)
    entry = _as_decimal(position.entry_price)
    if size is None or entry is None or entry <= 0:
        return None
    return size / entry


def _record_candidate_event(*, sess, candidate_id: str, event_type: str, message_text: str, payload: dict) -> None:
    latest = sess.scalar(
        select(BetaSignalCandidateEvent)
        .where(BetaSignalCandidateEvent.candidate_id == candidate_id)
        .order_by(desc(BetaSignalCandidateEvent.created_at))
        .limit(1)
    )
    payload_json = json.dumps(payload, sort_keys=True)
    if latest is not None and latest.event_type == event_type and latest.message_text == message_text:
        return
    sess.add(
        BetaSignalCandidateEvent(
            candidate_id=candidate_id,
            event_type=event_type,
            message_text=message_text,
            payload_json=payload_json,
        )
    )


def _bars_held(entry_bar_date, current_bar_date, bars: list[BetaDailyBar]) -> int:
    if entry_bar_date is None:
        return 0
    return len([bar for bar in bars if bar.bar_date > entry_bar_date and bar.bar_date <= current_bar_date])


def _latest_intraday_snapshot(sess, instrument_id: str) -> BetaIntradaySnapshot | None:
    return sess.scalar(
        select(BetaIntradaySnapshot)
        .where(BetaIntradaySnapshot.instrument_id == instrument_id)
        .order_by(desc(BetaIntradaySnapshot.observed_at))
        .limit(1)
    )


def _load_active_model(sess) -> dict | None:
    row = sess.scalar(
        select(BetaModelVersion)
        .where(BetaModelVersion.is_active.is_(True))
        .order_by(desc(BetaModelVersion.activated_at), desc(BetaModelVersion.created_at))
        .limit(1)
    )
    if row is None:
        return None
    try:
        feature_names = list(json.loads(row.feature_names_json))
        coefficients = [float(value) for value in json.loads(row.coefficients_json)]
        means = [float(value) for value in json.loads(row.feature_means_json or "[]")]
        scales = [float(value) for value in json.loads(row.feature_scales_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not feature_names or len(feature_names) != len(coefficients):
        return None
    if len(means) != len(feature_names) or len(scales) != len(feature_names):
        return None
    try:
        notes = json.loads(row.notes_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        notes = {}
    calibration = notes.get("confidence_calibration") if isinstance(notes, dict) else None
    feature_clip_lows = notes.get("feature_clip_lows") if isinstance(notes, dict) else None
    feature_clip_highs = notes.get("feature_clip_highs") if isinstance(notes, dict) else None
    return {
        "id": row.id,
        "version_code": row.version_code,
        "feature_names": feature_names,
        "coefficients": coefficients,
        "means": means,
        "scales": scales,
        "intercept": float(row.intercept_value or 0.0),
        "validation_sign_accuracy_pct": row.validation_sign_accuracy_pct,
        "confidence_calibration": calibration if isinstance(calibration, dict) else {},
        "feature_clip_lows": feature_clip_lows if isinstance(feature_clip_lows, list) else [],
        "feature_clip_highs": feature_clip_highs if isinstance(feature_clip_highs, list) else [],
        "notes": notes if isinstance(notes, dict) else {},
    }


def _load_validated_baseline_policy(sess) -> dict | None:
    row = sess.scalar(
        select(BetaModelVersion)
        .where(BetaModelVersion.model_name == BetaTrainingService._MODEL_NAME)
        .order_by(desc(BetaModelVersion.created_at))
        .limit(1)
    )
    if row is None:
        return None
    try:
        notes = json.loads(row.notes_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(notes, dict):
        return None
    policy = notes.get("validated_baseline_policy")
    if not isinstance(policy, dict):
        return None
    return {
        "policy_name": policy.get("policy_name"),
        "source_feature": policy.get("source_feature"),
        "holdout_sign_accuracy_pct": policy.get("holdout_sign_accuracy_pct"),
        "walkforward_sign_accuracy_pct": policy.get("walkforward_sign_accuracy_pct"),
        "confidence_calibration": policy.get("confidence_calibration") if isinstance(policy.get("confidence_calibration"), dict) else {},
        "version_code": row.version_code,
    }


def _confidence_from_calibration(calibration, default_accuracy_pct: float, predicted_return_pct: float) -> float:
    if not isinstance(calibration, dict):
        calibration = {}
    abs_prediction = abs(float(predicted_return_pct))
    bucket_confidence = None
    for bucket in calibration.get("buckets", []):
        if not isinstance(bucket, dict):
            continue
        min_value = float(bucket.get("min_abs_prediction_pct") or 0.0)
        max_value = float(bucket.get("max_abs_prediction_pct") or min_value)
        if min_value <= abs_prediction <= max_value:
            bucket_confidence = float(bucket.get("sign_accuracy_pct") or 0.0) / 100.0
            break
    if bucket_confidence is None:
        bucket_confidence = float(calibration.get("global_sign_accuracy_pct") or 0.0) / 100.0
    if bucket_confidence <= 0.0:
        bucket_confidence = float(default_accuracy_pct or 50.0) / 100.0
    return min(0.95, max(0.35, bucket_confidence))


def _calibrated_model_confidence(active_model: dict[str, object], predicted_return_pct: float) -> float:
    return _confidence_from_calibration(
        active_model.get("confidence_calibration"),
        float(active_model.get("validation_sign_accuracy_pct") or 50.0),
        predicted_return_pct,
    )


def _calibrated_baseline_confidence(policy: dict[str, object], predicted_return_pct: float) -> float:
    return _confidence_from_calibration(
        policy.get("confidence_calibration"),
        float(policy.get("walkforward_sign_accuracy_pct") or policy.get("holdout_sign_accuracy_pct") or 50.0),
        predicted_return_pct,
    )


def _baseline_policy_prediction(policy: dict[str, object], source_value: float | None) -> float | None:
    policy_name = str(policy.get("policy_name") or "")
    if policy_name == "zero_excess":
        return 0.0
    if source_value is None:
        return None
    if policy_name == "continuation_excess":
        return float(source_value)
    if policy_name == "mean_reversion_excess":
        return float(source_value) * -1.0
    return None


def _heuristic_confidence(
    *,
    predicted_return_pct: float,
    realized_vol: float,
    news_score: float,
    filing_score: float,
) -> float:
    stability_penalty = max(realized_vol, 0.005)
    raw_confidence = min(0.75, max(0.0, abs(predicted_return_pct) / max(stability_penalty * 80, 1.25)))
    raw_confidence += min(0.05, abs(news_score) / 25)
    raw_confidence += min(0.05, abs(filing_score) / 20)
    return min(0.58, max(0.25, raw_confidence))


def _recent_news_context(sess, instrument_id: str) -> dict[str, object]:
    links = list(
        sess.execute(
            select(BetaNewsArticle, BetaNewsArticleLink)
            .join(BetaNewsArticleLink, BetaNewsArticleLink.article_id == BetaNewsArticle.id)
            .where(BetaNewsArticleLink.instrument_id == instrument_id)
            .order_by(desc(BetaNewsArticle.published_at), desc(BetaNewsArticle.created_at))
            .limit(5)
        ).all()
    )
    if not links:
        return {
            "count": 0,
            "avg_sentiment": 0.0,
            "headline_titles": [],
            "recent_positive": 0,
            "recent_negative": 0,
        }
    newest_ts = None
    sentiment_values: list[float] = []
    titles: list[str] = []
    positive = 0
    negative = 0
    for article, _link in links:
        ts = article.published_at or article.created_at
        if newest_ts is None or ts > newest_ts:
            newest_ts = ts
        titles.append(article.title)
    if newest_ts is None:
        newest_ts = links[0][0].created_at
    cutoff = newest_ts - timedelta(days=3)
    for article, _link in links:
        ts = article.published_at or article.created_at
        if ts < cutoff:
            continue
        sentiment_values.append(float(article.sentiment_score or 0.0))
        if article.sentiment_label == "POSITIVE":
            positive += 1
        elif article.sentiment_label == "NEGATIVE":
            negative += 1
    avg_sentiment = round(sum(sentiment_values) / len(sentiment_values), 4) if sentiment_values else 0.0
    return {
        "count": len(sentiment_values),
        "avg_sentiment": avg_sentiment,
        "headline_titles": titles[:3],
        "recent_positive": positive,
        "recent_negative": negative,
    }


def _recent_filing_context(sess, instrument_id: str) -> dict[str, object]:
    links = list(
        sess.execute(
            select(BetaFilingEvent, BetaFilingEventLink)
            .join(BetaFilingEventLink, BetaFilingEventLink.event_id == BetaFilingEvent.id)
            .where(BetaFilingEventLink.instrument_id == instrument_id)
            .order_by(desc(BetaFilingEvent.published_at), desc(BetaFilingEvent.created_at))
            .limit(5)
        ).all()
    )
    if not links:
        return {
            "count": 0,
            "avg_sentiment": 0.0,
            "categories": [],
            "headline_titles": [],
            "recent_positive": 0,
            "recent_negative": 0,
        }
    newest_ts = None
    sentiment_values: list[float] = []
    titles: list[str] = []
    categories: set[str] = set()
    positive = 0
    negative = 0
    for event, _link in links:
        ts = event.published_at or event.created_at
        if newest_ts is None or ts > newest_ts:
            newest_ts = ts
        titles.append(event.title)
    if newest_ts is None:
        newest_ts = links[0][0].created_at
    cutoff = newest_ts - timedelta(days=7)
    for event, _link in links:
        ts = event.published_at or event.created_at
        if ts < cutoff:
            continue
        sentiment_values.append(float(event.sentiment_score or 0.0))
        categories.add(str(event.event_category or "OTHER"))
        if event.sentiment_label == "POSITIVE":
            positive += 1
        elif event.sentiment_label == "NEGATIVE":
            negative += 1
    avg_sentiment = round(sum(sentiment_values) / len(sentiment_values), 4) if sentiment_values else 0.0
    return {
        "count": len(sentiment_values),
        "avg_sentiment": avg_sentiment,
        "categories": sorted(categories),
        "headline_titles": titles[:3],
        "recent_positive": positive,
        "recent_negative": negative,
    }


def _close_position(
    *,
    sess,
    position: BetaDemoPosition,
    latest_mark_price: Decimal,
    market: str | None,
    settings: BetaSettings,
    event_type: str,
    reason: str,
    event_message: str,
    event_payload: dict,
    closed_at,
) -> None:
    entry = _as_decimal(position.entry_price)
    size = _as_decimal(position.size_gbp) or Decimal("0")
    units = _position_units(position)
    exit_fill = _exit_fill_price(latest_mark_price, settings, market)
    pnl_pct = Decimal("0")
    pnl_gbp = Decimal("0")
    if entry is not None and entry > 0 and units is not None and units > 0 and size > 0:
        gross_value = units * exit_fill
        pnl_gbp = gross_value - size
        pnl_pct = pnl_gbp / size
    position.status = event_type
    position.exit_price = _quant_price(exit_fill)
    position.pnl_pct = _quant_money(pnl_pct * Decimal("100"))
    position.pnl_gbp = _quant_money(pnl_gbp)
    position.exit_reason = reason
    position.closed_at = closed_at
    payload = dict(event_payload)
    payload["raw_exit_mark_price_gbp"] = _quant_price(latest_mark_price)
    payload["assumed_exit_fill_price_gbp"] = _quant_price(exit_fill)
    payload["market_friction_bps"] = float(_friction_bps(settings, market))
    sess.add(
        BetaDemoPositionEvent(
            position_id=position.id,
            event_type="CLOSED",
            message_text=event_message,
            payload_json=json.dumps(payload, sort_keys=True),
        )
    )
    BetaAllocationService.record_position_close(sess, position, note=reason)


class BetaScoringService:
    """Compute simple daily scores, watched candidates, and demo-position actions."""

    @staticmethod
    def run_daily_shadow_cycle(settings: BetaSettings, *, core_only: bool = False) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {
                "scored": 0,
                "recommended": 0,
                "candidates_created": 0,
                "positions_opened": 0,
                "positions_closed": 0,
                "active_instruments": 0,
                "scope": "CORE_TRACKED" if core_only else "FULL_UNIVERSE",
            }

        with BetaContext.write_session() as sess:
            ledger_state = BetaAllocationService.refresh_ledger_state(sess)
            risk_state = BetaRiskControlService.evaluate_recent_performance(sess, settings)
            entries_paused_before = bool(risk_state.demo_entries_paused)
            degradation_before = str(risk_state.degradation_status)
            governance_result = BetaTrainingService.enforce_active_model_governance(sess)
            active_model = _load_active_model(sess)
            active_strategy = BetaStrategyService.get_active_strategy(sess)
            validated_baseline_policy = _load_validated_baseline_policy(sess) if active_model is None else None
            promotion_support_available = active_model is not None or validated_baseline_policy is not None
            feature_definition_ids: dict[str, str] = {}
            if active_model is not None:
                feature_definition_ids = {
                    row.feature_name: row.id
                    for row in sess.scalars(
                        select(BetaFeatureDefinition).where(
                            BetaFeatureDefinition.feature_name.in_(active_model["feature_names"])
                        )
                    ).all()
                }
            baseline_policy_feature_definition_id = None
            if validated_baseline_policy is not None:
                source_feature = str(validated_baseline_policy.get("source_feature") or "")
                if source_feature:
                    feature_row = sess.scalar(
                        select(BetaFeatureDefinition).where(BetaFeatureDefinition.feature_name == source_feature).limit(1)
                    )
                    baseline_policy_feature_definition_id = feature_row.id if feature_row is not None else None
            instrument_stmt = select(BetaInstrument).where(BetaInstrument.is_active.is_(True))
            if core_only:
                instrument_stmt = instrument_stmt.where(BetaInstrument.core_security_id.is_not(None))
            instruments = list(sess.scalars(instrument_stmt).all())
            if not instruments:
                return {
                    "scored": 0,
                    "recommended": 0,
                    "candidates_created": 0,
                    "positions_opened": 0,
                    "positions_closed": 0,
                    "active_instruments": 0,
                    "scope": "CORE_TRACKED" if core_only else "FULL_UNIVERSE",
                }

            hypotheses_by_code = {
                row.code: row
                for row in sess.scalars(select(BetaHypothesis)).all()
            }
            signal_runtime_context = BetaHypothesisSignalService.load_runtime_context(sess)
            strategy_confidence_min = float(active_strategy.min_confidence_score) if active_strategy is not None else 0.55
            strategy_edge_min = float(active_strategy.min_expected_edge_score) if active_strategy is not None else 0.20
            score_run = BetaScoreRun(
                run_type="RESEARCH_SIGNAL_DAILY",
                status="SUCCESS",
                strategy_version_id=active_strategy.id if active_strategy is not None else None,
                model_version_id=active_model["id"] if active_model is not None else None,
            )
            sess.add(score_run)
            sess.flush()

            scored = 0
            recommended = 0
            candidates_created = 0
            positions_opened = 0
            positions_closed = 0
            skipped_insufficient_bars = 0
            skipped_invalid_close = 0
            skipped_missing_features = 0
            hypothesis_matches = 0
            validated_hypothesis_matches = 0
            signal_observations_created = 0
            recommendation_decisions_created = 0

            for instrument in instruments:
                open_position = sess.scalar(
                    select(BetaDemoPosition)
                    .where(
                        BetaDemoPosition.symbol == instrument.symbol,
                        BetaDemoPosition.status == "OPEN",
                    )
                    .order_by(desc(BetaDemoPosition.opened_at))
                )
                bars = list(
                    sess.scalars(
                        select(BetaDailyBar)
                        .where(BetaDailyBar.instrument_id == instrument.id)
                        .order_by(desc(BetaDailyBar.bar_date))
                        .limit(10)
                    ).all()
                )
                if len(bars) < 6:
                    skipped_insufficient_bars += 1
                    continue

                bars = list(reversed(bars))
                closes = [_as_decimal(row.close_price_gbp) for row in bars]
                if any(close is None or close <= 0 for close in closes):
                    skipped_invalid_close += 1
                    continue
                close_values = [close for close in closes if close is not None]
                latest_bar = bars[-1]
                latest = close_values[-1]
                prev_1 = close_values[-2]
                prev_5 = close_values[-6]
                intraday_snapshot = _latest_intraday_snapshot(sess, instrument.id)
                mark_price = latest
                mark_price_date = latest_bar.bar_date
                mark_observed_at = latest_bar.source_fetched_at or latest_bar.ingested_at
                mark_source = latest_bar.source
                intraday_percent_change = None
                if intraday_snapshot is not None:
                    intraday_price = _as_decimal(intraday_snapshot.price_gbp)
                    if intraday_price is not None and intraday_price > 0 and intraday_snapshot.price_date >= latest_bar.bar_date:
                        mark_price = intraday_price
                        mark_price_date = intraday_snapshot.price_date
                        mark_observed_at = intraday_snapshot.observed_at
                        mark_source = intraday_snapshot.source or latest_bar.source
                        intraday_percent_change = intraday_snapshot.percent_change
                market_status = BetaMarketSessionService.market_status(
                    instrument.exchange,
                    now_utc=mark_observed_at,
                )
                market_open = bool(market_status["is_open"])
                if prev_1 <= 0 or prev_5 <= 0:
                    continue

                one_day_return = float((mark_price / prev_1) - Decimal("1"))
                five_day_return = float((mark_price / prev_5) - Decimal("1"))
                returns = [
                    float((close_values[i] / close_values[i - 1]) - Decimal("1"))
                    for i in range(1, len(close_values))
                    if close_values[i - 1] > 0
                ]
                realized_vol = float(pstdev(returns[-5:])) if len(returns) >= 2 else 0.0
                model_prediction = None
                model_feature_values: dict[str, float] = {}
                missing_feature = True
                baseline_prediction = None
                baseline_source_value = None
                if active_model is not None and feature_definition_ids:
                    feature_vector: list[float] = []
                    missing_feature = False
                    clip_lows = active_model.get("feature_clip_lows") if isinstance(active_model, dict) else []
                    clip_highs = active_model.get("feature_clip_highs") if isinstance(active_model, dict) else []
                    for feature_name in active_model["feature_names"]:
                        feature_definition_id = feature_definition_ids.get(feature_name)
                        if feature_definition_id is None:
                            missing_feature = True
                            break
                        feature_row = sess.scalar(
                            select(BetaFeatureValue).where(
                                BetaFeatureValue.feature_definition_id == feature_definition_id,
                                BetaFeatureValue.instrument_id == instrument.id,
                                BetaFeatureValue.feature_date == latest_bar.bar_date,
                            )
                        )
                        if feature_row is None or feature_row.value_numeric is None:
                            missing_feature = True
                            break
                        numeric_value = float(feature_row.value_numeric)
                        if len(clip_lows) == len(active_model["feature_names"]) and len(clip_highs) == len(active_model["feature_names"]):
                            feature_idx = len(feature_vector)
                            numeric_value = min(
                                float(clip_highs[feature_idx]),
                                max(float(clip_lows[feature_idx]), numeric_value),
                            )
                        feature_vector.append(numeric_value)
                        model_feature_values[feature_name] = numeric_value
                if active_model is None and validated_baseline_policy is not None:
                    source_feature = str(validated_baseline_policy.get("source_feature") or "")
                    if baseline_policy_feature_definition_id is not None:
                        feature_row = sess.scalar(
                            select(BetaFeatureValue).where(
                                BetaFeatureValue.feature_definition_id == baseline_policy_feature_definition_id,
                                BetaFeatureValue.instrument_id == instrument.id,
                                BetaFeatureValue.feature_date == latest_bar.bar_date,
                            )
                        )
                        if feature_row is not None and feature_row.value_numeric is not None:
                            baseline_source_value = float(feature_row.value_numeric)
                    baseline_prediction = _baseline_policy_prediction(validated_baseline_policy, baseline_source_value)
                if not missing_feature:
                    standardized = []
                    for idx, raw_value in enumerate(feature_vector):
                        scale = active_model["scales"][idx] or 1.0
                        standardized.append((raw_value - active_model["means"][idx]) / scale)
                    model_prediction = active_model["intercept"] + sum(
                        standardized[idx] * active_model["coefficients"][idx]
                        for idx in range(len(standardized))
                    )
                elif active_model is not None:
                    skipped_missing_features += 1

                news_context = _recent_news_context(sess, instrument.id)
                filing_context = _recent_filing_context(sess, instrument.id)
                news_score = float(news_context["avg_sentiment"])
                filing_score = float(filing_context["avg_sentiment"])
                if model_prediction is not None:
                    prediction_source = "MODEL"
                    predicted_return_pct = float(model_prediction)
                    confidence = _calibrated_model_confidence(active_model, predicted_return_pct)
                    confidence_source = "MODEL_CALIBRATION"
                elif baseline_prediction is not None and validated_baseline_policy is not None:
                    prediction_source = "VALIDATED_BASELINE"
                    predicted_return_pct = float(baseline_prediction)
                    confidence = _calibrated_baseline_confidence(validated_baseline_policy, predicted_return_pct)
                    confidence_source = "BASELINE_CALIBRATION"
                else:
                    prediction_source = "HEURISTIC"
                    predicted_return_pct = (five_day_return * 100) + news_score + (filing_score * 1.5)
                    confidence = _heuristic_confidence(
                        predicted_return_pct=predicted_return_pct,
                        realized_vol=realized_vol,
                        news_score=news_score,
                        filing_score=filing_score,
                    )
                    confidence_source = "HEURISTIC_MAGNITUDE"
                edge = predicted_return_pct / max(max(realized_vol, 0.005) * 100, 0.5)

                direction = "NEUTRAL"
                rejection_reason = None
                signal_qualified = False

                if prediction_source in {"MODEL", "VALIDATED_BASELINE"}:
                    if predicted_return_pct <= -3.0:
                        direction = "RISK_OFF"
                        signal_qualified = confidence >= strategy_confidence_min
                    elif predicted_return_pct >= 2.0:
                        direction = "BULLISH"
                        signal_qualified = confidence >= strategy_confidence_min and edge >= strategy_edge_min
                        if not signal_qualified:
                            rejection_reason = "Predicted return is positive but confidence or edge is below the activation floor."
                    elif predicted_return_pct < -1.5:
                        direction = "BEARISH"
                        rejection_reason = "Predicted return is negative but below the risk-off threshold."
                elif predicted_return_pct <= -3.0 or five_day_return <= -0.04 or one_day_return <= -0.025:
                    direction = "RISK_OFF"
                    signal_qualified = confidence >= strategy_confidence_min
                elif predicted_return_pct >= 2.0 and one_day_return > -0.02:
                    direction = "BULLISH"
                    signal_qualified = confidence >= strategy_confidence_min and edge >= strategy_edge_min
                elif predicted_return_pct < -1.5 or five_day_return < -0.02:
                    direction = "BEARISH"
                    rejection_reason = "Momentum negative but below risk-off threshold."
                else:
                    rejection_reason = "Signal below bullish edge threshold."

                support_allows_recommendation = prediction_source in {"MODEL", "VALIDATED_BASELINE"}
                if signal_qualified and not support_allows_recommendation:
                    rejection_reason = "Live support is heuristic-only and cannot drive a recommendation."

                legacy_hypothesis_code = BetaHypothesisService.classify_hypothesis_code(
                    direction=direction,
                    news_context=news_context,
                    filing_context=filing_context,
                )
                legacy_hypothesis = hypotheses_by_code.get(legacy_hypothesis_code)
                family_label = (
                    legacy_hypothesis.title if legacy_hypothesis is not None
                    else ("Catalyst confirmation" if legacy_hypothesis_code == "CATALYST_CONFIRMATION" else "Trend recovery")
                )

                evidence = {
                    "one_day_return": round(one_day_return, 5),
                    "five_day_return": round(five_day_return, 5),
                    "predicted_return_pct": round(predicted_return_pct, 5),
                    "realized_volatility_5d": round(realized_vol, 5),
                    "latest_bar_date": bars[-1].bar_date.isoformat(),
                    "mark_price_date": mark_price_date.isoformat(),
                    "mark_source": mark_source,
                    "mark_observed_at": mark_observed_at.isoformat() if mark_observed_at is not None else None,
                    "mark_price_gbp": _quant_price(mark_price),
                    "intraday_percent_change": intraday_percent_change,
                    "market_open": market_open,
                    "minutes_until_close": market_status["minutes_until_close"],
                    "prediction_source": prediction_source,
                    "confidence_source": confidence_source,
                    "model_version": active_model["version_code"] if active_model is not None else None,
                    "validated_baseline_version": (
                        validated_baseline_policy.get("version_code") if validated_baseline_policy is not None else None
                    ),
                    "validated_baseline_policy": validated_baseline_policy,
                    "validated_baseline_source_value": baseline_source_value,
                    "strategy_version": active_strategy.version_code if active_strategy is not None else None,
                    "strategy_confidence_min": strategy_confidence_min,
                    "strategy_edge_min": strategy_edge_min,
                    "model_feature_values": model_feature_values,
                    "model_calibration": active_model.get("confidence_calibration") if active_model is not None else None,
                    "market": instrument.market,
                    "benchmark_key": instrument.benchmark_key,
                    "sector_key": instrument.sector_key,
                    "sector_label": instrument.sector_label,
                    "recent_news_count": news_context["count"],
                    "recent_news_avg_sentiment": news_score,
                    "recent_news_positive": news_context["recent_positive"],
                    "recent_news_negative": news_context["recent_negative"],
                    "recent_news_headlines": news_context["headline_titles"],
                    "recent_filing_count": filing_context["count"],
                    "recent_filing_avg_sentiment": filing_score,
                    "recent_filing_positive": filing_context["recent_positive"],
                    "recent_filing_negative": filing_context["recent_negative"],
                    "recent_filing_categories": filing_context["categories"],
                    "recent_filing_titles": filing_context["headline_titles"],
                    "legacy_hypothesis_code": legacy_hypothesis_code,
                    "legacy_hypothesis_title": legacy_hypothesis.title if legacy_hypothesis is not None else legacy_hypothesis_code,
                }

                signal_result = BetaHypothesisSignalService.evaluate_live_matches(
                    sess,
                    context=signal_runtime_context,
                    instrument=instrument,
                    decision_date=latest_bar.bar_date,
                    observation_time=mark_observed_at,
                    evidence=evidence,
                    direction=direction,
                    confidence=confidence,
                    edge=edge,
                    predicted_return_pct=predicted_return_pct,
                    prediction_source=prediction_source,
                    signal_qualified=signal_qualified,
                    candidate_promotion_allowed=support_allows_recommendation,
                )
                matched_definition = None
                matched_family = None
                matched_observation = None
                recommendation_decision = None
                hypothesis = legacy_hypothesis
                if signal_result.get("matched"):
                    hypothesis_matches += len(signal_result.get("matches") or [])
                    best_match = signal_result.get("best_match") or {}
                    matched_definition = best_match.get("definition")
                    matched_family = best_match.get("family")
                    matched_observation = best_match.get("observation")
                    recommendation_decision = signal_result.get("decision")
                    if matched_observation is not None:
                        signal_observations_created += len(signal_result.get("matches") or [])
                    if recommendation_decision is not None:
                        recommendation_decisions_created += 1
                    if signal_result.get("legacy_hypothesis") is not None:
                        hypothesis = signal_result.get("legacy_hypothesis")
                    if matched_family is not None:
                        family_label = matched_family.family_name
                    evidence["matched_hypothesis_code"] = matched_definition.hypothesis_code if matched_definition is not None else None
                    evidence["matched_hypothesis_name"] = matched_definition.name if matched_definition is not None else None
                    evidence["matched_hypothesis_status"] = str(best_match.get("belief_status") or "")
                    evidence["matched_hypothesis_confidence"] = float(best_match.get("belief_confidence") or 0.0)
                    evidence["matched_hypothesis_family_code"] = matched_family.family_code if matched_family is not None else None
                    evidence["matched_hypothesis_match_count"] = len(signal_result.get("matches") or [])
                    evidence["recommendation_decision_status"] = recommendation_decision.decision_status if recommendation_decision is not None else None
                    evidence["recommendation_reason_code"] = recommendation_decision.decision_reason_code if recommendation_decision is not None else None
                    evidence["recommendation_reason_text"] = recommendation_decision.decision_reason_text if recommendation_decision is not None else None
                    if str(best_match.get("belief_status") or "") == "VALIDATED":
                        validated_hypothesis_matches += 1
                    if recommendation_decision is not None and recommendation_decision.decision_reason_text:
                        rejection_reason = recommendation_decision.decision_reason_text
                else:
                    evidence["matched_hypothesis_code"] = None
                    evidence["matched_hypothesis_name"] = None
                    evidence["matched_hypothesis_status"] = None
                    evidence["matched_hypothesis_confidence"] = None
                    evidence["matched_hypothesis_family_code"] = None
                    evidence["matched_hypothesis_match_count"] = 0
                    evidence["recommendation_decision_status"] = "NO_MATCH"
                    evidence["recommendation_reason_code"] = "no_validated_hypothesis_match"
                    evidence["recommendation_reason_text"] = "No hypothesis match produced an actionable research decision."
                    rejection_reason = rejection_reason or "No validated hypothesis matched current market state."

                recommendation = bool(
                    recommendation_decision is not None and recommendation_decision.decision_status == "RECOMMENDED"
                )
                scored += 1
                if recommendation:
                    recommended += 1
                sess.add(
                    BetaScoreTape(
                        score_run_id=score_run.id,
                        instrument_id=instrument.id,
                        strategy_version_id=active_strategy.id if active_strategy is not None else None,
                        model_version_id=active_model["id"] if active_model is not None else None,
                        symbol=instrument.symbol,
                        direction=direction,
                        predicted_return_5d=predicted_return_pct,
                        realized_volatility_5d=realized_vol,
                        confidence_score=confidence,
                        expected_edge_score=edge,
                        recommendation_flag=recommendation,
                        rejection_reason=rejection_reason,
                        evidence_json=json.dumps(evidence, sort_keys=True),
                    )
                )

                candidate = sess.scalar(
                    select(BetaSignalCandidate)
                    .where(
                        BetaSignalCandidate.symbol == instrument.symbol,
                        BetaSignalCandidate.status.in_(("WATCHING", "PROMOTED")),
                    )
                    .order_by(desc(BetaSignalCandidate.updated_at))
                )

                if open_position is not None and open_position.entry_price:
                    entry = _as_decimal(open_position.entry_price)
                    units = _position_units(open_position)
                    size = _as_decimal(open_position.size_gbp) or Decimal("0")
                    exit_fill = _exit_fill_price(mark_price, settings, instrument.market)
                    if entry is not None and entry > 0 and units is not None and units > 0 and size > 0:
                        pnl_gbp_decimal = (units * exit_fill) - size
                        pnl_pct_decimal = pnl_gbp_decimal / size
                        open_position.pnl_pct = _quant_money(pnl_pct_decimal * Decimal("100"))
                        open_position.pnl_gbp = _quant_money(pnl_gbp_decimal)

                        held_bars = _bars_held(open_position.entry_bar_date, mark_price_date, bars)
                        target_pct = _as_decimal(open_position.target_return_pct)
                        stop_pct = _as_decimal(open_position.stop_loss_pct)
                        if target_pct is not None and pnl_pct_decimal * Decimal("100") >= target_pct and market_open:
                            _close_position(
                                sess=sess,
                                position=open_position,
                                latest_mark_price=mark_price,
                                market=instrument.market,
                                settings=settings,
                                event_type="CLOSED",
                                reason="Target return reached.",
                                event_message="Demo trade closed automatically at target.",
                                event_payload=evidence,
                                closed_at=mark_observed_at,
                            )
                            open_position = None
                            positions_closed += 1
                        elif (target_pct is not None and pnl_pct_decimal * Decimal("100") >= target_pct) and not market_open:
                            if open_position.candidate_id:
                                _record_candidate_event(
                                    sess=sess,
                                    candidate_id=open_position.candidate_id,
                                    event_type="WAITING",
                                    message_text="Target reached but market is closed; exit deferred until tradable hours.",
                                    payload=evidence,
                                )
                        elif stop_pct is not None and pnl_pct_decimal * Decimal("100") <= stop_pct and market_open:
                            _close_position(
                                sess=sess,
                                position=open_position,
                                latest_mark_price=mark_price,
                                market=instrument.market,
                                settings=settings,
                                event_type="CLOSED",
                                reason="Stop loss reached.",
                                event_message="Demo trade closed automatically at stop.",
                                event_payload=evidence,
                                closed_at=mark_observed_at,
                            )
                            open_position = None
                            positions_closed += 1
                        elif (stop_pct is not None and pnl_pct_decimal * Decimal("100") <= stop_pct) and not market_open:
                            if open_position.candidate_id:
                                _record_candidate_event(
                                    sess=sess,
                                    candidate_id=open_position.candidate_id,
                                    event_type="WAITING",
                                    message_text="Stop reached but market is closed; exit deferred until tradable hours.",
                                    payload=evidence,
                                )
                        elif (
                            open_position.planned_horizon_days is not None
                            and held_bars >= open_position.planned_horizon_days
                            and market_open
                        ):
                            _close_position(
                                sess=sess,
                                position=open_position,
                                latest_mark_price=mark_price,
                                market=instrument.market,
                                settings=settings,
                                event_type="CLOSED",
                                reason="Planned holding horizon reached.",
                                event_message="Demo trade closed automatically at time expiry.",
                                event_payload=evidence,
                                closed_at=mark_observed_at,
                            )
                            open_position = None
                            positions_closed += 1
                        elif (
                            open_position.planned_horizon_days is not None
                            and held_bars >= open_position.planned_horizon_days
                            and not market_open
                        ):
                            if open_position.candidate_id:
                                _record_candidate_event(
                                    sess=sess,
                                    candidate_id=open_position.candidate_id,
                                    event_type="WAITING",
                                    message_text="Time exit reached but market is closed; exit deferred until tradable hours.",
                                    payload=evidence,
                                )

                candidate_status = None
                candidate_message = None
                if recommendation_decision is not None:
                    if recommendation_decision.decision_status == "RECOMMENDED":
                        candidate_status = "PROMOTED"
                        candidate_message = "Candidate created from validated hypothesis recommendation."
                    elif recommendation_decision.decision_status == "WATCHING":
                        candidate_status = "WATCHING"
                        candidate_message = "Candidate created from validated hypothesis watch state."
                    elif recommendation_decision.decision_status == "REJECTED":
                        candidate_status = "REJECTED"
                        candidate_message = recommendation_decision.decision_reason_text or "Candidate rejected by hypothesis governance."
                    else:
                        candidate_status = "DISMISSED"
                        candidate_message = recommendation_decision.decision_reason_text or "Candidate blocked by hypothesis governance."

                if candidate_status in {"PROMOTED", "WATCHING"}:
                    if candidate is None:
                        candidate = BetaSignalCandidate(
                            instrument_id=instrument.id,
                            hypothesis_id=hypothesis.id if hypothesis is not None else None,
                            hypothesis_definition_id=matched_definition.id if matched_definition is not None else None,
                            signal_observation_id=matched_observation.id if matched_observation is not None else None,
                            recommendation_decision_id=recommendation_decision.id if recommendation_decision is not None else None,
                            symbol=instrument.symbol,
                            title=f"{instrument.symbol} {family_label} {direction.lower()} setup",
                            status=candidate_status,
                            direction=direction,
                            confidence_score=confidence,
                            expected_edge_score=edge,
                            market=instrument.market,
                            evidence_summary=(
                                f"{matched_definition.hypothesis_code if matched_definition is not None else legacy_hypothesis_code}: "
                                f"pred {predicted_return_pct:.2f}%, belief {float((signal_result.get('best_match') or {}).get('belief_confidence') or 0.0):.2f}."
                            ),
                            evidence_json=json.dumps(evidence, sort_keys=True),
                        )
                        sess.add(candidate)
                        sess.flush()
                        candidates_created += 1
                        if recommendation_decision is not None:
                            recommendation_decision.candidate_id = candidate.id
                        _record_candidate_event(
                            sess=sess,
                            candidate_id=candidate.id,
                            event_type="CREATED",
                            message_text=candidate_message or "Candidate created from hypothesis engine.",
                            payload=evidence,
                        )
                    else:
                        candidate.hypothesis_id = hypothesis.id if hypothesis is not None else None
                        candidate.hypothesis_definition_id = matched_definition.id if matched_definition is not None else None
                        candidate.signal_observation_id = matched_observation.id if matched_observation is not None else None
                        candidate.recommendation_decision_id = recommendation_decision.id if recommendation_decision is not None else None
                        candidate.status = candidate_status
                        candidate.direction = direction
                        candidate.confidence_score = confidence
                        candidate.expected_edge_score = edge
                        candidate.title = f"{instrument.symbol} {family_label} {direction.lower()} setup"
                        candidate.evidence_summary = (
                            f"{matched_definition.hypothesis_code if matched_definition is not None else legacy_hypothesis_code}: "
                            f"pred {predicted_return_pct:.2f}%, belief {float((signal_result.get('best_match') or {}).get('belief_confidence') or 0.0):.2f}."
                        )
                        candidate.evidence_json = json.dumps(evidence, sort_keys=True)
                        candidate.market = instrument.market
                        if recommendation_decision is not None:
                            recommendation_decision.candidate_id = candidate.id
                        _record_candidate_event(
                            sess=sess,
                            candidate_id=candidate.id,
                            event_type="UPDATED",
                            message_text=candidate_message or "Candidate refreshed from hypothesis engine.",
                            payload=evidence,
                        )

                    if recommendation and settings.demo_execution_enabled and direction == "BULLISH":
                        if open_position is None:
                            if not market_open:
                                _record_candidate_event(
                                    sess=sess,
                                    candidate_id=candidate.id,
                                    event_type="BLOCKED",
                                    message_text="Candidate qualified but the market is closed for demo execution.",
                                    payload=evidence,
                                )
                            elif not risk_state.demo_entries_paused:
                                requested_size = _as_decimal(_size_gbp(confidence, edge)) or Decimal("0")
                                sized_allocation = BetaAllocationService.size_for_new_position(sess, requested_size)
                                if sized_allocation > 0:
                                    entry_fill = _entry_fill_price(mark_price, settings, instrument.market)
                                    units = sized_allocation / entry_fill if entry_fill > 0 else Decimal("0")
                                    entry_payload = dict(evidence)
                                    entry_payload["raw_entry_mark_price_gbp"] = _quant_price(mark_price)
                                    entry_payload["assumed_entry_fill_price_gbp"] = _quant_price(entry_fill)
                                    entry_payload["market_friction_bps"] = float(
                                        _friction_bps(settings, instrument.market)
                                    )
                                    position = BetaDemoPosition(
                                        candidate_id=candidate.id,
                                        symbol=instrument.symbol,
                                        market=instrument.market,
                                        side="LONG",
                                        status="OPEN",
                                        confidence_score=confidence,
                                        expected_edge_score=edge,
                                        size_gbp=_quant_money(sized_allocation),
                                        units=_quant_price(units),
                                        entry_price=_quant_price(entry_fill),
                                        entry_bar_date=mark_price_date,
                                        target_return_pct="4.00",
                                        stop_loss_pct="-3.00",
                                        planned_horizon_days=5,
                                    )
                                    sess.add(position)
                                    sess.flush()
                                    positions_opened += 1
                                    sess.add(
                                        BetaDemoPositionEvent(
                                            position_id=position.id,
                                            event_type="OPENED",
                                            message_text="Demo trade opened automatically from promoted bullish candidate.",
                                            payload_json=json.dumps(entry_payload, sort_keys=True),
                                        )
                                    )
                                    BetaAllocationService.record_position_open(
                                        sess,
                                        position,
                                        note="Automatic bullish demo entry.",
                                    )
                                else:
                                    _record_candidate_event(
                                        sess=sess,
                                        candidate_id=candidate.id,
                                        event_type="BLOCKED",
                                        message_text="Candidate qualified but insufficient paper capital was available.",
                                        payload=evidence,
                                    )
                            else:
                                _record_candidate_event(
                                    sess=sess,
                                    candidate_id=candidate.id,
                                    event_type="BLOCKED",
                                    message_text=risk_state.pause_reason or "Candidate qualified but demo entries are paused.",
                                    payload=evidence,
                                )
                        else:
                            open_position.confidence_score = confidence
                            open_position.expected_edge_score = edge
                else:
                    if candidate is not None:
                        candidate.status = "REJECTED" if candidate_status == "REJECTED" else "DISMISSED"
                        candidate.rejection_reason = rejection_reason or candidate_message
                        candidate.hypothesis_definition_id = matched_definition.id if matched_definition is not None else candidate.hypothesis_definition_id
                        candidate.signal_observation_id = matched_observation.id if matched_observation is not None else candidate.signal_observation_id
                        candidate.recommendation_decision_id = recommendation_decision.id if recommendation_decision is not None else candidate.recommendation_decision_id
                        if recommendation_decision is not None:
                            recommendation_decision.candidate_id = candidate.id
                        _record_candidate_event(
                            sess=sess,
                            candidate_id=candidate.id,
                            event_type="REJECTED" if candidate_status == "REJECTED" else "DISMISSED",
                            message_text=rejection_reason or candidate_message or "Candidate dismissed by hypothesis engine.",
                            payload=evidence,
                        )

                if direction in {"RISK_OFF", "BEARISH"} and settings.demo_execution_enabled:
                    if open_position is not None:
                        if market_open:
                            _close_position(
                                sess=sess,
                                position=open_position,
                                latest_mark_price=mark_price,
                                market=instrument.market,
                                settings=settings,
                                event_type="RISK_OFF_EXIT",
                                reason="Automatic risk-off close from bearish score.",
                                event_message="Demo trade closed automatically on risk-off signal.",
                                event_payload=evidence,
                                closed_at=mark_observed_at,
                            )
                            positions_closed += 1
                        elif open_position.candidate_id:
                            _record_candidate_event(
                                sess=sess,
                                candidate_id=open_position.candidate_id,
                                event_type="WAITING",
                                message_text="Risk-off exit triggered but market is closed; exit deferred until tradable hours.",
                                payload=evidence,
                            )

            ledger_state = BetaAllocationService.refresh_ledger_state(sess)
            risk_state = BetaRiskControlService.evaluate_recent_performance(sess, settings)
            result = {
                "scope": "CORE_TRACKED" if core_only else "FULL_UNIVERSE",
                "active_instruments": len(instruments),
                "scored": scored,
                "recommended": recommended,
                "candidates_created": candidates_created,
                "positions_opened": positions_opened,
                "positions_closed": positions_closed,
                "skipped_insufficient_bars": skipped_insufficient_bars,
                "skipped_invalid_close": skipped_invalid_close,
                "skipped_missing_features": skipped_missing_features,
                "hypothesis_matches": hypothesis_matches,
                "validated_hypothesis_matches": validated_hypothesis_matches,
                "signal_observations_created": signal_observations_created,
                "recommendation_decisions_created": recommendation_decisions_created,
                "active_model_version": active_model["version_code"] if active_model is not None else None,
                "active_strategy_version": active_strategy.version_code if active_strategy is not None else None,
                "entries_paused": int(risk_state.demo_entries_paused),
                "entries_paused_changed": int(entries_paused_before != bool(risk_state.demo_entries_paused)),
                "degradation_before": degradation_before,
                "degradation_after": str(risk_state.degradation_status),
                "available_cash_gbp": int(
                    (_as_decimal(ledger_state.available_cash_gbp) or Decimal("0")).quantize(Decimal("1"))
                ),
                "active_model_governance": governance_result,
                "heuristic_only_mode": active_model is None and validated_baseline_policy is None,
                "validated_baseline_mode": active_model is None and validated_baseline_policy is not None,
                "validated_baseline_policy_name": (
                    validated_baseline_policy.get("policy_name") if validated_baseline_policy is not None else None
                ),
                "candidate_promotion_allowed": promotion_support_available,
            }
            score_run.notes_json = json.dumps(result, sort_keys=True)
            sess.flush()
            return result
