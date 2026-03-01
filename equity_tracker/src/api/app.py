"""
FastAPI application — Equity Tracker web API.

Threading safety
────────────────
All route handlers in this project are defined as ``async def``.  Starlette
runs ``async def`` handlers directly in the asyncio event loop (single
thread).  Our DB calls are synchronous and blocking, so they hold the loop
for their duration — but for a single-user LAN tool this is correct and
acceptable.

Do NOT convert route handlers to plain ``def``.  FastAPI wraps plain ``def``
handlers in ``asyncio.run_in_executor``, which dispatches them to a thread
pool.  Multiple threads would then compete for the same SQLite connection
(StaticPool), causing data races.

Do NOT run uvicorn with ``--workers > 1``.  ``run_api.py`` enforces this.

AppContext initialisation
─────────────────────────
Two paths:

1. **Env-var auto-unlock** (recommended for development and production):
   Set ``EQUITY_DB_PATH`` and ``EQUITY_DB_PASSWORD`` before starting the
   server.  The lifespan hook opens the database on startup.

2. **Manual unlock** (interactive / scripted):
   Start the server without env vars.  The API starts in a locked state.
   Call ``POST /admin/unlock`` with ``db_path`` + ``password`` to initialise.

Environment variables
─────────────────────
  EQUITY_DB_PATH          Absolute path to the .db file
  EQUITY_DB_PASSWORD      Database password (encrypted DB) or any value (plain)
  EQUITY_DB_ENCRYPTED     "true" (default) or "false" for plain-SQLite dev DB
  EQUITY_ALLOWED_ORIGINS  Comma-separated CORS origins, or "*" (default)
  EQUITY_TOTP_SECRET      Base32 TOTP secret (generate: scripts/setup_totp.py)
  EQUITY_SECRET_KEY       Session signing key (generate: secrets.token_hex(32))
  EQUITY_DOCS_ENABLED     "true" to expose /docs and /openapi.json (default: false)
  EQUITY_DEV_MODE         "true" to allow session cookie over plain HTTP (localhost)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError

from ..app_context import AppContext
from ..db.engine import DatabaseEngine
from ..db.migration_manager import ensure_migrated
from . import _state
from .auth import SessionExpired
from .limiter import limiter
from .middleware import SecurityHeadersMiddleware
from .routers import (
    admin,
    analytics,
    auth_router,
    calendar,
    catalog,
    dividends,
    history,
    portfolio,
    prices,
    reports,
    risk,
    scenario_lab,
    settings,
    tax_plan,
    ui,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_catalog_if_empty() -> None:
    """
    Seed the security_catalog table from the bundled CSV if it is empty.

    Called after AppContext is initialised (lifespan startup or admin unlock).
    Non-fatal: logs a warning on failure instead of crashing.
    """
    try:
        from ..app_context import AppContext as _AC
        from ..db.repository import SecurityCatalogRepository

        with _AC.read_session() as sess:
            count = SecurityCatalogRepository(sess).count()

        if count == 0:
            with _AC.write_session() as sess:
                inserted = SecurityCatalogRepository(sess).seed_from_csv()
            logger.info("Security catalogue seeded with %d entries.", inserted)
        else:
            logger.debug("Security catalogue already populated (%d entries).", count)
    except Exception as exc:
        logger.warning("Could not seed security catalogue: %s", exc)


def _open_engine_from_env() -> DatabaseEngine | None:
    """
    Attempt to open the database from environment variables.

    Returns a ``DatabaseEngine`` if both EQUITY_DB_PATH and
    EQUITY_DB_PASSWORD are set and non-empty, otherwise ``None``.

    Raises on bad path, wrong password, or missing SQLCipher.
    """
    db_path_str = os.environ.get("EQUITY_DB_PATH", "").strip()
    db_password = os.environ.get("EQUITY_DB_PASSWORD", "").strip()
    db_encrypted = os.environ.get("EQUITY_DB_ENCRYPTED", "true").lower() != "false"

    if not db_path_str:
        return None
    if db_encrypted and not db_password:
        return None

    path = Path(db_path_str)
    if db_encrypted:
        return DatabaseEngine.open(path, db_password)
    else:
        return DatabaseEngine.open_unencrypted(f"sqlite:///{path}")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Startup: optionally open DB. Shutdown: always close DB."""
    try:
        engine = _open_engine_from_env()
    except Exception as exc:
        logger.error(
            "Failed to auto-open database from environment variables: %s", exc
        )
        engine = None

    if engine is not None:
        db_path_str = os.environ.get("EQUITY_DB_PATH", "")
        db_path = Path(db_path_str) if db_path_str else None
        db_encrypted = os.environ.get("EQUITY_DB_ENCRYPTED", "true").lower() != "false"

        if not db_encrypted and db_path_str:
            try:
                ensure_migrated(f"sqlite:///{db_path_str}")
            except RuntimeError as exc:
                logger.critical(
                    "Migration failed — database will not be initialized: %s", exc
                )
                engine.dispose()
                engine = None

    if engine is not None:
        AppContext.initialize(engine)
        _state.set_db_path(db_path)
        logger.info("Database auto-initialized from env vars: %s", Path(db_path_str).name)
        _seed_catalog_if_empty()
    else:
        logger.info(
            "EQUITY_DB_PATH / EQUITY_DB_PASSWORD not set (or startup failed). "
            "API started in locked state. POST /admin/unlock to initialize."
        )

    yield

    _state.set_db_path(None)
    AppContext.lock()
    logger.info("Database connection closed on shutdown.")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

_docs_enabled = os.environ.get("EQUITY_DOCS_ENABLED", "false").lower() == "true"

app = FastAPI(
    title="Equity Tracker API",
    description=(
        "Local-only REST API for UK tax-aware equity portfolio tracking.\n\n"
        "**Running locked?** Call `POST /admin/unlock` with your database path "
        "and password to initialize, then use any other endpoint.\n\n"
        "All monetary values in responses are **decimal strings** — never floats."
    ),
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
# Shared limiter instance (key: remote IP).  Applied per-endpoint via
# @limiter.limit("N/period") decorators in auth_router and admin routers.

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# PRODUCTION: set EQUITY_ALLOWED_ORIGINS to your exact domain.
#   EQUITY_ALLOWED_ORIGINS=https://equity.yourdomain.com
#
# DEVELOPMENT (LAN-only): "*" is acceptable, but credentials cannot be used
# with wildcard origins (SameSite=Lax cookies still work for same-origin).

_origins_env = os.environ.get("EQUITY_ALLOWED_ORIGINS", "*").strip()
_allowed_origins: list[str] = (
    ["*"] if _origins_env == "*" else [o.strip() for o in _origins_env.split(",")]
)
_allow_credentials = _origins_env != "*"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(SessionExpired)
async def session_expired_handler(request: Request, _exc: SessionExpired) -> JSONResponse | RedirectResponse:
    """
    Handle unauthenticated requests.

    GET requests are redirected to the login page (browser-friendly).
    All other methods (POST, etc.) get a 401 JSON response (API-friendly).
    """
    if request.method == "GET":
        next_url = request.url.path
        return RedirectResponse(url=f"/auth/login?next={next_url}", status_code=303)
    return JSONResponse(
        status_code=401,
        content={"error": "session_expired", "message": "Login required."},
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
    """Map SQLAlchemy IntegrityError (e.g. duplicate external_id) to HTTP 409."""
    return JSONResponse(
        status_code=409,
        content={
            "error": "conflict",
            "message": "A record with this identifier already exists.",
            "detail": str(exc.orig) if exc.orig else str(exc),
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth_router.router)
app.include_router(admin.router)
app.include_router(analytics.router)
app.include_router(calendar.router)
app.include_router(catalog.router)
app.include_router(dividends.router)
app.include_router(history.router)
app.include_router(portfolio.router)
app.include_router(prices.router)
app.include_router(reports.router)
app.include_router(risk.router)
app.include_router(scenario_lab.router)
app.include_router(settings.router)
app.include_router(tax_plan.router)
app.include_router(ui.router)


# ---------------------------------------------------------------------------
# Static files (CSS, JS, images)
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Root health check (no DB required)
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"], summary="Health check")
async def health() -> dict[str, str]:
    """
    Always returns ``{"status": "ok"}`` regardless of DB state.

    Use ``GET /admin/status`` to check whether the database is unlocked.
    """
    return {"status": "ok"}
