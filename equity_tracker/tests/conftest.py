from __future__ import annotations

import os


# Ensure app imports during test collection do not depend on a local .env file.
os.environ.setdefault("EQUITY_SECRET_KEY", "test-secret-key-equity-tracker-testing-only-xx")
os.environ.setdefault("EQUITY_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("EQUITY_DEV_MODE", "true")
