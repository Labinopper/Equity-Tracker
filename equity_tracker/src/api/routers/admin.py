"""
Admin router — database lifecycle management.

Endpoints
─────────
  GET  /admin/status   Lock state — no DB required, always safe to call
  POST /admin/unlock   Open a database file and initialize AppContext
  POST /admin/lock     Close the database and clear AppContext

Design notes
────────────
- ``/admin/unlock`` accepts the absolute path to the ``.db`` file plus the
  password.  Set ``encrypted=false`` only for plain-SQLite development
  databases (created with ``DatabaseEngine.open_unencrypted()``).
- If the API was started with env-var auto-unlock, ``/admin/status`` will
  immediately return ``locked=false``.
- ``/admin/lock`` is a no-op if the DB is already locked (safe to call
  unconditionally on app shutdown or browser unload).
- No authentication is applied here — this is a single-user LAN tool.
  Callers are assumed to be the local user or a trusted LAN device.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ...app_context import AppContext
from ...db.engine import DatabaseEngine
from ...services.validation_report_service import ValidationReportService
from .. import _state
from ..dependencies import db_required

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class UnlockRequest(BaseModel):
    db_path: str = Field(
        ...,
        description="Absolute path to the SQLite / SQLCipher database file.",
        examples=["C:/Users/you/portfolio.db"],
    )
    password: str = Field(
        ...,
        description="Database password.  For encrypted databases this is the "
                    "passphrase used when the database was created.",
    )
    encrypted: bool = Field(
        True,
        description=(
            "True (default) for SQLCipher-encrypted databases.  "
            "False for plain-SQLite development databases."
        ),
    )


class AdminStatusResponse(BaseModel):
    locked: bool
    message: str


class AdminActionResponse(BaseModel):
    status: str
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=AdminStatusResponse,
    summary="Database lock state",
)
async def status() -> AdminStatusResponse:
    """
    Return whether the database is currently locked or unlocked.

    Safe to call at any time — does not require the database to be open.
    """
    if AppContext.is_initialized():
        return AdminStatusResponse(
            locked=False,
            message="Database is unlocked and ready.",
        )
    return AdminStatusResponse(
        locked=True,
        message="Database is locked. POST /admin/unlock to initialize.",
    )


@router.post(
    "/unlock",
    response_model=AdminActionResponse,
    status_code=200,
    summary="Unlock (open) the database",
)
async def unlock(req: UnlockRequest) -> AdminActionResponse:
    """
    Open a database file and initialize ``AppContext``.

    - If the API is already unlocked, returns ``status="already_unlocked"``
      without touching the existing connection.  Call ``/admin/lock`` first
      if you need to switch to a different database file.
    - For encrypted databases, the ``.salt`` file must exist alongside the
      ``.db`` file (created automatically by ``DatabaseEngine.create()``).
    - A wrong password will not raise an error here; it will raise on the
      first database query (SQLCipher behaviour).
    """
    if AppContext.is_initialized():
        return AdminActionResponse(
            status="already_unlocked",
            message=(
                "Database is already initialized. "
                "Call /admin/lock first if you need to switch databases."
            ),
        )

    path = Path(req.db_path)

    try:
        if req.encrypted:
            engine = DatabaseEngine.open(path, req.password)
        else:
            engine = DatabaseEngine.open_unencrypted(f"sqlite:///{path}")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        # SQLCipher not installed, argon2 not installed, etc.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to open database: {exc}",
        ) from exc

    AppContext.initialize(engine)
    _state.set_db_path(path)

    # Seed the instrument catalogue on first unlock if the table is empty.
    from ..app import _seed_catalog_if_empty
    _seed_catalog_if_empty()

    return AdminActionResponse(
        status="unlocked",
        message=f"Database initialized successfully: {path.name}",
    )


@router.post(
    "/lock",
    response_model=AdminActionResponse,
    summary="Lock (close) the database",
)
async def lock() -> AdminActionResponse:
    """
    Dispose the database engine and clear ``AppContext``.

    Safe to call even if the database is already locked (no-op).
    After this call, all protected endpoints return HTTP 503 until
    ``/admin/unlock`` is called again.
    """
    _state.set_db_path(None)
    AppContext.lock()
    return AdminActionResponse(
        status="locked",
        message="Database has been locked and connection disposed.",
    )


@router.get(
    "/validation_report",
    summary="Deterministic validation output suite (text/json)",
    response_class=PlainTextResponse,
    response_model=None,
)
async def validation_report(
    format: Literal["text", "json"] = Query(
        "text",
        description="Output format. text is copy/paste-friendly; json is machine-readable.",
    ),
    security_id: str | None = Query(
        None,
        description="Optional security filter. Accepts security id or ticker symbol.",
    ),
    as_of: datetime | None = Query(
        None,
        description="Optional ISO datetime cutoff. Uses latest price/fx <= this timestamp.",
    ),
    limit_lots: int | None = Query(
        None,
        ge=1,
        le=5000,
        description="Optional lot output cap. If exceeded, includes top N lots by value.",
    ),
    _: None = Depends(db_required),
) -> Any:
    db_path = _state.get_db_path()
    try:
        report = ValidationReportService.generate_report(
            security_filter=security_id,
            as_of=as_of,
            limit_lots=limit_lots,
            db_path=db_path,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc

    if format == "json":
        return JSONResponse(content=report)
    return PlainTextResponse(ValidationReportService.render_text(report))
