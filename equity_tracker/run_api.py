"""
Equity Tracker — web API entry point.

Run from the equity_tracker/ directory:

    python run_api.py

The server binds to 0.0.0.0:8000, making it reachable from other devices
on the local network.  Find your LAN IP with:

    ipconfig | findstr "IPv4"

Then access from any device at: http://YOUR_LAN_IP:8000

Environment variables (optional — auto-unlock on startup)
──────────────────────────────────────────────────────────
  EQUITY_DB_PATH        Absolute path to your portfolio.db file
  EQUITY_DB_PASSWORD    Database password (passphrase for SQLCipher DB)
  EQUITY_DB_ENCRYPTED   "true" (default) or "false" for plain-SQLite dev DB
  EQUITY_ALLOWED_ORIGINS  CORS origins, comma-separated, default "*"

Windows PowerShell example — auto-unlock:

    $env:EQUITY_DB_PATH     = "C:/Users/you/portfolio.db"
    $env:EQUITY_DB_PASSWORD = "your-passphrase"
    python run_api.py

Windows PowerShell example — start locked, unlock via /docs:

    python run_api.py
    # Visit http://localhost:8000/docs
    # POST /admin/unlock  with  { "db_path": "...", "password": "..." }

Threading constraint
────────────────────
workers=1 is mandatory.  The encrypted DatabaseEngine uses StaticPool
(a single persistent connection needed to keep SQLCipher unlocked for the
process lifetime).  Multiple workers would each maintain their own connection
pool but share no state, breaking AppContext.  Do not change workers to > 1.
"""

from pathlib import Path

import uvicorn
from src.env_bootstrap import load_project_dotenv
from src.process_lock import acquire_process_lock


def main() -> None:
    load_project_dotenv()
    lock = acquire_process_lock(Path(__file__).resolve().parent.parent / "data" / "run_api.lock")
    if lock is None:
        print("Equity Tracker is already running; refusing to start a duplicate web process.")
        return
    # Import after .env load so startup sees the intended runtime config.
    try:
        from src.api.app import app

        config = uvicorn.Config(
            app,
            host="0.0.0.0",   # bind all interfaces — required for LAN access
            port=8000,
            workers=1,         # MUST remain 1 — see threading constraint above
            reload=False,
            log_level="info",
        )
        server = uvicorn.Server(config)
        server.run()
    except KeyboardInterrupt:
        print("Equity Tracker received an external interrupt and is shutting down.")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
