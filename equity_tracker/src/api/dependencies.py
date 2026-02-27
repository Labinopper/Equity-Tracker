"""
Shared FastAPI dependencies.

Usage in route handlers::

    from .dependencies import db_required, session_required

    @router.get("/some/endpoint")
    async def my_endpoint(_: None = Depends(db_required)) -> ...:
        ...

``db_required`` raises HTTP 503 before the route body runs if the database
has not been unlocked.  This means every protected route gets an automatic,
consistent error response without any per-route guard code.

``session_required`` raises ``SessionExpired`` if the browser session cookie
is absent or invalid.  The exception handler in app.py converts this to a
303 redirect to /auth/login (for GET requests) or 401 JSON (for API calls).

Dependency order on routes that need both::

    dependencies=[Depends(session_required), Depends(db_required)]

``session_required`` runs first — no point checking the DB if the caller
is not authenticated.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..app_context import AppContext
from .auth import SESSION_COOKIE_NAME, SessionExpired, validate_session_token


def db_required() -> None:
    """
    FastAPI dependency: assert the database is initialized.

    Raises:
        HTTPException 503: if ``AppContext`` is not initialized (DB locked).
    """
    if not AppContext.is_initialized():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_locked",
                "message": (
                    "The database is locked. "
                    "POST /admin/unlock with db_path and password to initialize."
                ),
            },
        )


def session_required(request: Request) -> None:
    """
    FastAPI dependency: assert the browser session is authenticated.

    Reads the signed ``eq_session`` cookie set by POST /auth/login.
    Raises ``SessionExpired`` if the cookie is absent, tampered, or expired.

    The ``SessionExpired`` exception is caught by the handler registered in
    app.py, which redirects GET requests to /auth/login and returns 401 JSON
    for POST/API requests.

    Applied at router level (not per-route) for all data routers::

        router = APIRouter(..., dependencies=[Depends(session_required)])

    Routes that intentionally bypass this dependency:
      - GET  /health          — reverse-proxy health check
      - GET  /auth/login      — the login page itself
      - POST /auth/login      — TOTP submission
      - POST /auth/logout     — cookie deletion
      - GET  /admin/status    — lock-state probe (safe: returns no data)
      - POST /admin/unlock    — rate-limited; session not yet available
      - /static/*             — static files (served by StaticFiles middleware)
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not validate_session_token(token):
        raise SessionExpired()
