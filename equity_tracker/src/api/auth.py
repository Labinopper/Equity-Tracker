"""
Authentication logic — TOTP verification and session cookie signing.

Design
──────
Authentication is TOTP-only (RFC 6238, 6-digit codes, 30-second period).
The TOTP secret is stored in the EQUITY_TOTP_SECRET environment variable
and persists unchanged across restarts, updates, and redeployments.

To set up: run  python scripts/setup_totp.py
To reset:  run  python scripts/setup_totp.py --reset
To verify: run  python scripts/setup_totp.py --verify

Session tokens are signed with EQUITY_SECRET_KEY using itsdangerous
TimestampSigner.  The token payload is the fixed string "ok" — identity
is irrelevant for a single-user app.  The timestamp is embedded by the
signer and checked against SESSION_MAX_AGE_SECONDS on each request.

Cookie attributes
─────────────────
  HttpOnly  — not accessible to JavaScript
  Secure    — HTTPS only (set False via EQUITY_DEV_MODE=true for localhost dev)
  SameSite=Lax — sent on top-level navigation, NOT on cross-site sub-requests
  Path=/    — applies to all routes
  Max-Age   — SESSION_MAX_AGE_SECONDS (8 hours)
"""

from __future__ import annotations

import os

import pyotp
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE_NAME = "eq_session"
SESSION_MAX_AGE_SECONDS = 8 * 3600  # 8 hours


class SessionExpired(Exception):
    """
    Raised by session_required() when the session cookie is absent or invalid.

    Caught by the exception handler registered in app.py, which redirects
    GET requests to /auth/login and returns 401 JSON for API calls.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_secret_key() -> str:
    """
    Return EQUITY_SECRET_KEY from env.  Raises RuntimeError if unset.

    This is the signing key for session cookies — distinct from the TOTP secret.
    Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    """
    key = os.environ.get("EQUITY_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "EQUITY_SECRET_KEY env var is required for session signing. "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return key


def _get_totp_secret() -> str | None:
    """Return EQUITY_TOTP_SECRET, or None if not configured."""
    return os.environ.get("EQUITY_TOTP_SECRET", "").strip() or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_totp_configured() -> bool:
    """Return True if EQUITY_TOTP_SECRET is set and non-empty."""
    return bool(_get_totp_secret())


def verify_totp(code: str) -> bool:
    """
    Verify a 6-digit TOTP code against EQUITY_TOTP_SECRET.

    Tolerance: valid_window=1 accepts the previous and next 30-second windows
    (±30 seconds), matching RFC 6238 recommended tolerance for clock drift.

    Returns False if EQUITY_TOTP_SECRET is not configured (fail-closed).
    """
    secret = _get_totp_secret()
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code, valid_window=1))


def make_session_token() -> str:
    """
    Create a signed, timestamped session token.

    The payload is the fixed string "ok".  The timestamp is embedded by
    TimestampSigner and checked against SESSION_MAX_AGE_SECONDS on validate.
    """
    signer = TimestampSigner(_get_secret_key(), salt="eq-session-v1")
    return signer.sign("ok").decode()


def validate_session_token(token: str) -> bool:
    """
    Return True if token is valid and not older than SESSION_MAX_AGE_SECONDS.

    Returns False on any error: expired, tampered, missing key, etc.
    """
    try:
        signer = TimestampSigner(_get_secret_key(), salt="eq-session-v1")
        signer.unsign(token, max_age=SESSION_MAX_AGE_SECONDS)
        return True
    except (SignatureExpired, BadSignature, Exception):
        return False


def cookie_secure() -> bool:
    """
    Return True unless EQUITY_DEV_MODE=true is set.

    Allows the session cookie to be sent over plain HTTP during local
    development (localhost).  Must be True in production (behind Caddy/HTTPS).
    """
    return os.environ.get("EQUITY_DEV_MODE", "").lower() != "true"
