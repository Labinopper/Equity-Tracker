# Demo Data

This project includes a deterministic seed script for UI validation.

## Create Demo DB

From `equity_tracker/`:

```powershell
python scripts/seed_demo_db.py
```

This recreates:

- `data/demo.db`
- `data/demo.db.settings.json`

## Run App In Demo Mode

From `equity_tracker/` (PowerShell):

```powershell
$env:EQUITY_DB_PATH      = (Resolve-Path .\data\demo.db)
$env:EQUITY_DB_ENCRYPTED = "false"
$env:EQUITY_SECRET_KEY   = "demo-secret-key-not-for-production-use"
$env:EQUITY_TOTP_SECRET  = "JBSWY3DPEHPK3PXP"   # demo only
$env:EQUITY_DEV_MODE     = "true"
python run_api.py
```

Open `http://localhost:8000/` — you will be redirected to the login page.
Use the TOTP code generated from `EQUITY_TOTP_SECRET` (or skip auth entirely
by running `python scripts/setup_totp.py --verify` to see the current code).

- `http://localhost:8000/`
- `http://localhost:8000/simulate`

## Expected Outcomes

- Portfolio displays seeded schemes: `BROKERAGE`, `ESPP`, and `ESPP_PLUS`.
- Simulating disposal of `7` shares with no scheme filter allocates from the oldest `BROKERAGE` lot first, so employment tax is typically `0.00`.
- Simulating against taxable scheme lots (for example by filtering to `ESPP`) can produce non-zero employment tax when scheme rules apply.
- Values are deterministic across runs because the DB is fully recreated with fixed seed values each time.

## Minimal Smoke Check (Optional)

```powershell
@'
import os
from pathlib import Path
from fastapi.testclient import TestClient

os.environ["EQUITY_DB_PATH"]      = str(Path("data/demo.db").resolve())
os.environ["EQUITY_DB_ENCRYPTED"] = "false"
os.environ["EQUITY_SECRET_KEY"]   = "demo-secret-not-production"
os.environ["EQUITY_TOTP_SECRET"]  = "JBSWY3DPEHPK3PXP"
os.environ["EQUITY_DEV_MODE"]     = "true"

from src.api.app import app
from src.api.auth import SESSION_COOKIE_NAME, make_session_token

token = make_session_token()
with TestClient(app, cookies={SESSION_COOKIE_NAME: token}) as client:
    print(client.get("/admin/status").json())
    print(client.get("/").status_code)
    print(client.get("/simulate").status_code)
'@ | python -
```
