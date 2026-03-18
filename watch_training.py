"""Poll beta DB for recent training activity — refreshes every 2 seconds."""

import sqlite3
import os
import time

DB = r"C:\EquityTrackerData\portfolio.beta_research.db"

JOBS_QUERY = """
    SELECT status, completed_at
    FROM beta_job_runs
    WHERE job_name = 'beta_daily_training'
    ORDER BY completed_at DESC
    LIMIT 5
"""

DECISIONS_QUERY = """
    SELECT status_code, reason_code, performed, trained, activated,
           training_rows, validation_sign_accuracy_pct, created_at
    FROM beta_training_decisions
    ORDER BY created_at DESC
    LIMIT 8
"""

MODEL_QUERY = """
    SELECT version_code, is_active, is_challenger, status,
           validation_sign_accuracy_pct, validation_row_count, created_at
    FROM beta_model_versions
    ORDER BY created_at DESC
    LIMIT 5
"""

while True:
    os.system("cls")
    try:
        conn = sqlite3.connect(DB, timeout=5)
        cur = conn.cursor()

        print("=== Training Jobs ===")
        print(f"  {'STATUS':<10} {'COMPLETED AT':<28}")
        print(f"  {'------':<10} {'------------':<28}")
        for status, completed in cur.execute(JOBS_QUERY).fetchall():
            print(f"  {status:<10} {completed or '-':<28}")

        print()
        print("=== Training Decisions ===")
        print(f"  {'WHEN':<28} {'STATUS':<10} {'REASON':<36} {'ROWS':>6} {'ACC':>7} {'FLAGS'}")
        print(f"  {'----':<28} {'------':<10} {'------':<36} {'----':>6} {'---':>7} {'-----'}")
        for status_code, reason, performed, trained, activated, rows, acc, ts in cur.execute(DECISIONS_QUERY).fetchall():
            flags = []
            if trained:
                flags.append("TRAINED")
            if activated:
                flags.append("ACTIVATED")
            print(
                f"  {ts or '-':<28} {status_code:<10} {(reason or '-'):<36}"
                f" {(rows or '-'):>6} {f'{acc:.1f}%' if acc is not None else '-':>7}"
                f" {' '.join(flags)}"
            )

        print()
        print("=== Recent Models ===")
        print(f"  {'WHEN':<28} {'VERSION':<20} {'STATUS':<12} {'ACC':>7} {'ROWS':>6} {'FLAGS'}")
        print(f"  {'----':<28} {'-------':<20} {'------':<12} {'---':>7} {'----':>6} {'-----'}")
        for version, active, challenger, status, acc, val_rows, created in cur.execute(MODEL_QUERY).fetchall():
            flags = []
            if active:
                flags.append("ACTIVE")
            if challenger:
                flags.append("CHALLENGER")
            print(
                f"  {created or '-':<28} {version or '-':<20} {status or '-':<12}"
                f" {f'{acc:.1f}%' if acc is not None else '-':>7} {(val_rows or '-'):>6}"
                f" {' '.join(flags)}"
            )

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

    time.sleep(2)
