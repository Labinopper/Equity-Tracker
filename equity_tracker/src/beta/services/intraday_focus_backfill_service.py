"""Staged backfill for focus-name intraday evidence."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import func, select

from ..context import BetaContext
from ..db.models import (
    BetaIntradayFeatureLabelValue,
    BetaIntradayFeatureObservation,
    BetaIntradaySimulatedTrade,
    BetaMinuteBar,
)
from ..settings import BetaSettings
from .intraday_bar_fetch_service import BetaIntradayBarFetchService
from .intraday_outlook_service import BetaIntradayOutlookService
from .intraday_priority_service import BetaIntradayPriorityService
from .intraday_simulated_trade_service import BetaIntradaySimulatedTradeService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class BetaIntradayFocusBackfillService:
    """Build a usable focus-name intraday history in bounded stages."""

    @staticmethod
    def backfill_reasonable_history(
        settings: BetaSettings,
        *,
        target_days: int | None = None,
        credits_budget: int | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"focus_items": 0, "timeline_status": "NOT_INITIALIZED"}

        now = _coerce_utc(now_utc)
        focus_watchlist = BetaIntradayPriorityService.build_focus_watchlist(settings, now_utc=now)
        focus_items = list(focus_watchlist["items"])
        focus_ids = [item.instrument_id for item in focus_items if item.instrument_id]
        if not focus_ids:
            return {"focus_items": 0, "timeline_status": "NO_FOCUS_ITEMS"}

        final_target_days = max(7, int(target_days or settings.intraday_focus_backfill_target_days))
        stage_days = max(5, int(settings.intraday_focus_backfill_stage_days))
        daily_budget = max(1, int(credits_budget or settings.intraday_focus_backfill_credits_budget))

        with BetaContext.read_session() as sess:
            coverage_before = BetaIntradayFocusBackfillService._coverage_summary(
                sess,
                instrument_ids=focus_ids,
            )

        current_min_coverage = int(coverage_before.get("min_coverage_days") or 0)
        stage_target_days = BetaIntradayFocusBackfillService._next_stage_target(
            current_min_coverage=current_min_coverage,
            final_target_days=final_target_days,
            stage_days=stage_days,
        )
        should_extend_minute_history = bool(
            settings.intraday_bar_fetch_enabled
            and settings.intraday_bar_backfill_enabled
            and (
                int(coverage_before.get("symbols_missing_minute_history") or 0) > 0
                or current_min_coverage < stage_target_days
            )
        )

        bar_result: dict[str, object] = {
            "bars_written": 0,
            "instruments_backfilled": 0,
            "credits_used": 0,
        }
        if should_extend_minute_history:
            bar_result = BetaIntradayBarFetchService.backfill_historical_bars(
                priority_items=focus_items,
                target_days=stage_target_days,
                credits_budget=daily_budget,
            )

        outlook_result = BetaIntradayOutlookService.rebuild_recent_history(
            settings,
            target_days=min(stage_target_days, settings.intraday_execution_hypothesis_history_days),
            instrument_ids=focus_ids,
            now_utc=now,
        )

        simulated_trade_result: dict[str, object] = {
            "focus_items": len(focus_items),
            "trades_written": 0,
            "sessions_simulated": 0,
        }
        if settings.intraday_short_trade_simulation_enabled:
            simulated_trade_result = BetaIntradaySimulatedTradeService.rebuild_recent_history(
                settings,
                target_days=min(stage_target_days, settings.intraday_short_trade_history_days),
            )

        with BetaContext.read_session() as sess:
            coverage_after = BetaIntradayFocusBackfillService._coverage_summary(
                sess,
                instrument_ids=focus_ids,
            )
            evidence_after = BetaIntradayFocusBackfillService._evidence_summary(
                sess,
                instrument_ids=focus_ids,
                cutoff_date=(now - timedelta(days=stage_target_days)).date(),
            )

        remaining_days = max(0, final_target_days - int(coverage_after.get("min_coverage_days") or 0))
        remaining_runs = int(math.ceil(float(remaining_days) / float(stage_days))) if remaining_days > 0 else 0
        timeline_status = (
            "AT_TARGET"
            if remaining_runs == 0 and int(coverage_after.get("symbols_missing_minute_history") or 0) == 0
            else "BUILDING"
        )

        return {
            "focus_items": len(focus_items),
            "final_target_days": final_target_days,
            "stage_target_days": stage_target_days,
            "stage_days": stage_days,
            "timeline_status": timeline_status,
            "remaining_runs_estimate": remaining_runs,
            "coverage_before": coverage_before,
            "coverage_after": coverage_after,
            "bar_backfill": bar_result,
            "outlook_history": outlook_result,
            "simulated_trades": simulated_trade_result,
            "evidence_after": evidence_after,
        }

    @staticmethod
    def _next_stage_target(
        *,
        current_min_coverage: int,
        final_target_days: int,
        stage_days: int,
    ) -> int:
        if current_min_coverage <= 0:
            return min(final_target_days, stage_days)
        next_stage = ((current_min_coverage // stage_days) + 1) * stage_days
        return min(final_target_days, max(stage_days, next_stage))

    @staticmethod
    def _coverage_summary(
        sess,
        *,
        instrument_ids: list[str],
    ) -> dict[str, object]:
        rows = list(
            sess.execute(
                select(
                    BetaMinuteBar.instrument_id,
                    func.min(BetaMinuteBar.session_date),
                    func.max(BetaMinuteBar.session_date),
                    func.count(func.distinct(BetaMinuteBar.session_date)),
                )
                .where(BetaMinuteBar.instrument_id.in_(instrument_ids))
                .group_by(BetaMinuteBar.instrument_id)
            ).all()
        )
        coverage_days: list[int] = []
        session_counts: list[int] = []
        covered_ids: set[str] = set()
        for instrument_id, min_session_date, max_session_date, session_count in rows:
            if min_session_date is None or max_session_date is None:
                continue
            covered_ids.add(str(instrument_id))
            coverage_days.append(int((max_session_date - min_session_date).days) + 1)
            session_counts.append(int(session_count or 0))

        symbols_with_history = len(covered_ids)
        missing = max(0, len(instrument_ids) - symbols_with_history)
        return {
            "symbols_considered": len(instrument_ids),
            "symbols_with_minute_history": symbols_with_history,
            "symbols_missing_minute_history": missing,
            "min_coverage_days": min(coverage_days) if coverage_days else 0,
            "median_coverage_days": round(float(median(coverage_days)), 1) if coverage_days else 0.0,
            "max_coverage_days": max(coverage_days) if coverage_days else 0,
            "median_sessions_per_symbol": round(float(median(session_counts)), 1) if session_counts else 0.0,
        }

    @staticmethod
    def _evidence_summary(
        sess,
        *,
        instrument_ids: list[str],
        cutoff_date,
    ) -> dict[str, int]:
        observations = int(
            sess.scalar(
                select(func.count())
                .select_from(BetaIntradayFeatureObservation)
                .where(
                    BetaIntradayFeatureObservation.instrument_id.in_(instrument_ids),
                    BetaIntradayFeatureObservation.session_date >= cutoff_date,
                )
            )
            or 0
        )
        labels = int(
            sess.scalar(
                select(func.count())
                .select_from(BetaIntradayFeatureLabelValue)
                .where(
                    BetaIntradayFeatureLabelValue.instrument_id.in_(instrument_ids),
                    BetaIntradayFeatureLabelValue.session_date >= cutoff_date,
                )
            )
            or 0
        )
        simulated_trades = int(
            sess.scalar(
                select(func.count())
                .select_from(BetaIntradaySimulatedTrade)
                .where(
                    BetaIntradaySimulatedTrade.instrument_id.in_(instrument_ids),
                    BetaIntradaySimulatedTrade.session_date >= cutoff_date,
                )
            )
            or 0
        )
        return {
            "observations": observations,
            "labels": labels,
            "simulated_trades": simulated_trades,
        }
