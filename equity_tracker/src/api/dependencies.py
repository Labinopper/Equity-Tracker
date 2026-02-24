"""
Shared FastAPI dependencies.

Usage in route handlers::

    from .dependencies import db_required

    @router.get("/some/endpoint")
    async def my_endpoint(_: None = Depends(db_required)) -> ...:
        ...

``db_required`` raises HTTP 503 before the route body runs if the database
has not been unlocked.  This means every protected route gets an automatic,
consistent error response without any per-route guard code.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..app_context import AppContext


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
