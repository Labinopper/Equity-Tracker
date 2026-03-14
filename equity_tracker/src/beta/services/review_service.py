"""Persisted review runs over the current beta research state."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaAiReviewFinding,
    BetaAiReviewRun,
    BetaDemoPosition,
    BetaLedgerState,
    BetaRiskControlState,
    BetaSignalCandidate,
)


def _d(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


class BetaReviewService:
    """Store simple auditable review summaries for later inspection."""

    @staticmethod
    def ensure_daily_potential_gains_review() -> dict[str, int | str | bool]:
        if not BetaContext.is_initialized():
            return {"findings": 0, "review_run_id": "", "performed": False}

        with BetaContext.read_session() as sess:
            existing = sess.scalar(
                select(BetaAiReviewRun)
                .where(
                    BetaAiReviewRun.review_type == "potential_gains",
                    func.date(BetaAiReviewRun.created_at) == date.today().isoformat(),
                )
                .order_by(desc(BetaAiReviewRun.created_at))
            )
            if existing is not None:
                return {"findings": 0, "review_run_id": existing.id, "performed": False}

        result = BetaReviewService.run_potential_gains_review()
        result["performed"] = True
        return result

    @staticmethod
    def run_potential_gains_review() -> dict[str, int | str]:
        if not BetaContext.is_initialized():
            return {"findings": 0, "review_run_id": ""}

        with BetaContext.write_session() as sess:
            ledger = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
            risk = sess.scalar(select(BetaRiskControlState).where(BetaRiskControlState.id == 1))
            top_candidates = list(
                sess.scalars(
                    select(BetaSignalCandidate)
                    .where(BetaSignalCandidate.status.in_(("WATCHING", "PROMOTED")))
                    .order_by(
                        desc(BetaSignalCandidate.confidence_score),
                        desc(BetaSignalCandidate.expected_edge_score),
                    )
                    .limit(5)
                ).all()
            )
            open_positions = list(
                sess.scalars(
                    select(BetaDemoPosition)
                    .where(BetaDemoPosition.status == "OPEN")
                    .order_by(desc(BetaDemoPosition.confidence_score))
                    .limit(5)
                ).all()
            )

            summary = (
                f"Review based on {len(top_candidates)} watched candidates and {len(open_positions)} open demo positions. "
                f"Available cash GBP {ledger.available_cash_gbp if ledger is not None else '0.00'}."
            )
            review_run = BetaAiReviewRun(
                review_type="potential_gains",
                status="SUCCESS",
                summary_text=summary,
            )
            sess.add(review_run)
            sess.flush()

            findings = 0
            for candidate in top_candidates:
                projected_gain = candidate.expected_edge_score * 100
                sess.add(
                    BetaAiReviewFinding(
                        review_run_id=review_run.id,
                        finding_type="candidate_upside",
                        severity="INFO",
                        subject_symbol=candidate.symbol,
                        message_text=(
                            f"{candidate.symbol} remains a watched opportunity with confidence "
                            f"{candidate.confidence_score:.2f} and modeled edge {candidate.expected_edge_score:.2f}."
                        ),
                        payload_json=json.dumps(
                            {
                                "confidence_score": candidate.confidence_score,
                                "expected_edge_score": candidate.expected_edge_score,
                                "projected_gain_proxy": projected_gain,
                            },
                            sort_keys=True,
                        ),
                    )
                )
                findings += 1

            for position in open_positions:
                sess.add(
                    BetaAiReviewFinding(
                        review_run_id=review_run.id,
                        finding_type="open_position_status",
                        severity="INFO",
                        subject_symbol=position.symbol,
                        message_text=(
                            f"{position.symbol} is open with confidence {position.confidence_score:.2f}, "
                            f"expected edge {position.expected_edge_score:.2f}, and current P/L GBP {position.pnl_gbp or '0.00'}."
                        ),
                        payload_json=json.dumps(
                            {
                                "confidence_score": position.confidence_score,
                                "expected_edge_score": position.expected_edge_score,
                                "pnl_gbp": position.pnl_gbp,
                            },
                            sort_keys=True,
                        ),
                    )
                )
                findings += 1

            if risk is not None and risk.demo_entries_paused:
                sess.add(
                    BetaAiReviewFinding(
                        review_run_id=review_run.id,
                        finding_type="risk_control",
                        severity="WARNING",
                        message_text=risk.pause_reason or "New entries are currently paused by risk control.",
                        payload_json=json.dumps(
                            {
                                "degradation_status": risk.degradation_status,
                                "recent_win_rate_pct": risk.recent_win_rate_pct,
                                "recent_avg_pnl_pct": risk.recent_avg_pnl_pct,
                            },
                            sort_keys=True,
                        ),
                    )
                )
                findings += 1

            if ledger is not None:
                sess.add(
                    BetaAiReviewFinding(
                        review_run_id=review_run.id,
                        finding_type="capital_state",
                        severity="INFO",
                        message_text=(
                            f"Paper capital available GBP {ledger.available_cash_gbp}, "
                            f"deployed GBP {ledger.deployed_capital_gbp}, total equity GBP {ledger.total_equity_gbp}."
                        ),
                        payload_json=json.dumps(
                            {
                                "available_cash_gbp": ledger.available_cash_gbp,
                                "deployed_capital_gbp": ledger.deployed_capital_gbp,
                                "total_equity_gbp": ledger.total_equity_gbp,
                            },
                            sort_keys=True,
                        ),
                    )
                )
                findings += 1

            return {"findings": findings, "review_run_id": review_run.id}
