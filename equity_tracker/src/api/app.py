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
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from ..app_context import AppContext
from ..db.engine import DatabaseEngine
from ..db.migration_manager import ensure_migrated
from . import _state
from .routers import admin, catalog, portfolio, prices, reports, settings, ui

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
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Default: allow all origins — correct for a LAN-only, unauthenticated tool.
# Override: set EQUITY_ALLOWED_ORIGINS="http://192.168.1.x:3000,http://..."

_origins_env = os.environ.get("EQUITY_ALLOWED_ORIGINS", "*").strip()
_allowed_origins: list[str] = (
    ["*"] if _origins_env == "*" else [o.strip() for o in _origins_env.split(",")]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

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

app.include_router(admin.router)
app.include_router(catalog.router)
app.include_router(portfolio.router)
app.include_router(prices.router)
app.include_router(reports.router)
app.include_router(settings.router)
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
