"""
Authentication router — TOTP login and logout.

Endpoints
─────────
  GET  /auth/login   Render the TOTP login page
  POST /auth/login   Verify code; set session cookie on success
  POST /auth/logout  Clear session cookie

Design notes
────────────
- No password is involved.  Authentication is purely TOTP (RFC 6238).
- The TOTP secret lives in EQUITY_TOTP_SECRET and never changes between
  restarts.  Run `python scripts/setup_totp.py --reset` to rotate it.
- Rate limiting (5 attempts per 15 minutes per IP) is applied to
  POST /auth/login to prevent brute-force of the 6-digit code space.
- On failure the response is always HTTP 200 to avoid timing oracles.
  The error message is intentionally generic ("Invalid code").
- The `next` query / form field captures the originally requested URL
  so the user lands in the right place after login.
- `next` is sanitised: must start with "/" and not "//" (open-redirect guard).
- logout uses POST (not GET) so a foreign page cannot trigger logout via
  <img src="/auth/logout">.  SameSite=Lax on the cookie also prevents
  cross-site POST carrying the cookie.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .._templates import templates
from ..auth import (
    SESSION_COOKIE_NAME,
    cookie_secure,
    is_totp_configured,
    make_session_token,
    verify_totp,
    SESSION_MAX_AGE_SECONDS,
)
from ..limiter import limiter

router = APIRouter(tags=["auth"])


def _safe_next(next_url: str) -> str:
    """Sanitise redirect target: must be an absolute local path."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@router.get("/auth/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/") -> HTMLResponse:
    """Show the TOTP login page."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "next": next, "error": None},
    )


@router.post("/auth/login", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("5/15minutes")
async def login_submit(
    request: Request,
    code: str = Form(...),
    next: str = Form("/"),
) -> Response:
    """
    Verify TOTP code.

    On success: set signed session cookie, redirect to `next`.
    On failure: re-render login page with generic error (always HTTP 200).
    """
    if not is_totp_configured():
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "next": next,
                "error": "Authentication is not configured on this server.",
            },
            status_code=500,
        )

    if verify_totp(code.strip()):
        token = make_session_token()
        response = RedirectResponse(_safe_next(next), status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=cookie_secure(),
            samesite="lax",
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
        )
        return response

    # Always 200 on failure — avoid leaking timing information
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "next": next, "error": "Invalid code. Please try again."},
        status_code=200,
    )


@router.post("/auth/logout", response_class=HTMLResponse, include_in_schema=False)
async def logout(request: Request) -> Response:  # noqa: ARG001
    """Clear the session cookie and redirect to the login page."""
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
