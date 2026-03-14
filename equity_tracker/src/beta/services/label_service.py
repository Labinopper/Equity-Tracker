"""Explicit future-outcome labels over beta daily bars."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..context import BetaContext
from ..db.models import BetaBenchmarkBar, BetaDailyBar, BetaFeatureValue, BetaInstrument, BetaLabelDefinition, BetaLabelValue

_MIN_LABEL_BACKLOG_BARS = 30
_LABEL_DEFINITIONS_PER_DATE = 3


def _d(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class BetaLabelService:
    """Persist raw and market-relative forward-return labels for later model work."""

    @staticmethod
    def ensure_label_definitions(sess: Session) -> dict[str, str]:
        specs = (
            (
                "fwd_5d_return_pct",
                "v1",
                5,
                "Five-trading-day close-to-close percent return in GBP terms.",
                False,
            ),
            (
                "fwd_5d_excess_return_pct",
                "v1",
                5,
                "Five-trading-day return minus mapped benchmark forward return over the same horizon, falling back to same-market average when needed.",
                True,
            ),
            (
                "fwd_5d_sector_excess_return_pct",
                "v1",
                5,
                "Five-trading-day return minus heuristic sector cohort forward return over the same horizon.",
                False,
            ),
        )
        mapping: dict[str, str] = {}
        for label_name, version_code, horizon_days, definition_text, is_canonical in specs:
            existing = sess.scalar(
                select(BetaLabelDefinition).where(
                    BetaLabelDefinition.label_name == label_name,
                    BetaLabelDefinition.version_code == version_code,
                )
            )
            if existing is None:
                existing = BetaLabelDefinition(
                    label_name=label_name,
                    version_code=version_code,
                    horizon_days=horizon_days,
                    definition_text=definition_text,
                    is_canonical=is_canonical,
                )
                sess.add(existing)
                sess.flush()
            else:
                existing.horizon_days = horizon_days
                existing.definition_text = definition_text
                existing.is_canonical = is_canonical
            mapping[label_name] = existing.id
        return mapping

    @staticmethod
    def _resolve_target_instruments(
        sess: Session,
        *,
        instrument_ids: list[str] | None = None,
        core_only: bool = False,
    ) -> list[BetaInstrument]:
        stmt = select(BetaInstrument)
        if core_only:
            stmt = stmt.where(BetaInstrument.core_security_id.is_not(None))
        rows = list(sess.scalars(stmt).all())
        if instrument_ids is None:
            return rows
        allowed = set(instrument_ids)
        return [row for row in rows if row.id in allowed]

    @staticmethod
    def _label_backlog_instrument_ids(sess: Session, *, batch_size: int) -> list[str]:
        instruments = list(sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all())
        candidates: list[tuple[int, float, int, str]] = []
        for instrument in instruments:
            bar_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaDailyBar).where(BetaDailyBar.instrument_id == instrument.id)
                )
                or 0
            )
            if bar_count < _MIN_LABEL_BACKLOG_BARS:
                continue
            feature_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaFeatureValue).where(BetaFeatureValue.instrument_id == instrument.id)
                )
                or 0
            )
            if feature_count <= 0:
                continue
            label_count = int(
                sess.scalar(
                    select(func.count()).select_from(BetaLabelValue).where(BetaLabelValue.instrument_id == instrument.id)
                )
                or 0
            )
            expected_label_count = max(0, bar_count - 5) * _LABEL_DEFINITIONS_PER_DATE
            if label_count >= expected_label_count:
                continue
            priority = 0 if instrument.core_security_id else 1
            coverage_ratio = (label_count / expected_label_count) if expected_label_count else 0.0
            candidates.append((priority, coverage_ratio, -bar_count, instrument.id))
        candidates.sort()
        return [instrument_id for _priority, _coverage_ratio, _neg_bar_count, instrument_id in candidates[:batch_size]]

    @staticmethod
    def generate_daily_labels(*, instrument_ids: list[str] | None = None, core_only: bool = False) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"labels_written": 0}

        with BetaContext.write_session() as sess:
            label_definition_ids = BetaLabelService.ensure_label_definitions(sess)
            instruments = list(sess.scalars(select(BetaInstrument)).all())
            target_instruments = BetaLabelService._resolve_target_instruments(
                sess,
                instrument_ids=instrument_ids,
                core_only=core_only,
            )
            target_ids = [row.id for row in target_instruments]
            raw_returns_by_market_date: dict[tuple[str, object], list[tuple[str, float]]] = defaultdict(list)
            raw_returns_by_sector_date: dict[tuple[str, str, object], list[tuple[str, float]]] = defaultdict(list)
            instrument_returns: dict[tuple[str, object], tuple[float, object]] = {}
            benchmark_rows = list(
                sess.scalars(select(BetaBenchmarkBar).order_by(BetaBenchmarkBar.benchmark_key.asc(), BetaBenchmarkBar.bar_date.asc())).all()
            )
            benchmark_close_map: dict[tuple[str, object], Decimal] = {}
            for row in benchmark_rows:
                close = _d(row.close_price_gbp)
                if close is None or close <= 0:
                    continue
                benchmark_close_map[(row.benchmark_key, row.bar_date)] = close

            for instrument in instruments:
                bars = list(
                    sess.scalars(
                        select(BetaDailyBar)
                        .where(BetaDailyBar.instrument_id == instrument.id)
                        .order_by(BetaDailyBar.bar_date)
                    ).all()
                )
                closes = [_d(row.close_price_gbp) for row in bars]
                market = str(instrument.market or "OTHER")
                sector_key = str(instrument.sector_key or "GENERAL")
                for idx, bar in enumerate(bars):
                    horizon_index = idx + 5
                    if horizon_index >= len(bars):
                        continue
                    current_close = closes[idx]
                    future_close = closes[horizon_index]
                    if current_close is None or future_close is None or current_close <= 0:
                        continue
                    raw_return = float(((future_close / current_close) - Decimal("1")) * Decimal("100"))
                    key = (instrument.id, bar.bar_date)
                    instrument_returns[key] = (raw_return, bars[horizon_index].bar_date)
                    raw_returns_by_market_date[(market, bar.bar_date)].append(
                        (instrument.id, raw_return)
                    )
                    raw_returns_by_sector_date[(market, sector_key, bar.bar_date)].append((instrument.id, raw_return))

            written = 0
            existing_keys = {
                (row.label_definition_id, row.instrument_id, row.decision_date): row
                for row in sess.scalars(
                    select(BetaLabelValue).where(BetaLabelValue.instrument_id.in_(target_ids if target_ids else [""]))
                ).all()
            } if target_ids else {}
            for instrument in target_instruments:
                market = str(instrument.market or "OTHER")
                sector_key = str(instrument.sector_key or "GENERAL")
                benchmark_key = str(instrument.benchmark_key or "")
                instrument_keys = [
                    key for key in instrument_returns.keys() if key[0] == instrument.id
                ]
                for key in instrument_keys:
                    decision_date = key[1]
                    raw_return, horizon_end_date = instrument_returns[key]
                    peer_returns = raw_returns_by_market_date.get((market, decision_date), [])
                    comparison_returns = [value for instrument_id, value in peer_returns if instrument_id != instrument.id]
                    if not comparison_returns:
                        comparison_returns = [value for _, value in peer_returns]
                    market_benchmark = (
                        sum(comparison_returns) / len(comparison_returns) if comparison_returns else 0.0
                    )
                    sector_returns = raw_returns_by_sector_date.get((market, sector_key, decision_date), [])
                    sector_comparison_returns = [value for instrument_id, value in sector_returns if instrument_id != instrument.id]
                    if not sector_comparison_returns:
                        sector_comparison_returns = [value for _, value in sector_returns]
                    sector_benchmark = (
                        sum(sector_comparison_returns) / len(sector_comparison_returns) if sector_comparison_returns else market_benchmark
                    )

                    benchmark_excess_return = raw_return - market_benchmark
                    benchmark_start = benchmark_close_map.get((benchmark_key, decision_date))
                    benchmark_end = benchmark_close_map.get((benchmark_key, horizon_end_date))
                    if benchmark_start is not None and benchmark_end is not None and benchmark_start > 0:
                        benchmark_return = float(((benchmark_end / benchmark_start) - Decimal("1")) * Decimal("100"))
                        benchmark_excess_return = raw_return - benchmark_return
                    sector_excess_return = raw_return - sector_benchmark

                    rows_to_write = (
                        (label_definition_ids["fwd_5d_return_pct"], raw_return, "RAW_FORWARD_RETURN"),
                        (
                            label_definition_ids["fwd_5d_excess_return_pct"],
                            benchmark_excess_return,
                            f"BENCHMARK_EXCESS_VS_{benchmark_key or market}",
                        ),
                        (
                            label_definition_ids["fwd_5d_sector_excess_return_pct"],
                            sector_excess_return,
                            f"SECTOR_EXCESS_VS_{sector_key}",
                        ),
                    )
                    for label_definition_id, numeric_value, value_text in rows_to_write:
                        key = (label_definition_id, instrument.id, decision_date)
                        existing = existing_keys.get(key)
                        if existing is None:
                            existing = BetaLabelValue(
                                label_definition_id=label_definition_id,
                                instrument_id=instrument.id,
                                decision_date=decision_date,
                                horizon_end_date=horizon_end_date,
                                value_numeric=numeric_value,
                                value_text=value_text,
                            )
                            sess.add(existing)
                            existing_keys[key] = existing
                            written += 1
                        else:
                            existing.horizon_end_date = horizon_end_date
                            existing.value_numeric = numeric_value
                            existing.value_text = value_text

            return {
                "labels_written": written,
                "target_instruments": len(target_instruments),
                "scope": "CORE_ONLY" if core_only else ("SELECTED" if instrument_ids is not None else "FULL"),
            }

    @staticmethod
    def generate_core_tracked_labels() -> dict[str, int]:
        return BetaLabelService.generate_daily_labels(core_only=True)

    @staticmethod
    def generate_label_backlog(*, batch_size: int = 3) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"labels_written": 0, "selected_instruments": 0}
        with BetaContext.write_session() as sess:
            target_ids = BetaLabelService._label_backlog_instrument_ids(sess, batch_size=batch_size)
        result = BetaLabelService.generate_daily_labels(instrument_ids=target_ids)
        result["selected_instruments"] = len(target_ids)
        return result
