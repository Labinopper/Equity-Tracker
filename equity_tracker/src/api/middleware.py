"""
Security headers middleware.

Adds protective HTTP response headers to every response.  These headers
instruct browsers to apply additional safeguards regardless of page content.

CSP notes
─────────
- 'self' covers /static/style.css, /static/login.css, and all local scripts.
- fonts.googleapis.com / fonts.gstatic.com are needed by base.html.
- cdn.jsdelivr.net is needed by analytics.html for chart.js.
- No 'unsafe-inline' — all templates use external scripts/styles only.
- img-src allows data: URIs for inline SVGs and chart data URIs.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers on every HTTP response."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response
