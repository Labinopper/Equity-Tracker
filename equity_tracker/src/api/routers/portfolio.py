"""
Portfolio router — lot explorer reads and all write operations.

Phase WA endpoints (read)
──────────────────────────
  GET  /portfolio/summary                 Full portfolio tree

Phase WB endpoints (write)
───────────────────────────
  POST /portfolio/securities              Create a new security
  POST /portfolio/lots                    Record an acquisition lot
  POST /portfolio/disposals/simulate      FIFO preview (no DB write)
  POST /portfolio/disposals/commit        Commit a disposal atomically

Error mapping
─────────────
  ValueError from service layer  → HTTP 422 (validation / business logic)
  IntegrityError (UNIQUE violation)      → HTTP 409 (caught by app-level handler)
  AppContextError (DB not initialised)   → HTTP 503 (caught by db_required dep)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...services.portfolio_service import PortfolioService
from ...settings import AppSettings
from .. import _state
from ..dependencies import db_required, session_required
from ..schemas.portfolio import (
    AddLotRequest,
    AddSecurityRequest,
    CommitDisposalRequest,
    CommitDisposalResponse,
    EditLotRequest,
    EditLotResponse,
    LotSchema,
    PortfolioSummarySchema,
    SecuritySchema,
    SimulateDisposalRequest,
    SimulateDisposalResponse,
    TransferLotRequest,
    TransferLotResponse,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"], dependencies=[Depends(session_required)])


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@router.get(
    "/summary",
    response_model=PortfolioSummarySchema,
    summary="Full portfolio summary",
)
async def portfolio_summary(
    _: None = Depends(db_required),
) -> PortfolioSummarySchema:
    """
    All securities and active lots with CGT and economic cost totals.

    Monetary values are decimal strings.  Dates are ISO 8601.
    """
    result = PortfolioService.get_portfolio_summary()
    return PortfolioSummarySchema.from_service(result)


# ---------------------------------------------------------------------------
# Write — securities
# ---------------------------------------------------------------------------

@router.post(
    "/securities",
    response_model=SecuritySchema,
    status_code=201,
    summary="Create a new security",
)
async def add_security(
    req: AddSecurityRequest,
    _: None = Depends(db_required),
) -> SecuritySchema:
    """
    Create and persist a new Security with an audit INSERT entry.

    Returns HTTP 409 if a security with the same ticker already exists
    (unique constraint).  Ticker is upper-cased automatically.
    """
    try:
        security = PortfolioService.add_security(
            ticker=req.ticker,
            name=req.name,
            currency=req.currency,
            isin=req.isin,
            exchange=req.exchange,
            units_precision=req.units_precision,
            dividend_reminder_date=req.dividend_reminder_date,
            catalog_id=req.catalog_id,
            is_manual_override=req.is_manual_override,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    return SecuritySchema.from_orm(security)


# ---------------------------------------------------------------------------
# Write — lots
# ---------------------------------------------------------------------------

@router.post(
    "/lots",
    response_model=LotSchema,
    status_code=201,
    summary="Record an acquisition lot",
)
async def add_lot(
    req: AddLotRequest,
    _: None = Depends(db_required),
) -> LotSchema:
    """
    Create and persist a new acquisition Lot with an audit INSERT entry.

    ``tax_year`` is auto-derived from ``acquisition_date`` if omitted.
    ``external_id`` must be globally unique; pass the same value for a
    duplicate import to receive HTTP 409 instead of a double-entry.
    """
    try:
        lot = PortfolioService.add_lot(
            security_id=req.security_id,
            scheme_type=req.scheme_type,
            acquisition_date=req.acquisition_date,
            quantity=req.quantity,
            acquisition_price_gbp=req.acquisition_price_gbp,
            true_cost_per_share_gbp=req.true_cost_per_share_gbp,
            fmv_at_acquisition_gbp=req.fmv_at_acquisition_gbp,
            broker_currency=req.broker_currency,
            tax_year=req.tax_year,
            grant_id=req.grant_id,
            external_id=req.external_id,
            broker_reference=req.broker_reference,
            import_source=req.import_source,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    return LotSchema.from_orm(lot)


@router.patch(
    "/lots/{lot_id}",
    response_model=EditLotResponse,
    summary="Edit correction-safe lot fields",
)
async def edit_lot(
    lot_id: str,
    req: EditLotRequest,
    _: None = Depends(db_required),
) -> EditLotResponse:
    """Update a lot and emit an audit UPDATE entry when changes are applied."""
    try:
        lot, audit_id = PortfolioService.edit_lot(
            lot_id=lot_id,
            acquisition_date=req.acquisition_date,
            quantity=req.quantity,
            acquisition_price_gbp=req.acquisition_price_gbp,
            true_cost_per_share_gbp=req.true_cost_per_share_gbp,
            tax_year=req.tax_year,
            fmv_at_acquisition_gbp=req.fmv_at_acquisition_gbp,
            broker_currency=req.broker_currency,
            notes=req.notes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    return EditLotResponse(lot=LotSchema.from_orm(lot), audit_id=audit_id)


@router.post(
    "/lots/{lot_id}/transfer",
    response_model=TransferLotResponse,
    summary="Transfer lot into BROKERAGE custody (non-disposal)",
)
async def transfer_lot(
    lot_id: str,
    req: TransferLotRequest,
    _: None = Depends(db_required),
) -> TransferLotResponse:
    try:
        db_path = _state.get_db_path()
        settings = AppSettings.load(db_path) if db_path else None
        lot, audit_id = PortfolioService.transfer_lot_to_brokerage(
            lot_id=lot_id,
            notes=req.notes,
            settings=settings,
            quantity=req.quantity,
            destination_broker_currency=req.broker_currency,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    return TransferLotResponse(lot=LotSchema.from_orm(lot), audit_id=audit_id)


# ---------------------------------------------------------------------------
# Write — disposals
# ---------------------------------------------------------------------------

@router.post(
    "/disposals/simulate",
    response_model=SimulateDisposalResponse,
    summary="FIFO disposal preview (no DB write)",
)
async def simulate_disposal(
    req: SimulateDisposalRequest,
    _: None = Depends(db_required),
) -> SimulateDisposalResponse:
    """
    Run FIFO allocation without persisting anything.

    Check ``is_fully_allocated`` in the response.  If ``shortfall > 0``
    there are insufficient lots; do not call commit with the same quantity.
    """
    try:
        result = PortfolioService.simulate_disposal(
            security_id=req.security_id,
            quantity=req.quantity,
            price_per_share_gbp=req.price_per_share_gbp,
            broker_fees_gbp=req.broker_fees_gbp,
            scheme_type=req.scheme_type,
            as_of_date=req.as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    return SimulateDisposalResponse.from_fifo_result(result)


@router.post(
    "/disposals/commit",
    response_model=CommitDisposalResponse,
    status_code=201,
    summary="Commit a disposal atomically",
)
async def commit_disposal(
    req: CommitDisposalRequest,
    _: None = Depends(db_required),
) -> CommitDisposalResponse:
    """
    Run FIFO allocation and persist atomically.

    Creates a DISPOSAL Transaction and per-lot LotDisposal records,
    reduces lot quantities, and writes audit entries — all in one session.

    Raises HTTP 422 if insufficient lots exist (shortfall > 0).
    Raises HTTP 409 if ``external_id`` collides with an existing transaction.
    """
    try:
        transaction, disposals = PortfolioService.commit_disposal(
            security_id=req.security_id,
            quantity=req.quantity,
            price_per_share_gbp=req.price_per_share_gbp,
            transaction_date=req.transaction_date,
            scheme_type=req.scheme_type,
            broker_fees_gbp=req.broker_fees_gbp,
            broker_reference=req.broker_reference,
            import_source=req.import_source,
            external_id=req.external_id,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    return CommitDisposalResponse.from_service(transaction, disposals)
