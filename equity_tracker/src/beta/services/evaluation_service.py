"""Persist compact live evaluation summaries for the beta learning lane."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaConfidenceBucketSummary,
    BetaDemoPosition,
    BetaDirectionSummary,
    BetaEvaluationRun,
    BetaEvaluationSummary,
    BetaLabelDefinition,
    BetaLabelValue,
    BetaLedgerState,
    BetaScoreTape,
)

_CALIBRATION_WINDOW = 500


def _d(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _trend_label(pnl_pct_values: list[float]) -> str:
    if not pnl_pct_values:
        return "STABLE"
    recent = pnl_pct_values[:3]
    prior = pnl_pct_values[3:6]
    recent_avg = sum(recent) / len(recent)
    prior_avg = (sum(prior) / len(prior)) if prior else recent_avg
    if recent_avg > prior_avg + 0.25:
        return "IMPROVING"
    if recent_avg < prior_avg - 0.25:
        return "DECLINING"
    return "STABLE"


def _bucket_label(confidence: float) -> str:
    if confidence >= 0.72:
        return "HIGH"
    if confidence >= 0.55:
        return "MEDIUM"
    return "LOW"


def _alignment(direction: str, label_return: float) -> bool:
    if direction == "BULLISH":
        return label_return > 0
    if direction in {"BEARISH", "RISK_OFF"}:
        return label_return < 0
    return abs(label_return) <= 1.0


class BetaEvaluationService:
    """Compute lightweight persisted evaluation views from score tape and paper outcomes."""

    @staticmethod
    def run_live_evaluation() -> dict[str, int | float | str]:
        if not BetaContext.is_initialized():
            return {"evaluation_run_id": "", "labeled_scores": 0, "total_scores": 0}

        with BetaContext.write_session() as sess:
            total_scores = sess.scalar(select(func.count()).select_from(BetaScoreTape)) or 0
            recommended_scores = (
                sess.scalar(
                    select(func.count()).select_from(BetaScoreTape).where(BetaScoreTape.recommendation_flag.is_(True))
                )
                or 0
            )
            open_positions = (
                sess.scalar(
                    select(func.count()).select_from(BetaDemoPosition).where(BetaDemoPosition.status == "OPEN")
                )
                or 0
            )
            closed_positions_rows = list(
                sess.scalars(
                    select(BetaDemoPosition)
                    .where(BetaDemoPosition.status != "OPEN")
                    .order_by(desc(BetaDemoPosition.closed_at), desc(BetaDemoPosition.updated_at))
                ).all()
            )
            traded_candidates = len({row.candidate_id for row in closed_positions_rows if row.candidate_id} | {
                row.candidate_id
                for row in sess.scalars(select(BetaDemoPosition).where(BetaDemoPosition.status == "OPEN")).all()
                if row.candidate_id
            })

            pnl_pct_values = [float(_d(row.pnl_pct)) for row in closed_positions_rows if row.pnl_pct is not None]
            wins = len([row for row in closed_positions_rows if _d(row.pnl_gbp) > 0])
            win_rate_pct = round((wins / len(closed_positions_rows)) * 100, 1) if closed_positions_rows else 0.0
            avg_closed_pnl_pct = round(sum(pnl_pct_values) / len(pnl_pct_values), 2) if pnl_pct_values else 0.0
            trend_label = _trend_label(pnl_pct_values)

            ledger = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
            realized_pnl_gbp = ledger.realized_pnl_gbp if ledger is not None else "0.00"
            unrealized_pnl_gbp = ledger.unrealized_pnl_gbp if ledger is not None else "0.00"

            canonical_label = sess.scalar(
                select(BetaLabelDefinition).where(BetaLabelDefinition.is_canonical.is_(True)).limit(1)
            )
            recent_scores = list(
                sess.scalars(
                    select(BetaScoreTape).order_by(desc(BetaScoreTape.scored_at)).limit(_CALIBRATION_WINDOW)
                ).all()
            )
            label_map: dict[tuple[str, object], float] = {}
            if canonical_label is not None and recent_scores:
                instrument_ids = sorted({row.instrument_id for row in recent_scores})
                label_rows = list(
                    sess.scalars(
                        select(BetaLabelValue).where(
                            BetaLabelValue.label_definition_id == canonical_label.id,
                            BetaLabelValue.instrument_id.in_(instrument_ids),
                        )
                    ).all()
                )
                label_map = {
                    (row.instrument_id, row.decision_date): float(row.value_numeric)
                    for row in label_rows
                    if row.value_numeric is not None
                }

            labeled_scores = 0
            labeled_returns: list[float] = []
            bucket_stats: dict[str, dict[str, list[float] | int]] = defaultdict(
                lambda: {
                    "confidence": [],
                    "edge": [],
                    "returns": [],
                    "alignments": [],
                    "observations": 0,
                    "recommended": 0,
                }
            )
            direction_stats: dict[str, dict[str, list[float] | int]] = defaultdict(
                lambda: {
                    "confidence": [],
                    "edge": [],
                    "returns": [],
                    "alignments": [],
                    "observations": 0,
                    "recommended": 0,
                }
            )

            for row in recent_scores:
                bucket = _bucket_label(float(row.confidence_score or 0.0))
                bucket_entry = bucket_stats[bucket]
                direction_entry = direction_stats[row.direction]

                bucket_entry["observations"] += 1
                direction_entry["observations"] += 1
                bucket_entry["confidence"].append(float(row.confidence_score or 0.0))
                direction_entry["confidence"].append(float(row.confidence_score or 0.0))
                bucket_entry["edge"].append(float(row.expected_edge_score or 0.0))
                direction_entry["edge"].append(float(row.expected_edge_score or 0.0))
                if row.recommendation_flag:
                    bucket_entry["recommended"] += 1
                    direction_entry["recommended"] += 1

                key = (row.instrument_id, row.scored_at.date())
                future_return = label_map.get(key)
                if future_return is None:
                    continue
                labeled_scores += 1
                labeled_returns.append(future_return)
                aligned = 1.0 if _alignment(row.direction, future_return) else 0.0
                bucket_entry["returns"].append(future_return)
                direction_entry["returns"].append(future_return)
                bucket_entry["alignments"].append(aligned)
                direction_entry["alignments"].append(aligned)

            recommendation_rate_pct = round((recommended_scores / total_scores) * 100, 1) if total_scores else 0.0
            total_positions = open_positions + len(closed_positions_rows)
            conversion_rate_pct = round((total_positions / recommended_scores) * 100, 1) if recommended_scores else 0.0
            avg_labeled_return_pct = round(sum(labeled_returns) / len(labeled_returns), 2) if labeled_returns else 0.0

            summary_text = (
                f"{recommended_scores} of {total_scores} scores recommended; "
                f"{len(closed_positions_rows)} closed trades with win rate {win_rate_pct:.1f}%; "
                f"trend {trend_label.lower()}."
            )
            run = BetaEvaluationRun(
                evaluation_type="LIVE_TRAILING",
                status="SUCCESS",
                summary_text=summary_text,
            )
            sess.add(run)
            sess.flush()

            sess.add(
                BetaEvaluationSummary(
                    evaluation_run_id=run.id,
                    total_scores=total_scores,
                    recommended_scores=recommended_scores,
                    recommendation_rate_pct=recommendation_rate_pct,
                    labeled_scores=labeled_scores,
                    open_positions=open_positions,
                    closed_positions=len(closed_positions_rows),
                    traded_candidates=traded_candidates,
                    conversion_rate_pct=conversion_rate_pct,
                    win_rate_pct=win_rate_pct,
                    avg_closed_pnl_pct=avg_closed_pnl_pct,
                    realized_pnl_gbp=realized_pnl_gbp,
                    unrealized_pnl_gbp=unrealized_pnl_gbp,
                    avg_labeled_return_pct=avg_labeled_return_pct,
                    trend_label=trend_label,
                )
            )

            for bucket_label in ("HIGH", "MEDIUM", "LOW"):
                stats = bucket_stats[bucket_label]
                returns = stats["returns"]
                alignments = stats["alignments"]
                sess.add(
                    BetaConfidenceBucketSummary(
                        evaluation_run_id=run.id,
                        bucket_label=bucket_label,
                        observation_count=int(stats["observations"]),
                        recommendation_count=int(stats["recommended"]),
                        avg_confidence_score=round(sum(stats["confidence"]) / len(stats["confidence"]), 4)
                        if stats["confidence"]
                        else 0.0,
                        avg_expected_edge_score=round(sum(stats["edge"]) / len(stats["edge"]), 4)
                        if stats["edge"]
                        else 0.0,
                        avg_future_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
                        alignment_rate_pct=round((sum(alignments) / len(alignments)) * 100, 1) if alignments else None,
                    )
                )

            for direction in ("BULLISH", "BEARISH", "NEUTRAL", "RISK_OFF"):
                stats = direction_stats[direction]
                returns = stats["returns"]
                alignments = stats["alignments"]
                sess.add(
                    BetaDirectionSummary(
                        evaluation_run_id=run.id,
                        direction=direction,
                        observation_count=int(stats["observations"]),
                        recommendation_count=int(stats["recommended"]),
                        avg_confidence_score=round(sum(stats["confidence"]) / len(stats["confidence"]), 4)
                        if stats["confidence"]
                        else 0.0,
                        avg_expected_edge_score=round(sum(stats["edge"]) / len(stats["edge"]), 4)
                        if stats["edge"]
                        else 0.0,
                        avg_future_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
                        alignment_rate_pct=round((sum(alignments) / len(alignments)) * 100, 1) if alignments else None,
                    )
                )

            return {
                "evaluation_run_id": run.id,
                "total_scores": total_scores,
                "labeled_scores": labeled_scores,
                "win_rate_pct": win_rate_pct,
                "trend_label": trend_label,
            }
