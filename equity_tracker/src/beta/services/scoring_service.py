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
from .risk_control_service import BetaRiskControlService
from .session_service import BetaMarketSessionService
from ..settings import BetaSettings
from .hypothesis_service import BetaHypothesisService
from .strategy_service import BetaStrategyService


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
    return {
        "id": row.id,
        "version_code": row.version_code,
        "feature_names": feature_names,
        "coefficients": coefficients,
        "means": means,
        "scales": scales,
        "intercept": float(row.intercept_value or 0.0),
        "validation_sign_accuracy_pct": row.validation_sign_accuracy_pct,
    }


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
    def run_daily_shadow_cycle(settings: BetaSettings) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {
                "scored": 0,
                "recommended": 0,
                "candidates_created": 0,
                "positions_opened": 0,
                "positions_closed": 0,
            }

        with BetaContext.write_session() as sess:
            ledger_state = BetaAllocationService.refresh_ledger_state(sess)
            risk_state = BetaRiskControlService.evaluate_recent_performance(sess, settings)
            entries_paused_before = bool(risk_state.demo_entries_paused)
            degradation_before = str(risk_state.degradation_status)
            active_model = _load_active_model(sess)
            active_strategy = BetaStrategyService.get_active_strategy(sess)
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
            instruments = list(sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all())
            if not instruments:
                return {
                    "scored": 0,
                    "recommended": 0,
                    "candidates_created": 0,
                    "positions_opened": 0,
                    "positions_closed": 0,
                }

            hypotheses_by_code = {
                row.code: row
                for row in sess.scalars(select(BetaHypothesis)).all()
            }
            strategy_confidence_min = float(active_strategy.min_confidence_score) if active_strategy is not None else 0.55
            strategy_edge_min = float(active_strategy.min_expected_edge_score) if active_strategy is not None else 0.20
            score_run = BetaScoreRun(
                run_type="HEURISTIC_DAILY",
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
                    continue

                bars = list(reversed(bars))
                closes = [_as_decimal(row.close_price_gbp) for row in bars]
                if any(close is None or close <= 0 for close in closes):
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
                stability_penalty = max(realized_vol, 0.005)
                model_prediction = None
                model_feature_values: dict[str, float] = {}
                missing_feature = True
                if active_model is not None and feature_definition_ids:
                    feature_vector: list[float] = []
                    missing_feature = False
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
                        feature_vector.append(numeric_value)
                        model_feature_values[feature_name] = numeric_value
                if not missing_feature:
                    standardized = []
                    for idx, raw_value in enumerate(feature_vector):
                        scale = active_model["scales"][idx] or 1.0
                        standardized.append((raw_value - active_model["means"][idx]) / scale)
                    model_prediction = active_model["intercept"] + sum(
                        standardized[idx] * active_model["coefficients"][idx]
                        for idx in range(len(standardized))
                    )

                news_context = _recent_news_context(sess, instrument.id)
                filing_context = _recent_filing_context(sess, instrument.id)
                news_score = float(news_context["avg_sentiment"])
                filing_score = float(filing_context["avg_sentiment"])
                predicted_return_pct = model_prediction if model_prediction is not None else (five_day_return * 100)
                predicted_return_pct += news_score + (filing_score * 1.5)
                edge = predicted_return_pct / max(stability_penalty * 100, 0.5)
                confidence = min(0.99, max(0.0, abs(predicted_return_pct) / max(stability_penalty * 60, 1.0)))
                confidence = min(
                    0.99,
                    max(
                        0.0,
                        confidence
                        + min(0.12, abs(news_score) / 15)
                        + min(0.15, abs(filing_score) / 10),
                    ),
                )

                direction = "NEUTRAL"
                rejection_reason = None
                recommendation = False

                if predicted_return_pct <= -3.0 or five_day_return <= -0.04 or one_day_return <= -0.025:
                    direction = "RISK_OFF"
                    recommendation = confidence >= strategy_confidence_min
                elif predicted_return_pct >= 2.0 and one_day_return > -0.02:
                    direction = "BULLISH"
                    recommendation = confidence >= strategy_confidence_min and edge >= strategy_edge_min
                elif predicted_return_pct < -1.5 or five_day_return < -0.02:
                    direction = "BEARISH"
                    rejection_reason = "Momentum negative but below risk-off threshold."
                else:
                    rejection_reason = "Signal below bullish edge threshold."

                scored += 1
                if recommendation:
                    recommended += 1

                hypothesis_code = BetaHypothesisService.classify_hypothesis_code(
                    direction=direction,
                    news_context=news_context,
                    filing_context=filing_context,
                )
                hypothesis = hypotheses_by_code.get(hypothesis_code)
                family_label = "Catalyst confirmation" if hypothesis_code == "CATALYST_CONFIRMATION" else "Trend recovery"

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
                    "model_version": active_model["version_code"] if active_model is not None else None,
                    "strategy_version": active_strategy.version_code if active_strategy is not None else None,
                    "strategy_confidence_min": strategy_confidence_min,
                    "strategy_edge_min": strategy_edge_min,
                    "model_feature_values": model_feature_values,
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
                    "hypothesis_code": hypothesis_code,
                    "hypothesis_title": hypothesis.title if hypothesis is not None else hypothesis_code,
                }
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

                if recommendation:
                    if candidate is None:
                        candidate = BetaSignalCandidate(
                            instrument_id=instrument.id,
                            hypothesis_id=hypothesis.id if hypothesis is not None else None,
                            symbol=instrument.symbol,
                            title=f"{instrument.symbol} {family_label} {direction.lower()} setup",
                            status="PROMOTED" if confidence >= 0.72 else "WATCHING",
                            direction=direction,
                            confidence_score=confidence,
                            expected_edge_score=edge,
                            market=instrument.market,
                            evidence_summary=(
                                f"Pred {predicted_return_pct:.2f}%; 1d return {one_day_return:.2%}; "
                                f"volatility {realized_vol:.2%}."
                            ),
                            evidence_json=json.dumps(evidence, sort_keys=True),
                        )
                        sess.add(candidate)
                        sess.flush()
                        candidates_created += 1
                        _record_candidate_event(
                            sess=sess,
                            candidate_id=candidate.id,
                            event_type="CREATED",
                            message_text="Candidate created from heuristic daily score.",
                            payload=evidence,
                        )
                    else:
                        candidate.hypothesis_id = hypothesis.id if hypothesis is not None else None
                        candidate.status = "PROMOTED" if confidence >= 0.72 else "WATCHING"
                        candidate.direction = direction
                        candidate.confidence_score = confidence
                        candidate.expected_edge_score = edge
                        candidate.title = f"{instrument.symbol} {family_label} {direction.lower()} setup"
                        candidate.evidence_summary = (
                            f"Pred {predicted_return_pct:.2f}%; 1d return {one_day_return:.2%}; "
                            f"volatility {realized_vol:.2%}."
                        )
                        candidate.evidence_json = json.dumps(evidence, sort_keys=True)
                        candidate.market = instrument.market
                        _record_candidate_event(
                            sess=sess,
                            candidate_id=candidate.id,
                            event_type="UPDATED",
                            message_text="Candidate refreshed from heuristic daily score.",
                            payload=evidence,
                        )

                    if settings.demo_execution_enabled and direction == "BULLISH":
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
                        candidate.status = "DISMISSED"
                        candidate.rejection_reason = rejection_reason
                        _record_candidate_event(
                            sess=sess,
                            candidate_id=candidate.id,
                            event_type="DISMISSED",
                            message_text=rejection_reason or "Candidate dismissed by heuristic score.",
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
            sess.flush()

            return {
                "scored": scored,
                "recommended": recommended,
                "candidates_created": candidates_created,
                "positions_opened": positions_opened,
                "positions_closed": positions_closed,
                "entries_paused": int(risk_state.demo_entries_paused),
                "entries_paused_changed": int(entries_paused_before != bool(risk_state.demo_entries_paused)),
                "degradation_before": degradation_before,
                "degradation_after": str(risk_state.degradation_status),
                "available_cash_gbp": int((_as_decimal(ledger_state.available_cash_gbp) or Decimal("0")).quantize(Decimal("1"))),
            }
