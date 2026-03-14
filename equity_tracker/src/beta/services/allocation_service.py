"""Paper-capital accounting and allocation helpers for the beta demo lane."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import BetaCashLedgerEntry, BetaDemoPosition, BetaLedgerState

_ZERO = Decimal("0.00")
_MIN_POSITION = Decimal("100.00")


def _d(value: str | None) -> Decimal:
    if value is None:
        return _ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return _ZERO


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


class BetaAllocationService:
    """Maintain the paper ledger and constrain demo-position sizing."""

    @staticmethod
    def ensure_ledger_state(sess: Session) -> BetaLedgerState:
        state = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
        if state is None:
            state = BetaLedgerState(
                id=1,
                base_currency="GBP",
                starting_capital_gbp="10000.00",
                available_cash_gbp="10000.00",
                deployed_capital_gbp="0.00",
                realized_pnl_gbp="0.00",
                unrealized_pnl_gbp="0.00",
                total_equity_gbp="10000.00",
            )
            sess.add(state)
            sess.flush()
        existing_initial = sess.scalar(
            select(BetaCashLedgerEntry).where(BetaCashLedgerEntry.entry_type == "INITIAL_CAPITAL")
        )
        if existing_initial is None:
            sess.add(
                BetaCashLedgerEntry(
                    entry_type="INITIAL_CAPITAL",
                    amount_gbp=state.starting_capital_gbp,
                    balance_after_gbp=state.starting_capital_gbp,
                    note="Initial paper capital.",
                )
            )
        return state

    @staticmethod
    def refresh_ledger_state(sess: Session) -> BetaLedgerState:
        state = BetaAllocationService.ensure_ledger_state(sess)
        starting = _d(state.starting_capital_gbp)
        open_positions = list(
            sess.scalars(select(BetaDemoPosition).where(BetaDemoPosition.status == "OPEN")).all()
        )
        closed_positions = list(
            sess.scalars(select(BetaDemoPosition).where(BetaDemoPosition.status != "OPEN")).all()
        )

        deployed = sum((_d(row.size_gbp) for row in open_positions), _ZERO)
        realized = sum((_d(row.pnl_gbp) for row in closed_positions), _ZERO)
        unrealized = sum((_d(row.pnl_gbp) for row in open_positions), _ZERO)
        available = starting + realized - deployed
        if available < _ZERO:
            available = _ZERO
        total = available + deployed + unrealized

        state.available_cash_gbp = _money(available)
        state.deployed_capital_gbp = _money(deployed)
        state.realized_pnl_gbp = _money(realized)
        state.unrealized_pnl_gbp = _money(unrealized)
        state.total_equity_gbp = _money(total)
        return state

    @staticmethod
    def drawdown_risk_multiplier(sess: Session) -> Decimal:
        state = BetaAllocationService.refresh_ledger_state(sess)
        starting = _d(state.starting_capital_gbp)
        total = _d(state.total_equity_gbp)
        if starting <= 0:
            return Decimal("1.00")
        drawdown = (starting - total) / starting
        if drawdown >= Decimal("0.10"):
            return Decimal("0.50")
        if drawdown >= Decimal("0.05"):
            return Decimal("0.75")
        return Decimal("1.00")

    @staticmethod
    def size_for_new_position(sess: Session, proposed_size_gbp: Decimal) -> Decimal:
        state = BetaAllocationService.refresh_ledger_state(sess)
        available = _d(state.available_cash_gbp)
        risk_multiplier = BetaAllocationService.drawdown_risk_multiplier(sess)
        adjusted = (proposed_size_gbp * risk_multiplier).quantize(Decimal("0.01"))
        if adjusted > available:
            adjusted = available
        if adjusted < _MIN_POSITION:
            return _ZERO
        return adjusted

    @staticmethod
    def record_position_open(sess: Session, position: BetaDemoPosition, *, note: str) -> BetaLedgerState:
        state = BetaAllocationService.refresh_ledger_state(sess)
        available_before = _d(state.available_cash_gbp)
        size = _d(position.size_gbp)
        balance_after = available_before - size
        if balance_after < _ZERO:
            balance_after = _ZERO
        sess.add(
            BetaCashLedgerEntry(
                position_id=position.id,
                entry_type="POSITION_OPEN",
                amount_gbp=_money(-size),
                balance_after_gbp=_money(balance_after),
                note=note,
            )
        )
        return BetaAllocationService.refresh_ledger_state(sess)

    @staticmethod
    def record_position_close(sess: Session, position: BetaDemoPosition, *, note: str) -> BetaLedgerState:
        state = BetaAllocationService.refresh_ledger_state(sess)
        available_before = _d(state.available_cash_gbp)
        size = _d(position.size_gbp)
        pnl = _d(position.pnl_gbp)
        amount = size + pnl
        balance_after = available_before + amount
        sess.add(
            BetaCashLedgerEntry(
                position_id=position.id,
                entry_type="POSITION_CLOSE",
                amount_gbp=_money(amount),
                balance_after_gbp=_money(balance_after),
                note=note,
            )
        )
        return BetaAllocationService.refresh_ledger_state(sess)
