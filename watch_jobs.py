"""Poll beta DB for all recent job runs — refreshes every 2 seconds."""

import sqlite3
import os
import time

DB = r"C:\EquityTrackerData\portfolio.beta_research.db"

LATEST_JOBS_QUERY = """
    SELECT job_name, job_type, status, max(completed_at) AS last_run
    FROM beta_job_runs
    GROUP BY job_name
    ORDER BY last_run DESC
"""

RECENT_RUNS_QUERY = """
    SELECT job_name, job_type, status, started_at, completed_at
    FROM beta_job_runs
    ORDER BY completed_at DESC
    LIMIT 15
"""

FAILURES_QUERY = """
    SELECT job_name, status, completed_at, details_json
    FROM beta_job_runs
    WHERE status != 'SUCCESS'
    ORDER BY completed_at DESC
    LIMIT 5
"""

while True:
    os.system("cls")
    try:
        conn = sqlite3.connect(DB, timeout=5)
        cur = conn.cursor()

        print("=== Latest Run Per Job ===")
        print(f"  {'JOB NAME':<42} {'TYPE':<18} {'STATUS':<10} {'LAST RUN'}")
        print(f"  {'--------':<42} {'----':<18} {'------':<10} {'--------'}")
        for name, jtype, status, last_run in cur.execute(LATEST_JOBS_QUERY).fetchall():
            marker = "*" if status != "SUCCESS" else " "
            print(f" {marker}{name:<42} {jtype:<18} {status:<10} {last_run or '-'}")

        print()
        print("=== 15 Most Recent Runs ===")
        print(f"  {'COMPLETED AT':<28} {'STATUS':<10} {'JOB NAME':<42} {'TYPE'}")
        print(f"  {'------------':<28} {'------':<10} {'--------':<42} {'----'}")
        for name, jtype, status, started, completed in cur.execute(RECENT_RUNS_QUERY).fetchall():
            marker = "*" if status != "SUCCESS" else " "
            print(f" {marker}{completed or '-':<28} {status:<10} {name:<42} {jtype}")

        failures = cur.execute(FAILURES_QUERY).fetchall()
        if failures:
            print()
            print("=== Recent Failures ===")
            print(f"  {'COMPLETED AT':<28} {'STATUS':<10} {'JOB NAME'}")
            print(f"  {'------------':<28} {'------':<10} {'--------'}")
            for name, status, completed, details in failures:
                print(f"  {completed or '-':<28} {status:<10} {name}")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

    time.sleep(2)
