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

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator
from zoneinfo import ZoneInfo

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
    cash,
    calendar,
    catalog,
    dividends,
    history,
    portfolio,
    prices,
    reports,
    risk,
    scenario_lab,
    sell_plan,
    strategic,
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
    Backward-compatible wrapper for initial catalogue availability.

    Called after AppContext is initialised (lifespan startup or admin unlock).
    Non-fatal: logs a warning on failure instead of crashing.
    """
    _ensure_security_catalog_available(force_refresh=False)


def _ensure_security_catalog_available(*, force_refresh: bool) -> None:
    """
    Ensure the security catalogue exists.

    When Twelve Data is configured, the catalogue is refreshed from the live
    provider on startup and then weekly. If that fails and the table is empty,
    the bundled CSV remains as a fallback.
    """
    try:
        from ..app_context import AppContext as _AC
        from ..db.repository import SecurityCatalogRepository
        from ..services.twelve_data_catalog_service import TwelveDataCatalogService

        with _AC.read_session() as sess:
            count = SecurityCatalogRepository(sess).count()

        if TwelveDataCatalogService.is_configured():
            try:
                result = TwelveDataCatalogService.sync_if_due(force=force_refresh)
            except Exception as exc:
                logger.warning("Live catalogue sync failed; falling back to bundled seed if needed: %s", exc)
                result = None
            if result is not None:
                logger.info(
                    "Security catalogue synced from Twelve Data: inserted=%d updated=%d deleted=%d total=%d.",
                    result.get("inserted", 0),
                    result.get("updated", 0),
                    result.get("deleted", 0),
                    result.get("total", 0),
                )
                return

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
# Nightly history backfill scheduler
# ---------------------------------------------------------------------------

_UK_TZ = ZoneInfo("Europe/London")
_UTC = ZoneInfo("UTC")


def _seconds_until_11pm_uk() -> float:
    """Return seconds until the next 23:00 Europe/London wall-clock time."""
    now = datetime.now(_UK_TZ)
    target = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _seconds_until_next_tick(step_seconds: int = 5) -> float:
    now = datetime.now(_UTC)
    step_seconds = max(1, int(step_seconds))
    current_second = now.second + (now.microsecond / 1_000_000)
    seconds_to_add = step_seconds - (current_second % step_seconds)
    if seconds_to_add <= 0:
        seconds_to_add = float(step_seconds)
    target = now + timedelta(seconds=seconds_to_add)
    target = target.replace(microsecond=0)
    return max(1.0, (target - now).total_seconds())


async def _nightly_history_task() -> None:
    """
    Background task: maintain 366 days of pre-acquisition price history.

    On startup, runs immediately if the DB is ready and any security is missing
    extended history (handles the "not yet 366 days" cold-start case).

    Thereafter sleeps until 23:00 UK time each night and runs again so that
    yesterday's close is incorporated and any gaps added by new acquisitions
    are filled.
    """
    from ..services.price_service import PriceService

    # Cold-start check: run immediately if the DB is already open.
    if AppContext.is_initialized():
        try:
            result = PriceService.backfill_extended_history_all()
            _log_backfill_result("Startup", result)
        except Exception:
            logger.exception("Startup extended history backfill failed.")

    while True:
        sleep_secs = _seconds_until_11pm_uk()
        logger.debug("Next extended history backfill in %.0f s (23:00 UK).", sleep_secs)
        await asyncio.sleep(sleep_secs)

        if not AppContext.is_initialized():
            logger.info("DB not initialised; skipping nightly history backfill.")
            continue

        try:
            result = PriceService.backfill_extended_history_all()
            _log_backfill_result("Nightly", result)
        except Exception:
            logger.exception("Nightly extended history backfill failed.")


async def _intraday_quote_refresh_task() -> None:
    """
    Background task: refresh a rate-limited subset of open-market securities every 5 seconds.

    Twelve Data is only used when configured. Otherwise this task exits immediately.
    """
    from ..services.price_service import PriceService
    from ..services.twelve_data_price_service import TwelveDataPriceService

    if not TwelveDataPriceService.is_configured():
        logger.info("Twelve Data intraday refresh disabled; no API key configured.")
        return

    while True:
        try:
            if AppContext.is_initialized():
                result = PriceService.refresh_intraday_budgeted()
                if result.get("fetched") or result.get("errors"):
                    logger.info(
                        "Intraday refresh: fetched=%d planned=%d remaining_calls=%d tracked_instruments=%d errors=%d.",
                        result.get("fetched", 0),
                        result.get("planned", 0),
                        result.get("remaining_calls", 0),
                        result.get("tracked_instruments", 0),
                        len(result.get("errors", [])),
                    )
        except Exception:
            logger.exception("Intraday budgeted refresh failed.")

        await asyncio.sleep(5)


async def _fx_refresh_task() -> None:
    """
    Background task: refresh active FX pairs 24/7 under the shared Twelve Data minute cap.
    """
    from ..services.price_service import PriceService
    from ..services.twelve_data_price_service import TwelveDataPriceService

    if not TwelveDataPriceService.is_configured():
        logger.info("Twelve Data FX refresh disabled; no API key configured.")
        return

    while True:
        try:
            if AppContext.is_initialized():
                result = PriceService.refresh_fx_budgeted()
                if result.get("fetched") or result.get("errors"):
                    logger.info(
                        "FX refresh: fetched=%d planned=%d remaining_calls=%d tracked_instruments=%d errors=%d.",
                        result.get("fetched", 0),
                        result.get("planned", 0),
                        result.get("remaining_calls", 0),
                        result.get("tracked_instruments", 0),
                        len(result.get("errors", [])),
                    )
        except Exception:
            logger.exception("FX budgeted refresh failed.")

        await asyncio.sleep(5)


async def _weekly_catalog_sync_task() -> None:
    """
    Background task: keep the Add Security catalogue fresh from Twelve Data.

    Sync runs once on startup via the lifespan hook and then checks every 6
    hours whether the weekly window has elapsed.
    """
    while True:
        try:
            if AppContext.is_initialized():
                _ensure_security_catalog_available(force_refresh=False)
        except Exception:
            logger.exception("Weekly security catalogue sync failed.")
        await asyncio.sleep(6 * 60 * 60)


async def _twelve_data_stream_task() -> None:
    from ..services.twelve_data_stream_service import TwelveDataStreamService

    if not TwelveDataStreamService.is_enabled():
        logger.info("Twelve Data WebSocket stream disabled by configuration.")
        return

    await TwelveDataStreamService.run_forever()


def _log_backfill_result(label: str, result: dict) -> None:
    days = result.get("backfilled_days", 0)
    rate_limited = result.get("rate_limited", [])
    errors = result.get("errors", [])
    if days or rate_limited or errors:
        logger.info(
            "%s history backfill: %d days written, rate-limited=%s, errors=%d.",
            label, days, rate_limited or "none", len(errors),
        )
        for err in errors:
            logger.warning("  backfill error — %s: %s", err.get("ticker"), err.get("error"))
    else:
        logger.debug("%s history backfill: nothing to do (all securities up to date).", label)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Startup: optionally open DB. Shutdown: always close DB."""
    app.state.server_started_at_utc = datetime.now(timezone.utc)

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
        _ensure_security_catalog_available(force_refresh=True)
    else:
        logger.info(
            "EQUITY_DB_PATH / EQUITY_DB_PASSWORD not set (or startup failed). "
            "API started in locked state. POST /admin/unlock to initialize."
        )

    history_task = asyncio.create_task(_nightly_history_task())
    intraday_task = asyncio.create_task(_intraday_quote_refresh_task())
    fx_task = asyncio.create_task(_fx_refresh_task())
    catalog_task = asyncio.create_task(_weekly_catalog_sync_task())
    stream_task = asyncio.create_task(_twelve_data_stream_task())

    yield

    history_task.cancel()
    intraday_task.cancel()
    fx_task.cancel()
    catalog_task.cancel()
    stream_task.cancel()
    with suppress(asyncio.CancelledError):
        await history_task
    with suppress(asyncio.CancelledError):
        await intraday_task
    with suppress(asyncio.CancelledError):
        await fx_task
    with suppress(asyncio.CancelledError):
        await catalog_task
    with suppress(asyncio.CancelledError):
        await stream_task

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
app.include_router(cash.router)
app.include_router(calendar.router)
app.include_router(catalog.router)
app.include_router(dividends.router)
app.include_router(history.router)
app.include_router(portfolio.router)
app.include_router(prices.router)
app.include_router(reports.router)
app.include_router(risk.router)
app.include_router(scenario_lab.router)
app.include_router(sell_plan.router)
app.include_router(strategic.router)
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
