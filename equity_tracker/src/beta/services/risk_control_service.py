"""Automatic pause/resume governance for the demo-trade lane."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db.models import BetaDemoPosition, BetaRiskControlState
from ..settings import BetaSettings


def _d(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaRiskControlService:
    """Evaluate recent paper-trade outcomes and gate new entries when needed."""

    @staticmethod
    def ensure_state(sess: Session) -> BetaRiskControlState:
        state = sess.scalar(select(BetaRiskControlState).where(BetaRiskControlState.id == 1))
        if state is None:
            state = BetaRiskControlState(
                id=1,
                demo_entries_paused=False,
                degradation_status="NORMAL",
                recent_closed_trades=0,
                recent_win_rate_pct=0.0,
                recent_avg_pnl_pct=0.0,
            )
            sess.add(state)
            sess.flush()
        return state

    @staticmethod
    def evaluate_recent_performance(sess: Session, settings: BetaSettings) -> BetaRiskControlState:
        state = BetaRiskControlService.ensure_state(sess)
        closed_positions = list(
            sess.scalars(
                select(BetaDemoPosition)
                .where(BetaDemoPosition.status != "OPEN")
                .order_by(desc(BetaDemoPosition.closed_at), desc(BetaDemoPosition.updated_at))
                .limit(6)
            ).all()
        )

        pnl_pct_values = [float(_d(row.pnl_pct)) for row in closed_positions if row.pnl_pct is not None]
        wins = len([row for row in closed_positions if _d(row.pnl_gbp) > 0])
        state.recent_closed_trades = len(closed_positions)
        state.recent_win_rate_pct = (
            round((wins / len(closed_positions)) * 100, 1) if closed_positions else 0.0
        )
        state.recent_avg_pnl_pct = (
            round(sum(pnl_pct_values) / len(pnl_pct_values), 2) if pnl_pct_values else 0.0
        )

        should_pause = (
            settings.auto_pause_entries_on_degradation
            and len(closed_positions) >= 4
            and (
                state.recent_win_rate_pct < 35.0
                or state.recent_avg_pnl_pct <= -2.0
            )
        )
        should_resume = (
            settings.auto_resume_entries_on_recovery
            and state.demo_entries_paused
            and len(closed_positions) >= 4
            and state.recent_win_rate_pct >= 50.0
            and state.recent_avg_pnl_pct >= 0.25
        )

        if should_pause and not state.demo_entries_paused:
            state.demo_entries_paused = True
            state.pause_reason = (
                f"Recent closed trades deteriorated: win rate {state.recent_win_rate_pct:.1f}% "
                f"and avg P/L {state.recent_avg_pnl_pct:.2f}%."
            )
            state.degradation_status = "PAUSED"
            state.auto_paused_at = _utcnow()
        elif should_resume:
            state.demo_entries_paused = False
            state.pause_reason = None
            state.degradation_status = "RECOVERING"
            state.last_resumed_at = _utcnow()
        elif state.demo_entries_paused:
            state.degradation_status = "PAUSED"
        else:
            state.degradation_status = "NORMAL"

        return state
