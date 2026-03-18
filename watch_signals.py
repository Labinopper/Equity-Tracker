"""Poll beta DB for WAIT_FOR_CLOSE_CONFIRMATION and NO_ACTION execution signals — refreshes every 2 seconds."""

import sqlite3
import os
import time

DB = r"C:\EquityTrackerData\portfolio.beta_research.db"

SIGNALS_QUERY = """
    SELECT s.symbol, s.signal_type, s.session_date, s.signal_time,
           s.session_state, s.confidence_score, s.rationale_text,
           s.event_trigger_code, h.hypothesis_code, h.name
    FROM beta_execution_signals s
    LEFT JOIN beta_execution_hypothesis_definitions h
        ON s.execution_hypothesis_definition_id = h.id
    ORDER BY s.signal_time DESC
    LIMIT 30
"""

COUNTS_QUERY = """
    SELECT signal_type, COUNT(*) AS cnt
    FROM beta_execution_signals
    GROUP BY signal_type
    ORDER BY signal_type
"""

while True:
    os.system("cls")
    try:
        conn = sqlite3.connect(DB, timeout=5)
        cur = conn.cursor()

        print("=== Signal Counts ===")
        print(f"  {'SIGNAL TYPE':<34} {'COUNT':>6}")
        print(f"  {'-----------':<34} {'-----':>6}")
        for signal_type, cnt in cur.execute(COUNTS_QUERY).fetchall():
            print(f"  {signal_type:<34} {cnt:>6}")

        print()
        print("=== Recent Signals (WAIT_FOR_CLOSE_CONFIRMATION / NO_ACTION) ===")
        print(
            f"  {'WHEN':<22} {'SYMBOL':<8} {'TYPE':<34}"
            f" {'SESSION':<14} {'CONF':>5} {'TRIGGER':<20} {'HYPOTHESIS'}"
        )
        print(
            f"  {'----':<22} {'------':<8} {'----':<34}"
            f" {'-------':<14} {'----':>5} {'-------':<20} {'----------'}"
        )
        for (symbol, signal_type, session_date, signal_time,
             session_state, confidence, rationale,
             trigger, hyp_code, hyp_name) in cur.execute(SIGNALS_QUERY).fetchall():
            ts = (signal_time or "-")[:22]
            print(
                f"  {ts:<22} {symbol or '-':<8} {signal_type:<34}"
                f" {session_state or '-':<14} {confidence:>5.2f}"
                f" {(trigger or '-'):<20} {hyp_code or '-'}"
            )

        print()
        print("=== Rationales (last 5 with text) ===")
        RATIONALE_QUERY = """
            SELECT symbol, signal_type, signal_time, rationale_text
            FROM beta_execution_signals
            WHERE signal_type IN ('WAIT_FOR_CLOSE_CONFIRMATION', 'NO_ACTION')
              AND rationale_text IS NOT NULL
              AND rationale_text != ''
            ORDER BY signal_time DESC
            LIMIT 5
        """
        print(f"  {'WHEN':<22} {'SYMBOL':<8} {'TYPE':<34} {'RATIONALE'}")
        print(f"  {'----':<22} {'------':<8} {'----':<34} {'---------'}")
        for symbol, signal_type, signal_time, rationale in cur.execute(RATIONALE_QUERY).fetchall():
            ts = (signal_time or "-")[:22]
            text = (rationale or "-")
            print(f"  {ts:<22} {symbol or '-':<8} {signal_type:<34} {text}")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

    time.sleep(2)
