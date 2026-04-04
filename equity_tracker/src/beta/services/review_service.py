"""Persisted review runs over the current beta research state."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import desc, func, select, text

from ..context import BetaContext
from ..db.models import (
    BetaAiReviewFinding,
    BetaAiReviewRun,
    BetaDemoPosition,
    BetaIntradaySimulatedTrade,
    BetaLedgerState,
    BetaRiskControlState,
)
from ..settings import BetaSettings
from ..state import get_beta_db_path

_PROFIT_LOOKBACK_DAYS = 45
_PROFIT_MIN_ROWS = 80
_PROFIT_MIN_SYMBOLS = 5
_PROFIT_MIN_MEAN_EDGE_PCT = 0.10
_PROFIT_MIN_WIN_RATE = 0.52
_PROFIT_TOP_LIMIT = 3
_PROFIT_MAX_SINGLE_SYMBOL_SHARE = 0.45
_PROFIT_MAX_TOP_TWO_SYMBOL_SHARE = 0.65


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    clipped_pct = min(1.0, max(0.0, float(pct)))
    index = (len(ordered) - 1) * clipped_pct
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    weight = index - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _load_cost_drag_pct() -> float:
    try:
        settings = BetaSettings.load(get_beta_db_path())
    except Exception:
        return 0.12
    total_bps = (
        float(settings.intraday_execution_commission_bps)
        + float(settings.intraday_execution_spread_bps)
        + float(settings.intraday_execution_slippage_bps)
    )
    return total_bps / 100.0


def _stable_profit_pockets(
    sess,
    *,
    cost_drag_pct: float,
    settings: BetaSettings,
) -> dict[str, list[dict[str, object]]]:
    cutoff_session_date = (date.today() - timedelta(days=_PROFIT_LOOKBACK_DAYS - 1)).isoformat()
    evaluation_horizons = [
        int(value)
        for value in (getattr(settings, "intraday_pattern_evaluation_horizons_minutes", []) or [])
        if int(value) in {5, 15, 30, 60, 120}
    ] or [15, 30]
    query = text(
        """
        WITH base AS (
            SELECT
                o.instrument_id,
                o.symbol,
                o.session_date,
                o.observed_at,
                o.state_code,
                o.state_label,
                l.future_5m_return_pct,
                l.future_15m_return_pct,
                l.future_30m_return_pct,
                l.future_60m_return_pct,
                l.future_120m_return_pct,
                CAST(
                    (
                        (CAST(strftime('%H', o.observed_at) AS INTEGER) * 60)
                        + CAST(strftime('%M', o.observed_at) AS INTEGER)
                    ) / 15 AS INTEGER
                ) AS audit_bucket
            FROM beta_intraday_feature_observations o
            JOIN beta_intraday_feature_label_values l
              ON l.observation_id = o.id
            WHERE o.session_date >= :cutoff_session_date
              AND o.session_state = 'REGULAR_OPEN'
              AND l.evaluation_complete = 1
              AND l.future_15m_return_pct IS NOT NULL
              AND l.future_30m_return_pct IS NOT NULL
        ),
        canonical AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY instrument_id, session_date, audit_bucket
                    ORDER BY observed_at DESC
                ) AS rn
            FROM base
        )
        SELECT
            state_code,
            state_label,
            symbol,
            session_date,
            future_5m_return_pct,
            future_15m_return_pct,
            future_30m_return_pct,
            future_60m_return_pct,
            future_120m_return_pct
        FROM canonical
        WHERE rn = 1
        """
    )
    rows = list(sess.execute(query, {"cutoff_session_date": cutoff_session_date}).mappings())
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in rows:
        state_code = str(row["state_code"] or "").strip().upper()
        if not state_code:
            continue
        grouped[state_code].append(dict(row))
        labels.setdefault(state_code, str(row["state_label"] or state_code))

    long_pockets: list[dict[str, object]] = []
    short_pockets: list[dict[str, object]] = []
    for state_code, state_rows in grouped.items():
        candidates: list[dict[str, object]] = []
        for horizon_minutes in evaluation_horizons:
            value_key = f"future_{horizon_minutes}m_return_pct"
            for direction in ("LONG", "SHORT"):
                returns: list[float] = []
                symbols: set[str] = set()
                sessions: set[str] = set()
                for row in state_rows:
                    raw_return = row.get(value_key)
                    if raw_return is None:
                        continue
                    realized = float(raw_return)
                    edge_return = realized - cost_drag_pct if direction == "LONG" else (-realized) - cost_drag_pct
                    returns.append(edge_return)
                    symbol = str(row["symbol"] or "").strip().upper()
                    session_date = str(row["session_date"] or "").strip()
                    if symbol:
                        symbols.add(symbol)
                    if session_date:
                        sessions.add(session_date)
                if len(returns) < _PROFIT_MIN_ROWS or len(symbols) < _PROFIT_MIN_SYMBOLS:
                    continue
                mean_edge = _mean(returns)
                median_edge = _percentile(returns, 0.50)
                win_rate = _mean([1.0 if value > 0.0 else 0.0 for value in returns])
                symbol_counts = sorted(
                    (
                        sum(1 for row in state_rows if str(row["symbol"] or "").strip().upper() == symbol)
                        for symbol in symbols
                    ),
                    reverse=True,
                )
                top_symbol_share = (
                    float(symbol_counts[0]) / float(len(returns))
                    if returns and symbol_counts
                    else None
                )
                top_two_symbol_share = (
                    float(sum(symbol_counts[:2])) / float(len(returns))
                    if returns and symbol_counts
                    else None
                )
                if (
                    mean_edge is None
                    or median_edge is None
                    or win_rate is None
                    or mean_edge < _PROFIT_MIN_MEAN_EDGE_PCT
                    or median_edge <= 0.0
                    or win_rate < _PROFIT_MIN_WIN_RATE
                    or (
                        top_symbol_share is not None
                        and top_symbol_share > _PROFIT_MAX_SINGLE_SYMBOL_SHARE
                    )
                    or (
                        top_two_symbol_share is not None
                        and top_two_symbol_share > _PROFIT_MAX_TOP_TWO_SYMBOL_SHARE
                    )
                ):
                    continue
                candidates.append(
                    {
                        "state_code": state_code,
                        "state_label": labels.get(state_code, state_code),
                        "direction": direction,
                        "horizon_minutes": horizon_minutes,
                        "row_count": len(returns),
                        "symbol_count": len(symbols),
                        "session_count": len(sessions),
                        "avg_post_cost_return_pct": mean_edge,
                        "median_post_cost_return_pct": median_edge,
                        "win_rate": win_rate,
                        "top_symbol_share": top_symbol_share,
                        "top_two_symbol_share": top_two_symbol_share,
                    }
                )
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda item: (
                float(item["avg_post_cost_return_pct"]),
                float(item["median_post_cost_return_pct"]),
                float(item["win_rate"]),
                int(item["row_count"]),
            ),
        )
        if str(best["direction"]) == "LONG":
            long_pockets.append(best)
        else:
            short_pockets.append(best)

    sort_key = lambda item: (
        float(item["avg_post_cost_return_pct"]),
        float(item["median_post_cost_return_pct"]),
        float(item["win_rate"]),
        int(item["row_count"]),
    )
    long_pockets.sort(key=sort_key, reverse=True)
    short_pockets.sort(key=sort_key, reverse=True)
    return {
        "long": long_pockets[:_PROFIT_TOP_LIMIT],
        "short": short_pockets[:_PROFIT_TOP_LIMIT],
    }


def _simulated_trade_profit_states(closed_sim_trades: list[BetaIntradaySimulatedTrade]) -> list[dict[str, object]]:
    profitable_states: list[dict[str, object]] = []
    state_stats: dict[str, dict[str, object]] = {}
    for trade in closed_sim_trades:
        direction = str(trade.direction or "LONG").strip().upper() or "LONG"
        state_code = str(trade.state_code or trade.state_label or "UNKNOWN").strip() or "UNKNOWN"
        state_key = f"{state_code}|{direction}"
        stats = state_stats.setdefault(
            state_key,
            {
                "state_code": state_code,
                "state_label": str(trade.state_label or trade.state_code or "Unknown state"),
                "direction": direction,
                "returns": [],
                "wins": 0,
            },
        )
        realized_post_cost = float(trade.realized_post_cost_return_pct or 0.0)
        returns = stats["returns"]
        returns.append(realized_post_cost)
        if realized_post_cost > 0.0:
            stats["wins"] = int(stats["wins"]) + 1

    for state_key, stats in state_stats.items():
        returns = [float(value) for value in stats["returns"]]
        if len(returns) < 3:
            continue
        avg_post_cost_return_pct = sum(returns) / len(returns)
        win_rate = float(stats["wins"]) / len(returns)
        profitable_states.append(
            {
                "state_code": str(stats["state_code"]),
                "state_label": str(stats["state_label"]),
                "direction": str(stats["direction"]),
                "trade_count": len(returns),
                "avg_post_cost_return_pct": round(avg_post_cost_return_pct, 4),
                "win_rate": round(win_rate, 4),
            }
        )
    profitable_states.sort(
        key=lambda item: (
            float(item["avg_post_cost_return_pct"]),
            float(item["win_rate"]),
            int(item["trade_count"]),
        ),
        reverse=True,
    )
    return profitable_states[:5]


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

        cost_drag_pct = _load_cost_drag_pct()
        beta_db_path = get_beta_db_path()
        settings = BetaSettings.load(beta_db_path) if beta_db_path is not None else BetaSettings()
        with BetaContext.write_session() as sess:
            ledger = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
            risk = sess.scalar(select(BetaRiskControlState).where(BetaRiskControlState.id == 1))
            closed_sim_trades = list(
                sess.scalars(
                    select(BetaIntradaySimulatedTrade)
                    .where(
                        BetaIntradaySimulatedTrade.status == "CLOSED",
                        BetaIntradaySimulatedTrade.realized_post_cost_return_pct.is_not(None),
                    )
                    .order_by(
                        desc(BetaIntradaySimulatedTrade.exit_observed_at),
                        desc(BetaIntradaySimulatedTrade.updated_at),
                    )
                    .limit(500)
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
            stable_pockets = _stable_profit_pockets(sess, cost_drag_pct=cost_drag_pct, settings=settings)
            profitable_states = _simulated_trade_profit_states(closed_sim_trades)

            pocket_count = len(stable_pockets["long"]) + len(stable_pockets["short"])
            summary = (
                f"Review based on {pocket_count} stable intraday profit pockets, "
                f"{len(profitable_states)} simulated trade states, and {len(open_positions)} open demo positions. "
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
            for direction_key, pockets in (("LONG", stable_pockets["long"]), ("SHORT", stable_pockets["short"])):
                for pocket in pockets:
                    sess.add(
                        BetaAiReviewFinding(
                            review_run_id=review_run.id,
                            finding_type="intraday_profit_pocket",
                            severity="INFO",
                            subject_symbol=str(pocket["state_code"]),
                            message_text=(
                                f"{pocket['state_label']} has been a stable {direction_key.lower()} pocket over "
                                f"{int(pocket['horizon_minutes'])}m with {float(pocket['avg_post_cost_return_pct']):+.2f}% "
                                f"average post-cost return, {float(pocket['median_post_cost_return_pct']):+.2f}% median, "
                                f"and {float(pocket['win_rate']) * 100.0:.0f}% hit rate."
                            ),
                            payload_json=json.dumps(
                                {
                                    "state_code": pocket["state_code"],
                                    "state_label": pocket["state_label"],
                                    "direction": pocket["direction"],
                                    "horizon_minutes": pocket["horizon_minutes"],
                                    "row_count": pocket["row_count"],
                                    "symbol_count": pocket["symbol_count"],
                                    "session_count": pocket["session_count"],
                                    "avg_post_cost_return_pct": round(float(pocket["avg_post_cost_return_pct"]), 4),
                                    "median_post_cost_return_pct": round(
                                        float(pocket["median_post_cost_return_pct"]),
                                        4,
                                    ),
                                    "win_rate": round(float(pocket["win_rate"]), 4),
                                    "top_symbol_share": round(float(pocket["top_symbol_share"]), 4),
                                    "top_two_symbol_share": round(float(pocket["top_two_symbol_share"]), 4),
                                },
                                sort_keys=True,
                            ),
                        )
                    )
                    findings += 1

            for state in profitable_states:
                sess.add(
                    BetaAiReviewFinding(
                        review_run_id=review_run.id,
                        finding_type="simulated_trade_state",
                        severity="INFO",
                        subject_symbol=str(state["state_code"]),
                        message_text=(
                            f"{state['state_label']} {str(state['direction']).lower()} trades averaged "
                            f"{float(state['avg_post_cost_return_pct']):+.2f}% post-cost across "
                            f"{int(state['trade_count'])} simulated trades with "
                            f"{float(state['win_rate']) * 100.0:.0f}% win rate."
                        ),
                        payload_json=json.dumps(
                            {
                                "state_code": state["state_code"],
                                "state_label": state["state_label"],
                                "direction": state["direction"],
                                "trade_count": state["trade_count"],
                                "avg_post_cost_return_pct": state["avg_post_cost_return_pct"],
                                "win_rate": state["win_rate"],
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
