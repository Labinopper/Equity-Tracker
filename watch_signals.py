"""Watch the beta DB for current intraday guidance on held shares."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from equity_tracker.src.beta.settings import BetaSettings
from watch_beta_common import resolve_beta_db_path

DB = resolve_beta_db_path()
REFRESH_SECONDS = float(os.environ.get("BETA_WATCH_REFRESH_SECONDS", "30"))
RUN_ONCE = os.environ.get("BETA_WATCH_ONCE", "").strip().lower() in {"1", "true", "yes", "y"}
RECENT_SIGNAL_LIMIT = max(1, int(os.environ.get("BETA_WATCH_RECENT_LIMIT", "12")))
ACTIONABLE_SIGNAL_LIMIT = max(1, int(os.environ.get("BETA_WATCH_ACTIONABLE_LIMIT", "8")))
OUTLOOK_LIMIT = max(1, int(os.environ.get("BETA_WATCH_OUTLOOK_LIMIT", "10")))
SHOW_HELD_GUIDANCE_ONLY = os.environ.get("BETA_WATCH_HELD_GUIDANCE_ONLY", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "n",
}
LIVE_ANALOG_ENABLED = os.environ.get("BETA_WATCH_LIVE_ANALOG_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "n",
}
LIVE_ANALOG_NEIGHBORS = max(10, int(os.environ.get("BETA_WATCH_LIVE_ANALOG_NEIGHBORS", "60")))
LIVE_ANALOG_MIN_CANDIDATES = max(LIVE_ANALOG_NEIGHBORS, int(os.environ.get("BETA_WATCH_LIVE_ANALOG_MIN_CANDIDATES", "120")))
LIVE_ANALOG_CANDIDATE_LIMIT = max(
    LIVE_ANALOG_MIN_CANDIDATES,
    int(os.environ.get("BETA_WATCH_LIVE_ANALOG_CANDIDATE_LIMIT", "3000")),
)
LIVE_ANALOG_MIN_FEATURE_OVERLAP = max(
    4,
    int(os.environ.get("BETA_WATCH_LIVE_ANALOG_MIN_FEATURE_OVERLAP", "6")),
)
OUTLOOK_AUDIT_ENABLED = os.environ.get("BETA_WATCH_OUTLOOK_AUDIT_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "n",
}
OUTLOOK_AUDIT_LOOKBACK_DAYS = max(5, int(os.environ.get("BETA_WATCH_OUTLOOK_AUDIT_LOOKBACK_DAYS", "14")))
OUTLOOK_AUDIT_CANONICAL_MINUTES = max(5, int(os.environ.get("BETA_WATCH_OUTLOOK_AUDIT_CANONICAL_MINUTES", "15")))
OUTLOOK_AUDIT_MIN_ROWS = max(20, int(os.environ.get("BETA_WATCH_OUTLOOK_AUDIT_MIN_ROWS", "40")))
PROFIT_PROFILE_ENABLED = os.environ.get("BETA_WATCH_PROFIT_PROFILE_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "n",
}
PROFIT_PROFILE_LOOKBACK_DAYS = max(14, int(os.environ.get("BETA_WATCH_PROFIT_PROFILE_LOOKBACK_DAYS", "45")))
PROFIT_PROFILE_RECENT_DAYS = max(5, int(os.environ.get("BETA_WATCH_PROFIT_PROFILE_RECENT_DAYS", "10")))
PROFIT_PROFILE_MIN_ROWS = max(40, int(os.environ.get("BETA_WATCH_PROFIT_PROFILE_MIN_ROWS", "80")))
PROFIT_PROFILE_MIN_SYMBOLS = max(3, int(os.environ.get("BETA_WATCH_PROFIT_PROFILE_MIN_SYMBOLS", "5")))
PROFIT_PROFILE_MIN_MEAN_EDGE_PCT = max(
    0.05,
    float(os.environ.get("BETA_WATCH_PROFIT_PROFILE_MIN_MEAN_EDGE_PCT", "0.10")),
)
PROFIT_PROFILE_MIN_WIN_RATE = min(
    0.80,
    max(0.50, float(os.environ.get("BETA_WATCH_PROFIT_PROFILE_MIN_WIN_RATE", "0.52"))),
)
PROFIT_PROFILE_TOP_LIMIT = max(1, int(os.environ.get("BETA_WATCH_PROFIT_PROFILE_TOP_LIMIT", "4")))
PROFIT_PROFILE_MAX_SINGLE_SYMBOL_SHARE = min(
    1.0,
    max(0.10, float(os.environ.get("BETA_WATCH_PROFIT_PROFILE_MAX_SINGLE_SYMBOL_SHARE", "0.45"))),
)
PROFIT_PROFILE_MAX_TOP_TWO_SYMBOL_SHARE = min(
    1.0,
    max(0.10, float(os.environ.get("BETA_WATCH_PROFIT_PROFILE_MAX_TOP_TWO_SYMBOL_SHARE", "0.65"))),
)

_ECONOMIC_SIGNAL_COLUMNS = {
    "expected_edge_pct",
    "expected_hold_minutes",
    "historical_win_rate",
    "post_cost_edge_pct",
    "economic_annotation_sample_size",
    "economic_opportunity_status",
    "economic_non_actionable_reason",
}
_SIGNAL_ACTION_COLUMNS = {
    "recommended_action_side",
    "recommended_action_code",
    "recommended_action_label",
}

_OUTLOOK_COLUMNS = {
    "state_label",
    "expected_return_15m_pct",
    "expected_return_30m_pct",
    "post_cost_expected_return_15m_pct",
    "historical_win_rate",
    "confidence_score",
    "confidence_label",
    "confidence_reasons_json",
    "outlook_sample_size",
    "matched_instrument_count",
    "opportunity_status",
    "non_actionable_reason",
}
_OUTLOOK_ACTION_COLUMNS = {
    "recommended_action_side",
    "recommended_action_code",
    "recommended_action_label",
}
_SIM_TRADE_SOURCE_COLUMNS = {
    "simulation_source",
}
_POSITION_STATE_COLUMNS = {
    "symbol",
    "position_source",
    "position_status",
    "units",
    "updated_at",
}
_LIVE_ANALOG_OUTLOOK_COLUMNS = {
    "id",
    "instrument_id",
    "session_state",
    "state_code",
    "state_family_code",
    "event_trigger_code",
    "feature_snapshot_json",
}
_LIVE_ANALOG_LABEL_COLUMNS = {
    "observation_id",
    "future_15m_return_pct",
    "future_30m_return_pct",
    "evaluation_complete",
}
_LIVE_ANALOG_INSTRUMENT_COLUMNS = {
    "id",
    "market",
}
_LIVE_ANALOG_FEATURE_SCALES = {
    "return_since_open_pct": 0.50,
    "distance_from_vwap_pct": 0.20,
    "return_last_5m_pct": 0.25,
    "return_last_15m_pct": 0.40,
    "gap_from_prev_close_pct": 1.25,
    "intraday_range_pct": 1.00,
    "breakout_above_first_30m_high_pct": 0.75,
    "breakdown_below_first_30m_low_pct": 0.75,
    "reversal_from_high_15m_pct": 0.75,
    "reversal_from_low_15m_pct": 0.75,
    "session_progress_pct": 8.00,
    "cumulative_volume_vs_expected": 0.60,
    "volume_last_15m_vs_expected": 0.60,
}


def _clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _fmt_confidence(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _load_cost_drag_pct() -> float:
    try:
        settings = BetaSettings.load(DB)
    except Exception:
        return 0.12
    total_bps = (
        float(settings.intraday_execution_commission_bps)
        + float(settings.intraday_execution_spread_bps)
        + float(settings.intraday_execution_slippage_bps)
    )
    return total_bps / 100.0


COST_DRAG_PCT = _load_cost_drag_pct()


def _fmt_signed_pct(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _fmt_rate(value) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100.0:.0f}%"


def _fmt_signed_rate(value) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100.0:+.0f}%"


def _fmt_units(value) -> str:
    text = str(value or "").strip()
    return text or "-"


def _fmt_hold_window(raw_value, min_minutes, max_minutes) -> str:
    if raw_value:
        try:
            parsed = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list) and len(parsed) == 2:
            try:
                low = int(parsed[0])
                high = int(parsed[1])
                return f"{low}-{high}m"
            except (TypeError, ValueError):
                pass
    if min_minutes is not None and max_minutes is not None:
        return f"{int(min_minutes)}-{int(max_minutes)}m"
    return "-"


def _json_dict(raw_value) -> dict[str, object]:
    if not raw_value:
        return {}
    try:
        payload = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _short_text(value, *, max_len: int = 120) -> str:
    text = str(value or "-").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _action_summary(side, code, label) -> str:
    if not side and not code and not label:
        return "-"
    parts: list[str] = []
    if side:
        parts.append(str(side))
    if code:
        parts.append(str(code))
    head = "/".join(parts) if parts else "-"
    if label:
        return f"{head}  {label}"
    return head


def _reason_summary(raw_value) -> str:
    payload = _json_dict(raw_value)
    if not payload:
        return "-"
    sample_size = int(payload.get("sample_size") or 0)
    exact_state_sample_size = int(payload.get("exact_state_sample_size") or 0)
    matched_instruments = int(payload.get("matched_instruments") or 0)
    win_rate = payload.get("historical_win_rate")
    median_15 = payload.get("median_15m_return_pct")
    top_symbol_share = payload.get("top_symbol_share")
    exact_state_match = bool(payload.get("exact_state_match"))
    reason_codes = [str(code) for code in payload.get("reason_codes", []) if str(code).strip()]
    failed_reason_codes = [str(code) for code in payload.get("failed_reason_codes", []) if str(code).strip()]
    parts: list[str] = []
    if sample_size:
        parts.append(f"n={sample_size}")
    if exact_state_sample_size:
        parts.append(f"exact={exact_state_sample_size}")
    if matched_instruments:
        parts.append(f"names={matched_instruments}")
    parts.append("match=exact" if exact_state_match else "match=family")
    if win_rate is not None:
        parts.append(f"win={float(win_rate) * 100.0:.0f}%")
    if median_15 is not None:
        parts.append(f"median={float(median_15):+.2f}%")
    if top_symbol_share is not None:
        parts.append(f"top={float(top_symbol_share) * 100.0:.0f}%")
    if failed_reason_codes:
        parts.append(f"gates={','.join(failed_reason_codes[:2])}")
    elif reason_codes:
        parts.append(",".join(reason_codes[:3]))
    return "  ".join(parts) if parts else "-"


def _normalize_symbols(symbols: list[str] | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for symbol in symbols or []:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _symbol_filter(column_expr: str, symbols: list[str] | None) -> tuple[str, list[object]]:
    normalized = _normalize_symbols(symbols)
    if not normalized:
        return "", []
    placeholders = ", ".join("?" for _ in normalized)
    return f" AND UPPER({column_expr}) IN ({placeholders})", list(normalized)


def _feature_vector(raw_value) -> dict[str, float]:
    payload = _json_dict(raw_value)
    features: dict[str, float] = {}
    for key in _LIVE_ANALOG_FEATURE_SCALES:
        parsed = _safe_float(payload.get(key))
        if parsed is not None:
            features[key] = parsed
    return features


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


def _weighted_average(values: list[float], weights: list[float]) -> float | None:
    if not values or not weights or len(values) != len(weights):
        return None
    weight_total = sum(weights)
    if weight_total <= 0.0:
        return None
    return sum(value * weight for value, weight in zip(values, weights)) / weight_total


def _split_event_codes(raw_value) -> set[str]:
    text = str(raw_value or "").strip()
    if not text:
        return set()
    return {part.strip().upper() for part in text.split("|") if part.strip()}


def _outlook_row_key(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    row_id = row["id"] if "id" in keys else None
    if row_id:
        return str(row_id)
    return f"{str(row['symbol'] or '').strip().upper()}|{str(row['observed_at'] or '')}"


def _analog_distance(
    current_features: dict[str, float],
    candidate_features: dict[str, float],
    *,
    current_event_codes: set[str],
    candidate_event_codes: set[str],
    current_state_code: str,
    candidate_state_code: str,
    current_symbol: str,
    candidate_symbol: str,
) -> tuple[float, int] | None:
    distance_sq = 0.0
    overlap = 0
    for feature_name, scale in _LIVE_ANALOG_FEATURE_SCALES.items():
        current_value = current_features.get(feature_name)
        candidate_value = candidate_features.get(feature_name)
        if current_value is None or candidate_value is None:
            continue
        overlap += 1
        normalized_diff = (current_value - candidate_value) / max(scale, 0.0001)
        distance_sq += normalized_diff * normalized_diff
    if overlap < LIVE_ANALOG_MIN_FEATURE_OVERLAP:
        return None

    distance = math.sqrt(distance_sq / overlap)
    if current_state_code and current_state_code == candidate_state_code:
        distance *= 0.92
    if current_symbol and current_symbol == candidate_symbol:
        distance *= 0.88
    if current_event_codes:
        if current_event_codes & candidate_event_codes:
            distance *= 0.94
        elif candidate_event_codes:
            distance *= 1.06
    return distance, overlap


def _fetch_live_analog_candidates(
    conn: sqlite3.Connection,
    *,
    state_field: str,
    state_value: str,
    session_state: str,
    observed_at,
    instrument_market: str | None,
    instrument_market_enabled: bool,
) -> list[sqlite3.Row]:
    if state_field not in {"state_code", "state_family_code"}:
        return []
    if not state_value:
        return []

    instrument_join = ""
    market_clause = ""
    params: list[object] = [state_value, session_state, observed_at]
    if instrument_market_enabled:
        instrument_join = """
        LEFT JOIN beta_instruments i
          ON i.id = o.instrument_id
        """
    if instrument_market_enabled and instrument_market:
        market_clause = "AND UPPER(COALESCE(i.market, '')) = ?"
        params.append(str(instrument_market).strip().upper())

    return conn.execute(
        f"""
        SELECT
            o.symbol,
            o.observed_at,
            o.state_code,
            o.state_family_code,
            o.event_trigger_code,
            o.feature_snapshot_json,
            l.future_15m_return_pct,
            l.future_30m_return_pct
        FROM beta_intraday_feature_observations o
        JOIN beta_intraday_feature_label_values l
          ON l.observation_id = o.id
        {instrument_join}
        WHERE o.{state_field} = ?
          AND o.session_state = ?
          AND o.observed_at < ?
          {market_clause}
          AND l.evaluation_complete = 1
          AND l.future_15m_return_pct IS NOT NULL
        ORDER BY o.observed_at DESC
        LIMIT {int(LIVE_ANALOG_CANDIDATE_LIMIT)}
        """,
        params,
    ).fetchall()


def _build_live_analog_forecast(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    instrument_market_enabled: bool,
) -> dict[str, object] | None:
    current_features = _feature_vector(row["feature_snapshot_json"])
    current_symbol = str(row["symbol"] or "").strip().upper()
    current_state_code = str(row["state_code"] or "").strip().upper()
    current_state_family = str(row["state_family_code"] or "").strip().upper()
    current_session_state = str(row["session_state"] or "").strip().upper()
    current_market = str(row["instrument_market"] or "").strip().upper() or None
    current_event_codes = _split_event_codes(row["event_trigger_code"])
    observed_at = row["observed_at"]

    if not current_features or not current_session_state or not observed_at:
        return None

    basis = "state_code"
    candidate_rows = _fetch_live_analog_candidates(
        conn,
        state_field="state_code",
        state_value=current_state_code,
        session_state=current_session_state,
        observed_at=observed_at,
        instrument_market=current_market,
        instrument_market_enabled=instrument_market_enabled,
    )
    if len(candidate_rows) < LIVE_ANALOG_MIN_CANDIDATES:
        basis = "state_family"
        candidate_rows = _fetch_live_analog_candidates(
            conn,
            state_field="state_family_code",
            state_value=current_state_family,
            session_state=current_session_state,
            observed_at=observed_at,
            instrument_market=current_market,
            instrument_market_enabled=instrument_market_enabled,
        )
    if not candidate_rows:
        return None

    analog_rows: list[dict[str, object]] = []
    for candidate in candidate_rows:
        candidate_features = _feature_vector(candidate["feature_snapshot_json"])
        distance_result = _analog_distance(
            current_features,
            candidate_features,
            current_event_codes=current_event_codes,
            candidate_event_codes=_split_event_codes(candidate["event_trigger_code"]),
            current_state_code=current_state_code,
            candidate_state_code=str(candidate["state_code"] or "").strip().upper(),
            current_symbol=current_symbol,
            candidate_symbol=str(candidate["symbol"] or "").strip().upper(),
        )
        if distance_result is None:
            continue
        distance, overlap = distance_result
        analog_rows.append(
            {
                "symbol": str(candidate["symbol"] or "").strip().upper(),
                "distance": distance,
                "overlap": overlap,
                "future_15m_return_pct": float(candidate["future_15m_return_pct"]),
                "future_30m_return_pct": _safe_float(candidate["future_30m_return_pct"]),
            }
        )
    if not analog_rows:
        return None

    analog_rows.sort(key=lambda item: (float(item["distance"]), -int(item["overlap"])))
    neighbors = analog_rows[:LIVE_ANALOG_NEIGHBORS]
    weights = [1.0 / max(0.05, (float(row["distance"]) ** 2)) for row in neighbors]
    values_15 = [float(row["future_15m_return_pct"]) for row in neighbors]
    values_30 = [float(row["future_30m_return_pct"]) for row in neighbors if row["future_30m_return_pct"] is not None]
    weights_30 = [weight for row, weight in zip(neighbors, weights) if row["future_30m_return_pct"] is not None]
    expected_return_15m_pct = _weighted_average(values_15, weights)
    positive_win_rate = _weighted_average([1.0 if value > 0.0 else 0.0 for value in values_15], weights)
    directional_win_rate = positive_win_rate
    if expected_return_15m_pct is not None and positive_win_rate is not None and expected_return_15m_pct < 0.0:
        directional_win_rate = 1.0 - positive_win_rate

    unique_symbols = {str(row["symbol"]) for row in neighbors if str(row["symbol"]).strip()}
    same_symbol_neighbors = len([row for row in neighbors if str(row["symbol"]) == current_symbol])

    return {
        "basis": basis,
        "neighbor_count": len(neighbors),
        "candidate_count": len(analog_rows),
        "unique_symbol_count": len(unique_symbols),
        "same_symbol_neighbors": same_symbol_neighbors,
        "avg_distance": _weighted_average([float(row["distance"]) for row in neighbors], weights),
        "expected_return_15m_pct": expected_return_15m_pct,
        "expected_return_30m_pct": _weighted_average(values_30, weights_30) if values_30 else None,
        "historical_win_rate": directional_win_rate,
        "positive_win_rate": positive_win_rate,
        "median_15m_return_pct": _percentile(values_15, 0.50),
        "p25_15m_return_pct": _percentile(values_15, 0.25),
        "p75_15m_return_pct": _percentile(values_15, 0.75),
    }


def _latest_outlook_session_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute(
        """
        SELECT MAX(session_date) AS session_date
        FROM beta_intraday_feature_observations
        """
    ).fetchone()
    raw_value = row["session_date"] if row is not None else None
    if not raw_value:
        return None
    try:
        return date.fromisoformat(str(raw_value)[:10])
    except ValueError:
        return None


def _audit_cutoff_session_date(conn: sqlite3.Connection) -> str | None:
    latest_session_date = _latest_outlook_session_date(conn)
    if latest_session_date is None:
        return None
    cutoff = latest_session_date - timedelta(days=max(0, OUTLOOK_AUDIT_LOOKBACK_DAYS - 1))
    return cutoff.isoformat()


def _fetch_canonical_outlook_audit_rows(
    conn: sqlite3.Connection,
    *,
    cutoff_session_date: str,
    action_enabled: bool,
    symbols: list[str] | None = None,
    state_field: str | None = None,
    state_value: str | None = None,
    session_state: str = "REGULAR_OPEN",
    before_observed_at=None,
) -> list[sqlite3.Row]:
    symbol_filter, symbol_params = _symbol_filter("o.symbol", symbols)
    state_filter = ""
    state_params: list[object] = []
    if state_field is not None:
        if state_field not in {"state_code", "state_family_code"}:
            return []
        normalized_value = str(state_value or "").strip().upper()
        if not normalized_value:
            return []
        state_filter = f" AND UPPER(COALESCE(o.{state_field}, '')) = ?"
        state_params.append(normalized_value)
    before_filter = ""
    before_params: list[object] = []
    if before_observed_at:
        before_filter = " AND o.observed_at < ?"
        before_params.append(before_observed_at)

    action_select = """
            NULL AS recommended_action_side,
    """
    if action_enabled:
        action_select = """
            o.recommended_action_side,
    """

    params: list[object] = [cutoff_session_date, session_state]
    params.extend(symbol_params)
    params.extend(state_params)
    params.extend(before_params)
    return conn.execute(
        f"""
        WITH base AS (
            SELECT
                o.id,
                o.instrument_id,
                o.symbol,
                o.session_date,
                o.observed_at,
                o.session_state,
                o.priority_tier,
                o.state_code,
                o.state_family_code,
                o.state_label,
                {action_select}
                o.expected_return_15m_pct,
                o.post_cost_expected_return_15m_pct,
                o.historical_win_rate,
                o.confidence_label,
                o.confidence_score,
                l.future_15m_return_pct,
                l.future_30m_return_pct,
                CAST(
                    (
                        (CAST(strftime('%H', o.observed_at) AS INTEGER) * 60)
                        + CAST(strftime('%M', o.observed_at) AS INTEGER)
                    ) / {int(OUTLOOK_AUDIT_CANONICAL_MINUTES)} AS INTEGER
                ) AS audit_bucket
            FROM beta_intraday_feature_observations o
            JOIN beta_intraday_feature_label_values l
              ON l.observation_id = o.id
            WHERE o.session_date >= ?
              AND o.session_state = ?
              AND l.evaluation_complete = 1
              AND l.future_15m_return_pct IS NOT NULL
              AND o.expected_return_15m_pct IS NOT NULL
              {symbol_filter}
              {state_filter}
              {before_filter}
        ),
        canonical AS (
            SELECT
                instrument_id,
                session_date,
                audit_bucket,
                MAX(observed_at) AS observed_at
            FROM base
            GROUP BY instrument_id, session_date, audit_bucket
        )
        SELECT
            b.id,
            b.instrument_id,
            b.symbol,
            b.session_date,
            b.observed_at,
            b.session_state,
            b.priority_tier,
            b.state_code,
            b.state_family_code,
            b.state_label,
            b.recommended_action_side,
            b.expected_return_15m_pct,
            b.post_cost_expected_return_15m_pct,
            b.historical_win_rate,
            b.confidence_label,
            b.confidence_score,
            b.future_15m_return_pct,
            b.future_30m_return_pct
        FROM base b
        JOIN canonical c
          ON c.instrument_id = b.instrument_id
         AND c.session_date = b.session_date
         AND c.audit_bucket = b.audit_bucket
         AND c.observed_at = b.observed_at
        ORDER BY b.observed_at DESC
        """,
        params,
    ).fetchall()


def _summarize_outlook_audit_rows(
    rows: list[sqlite3.Row],
    *,
    same_symbol: str | None = None,
) -> dict[str, object] | None:
    if not rows:
        return None

    predicted_returns: list[float] = []
    realized_returns: list[float] = []
    predicted_win_rates: list[float] = []
    abs_errors: list[float] = []
    biases: list[float] = []
    directional_successes: list[float] = []
    action_signed_post_cost: list[float] = []
    symbols: set[str] = set()
    sessions: set[str] = set()
    confidence_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    normalized_same_symbol = str(same_symbol or "").strip().upper()
    same_symbol_count = 0

    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        session_date = str(row["session_date"] or "").strip()
        if symbol:
            symbols.add(symbol)
        if session_date:
            sessions.add(session_date)
        if normalized_same_symbol and symbol == normalized_same_symbol:
            same_symbol_count += 1

        confidence_label = str(row["confidence_label"] or "").strip().upper()
        if confidence_label in confidence_counts:
            confidence_counts[confidence_label] += 1

        predicted_return = _safe_float(row["expected_return_15m_pct"])
        realized_return = _safe_float(row["future_15m_return_pct"])
        predicted_win_rate = _safe_float(row["historical_win_rate"])
        if predicted_return is None or realized_return is None:
            continue

        predicted_returns.append(predicted_return)
        realized_returns.append(realized_return)
        if predicted_win_rate is not None:
            predicted_win_rates.append(predicted_win_rate)
        abs_errors.append(abs(predicted_return - realized_return))
        biases.append(realized_return - predicted_return)

        if predicted_return > 0.0:
            directional_successes.append(1.0 if realized_return > 0.0 else 0.0)
        elif predicted_return < 0.0:
            directional_successes.append(1.0 if realized_return < 0.0 else 0.0)

        action_side = str(row["recommended_action_side"] or "").strip().upper()
        if action_side == "BUY":
            action_signed_post_cost.append(realized_return - COST_DRAG_PCT)
        elif action_side == "SELL":
            action_signed_post_cost.append((-realized_return) - COST_DRAG_PCT)

    if not predicted_returns:
        return None

    avg_predicted_win_rate = _mean(predicted_win_rates)
    actual_directional_success_rate = _mean(directional_successes)
    calibration_gap = None
    if avg_predicted_win_rate is not None and actual_directional_success_rate is not None:
        calibration_gap = actual_directional_success_rate - avg_predicted_win_rate

    return {
        "row_count": len(predicted_returns),
        "symbol_count": len(symbols),
        "session_count": len(sessions),
        "same_symbol_count": same_symbol_count,
        "confidence_counts": confidence_counts,
        "avg_predicted_return_15m_pct": _mean(predicted_returns),
        "avg_realized_return_15m_pct": _mean(realized_returns),
        "avg_predicted_win_rate": avg_predicted_win_rate,
        "actual_directional_success_rate": actual_directional_success_rate,
        "calibration_gap": calibration_gap,
        "mae_15m_return_pct": _mean(abs_errors),
        "bias_15m_return_pct": _mean(biases),
        "action_row_count": len(action_signed_post_cost),
        "action_avg_signed_post_cost_15m_pct": _mean(action_signed_post_cost),
        "action_success_rate": _mean([1.0 if value > 0.0 else 0.0 for value in action_signed_post_cost]),
    }


def _build_held_outlook_audit(
    conn: sqlite3.Connection,
    *,
    cutoff_session_date: str,
    action_enabled: bool,
    held_symbols: list[str],
) -> dict[str, object] | None:
    audit_rows = _fetch_canonical_outlook_audit_rows(
        conn,
        cutoff_session_date=cutoff_session_date,
        action_enabled=action_enabled,
        symbols=held_symbols,
    )
    return _summarize_outlook_audit_rows(audit_rows)


def _build_state_outlook_audit(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    cutoff_session_date: str,
    action_enabled: bool,
) -> dict[str, object] | None:
    current_state_code = str(row["state_code"] or "").strip().upper()
    current_state_family = str(row["state_family_code"] or "").strip().upper()
    current_session_state = str(row["session_state"] or "").strip().upper()
    current_symbol = str(row["symbol"] or "").strip().upper()
    observed_at = row["observed_at"]
    if not current_session_state or not observed_at:
        return None

    basis = "state_code"
    audit_rows = _fetch_canonical_outlook_audit_rows(
        conn,
        cutoff_session_date=cutoff_session_date,
        action_enabled=action_enabled,
        state_field="state_code",
        state_value=current_state_code,
        session_state=current_session_state,
        before_observed_at=observed_at,
    )
    if len(audit_rows) < OUTLOOK_AUDIT_MIN_ROWS:
        basis = "state_family"
        audit_rows = _fetch_canonical_outlook_audit_rows(
            conn,
            cutoff_session_date=cutoff_session_date,
            action_enabled=action_enabled,
            state_field="state_family_code",
            state_value=current_state_family,
            session_state=current_session_state,
            before_observed_at=observed_at,
        )
    summary = _summarize_outlook_audit_rows(audit_rows, same_symbol=current_symbol)
    if summary is None:
        return None
    summary["basis"] = basis
    return summary


def _profit_cutoff_session_date(conn: sqlite3.Connection, *, lookback_days: int) -> str | None:
    latest_session_date = _latest_outlook_session_date(conn)
    if latest_session_date is None:
        return None
    cutoff = latest_session_date - timedelta(days=max(0, lookback_days - 1))
    return cutoff.isoformat()


def _classify_profit_candidate(
    *,
    mean_edge: float | None,
    median_edge: float | None,
    win_rate: float | None,
    row_count: int,
    symbol_count: int,
    top_symbol_share: float | None,
    top_two_symbol_share: float | None,
) -> str:
    if (
        row_count < PROFIT_PROFILE_MIN_ROWS
        or symbol_count < PROFIT_PROFILE_MIN_SYMBOLS
        or mean_edge is None
        or win_rate is None
    ):
        return "INSUFFICIENT"
    if mean_edge < PROFIT_PROFILE_MIN_MEAN_EDGE_PCT:
        return "NONE"
    if (
        top_symbol_share is not None
        and top_symbol_share > PROFIT_PROFILE_MAX_SINGLE_SYMBOL_SHARE
    ) or (
        top_two_symbol_share is not None
        and top_two_symbol_share > PROFIT_PROFILE_MAX_TOP_TWO_SYMBOL_SHARE
    ):
        return "CONCENTRATED"
    if (median_edge or 0.0) > 0.0 and win_rate >= PROFIT_PROFILE_MIN_WIN_RATE:
        return "STABLE"
    if (median_edge or 0.0) <= 0.0 or win_rate < 0.50:
        return "TAIL"
    return "SPECULATIVE"


def _profit_edge_summary(
    rows: list[sqlite3.Row],
    *,
    horizon_minutes: int,
    direction: str,
    same_symbol: str | None = None,
) -> dict[str, object] | None:
    if horizon_minutes not in {15, 30}:
        return None
    direction_key = str(direction or "").strip().upper()
    if direction_key not in {"LONG", "SHORT"}:
        return None

    returns: list[float] = []
    positive_returns: list[float] = []
    negative_returns: list[float] = []
    symbols: set[str] = set()
    sessions: set[str] = set()
    symbol_row_counts: dict[str, int] = {}
    same_symbol_count = 0
    normalized_same_symbol = str(same_symbol or "").strip().upper()

    for row in rows:
        raw_return = _safe_float(row[f"future_{horizon_minutes}m_return_pct"])
        if raw_return is None:
            continue
        edge_return = raw_return - COST_DRAG_PCT if direction_key == "LONG" else (-raw_return) - COST_DRAG_PCT
        returns.append(edge_return)

        symbol = str(row["symbol"] or "").strip().upper()
        session_date = str(row["session_date"] or "").strip()
        if symbol:
            symbols.add(symbol)
            symbol_row_counts[symbol] = symbol_row_counts.get(symbol, 0) + 1
        if session_date:
            sessions.add(session_date)
        if normalized_same_symbol and symbol == normalized_same_symbol:
            same_symbol_count += 1

        if edge_return > 0.0:
            positive_returns.append(edge_return)
        else:
            negative_returns.append(edge_return)

    if not returns:
        return None

    mean_edge = _mean(returns)
    median_edge = _percentile(returns, 0.50)
    win_rate = _mean([1.0 if value > 0.0 else 0.0 for value in returns])
    avg_win = _mean(positive_returns)
    avg_loss = abs(_mean(negative_returns) or 0.0) if negative_returns else None
    payoff_ratio = None
    if avg_win is not None and avg_loss is not None and avg_loss > 1e-9:
        payoff_ratio = avg_win / avg_loss

    row_count = len(returns)
    symbol_count = len(symbols)
    ordered_symbol_counts = sorted(symbol_row_counts.values(), reverse=True)
    top_symbol_share = (
        float(ordered_symbol_counts[0]) / float(row_count)
        if row_count > 0 and ordered_symbol_counts
        else None
    )
    top_two_symbol_share = (
        float(sum(ordered_symbol_counts[:2])) / float(row_count)
        if row_count > 0 and ordered_symbol_counts
        else None
    )
    status = _classify_profit_candidate(
        mean_edge=mean_edge,
        median_edge=median_edge,
        win_rate=win_rate,
        row_count=row_count,
        symbol_count=symbol_count,
        top_symbol_share=top_symbol_share,
        top_two_symbol_share=top_two_symbol_share,
    )

    score = float(mean_edge or 0.0) * 100.0
    score += max(0.0, float(median_edge or 0.0)) * 60.0
    score += max(0.0, float(win_rate or 0.0) - 0.50) * 40.0
    score += min(row_count, 500) / 50.0
    if status == "STABLE":
        score += 100.0
    elif status == "TAIL":
        score += 25.0
    elif status == "SPECULATIVE":
        score += 40.0

    return {
        "direction": direction_key,
        "horizon_minutes": horizon_minutes,
        "row_count": row_count,
        "symbol_count": symbol_count,
        "session_count": len(sessions),
        "same_symbol_count": same_symbol_count,
        "avg_post_cost_return_pct": mean_edge,
        "median_post_cost_return_pct": median_edge,
        "p25_post_cost_return_pct": _percentile(returns, 0.25),
        "p75_post_cost_return_pct": _percentile(returns, 0.75),
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "payoff_ratio": payoff_ratio,
        "top_symbol_share": top_symbol_share,
        "top_two_symbol_share": top_two_symbol_share,
        "status": status,
        "score": score,
    }


def _profit_profile_from_rows(
    rows: list[sqlite3.Row],
    *,
    same_symbol: str | None = None,
) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for horizon_minutes in (15, 30):
        for direction in ("LONG", "SHORT"):
            summary = _profit_edge_summary(
                rows,
                horizon_minutes=horizon_minutes,
                direction=direction,
                same_symbol=same_symbol,
            )
            if summary is not None:
                candidates.append(summary)
    if not candidates:
        return None

    status_rank = {"STABLE": 4, "SPECULATIVE": 3, "TAIL": 2, "NONE": 1, "INSUFFICIENT": 0}
    best = max(
        candidates,
        key=lambda item: (
            status_rank.get(str(item.get("status")), 0),
            float(item.get("score") or 0.0),
            float(item.get("avg_post_cost_return_pct") or 0.0),
        ),
    )
    return {
        "best": best,
        "candidates": candidates,
    }


def _profit_profile_note(profile: dict[str, object] | None) -> str:
    if not profile:
        return "no profit profile available"
    best = profile.get("best") if isinstance(profile.get("best"), dict) else {}
    status = str(best.get("status") or "INSUFFICIENT").upper()
    direction = str(best.get("direction") or "").upper()
    if status == "STABLE" and direction == "LONG":
        return "repeatable long edge after costs"
    if status == "STABLE" and direction == "SHORT":
        return "repeatable short edge after costs"
    if status == "CONCENTRATED":
        return "edge is too concentrated in one or two symbols to trust broadly"
    if status == "TAIL" and direction == "LONG":
        return "positive long average came from tail winners, not stable follow-through"
    if status == "TAIL" and direction == "SHORT":
        return "positive short average came from tail drops, not stable downside"
    if status == "SPECULATIVE":
        return "some post-cost edge exists, but reliability is not proven"
    if status == "INSUFFICIENT":
        return "not enough history for a reliable profit profile"
    return "no repeatable post-cost edge"


def _build_state_profit_profile(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    cutoff_session_date: str,
    recent_cutoff_session_date: str | None,
) -> dict[str, object] | None:
    current_state_code = str(row["state_code"] or "").strip().upper()
    current_state_family = str(row["state_family_code"] or "").strip().upper()
    current_session_state = str(row["session_state"] or "").strip().upper()
    current_symbol = str(row["symbol"] or "").strip().upper()
    observed_at = row["observed_at"]
    if not current_session_state or not observed_at:
        return None

    basis = "state_code"
    profile_rows = _fetch_canonical_outlook_audit_rows(
        conn,
        cutoff_session_date=cutoff_session_date,
        action_enabled=False,
        state_field="state_code",
        state_value=current_state_code,
        session_state=current_session_state,
        before_observed_at=observed_at,
    )
    if len(profile_rows) < PROFIT_PROFILE_MIN_ROWS:
        basis = "state_family"
        profile_rows = _fetch_canonical_outlook_audit_rows(
            conn,
            cutoff_session_date=cutoff_session_date,
            action_enabled=False,
            state_field="state_family_code",
            state_value=current_state_family,
            session_state=current_session_state,
            before_observed_at=observed_at,
        )
    profile = _profit_profile_from_rows(profile_rows, same_symbol=current_symbol)
    if profile is None:
        return None

    recent_profile = None
    if recent_cutoff_session_date:
        recent_rows = [
            item
            for item in profile_rows
            if str(item["session_date"] or "").strip() >= recent_cutoff_session_date
        ]
        recent_profile = _profit_profile_from_rows(recent_rows, same_symbol=current_symbol)

    profile["basis"] = basis
    profile["row_count"] = len(profile_rows)
    profile["note"] = _profit_profile_note(profile)
    profile["recent"] = recent_profile
    return profile


def _build_profit_pocket_leaders(
    conn: sqlite3.Connection,
    *,
    cutoff_session_date: str,
) -> dict[str, list[dict[str, object]]]:
    rows = _fetch_canonical_outlook_audit_rows(
        conn,
        cutoff_session_date=cutoff_session_date,
        action_enabled=False,
    )
    grouped: dict[str, list[sqlite3.Row]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        state_code = str(row["state_code"] or "").strip().upper()
        if not state_code:
            continue
        grouped.setdefault(state_code, []).append(row)
        labels.setdefault(state_code, str(row["state_label"] or state_code))

    long_leaders: list[dict[str, object]] = []
    short_leaders: list[dict[str, object]] = []
    for state_code, state_rows in grouped.items():
        profile = _profit_profile_from_rows(state_rows)
        if profile is None:
            continue
        best = profile.get("best") if isinstance(profile.get("best"), dict) else None
        if best is None or str(best.get("status") or "").upper() != "STABLE":
            continue
        leader = {
            **best,
            "state_code": state_code,
            "state_label": labels.get(state_code, state_code),
        }
        if str(best.get("direction") or "").upper() == "LONG":
            long_leaders.append(leader)
        elif str(best.get("direction") or "").upper() == "SHORT":
            short_leaders.append(leader)

    sort_key = lambda item: (
        float(item.get("avg_post_cost_return_pct") or 0.0),
        float(item.get("median_post_cost_return_pct") or 0.0),
        float(item.get("win_rate") or 0.0),
        int(item.get("row_count") or 0),
    )
    long_leaders.sort(key=sort_key, reverse=True)
    short_leaders.sort(key=sort_key, reverse=True)
    return {
        "long": long_leaders[:PROFIT_PROFILE_TOP_LIMIT],
        "short": short_leaders[:PROFIT_PROFILE_TOP_LIMIT],
    }


def _analog_read_label(analog: dict[str, object] | None) -> str:
    if not analog:
        return "unclear"
    expected_15 = _safe_float(analog.get("expected_return_15m_pct"))
    median_15 = _safe_float(analog.get("median_15m_return_pct"))
    p25_15 = _safe_float(analog.get("p25_15m_return_pct"))
    p75_15 = _safe_float(analog.get("p75_15m_return_pct"))
    anchor = median_15 if median_15 is not None else expected_15
    if anchor is None:
        return "unclear"
    if anchor <= -0.12 and p75_15 is not None and p75_15 < 0.0:
        return "downside continuation"
    if anchor >= 0.12 and p25_15 is not None and p25_15 > 0.0:
        return "upside continuation"
    if anchor <= -0.05:
        return "slight downside drift"
    if anchor >= 0.05:
        return "slight upside drift"
    if p25_15 is not None and p75_15 is not None and p25_15 < 0.0 < p75_15:
        return "two-way noise"
    return "flat drift"


def _feedback_summary(
    row: sqlite3.Row,
    *,
    analog: dict[str, object] | None,
    state_audit: dict[str, object] | None,
    profit_profile: dict[str, object] | None,
) -> dict[str, str]:
    action_side = str(row["recommended_action_side"] or "").strip().upper()
    opportunity_status = str(row["opportunity_status"] or "").strip().upper()
    non_actionable_reason = str(row["non_actionable_reason"] or "").strip().lower()
    analog_read = _analog_read_label(analog)
    analog_dir_win = (
        _safe_float(analog.get("historical_win_rate"))
        if analog is not None
        else _safe_float(row["historical_win_rate"])
    )
    audit_dir = _safe_float(state_audit.get("actual_directional_success_rate")) if state_audit else None
    action_hit = _safe_float(state_audit.get("action_success_rate")) if state_audit else None
    calibration_gap = _safe_float(state_audit.get("calibration_gap")) if state_audit else None
    best_profit = profit_profile.get("best") if isinstance(profit_profile, dict) and isinstance(profit_profile.get("best"), dict) else {}
    profit_status = str(best_profit.get("status") or "").upper()
    profit_direction = str(best_profit.get("direction") or "").upper()
    profit_note = str(profit_profile.get("note") or "").strip() if profit_profile else ""

    if profit_status == "TAIL":
        return {
            "verdict": "tail-driven / wait",
            "usefulness": "LOW",
            "read": analog_read,
            "because": profit_note or "historical edge came from tail moves rather than repeatable follow-through",
        }

    if profit_status == "CONCENTRATED":
        return {
            "verdict": "concentrated / wait",
            "usefulness": "LOW",
            "read": analog_read,
            "because": profit_note or "state edge is overly concentrated in one or two symbols",
        }

    if profit_status == "NONE":
        return {
            "verdict": "no edge / wait",
            "usefulness": "LOW",
            "read": analog_read,
            "because": profit_note or "this state has not shown repeatable post-cost edge",
        }

    if action_side in {"BUY", "SELL"} and (opportunity_status != "ACTIONABLE" or non_actionable_reason):
        verdict = "hold / ignore signal" if action_side == "BUY" else "hold / ignore sell"
        because = "state is not tradeable on current evidence"
        if action_hit is not None and action_hit < 0.45:
            because = f"historical {action_side.lower()} actions in this state have been weak after costs"
        elif audit_dir is not None and audit_dir < 0.50:
            because = "this state has not shown reliable directional follow-through"
        elif analog_dir_win is not None and analog_dir_win < 0.55:
            because = "the live analog read is not strong enough to support trading the signal"
        return {
            "verdict": verdict,
            "usefulness": "LOW",
            "read": analog_read,
            "because": because,
        }

    if (
        action_side in {"BUY", "SELL"}
        and opportunity_status == "ACTIONABLE"
        and profit_status == "STABLE"
        and profit_direction == action_side
        and analog_dir_win is not None
        and analog_dir_win >= 0.58
        and (audit_dir is None or audit_dir >= 0.55)
        and (calibration_gap is None or calibration_gap > -0.03)
    ):
        verdict = "buy bias usable" if action_side == "BUY" else "sell bias usable"
        return {
            "verdict": verdict,
            "usefulness": "HIGH",
            "read": analog_read,
            "because": "current signal, analog read, and trailing state audit are aligned",
        }

    if analog_read in {"slight downside drift", "downside continuation", "slight upside drift", "upside continuation"}:
        return {
            "verdict": "directional read only",
            "usefulness": "MEDIUM" if audit_dir is not None and audit_dir >= 0.52 and profit_status == "STABLE" else "LOW",
            "read": analog_read,
            "because": (
                "direction is visible, but the state has not earned stable profit-pocket status"
                if profit_status != "STABLE"
                else "direction is visible, but the historical audit is not strong enough for a trade signal"
            ),
        }

    return {
        "verdict": "noise / wait",
        "usefulness": "LOW",
        "read": analog_read,
        "because": "the current state is better treated as noise than as entry or exit timing",
    }


def _display_outlook_action_summary(row: sqlite3.Row, feedback: dict[str, str]) -> str:
    action_side = str(row["recommended_action_side"] or "").strip().upper()
    action_line = _action_summary(
        action_side,
        row["recommended_action_code"],
        row["recommended_action_label"],
    )
    if action_line == "-":
        return action_line

    final_verdict = str(feedback.get("verdict") or "").strip().lower()
    if action_side in {"BUY", "SELL"} and final_verdict not in {"buy bias usable", "sell bias usable"}:
        return _action_summary("HOLD", "NO_ACTION", "No trade action")
    return action_line


def _print_counts(title: str, rows: list[sqlite3.Row | tuple], *, label: str, value: str) -> None:
    print(title)
    print(f"  {label:<34} {value:>8}")
    print(f"  {'-' * min(len(label), 34):<34} {'-' * min(len(value), 8):>8}")
    if not rows:
        print(f"  {'-':<34} {0:>8}")
        print()
        return
    for row in rows:
        if isinstance(row, sqlite3.Row):
            key = row[0]
            val = row[1]
        else:
            key, val = row
        print(f"  {str(key or '-'): <34} {int(val or 0):>8}")
    print()


def _fetch_runtime_summary(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT supervisor_status, supervisor_pid, last_heartbeat_at, updated_at
        FROM beta_system_status
        WHERE id = 1
        """
    ).fetchone()


def _fetch_execution_totals(conn: sqlite3.Connection, economic_enabled: bool) -> sqlite3.Row:
    if economic_enabled:
        return conn.execute(
            """
            SELECT
                COUNT(*) AS signal_count,
                SUM(CASE WHEN economic_opportunity_status IS NOT NULL THEN 1 ELSE 0 END) AS reviewed_count,
                SUM(CASE WHEN expected_edge_pct IS NOT NULL THEN 1 ELSE 0 END) AS edge_qualified_count,
                SUM(CASE WHEN economic_opportunity_status = 'ACTIONABLE' THEN 1 ELSE 0 END) AS actionable_count,
                MAX(signal_time) AS latest_signal_time
            FROM beta_execution_signals
            """
        ).fetchone()
    return conn.execute(
        """
        SELECT
            COUNT(*) AS signal_count,
            0 AS reviewed_count,
            0 AS edge_qualified_count,
            0 AS actionable_count,
            MAX(signal_time) AS latest_signal_time
        FROM beta_execution_signals
        """
    ).fetchone()


def _fetch_signal_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT signal_type, COUNT(*) AS cnt
        FROM beta_execution_signals
        GROUP BY signal_type
        ORDER BY signal_type
        """
    ).fetchall()


def _fetch_economic_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT economic_opportunity_status, COUNT(*) AS cnt
        FROM beta_execution_signals
        GROUP BY economic_opportunity_status
        ORDER BY economic_opportunity_status
        """
    ).fetchall()


def _fetch_non_actionable_reasons(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT economic_non_actionable_reason, COUNT(*) AS cnt
        FROM beta_execution_signals
        WHERE economic_opportunity_status = 'NON_ACTIONABLE'
        GROUP BY economic_non_actionable_reason
        ORDER BY cnt DESC, economic_non_actionable_reason ASC
        LIMIT 8
        """
    ).fetchall()


def _fetch_held_positions(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT symbol, units, position_source, updated_at
        FROM beta_position_states
        WHERE position_status = 'OPEN'
          AND position_source IN ('DEMO', 'MANUAL')
        ORDER BY updated_at DESC
        """
    ).fetchall()
    seen: set[str] = set()
    held_positions: list[dict[str, object]] = []
    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        held_positions.append(
            {
                "symbol": symbol,
                "units": row["units"],
                "position_source": row["position_source"],
                "updated_at": row["updated_at"],
            }
        )
    return held_positions


def _fetch_outlook_counts(conn: sqlite3.Connection, *, symbols: list[str] | None = None) -> list[sqlite3.Row]:
    symbol_filter, params = _symbol_filter("o.symbol", symbols)
    return conn.execute(
        f"""
        WITH latest_session AS (
            SELECT MAX(session_date) AS session_date
            FROM beta_intraday_feature_observations
        ),
        latest AS (
            SELECT instrument_id, MAX(observed_at) AS observed_at
            FROM beta_intraday_feature_observations
            WHERE session_date = (SELECT session_date FROM latest_session)
            GROUP BY instrument_id
        )
        SELECT o.opportunity_status, COUNT(*) AS cnt
        FROM beta_intraday_feature_observations o
        JOIN latest
          ON latest.instrument_id = o.instrument_id
         AND latest.observed_at = o.observed_at
        WHERE 1 = 1
        {symbol_filter}
        GROUP BY o.opportunity_status
        ORDER BY o.opportunity_status
        """,
        params,
    ).fetchall()


def _fetch_recent_outlooks(
    conn: sqlite3.Connection,
    *,
    limit: int | None,
    action_enabled: bool,
    symbols: list[str] | None = None,
    instrument_market_enabled: bool = False,
) -> list[sqlite3.Row]:
    action_select = """
            NULL AS recommended_action_side,
            NULL AS recommended_action_code,
            NULL AS recommended_action_label,
    """
    if action_enabled:
        action_select = """
            o.recommended_action_side,
            o.recommended_action_code,
            o.recommended_action_label,
    """
    instrument_market_select = """
            NULL AS instrument_market,
    """
    instrument_market_join = ""
    if instrument_market_enabled:
        instrument_market_select = """
            i.market AS instrument_market,
    """
        instrument_market_join = """
        LEFT JOIN beta_instruments i
          ON i.id = o.instrument_id
    """
    symbol_filter, params = _symbol_filter("o.symbol", symbols)
    limit_clause = "" if limit is None else f"\n        LIMIT {int(limit)}"
    return conn.execute(
        f"""
        WITH latest_session AS (
            SELECT MAX(session_date) AS session_date
            FROM beta_intraday_feature_observations
        ),
        latest AS (
            SELECT instrument_id, MAX(observed_at) AS observed_at
            FROM beta_intraday_feature_observations
            WHERE session_date = (SELECT session_date FROM latest_session)
            GROUP BY instrument_id
        )
        SELECT
            o.id,
            o.symbol,
            o.priority_tier,
            o.observed_at,
            o.session_state,
            o.state_code,
            o.state_family_code,
            o.state_label,
            o.event_trigger_code,
            o.signal_type,
            o.feature_snapshot_json,
            {instrument_market_select}
            o.opportunity_status,
            o.expected_return_15m_pct,
            o.expected_return_30m_pct,
            o.post_cost_expected_return_15m_pct,
            o.historical_win_rate,
            o.confidence_score,
            o.confidence_label,
            o.confidence_reasons_json,
            o.outlook_sample_size,
            o.matched_instrument_count,
            o.non_actionable_reason,
            {action_select}
            o.rationale_text
        FROM beta_intraday_feature_observations o
        JOIN latest
          ON latest.instrument_id = o.instrument_id
         AND latest.observed_at = o.observed_at
        {instrument_market_join}
        WHERE 1 = 1
        {symbol_filter}
        ORDER BY
            CASE o.opportunity_status
                WHEN 'ACTIONABLE' THEN 0
                WHEN 'INFORMATIONAL' THEN 1
                ELSE 2
            END,
            ABS(COALESCE(o.post_cost_expected_return_15m_pct, o.expected_return_15m_pct, 0)) DESC,
            o.observed_at DESC
        {limit_clause}
        """,
        params,
    ).fetchall()


def _fetch_sim_trade_totals(conn: sqlite3.Connection, *, source_enabled: bool) -> sqlite3.Row:
    source_select = """
            0 AS live_forward_count,
            0 AS historical_backfill_count,
    """
    if source_enabled:
        source_select = """
            SUM(CASE WHEN simulation_source = 'LIVE_FORWARD' THEN 1 ELSE 0 END) AS live_forward_count,
            SUM(CASE WHEN simulation_source = 'HISTORICAL_BACKFILL' THEN 1 ELSE 0 END) AS historical_backfill_count,
    """
    return conn.execute(
        f"""
        SELECT
            COUNT(*) AS trade_count,
            SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_count,
            SUM(CASE WHEN status = 'CLOSED' AND realized_post_cost_return_pct > 0 THEN 1 ELSE 0 END) AS win_count,
            AVG(CASE WHEN status = 'CLOSED' THEN realized_post_cost_return_pct END) AS avg_post_cost_return_pct,
            AVG(CASE WHEN status = 'CLOSED' THEN hold_minutes END) AS avg_hold_minutes,
            {source_select}
            MAX(COALESCE(exit_observed_at, latest_observed_at, entry_observed_at)) AS latest_activity_at
        FROM beta_intraday_simulated_trades
        """
    ).fetchone()


def _fetch_sim_trade_exit_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT COALESCE(exit_reason_code, status) AS reason_code, COUNT(*) AS cnt
        FROM beta_intraday_simulated_trades
        GROUP BY COALESCE(exit_reason_code, status)
        ORDER BY cnt DESC, reason_code ASC
        LIMIT 8
        """
    ).fetchall()


def _fetch_open_sim_trades(conn: sqlite3.Connection, *, limit: int, source_enabled: bool) -> list[sqlite3.Row]:
    source_select = """
            NULL AS simulation_source,
    """
    if source_enabled:
        source_select = """
            simulation_source,
    """
    return conn.execute(
        f"""
        SELECT
            symbol,
            focus_bucket,
            direction,
            {source_select}
            created_at,
            entry_observed_at,
            state_label,
            entry_action_label,
            current_action_label,
            expected_return_15m_pct,
            post_cost_expected_return_15m_pct,
            confidence_score,
            target_return_pct,
            stop_loss_pct,
            latest_return_pct,
            hold_minutes
        FROM beta_intraday_simulated_trades
        WHERE status = 'OPEN'
        ORDER BY entry_observed_at DESC
        LIMIT {int(limit)}
        """
    ).fetchall()


def _fetch_closed_sim_trades(
    conn: sqlite3.Connection,
    *,
    limit: int,
    source_enabled: bool,
    simulation_source: str | None = None,
) -> list[sqlite3.Row]:
    source_select = """
            NULL AS simulation_source,
    """
    where_extra = ""
    params: list[object] = []
    if source_enabled:
        source_select = """
            simulation_source,
    """
        if simulation_source:
            where_extra = " AND simulation_source = ?"
            params.append(simulation_source)
    return conn.execute(
        f"""
        SELECT
            symbol,
            focus_bucket,
            direction,
            {source_select}
            created_at,
            entry_observed_at,
            exit_observed_at,
            state_label,
            realized_return_pct,
            realized_post_cost_return_pct,
            hold_minutes,
            exit_reason_code,
            exit_reason_text
        FROM beta_intraday_simulated_trades
        WHERE status = 'CLOSED'
        {where_extra}
        ORDER BY created_at DESC, COALESCE(exit_observed_at, latest_observed_at, entry_observed_at) DESC
        LIMIT {int(limit)}
        """,
        params,
    ).fetchall()


def _recent_signals_query(*, economic_enabled: bool, action_enabled: bool, actionable_only: bool, limit: int) -> str:
    economic_select = ""
    if economic_enabled:
        economic_select = """
            , s.expected_edge_pct
            , s.expected_hold_minutes
            , s.expected_hold_min_minutes
            , s.expected_hold_max_minutes
            , s.historical_win_rate
            , s.post_cost_edge_pct
            , s.economic_annotation_sample_size
            , s.economic_opportunity_status
            , s.economic_non_actionable_reason
        """
    action_select = """
            , NULL AS recommended_action_side
            , NULL AS recommended_action_code
            , NULL AS recommended_action_label
    """
    if action_enabled:
        action_select = """
            , s.recommended_action_side
            , s.recommended_action_code
            , s.recommended_action_label
        """
    where_clause = ""
    if actionable_only and economic_enabled:
        where_clause = "WHERE s.economic_opportunity_status = 'ACTIONABLE'"
    return f"""
        SELECT
            s.symbol,
            s.signal_type,
            s.session_date,
            s.signal_time,
            s.session_state,
            s.confidence_score,
            s.rationale_text,
            s.event_trigger_code,
            h.hypothesis_code,
            h.name
            {economic_select}
            {action_select}
        FROM beta_execution_signals s
        LEFT JOIN beta_execution_hypothesis_definitions h
            ON s.execution_hypothesis_definition_id = h.id
        {where_clause}
        ORDER BY s.signal_time DESC, s.created_at DESC
        LIMIT {int(limit)}
    """


def _print_signal_blocks(
    title: str,
    rows: list[sqlite3.Row],
    *,
    economic_enabled: bool,
) -> None:
    print(title)
    if not rows:
        print("  none")
        print()
        return

    for row in rows:
        when_text = str(row["signal_time"] or "-")[:19]
        base_line = (
            f"  {when_text}  {str(row['symbol'] or '-'): <8}  "
            f"{str(row['signal_type'] or '-'): <28}  "
            f"{str(row['session_state'] or '-'): <12}  conf={_fmt_confidence(row['confidence_score'])}"
        )
        if economic_enabled:
            base_line += f"  econ={str(row['economic_opportunity_status'] or '-')}"
        print(base_line)

        hypothesis_text = row["hypothesis_code"] or row["name"] or "-"
        trigger_text = row["event_trigger_code"] or "-"
        detail_line = f"    hypothesis={hypothesis_text}  trigger={trigger_text}"
        if economic_enabled:
            detail_line += (
                f"  edge={_fmt_signed_pct(row['expected_edge_pct'])}"
                f"  post={_fmt_signed_pct(row['post_cost_edge_pct'])}"
                f"  win={_fmt_rate(row['historical_win_rate'])}"
                f"  hold={_fmt_hold_window(row['expected_hold_minutes'], row['expected_hold_min_minutes'], row['expected_hold_max_minutes'])}"
                f"  n={int(row['economic_annotation_sample_size'] or 0)}"
            )
        print(detail_line)
        action_line = _display_outlook_action_summary(row, feedback)
        if action_line != "-":
            print(f"    action={action_line}")

        if economic_enabled and row["economic_non_actionable_reason"]:
            print(f"    reason={row['economic_non_actionable_reason']}")
        if row["rationale_text"]:
            print(f"    rationale={_short_text(row['rationale_text'])}")
        print()


def _print_outlook_audit_summary(title: str, summary: dict[str, object] | None) -> None:
    print(title)
    if summary is None:
        print("  none")
        print()
        return

    confidence_counts = summary.get("confidence_counts") or {}
    print(
        f"  trailing={OUTLOOK_AUDIT_LOOKBACK_DAYS}d"
        f"  canonical={OUTLOOK_AUDIT_CANONICAL_MINUTES}m"
        f"  cost_drag={_fmt_signed_pct(COST_DRAG_PCT)}"
    )
    print(
        f"  audited={int(summary.get('row_count') or 0)}"
        f"  sessions={int(summary.get('session_count') or 0)}"
        f"  names={int(summary.get('symbol_count') or 0)}"
        f"  high={int(confidence_counts.get('HIGH') or 0)}"
        f"  medium={int(confidence_counts.get('MEDIUM') or 0)}"
        f"  low={int(confidence_counts.get('LOW') or 0)}"
    )
    print(
        f"  pred_win={_fmt_rate(summary.get('avg_predicted_win_rate'))}"
        f"  actual_dir={_fmt_rate(summary.get('actual_directional_success_rate'))}"
        f"  gap={_fmt_signed_rate(summary.get('calibration_gap'))}"
        f"  mae15={_fmt_signed_pct(summary.get('mae_15m_return_pct'))}"
        f"  bias15={_fmt_signed_pct(summary.get('bias_15m_return_pct'))}"
        f"  realized15={_fmt_signed_pct(summary.get('avg_realized_return_15m_pct'))}"
    )
    if int(summary.get("action_row_count") or 0) > 0:
        print(
            f"  buy_sell_rows={int(summary.get('action_row_count') or 0)}"
            f"  action_hit={_fmt_rate(summary.get('action_success_rate'))}"
            f"  action_post15={_fmt_signed_pct(summary.get('action_avg_signed_post_cost_15m_pct'))}"
        )
    print()


def _print_profit_pocket_leaders(title: str, leaders: dict[str, list[dict[str, object]]] | None) -> None:
    print(title)
    if not leaders:
        print("  none")
        print()
        return

    long_rows = leaders.get("long") or []
    short_rows = leaders.get("short") or []
    if not long_rows and not short_rows:
        print("  none")
        print()
        return

    if long_rows:
        print("  stable long pockets:")
        for row in long_rows:
            print(
                f"    {str(row.get('state_label') or row.get('state_code') or '-'): <34}"
                f" {int(row.get('horizon_minutes') or 0):>2}m"
                f"  post={_fmt_signed_pct(row.get('avg_post_cost_return_pct'))}"
                f"  median={_fmt_signed_pct(row.get('median_post_cost_return_pct'))}"
                f"  win={_fmt_rate(row.get('win_rate'))}"
                f"  n={int(row.get('row_count') or 0)}"
                f"  names={int(row.get('symbol_count') or 0)}"
                f"  top2={_fmt_rate(row.get('top_two_symbol_share'))}"
            )
    if short_rows:
        print("  stable short pockets:")
        for row in short_rows:
            print(
                f"    {str(row.get('state_label') or row.get('state_code') or '-'): <34}"
                f" {int(row.get('horizon_minutes') or 0):>2}m"
                f"  post={_fmt_signed_pct(row.get('avg_post_cost_return_pct'))}"
                f"  median={_fmt_signed_pct(row.get('median_post_cost_return_pct'))}"
                f"  win={_fmt_rate(row.get('win_rate'))}"
                f"  n={int(row.get('row_count') or 0)}"
                f"  names={int(row.get('symbol_count') or 0)}"
                f"  top2={_fmt_rate(row.get('top_two_symbol_share'))}"
            )
    print()


def _print_outlook_blocks(
    title: str,
    rows: list[sqlite3.Row],
    *,
    analog_forecasts: dict[str, dict[str, object]] | None = None,
    state_audits: dict[str, dict[str, object]] | None = None,
    profit_profiles: dict[str, dict[str, object]] | None = None,
) -> None:
    print(title)
    if not rows:
        print("  none")
        print()
        return

    for row in rows:
        analog = analog_forecasts.get(_outlook_row_key(row)) if analog_forecasts else None
        state_audit = state_audits.get(_outlook_row_key(row)) if state_audits else None
        profit_profile = profit_profiles.get(_outlook_row_key(row)) if profit_profiles else None
        feedback = _feedback_summary(
            row,
            analog=analog,
            state_audit=state_audit,
            profit_profile=profit_profile,
        )
        reason_payload = _json_dict(row["confidence_reasons_json"])
        median_15 = reason_payload.get("median_15m_return_pct")
        top_symbol_share = reason_payload.get("top_symbol_share")
        when_text = str(row["observed_at"] or "-")[:19]
        print(
            f"  {when_text}  {str(row['symbol'] or '-'): <8}  "
            f"{str(row['priority_tier'] or '-'): <12}  "
            f"{str(row['opportunity_status'] or '-'): <22}  "
            f"conf={str(row['confidence_label'] or '-')}({ _fmt_confidence(row['confidence_score']) })"
        )
        print(
            f"    state={str(row['state_label'] or '-')}"
            f"  signal={str(row['signal_type'] or '-')}"
        )
        action_line = _action_summary(
            row["recommended_action_side"],
            row["recommended_action_code"],
            row["recommended_action_label"],
        )
        if action_line != "-":
            print(f"    action={action_line}")
        print(
            f"    feedback={feedback['verdict']}"
            f"  usefulness={feedback['usefulness']}"
            f"  read={feedback['read']}"
        )
        print(f"    because={feedback['because']}")
        if profit_profile:
            best_profit = profit_profile.get("best") if isinstance(profit_profile.get("best"), dict) else {}
            recent_profit = profit_profile.get("recent") if isinstance(profit_profile.get("recent"), dict) else None
            recent_best = recent_profit.get("best") if isinstance(recent_profit, dict) and isinstance(recent_profit.get("best"), dict) else {}
            print(
                f"    profit={str(best_profit.get('status') or '-')} {str(best_profit.get('direction') or '-')} "
                f"{int(best_profit.get('horizon_minutes') or 0)}m"
                f"  post={_fmt_signed_pct(best_profit.get('avg_post_cost_return_pct'))}"
                f"  median={_fmt_signed_pct(best_profit.get('median_post_cost_return_pct'))}"
                f"  dir_win={_fmt_rate(best_profit.get('win_rate'))}"
                f"  payoff={_fmt_confidence(best_profit.get('payoff_ratio'))}"
            )
            print(
                f"    profit_basis={str(profit_profile.get('basis') or '-')}"
                f"  n={int(best_profit.get('row_count') or 0)}"
                f"  sessions={int(best_profit.get('session_count') or 0)}"
                f"  names={int(best_profit.get('symbol_count') or 0)}"
                f"  same_symbol={int(best_profit.get('same_symbol_count') or 0)}"
                f"  top1={_fmt_rate(best_profit.get('top_symbol_share'))}"
                f"  top2={_fmt_rate(best_profit.get('top_two_symbol_share'))}"
            )
            if recent_best:
                print(
                    f"    recent_profit={str(recent_best.get('status') or '-')} {str(recent_best.get('direction') or '-')} "
                    f"{int(recent_best.get('horizon_minutes') or 0)}m"
                    f"  recent_post={_fmt_signed_pct(recent_best.get('avg_post_cost_return_pct'))}"
                    f"  recent_win={_fmt_rate(recent_best.get('win_rate'))}"
                    f"  recent_n={int(recent_best.get('row_count') or 0)}"
                )
            print(f"    profit_note={profit_profile.get('note') or '-'}")
        if analog:
            print(
                f"    analog_next15={_fmt_signed_pct(analog.get('expected_return_15m_pct'))}"
                f"  analog30={_fmt_signed_pct(analog.get('expected_return_30m_pct'))}"
                f"  analog_dir_win={_fmt_rate(analog.get('historical_win_rate'))}"
                f"  analog_median15={_fmt_signed_pct(analog.get('median_15m_return_pct'))}"
                f"  band15=[{_fmt_signed_pct(analog.get('p25_15m_return_pct'))}, { _fmt_signed_pct(analog.get('p75_15m_return_pct')) }]"
            )
            print(
                f"    analog_basis={str(analog.get('basis') or '-')}"
                f"  k={int(analog.get('neighbor_count') or 0)}"
                f"  pool={int(analog.get('candidate_count') or 0)}"
                f"  names={int(analog.get('unique_symbol_count') or 0)}"
                f"  same_symbol={int(analog.get('same_symbol_neighbors') or 0)}"
                f"  dist={_fmt_confidence(analog.get('avg_distance'))}"
            )
            print(
                f"    bucket15={_fmt_signed_pct(row['expected_return_15m_pct'])}"
                f"  bucket30={_fmt_signed_pct(row['expected_return_30m_pct'])}"
                f"  bucket_post15={_fmt_signed_pct(row['post_cost_expected_return_15m_pct'])}"
                f"  bucket_win={_fmt_rate(row['historical_win_rate'])}"
                f"  bucket_median15={_fmt_signed_pct(median_15)}"
                f"  n={int(row['outlook_sample_size'] or 0)}"
                f"  names={int(row['matched_instrument_count'] or 0)}"
                f"  top={_fmt_rate(top_symbol_share)}"
            )
        else:
            print(
                f"    next15={_fmt_signed_pct(row['expected_return_15m_pct'])}"
                f"  next30={_fmt_signed_pct(row['expected_return_30m_pct'])}"
                f"  post15={_fmt_signed_pct(row['post_cost_expected_return_15m_pct'])}"
                f"  win={_fmt_rate(row['historical_win_rate'])}"
                f"  median15={_fmt_signed_pct(median_15)}"
                f"  n={int(row['outlook_sample_size'] or 0)}"
                f"  names={int(row['matched_instrument_count'] or 0)}"
                f"  top={_fmt_rate(top_symbol_share)}"
            )
        if state_audit:
            print(
                f"    audit_pred_win={_fmt_rate(state_audit.get('avg_predicted_win_rate'))}"
                f"  audit_actual_dir={_fmt_rate(state_audit.get('actual_directional_success_rate'))}"
                f"  audit_gap={_fmt_signed_rate(state_audit.get('calibration_gap'))}"
                f"  audit_mae15={_fmt_signed_pct(state_audit.get('mae_15m_return_pct'))}"
                f"  audit_bias15={_fmt_signed_pct(state_audit.get('bias_15m_return_pct'))}"
                f"  audit_realized15={_fmt_signed_pct(state_audit.get('avg_realized_return_15m_pct'))}"
            )
            print(
                f"    audit_basis={str(state_audit.get('basis') or '-')}"
                f"  audited={int(state_audit.get('row_count') or 0)}"
                f"  sessions={int(state_audit.get('session_count') or 0)}"
                f"  names={int(state_audit.get('symbol_count') or 0)}"
                f"  same_symbol={int(state_audit.get('same_symbol_count') or 0)}"
                f"  action_hit={_fmt_rate(state_audit.get('action_success_rate'))}"
                f"  action_post15={_fmt_signed_pct(state_audit.get('action_avg_signed_post_cost_15m_pct'))}"
            )
        if row["non_actionable_reason"]:
            print(f"    not_tradeable={row['non_actionable_reason']}")
        print(f"    why={_reason_summary(row['confidence_reasons_json'])}")
        if row["rationale_text"]:
            print(f"    rationale={_short_text(row['rationale_text'])}")
        print()


def _print_held_positions(rows: list[dict[str, object]]) -> None:
    print("=== Held Shares ===")
    if not rows:
        print("  none")
        print()
        return

    for row in rows:
        updated_at = str(row.get("updated_at") or "-")[:19]
        print(
            f"  {str(row.get('symbol') or '-'): <8}"
            f"  units={_fmt_units(row.get('units')):<14}"
            f"  source={str(row.get('position_source') or '-'): <8}"
            f"  updated={updated_at}"
        )
    print()


def _print_sim_trade_blocks(title: str, rows: list[sqlite3.Row], *, closed: bool) -> None:
    print(title)
    if not rows:
        print("  none")
        print()
        return

    for row in rows:
        start_text = str(row["entry_observed_at"] or "-")[:19]
        source_text = str(row["simulation_source"] or "LIVE_FORWARD")
        recorded_text = str(row["created_at"] or "-")[:19]
        if closed:
            end_text = str(row["exit_observed_at"] or "-")[:19]
            print(
                f"  {end_text}  {str(row['symbol'] or '-'): <8}  "
                f"{str(row['focus_bucket'] or '-'): <14}  "
                f"{str(row['direction'] or '-'): <5}  "
                f"post={_fmt_signed_pct(row['realized_post_cost_return_pct'])}"
                f"  src={source_text}"
            )
            print(
                f"    opened={start_text}  state={str(row['state_label'] or '-')}"
                f"  raw={_fmt_signed_pct(row['realized_return_pct'])}"
                f"  hold={int(row['hold_minutes'] or 0)}m"
                f"  exit={str(row['exit_reason_code'] or '-')}"
                f"  recorded={recorded_text}"
            )
            if row["exit_reason_text"]:
                print(f"    note={_short_text(row['exit_reason_text'])}")
        else:
            print(
                f"  {start_text}  {str(row['symbol'] or '-'): <8}  "
                f"{str(row['focus_bucket'] or '-'): <14}  "
                f"{str(row['direction'] or '-'): <5}  "
                f"live={_fmt_signed_pct(row['latest_return_pct'])}"
                f"  src={source_text}"
            )
            print(
                f"    state={str(row['state_label'] or '-')}"
                f"  entry={str(row['entry_action_label'] or '-')}"
                f"  current={str(row['current_action_label'] or '-')}"
                f"  recorded={recorded_text}"
            )
            print(
                f"    exp15={_fmt_signed_pct(row['expected_return_15m_pct'])}"
                f"  post15={_fmt_signed_pct(row['post_cost_expected_return_15m_pct'])}"
                f"  conf={_fmt_confidence(row['confidence_score'])}"
                f"  target={_fmt_signed_pct(row['target_return_pct'])}"
                f"  stop={_fmt_signed_pct(-float(row['stop_loss_pct'])) if row['stop_loss_pct'] is not None else '-'}"
                f"  hold={int(row['hold_minutes'] or 0)}m"
            )
        print()


while True:
    if not RUN_ONCE:
        _clear_screen()
    try:
        with _connect(DB) as conn:
            signal_columns = _table_columns(conn, "beta_execution_signals")
            economic_enabled = _ECONOMIC_SIGNAL_COLUMNS.issubset(signal_columns)
            signal_action_enabled = _SIGNAL_ACTION_COLUMNS.issubset(signal_columns)
            outlook_table_enabled = _table_exists(conn, "beta_intraday_feature_observations")
            outlook_columns = _table_columns(conn, "beta_intraday_feature_observations") if outlook_table_enabled else set()
            outlook_enabled = outlook_table_enabled and _OUTLOOK_COLUMNS.issubset(outlook_columns)
            outlook_action_enabled = outlook_table_enabled and _OUTLOOK_ACTION_COLUMNS.issubset(outlook_columns)
            live_analog_label_table_enabled = _table_exists(conn, "beta_intraday_feature_label_values")
            live_analog_label_columns = (
                _table_columns(conn, "beta_intraday_feature_label_values")
                if live_analog_label_table_enabled
                else set()
            )
            instrument_table_enabled = _table_exists(conn, "beta_instruments")
            instrument_columns = _table_columns(conn, "beta_instruments") if instrument_table_enabled else set()
            instrument_market_enabled = instrument_table_enabled and _LIVE_ANALOG_INSTRUMENT_COLUMNS.issubset(
                instrument_columns
            )
            live_analog_enabled = (
                LIVE_ANALOG_ENABLED
                and outlook_enabled
                and _LIVE_ANALOG_OUTLOOK_COLUMNS.issubset(outlook_columns)
                and live_analog_label_table_enabled
                and _LIVE_ANALOG_LABEL_COLUMNS.issubset(live_analog_label_columns)
            )
            position_state_enabled = _table_exists(conn, "beta_position_states") and _POSITION_STATE_COLUMNS.issubset(
                _table_columns(conn, "beta_position_states")
            )
            held_positions = _fetch_held_positions(conn) if position_state_enabled else []
            held_symbols = [str(row["symbol"]) for row in held_positions]
            sim_trade_enabled = _table_exists(conn, "beta_intraday_simulated_trades")
            sim_trade_source_enabled = (
                sim_trade_enabled
                and _SIM_TRADE_SOURCE_COLUMNS.issubset(_table_columns(conn, "beta_intraday_simulated_trades"))
            )
            runtime = _fetch_runtime_summary(conn)

            print(f"DB: {DB}")
            print(f"Refresh: {REFRESH_SECONDS:.1f}s")
            print()

            print("=== Runtime ===")
            if runtime is None:
                print("  supervisor: unavailable")
            else:
                print(
                    "  supervisor:"
                    f" {runtime['supervisor_status'] or '-'}"
                    f"  pid={runtime['supervisor_pid'] or '-'}"
                    f"  heartbeat={runtime['last_heartbeat_at'] or '-'}"
                )
            if SHOW_HELD_GUIDANCE_ONLY:
                held_outlook_counts = (
                    _fetch_outlook_counts(conn, symbols=held_symbols)
                    if outlook_enabled and held_symbols
                    else []
                )
                recent_outlooks = (
                    _fetch_recent_outlooks(
                        conn,
                        limit=None,
                        action_enabled=outlook_action_enabled,
                        symbols=held_symbols,
                        instrument_market_enabled=instrument_market_enabled,
                    )
                    if outlook_enabled and held_symbols
                    else []
                )
                live_analog_forecasts: dict[str, dict[str, object]] = {}
                held_outlook_audit = None
                state_outlook_audits: dict[str, dict[str, object]] = {}
                state_profit_profiles: dict[str, dict[str, object]] = {}
                audit_cutoff_session_date = _audit_cutoff_session_date(conn) if OUTLOOK_AUDIT_ENABLED else None
                profit_cutoff_session_date = (
                    _profit_cutoff_session_date(conn, lookback_days=PROFIT_PROFILE_LOOKBACK_DAYS)
                    if PROFIT_PROFILE_ENABLED
                    else None
                )
                recent_profit_cutoff_session_date = (
                    _profit_cutoff_session_date(conn, lookback_days=PROFIT_PROFILE_RECENT_DAYS)
                    if PROFIT_PROFILE_ENABLED
                    else None
                )
                profit_pocket_leaders = None
                if live_analog_enabled and recent_outlooks:
                    for row in recent_outlooks:
                        forecast = _build_live_analog_forecast(
                            conn,
                            row,
                            instrument_market_enabled=instrument_market_enabled,
                        )
                        if forecast is not None:
                            live_analog_forecasts[_outlook_row_key(row)] = forecast
                if audit_cutoff_session_date and recent_outlooks:
                    held_outlook_audit = _build_held_outlook_audit(
                        conn,
                        cutoff_session_date=audit_cutoff_session_date,
                        action_enabled=outlook_action_enabled,
                        held_symbols=held_symbols,
                    )
                    for row in recent_outlooks:
                        audit = _build_state_outlook_audit(
                            conn,
                            row,
                            cutoff_session_date=audit_cutoff_session_date,
                            action_enabled=outlook_action_enabled,
                        )
                        if audit is not None:
                            state_outlook_audits[_outlook_row_key(row)] = audit
                if profit_cutoff_session_date and recent_outlooks:
                    profit_pocket_leaders = _build_profit_pocket_leaders(
                        conn,
                        cutoff_session_date=profit_cutoff_session_date,
                    )
                    for row in recent_outlooks:
                        profile = _build_state_profit_profile(
                            conn,
                            row,
                            cutoff_session_date=profit_cutoff_session_date,
                            recent_cutoff_session_date=recent_profit_cutoff_session_date,
                        )
                        if profile is not None:
                            state_profit_profiles[_outlook_row_key(row)] = profile
                if position_state_enabled:
                    print(
                        "  held shares:"
                        f" symbols={len(held_positions)}"
                        f"  guidance_rows={len(recent_outlooks)}"
                    )
                else:
                    print("  held shares: unavailable on this DB schema")
                if outlook_enabled:
                    print(
                        "  intraday guidance:"
                        f" actionable={sum(int(row[1] or 0) for row in held_outlook_counts if str(row[0] or '') == 'ACTIONABLE')}"
                        f"  informational={sum(int(row[1] or 0) for row in held_outlook_counts if str(row[0] or '') == 'INFORMATIONAL')}"
                        f"  other={sum(int(row[1] or 0) for row in held_outlook_counts if str(row[0] or '') not in {'ACTIONABLE', 'INFORMATIONAL'})}"
                    )
                else:
                    print("  intraday guidance: unavailable on this DB schema")
                if LIVE_ANALOG_ENABLED:
                    if live_analog_enabled:
                        print(
                            "  live analog:"
                            f" enabled  k={LIVE_ANALOG_NEIGHBORS}"
                            f"  min_pool={LIVE_ANALOG_MIN_CANDIDATES}"
                            f"  candidate_limit={LIVE_ANALOG_CANDIDATE_LIMIT}"
                        )
                    else:
                        print("  live analog: unavailable on this DB schema")
                if OUTLOOK_AUDIT_ENABLED:
                    if audit_cutoff_session_date is not None:
                        print(
                            "  outlook audit:"
                            f" enabled  lookback={OUTLOOK_AUDIT_LOOKBACK_DAYS}d"
                            f"  canonical={OUTLOOK_AUDIT_CANONICAL_MINUTES}m"
                            f"  min_rows={OUTLOOK_AUDIT_MIN_ROWS}"
                        )
                    else:
                        print("  outlook audit: unavailable on this DB schema")
                if PROFIT_PROFILE_ENABLED:
                    if profit_cutoff_session_date is not None:
                        print(
                            "  profit profile:"
                            f" enabled  lookback={PROFIT_PROFILE_LOOKBACK_DAYS}d"
                            f"  recent={PROFIT_PROFILE_RECENT_DAYS}d"
                            f"  min_rows={PROFIT_PROFILE_MIN_ROWS}"
                        )
                    else:
                        print("  profit profile: unavailable on this DB schema")
                print()

                if position_state_enabled:
                    _print_held_positions(held_positions)
                else:
                    print("=== Held Shares ===")
                    print("  unavailable on this DB schema")
                    print()

                if outlook_enabled:
                    _print_counts(
                        "=== Held Guidance Status Counts ===",
                        held_outlook_counts,
                        label="OUTLOOK STATUS",
                        value="COUNT",
                    )
                    if OUTLOOK_AUDIT_ENABLED:
                        _print_outlook_audit_summary(
                            "=== Held Guidance Audit ===",
                            held_outlook_audit,
                        )
                    if PROFIT_PROFILE_ENABLED:
                        _print_profit_pocket_leaders(
                            "=== Where Profit Has Shown Up ===",
                            profit_pocket_leaders,
                        )
                    _print_outlook_blocks(
                        "=== Current Intraday Guidance For Held Shares ===",
                        recent_outlooks,
                        analog_forecasts=live_analog_forecasts,
                        state_audits=state_outlook_audits,
                        profit_profiles=state_profit_profiles,
                    )
                else:
                    print("=== Current Intraday Guidance For Held Shares ===")
                    print("  unavailable on this DB schema")
                    print()
            else:
                totals = _fetch_execution_totals(conn, economic_enabled)
                signal_counts = _fetch_signal_counts(conn)
                outlook_counts = _fetch_outlook_counts(conn) if outlook_enabled else []
                sim_trade_totals = (
                    _fetch_sim_trade_totals(conn, source_enabled=sim_trade_source_enabled)
                    if sim_trade_enabled
                    else None
                )
                sim_trade_exit_counts = _fetch_sim_trade_exit_counts(conn) if sim_trade_enabled else []
                open_sim_trades = (
                    _fetch_open_sim_trades(conn, limit=8, source_enabled=sim_trade_source_enabled)
                    if sim_trade_enabled
                    else []
                )
                closed_sim_trades = (
                    _fetch_closed_sim_trades(
                        conn,
                        limit=8,
                        source_enabled=sim_trade_source_enabled,
                    )
                    if sim_trade_enabled
                    else []
                )
                closed_live_sim_trades = (
                    _fetch_closed_sim_trades(
                        conn,
                        limit=8,
                        source_enabled=sim_trade_source_enabled,
                        simulation_source="LIVE_FORWARD",
                    )
                    if sim_trade_source_enabled
                    else []
                )
                closed_historical_sim_trades = (
                    _fetch_closed_sim_trades(
                        conn,
                        limit=8,
                        source_enabled=sim_trade_source_enabled,
                        simulation_source="HISTORICAL_BACKFILL",
                    )
                    if sim_trade_source_enabled
                    else []
                )
                recent_outlooks = (
                    _fetch_recent_outlooks(conn, limit=OUTLOOK_LIMIT, action_enabled=outlook_action_enabled)
                    if outlook_enabled
                    else []
                )
                recent_actionable = (
                    conn.execute(
                        _recent_signals_query(
                            economic_enabled=economic_enabled,
                            action_enabled=signal_action_enabled,
                            actionable_only=True,
                            limit=ACTIONABLE_SIGNAL_LIMIT,
                        )
                    ).fetchall()
                    if economic_enabled
                    else []
                )
                recent_signals = conn.execute(
                    _recent_signals_query(
                        economic_enabled=economic_enabled,
                        action_enabled=signal_action_enabled,
                        actionable_only=False,
                        limit=RECENT_SIGNAL_LIMIT,
                    )
                ).fetchall()

                print(
                    "  signals:"
                    f" total={int(totals['signal_count'] or 0)}"
                    f"  latest={totals['latest_signal_time'] or '-'}"
                )
                if economic_enabled:
                    print(
                        "  economic annotations:"
                        f" reviewed={int(totals['reviewed_count'] or 0)}"
                        f"  edge_qualified={int(totals['edge_qualified_count'] or 0)}"
                        f"  actionable={int(totals['actionable_count'] or 0)}"
                        f"  non_actionable={int((totals['signal_count'] or 0) - (totals['actionable_count'] or 0))}"
                    )
                else:
                    print("  economic annotations: unavailable on this DB schema")
                if outlook_enabled:
                    print(
                        "  intraday outlooks:"
                        f" latest_symbols={sum(int(row[1] or 0) for row in outlook_counts)}"
                        f"  actionable={sum(int(row[1] or 0) for row in outlook_counts if str(row[0] or '') == 'ACTIONABLE')}"
                        f"  informational={sum(int(row[1] or 0) for row in outlook_counts if str(row[0] or '') == 'INFORMATIONAL')}"
                    )
                else:
                    print("  intraday outlooks: unavailable on this DB schema")
                if sim_trade_enabled and sim_trade_totals is not None:
                    print(
                        "  simulated short trades:"
                        f" total={int(sim_trade_totals['trade_count'] or 0)}"
                        f"  open={int(sim_trade_totals['open_count'] or 0)}"
                        f"  closed={int(sim_trade_totals['closed_count'] or 0)}"
                        f"  live_forward={int(sim_trade_totals['live_forward_count'] or 0)}"
                        f"  historical={int(sim_trade_totals['historical_backfill_count'] or 0)}"
                        f"  wins={int(sim_trade_totals['win_count'] or 0)}"
                        f"  avg_post={_fmt_signed_pct(sim_trade_totals['avg_post_cost_return_pct'])}"
                        f"  avg_hold={int(float(sim_trade_totals['avg_hold_minutes'] or 0))}m"
                    )
                else:
                    print("  simulated short trades: unavailable on this DB schema")
                print()

                _print_counts(
                    "=== Signal Counts ===",
                    signal_counts,
                    label="SIGNAL TYPE",
                    value="COUNT",
                )

                if economic_enabled:
                    _print_counts(
                        "=== Economic Status Counts ===",
                        _fetch_economic_counts(conn),
                        label="STATUS",
                        value="COUNT",
                    )
                    _print_counts(
                        "=== Top Non-Actionable Reasons ===",
                        _fetch_non_actionable_reasons(conn),
                        label="REASON",
                        value="COUNT",
                    )
                    _print_signal_blocks(
                        "=== Recent Actionable Opportunities ===",
                        recent_actionable,
                        economic_enabled=True,
                    )

                if outlook_enabled:
                    _print_counts(
                        "=== Latest Outlook Status Counts ===",
                        outlook_counts,
                        label="OUTLOOK STATUS",
                        value="COUNT",
                    )
                    _print_outlook_blocks(
                        "=== Current Intraday Guidance ===",
                        recent_outlooks,
                    )

                if sim_trade_enabled:
                    _print_counts(
                        "=== Simulated Trade Exit Counts ===",
                        sim_trade_exit_counts,
                        label="EXIT REASON",
                        value="COUNT",
                    )
                    _print_sim_trade_blocks(
                        "=== Open Simulated Short Trades ===",
                        open_sim_trades,
                        closed=False,
                    )
                    if sim_trade_source_enabled:
                        _print_sim_trade_blocks(
                            "=== Recent Closed Live-Forward Simulated Trades ===",
                            closed_live_sim_trades,
                            closed=True,
                        )
                        _print_sim_trade_blocks(
                            "=== Recent Historical Backfill Simulated Trades ===",
                            closed_historical_sim_trades,
                            closed=True,
                        )
                    else:
                        _print_sim_trade_blocks(
                            "=== Recent Closed Simulated Short Trades ===",
                            closed_sim_trades,
                            closed=True,
                        )

                _print_signal_blocks(
                    "=== Recent Signals ===",
                    recent_signals,
                    economic_enabled=economic_enabled,
                )
    except Exception as exc:
        print(f"DB: {DB}")
        print()
        print(f"Error: {exc}")

    if RUN_ONCE:
        break
    time.sleep(REFRESH_SECONDS)
