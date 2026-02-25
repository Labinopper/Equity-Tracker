"""
Pydantic schemas for portfolio endpoints — requests and responses.

Response contract (established in Phase WA):
  - Monetary fields: always str (Decimal strings, never float)
  - date / datetime fields: Python objects → ISO 8601 via Pydantic v2
  - ORM objects never returned directly; factory classmethods map them

Request contract (added in Phase WB):
  - Monetary inputs use Decimal type — Pydantic v2 coerces from JSON
    strings or numbers safely.  Never accept bare float in financial inputs.
  - String-enum inputs (scheme_type, currency) are validated against
    known-good values from the ORM models module.
  - Optional fields default to None; callers omit them in JSON.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from ...db.models import VALID_SCHEME_TYPES, Lot, Security, Transaction
from ...services.portfolio_service import LotSummary, PortfolioSummary, SecuritySummary


# ---------------------------------------------------------------------------
# ── Response schemas (read, established Phase WA) ──────────────────────────
# ---------------------------------------------------------------------------

class SecuritySchema(BaseModel):
    """A tradeable security / instrument."""

    id: str
    ticker: str
    name: str
    currency: str
    isin: str | None = None
    exchange: str | None = None
    units_precision: int

    @classmethod
    def from_orm(cls, sec: Security) -> "SecuritySchema":
        return cls(
            id=sec.id,
            ticker=sec.ticker,
            name=sec.name,
            currency=sec.currency,
            isin=sec.isin,
            exchange=sec.exchange,
            units_precision=sec.units_precision,
        )


class LotSchema(BaseModel):
    """An individual acquisition lot.  Monetary fields are decimal strings."""

    id: str
    security_id: str
    scheme_type: str
    tax_year: str
    acquisition_date: date
    quantity: str
    quantity_remaining: str
    acquisition_price_gbp: str
    true_cost_per_share_gbp: str
    fmv_at_acquisition_gbp: str | None = None
    acquisition_price_original_ccy: str | None = None
    original_currency: str | None = None
    broker_currency: str | None = None
    fx_rate_at_acquisition: str | None = None
    fx_rate_source: str | None = None
    grant_id: str | None = None
    external_id: str | None = None
    broker_reference: str | None = None
    notes: str | None = None

    @classmethod
    def from_orm(cls, lot: Lot) -> "LotSchema":
        return cls(
            id=lot.id,
            security_id=lot.security_id,
            scheme_type=lot.scheme_type,
            tax_year=lot.tax_year,
            acquisition_date=lot.acquisition_date,
            quantity=lot.quantity,
            quantity_remaining=lot.quantity_remaining,
            acquisition_price_gbp=lot.acquisition_price_gbp,
            true_cost_per_share_gbp=lot.true_cost_per_share_gbp,
            fmv_at_acquisition_gbp=lot.fmv_at_acquisition_gbp,
            acquisition_price_original_ccy=lot.acquisition_price_original_ccy,
            original_currency=lot.original_currency,
            broker_currency=lot.broker_currency,
            fx_rate_at_acquisition=lot.fx_rate_at_acquisition,
            fx_rate_source=lot.fx_rate_source,
            grant_id=lot.grant_id,
            external_id=lot.external_id,
            broker_reference=lot.broker_reference,
            notes=lot.notes,
        )


class LotSummarySchema(BaseModel):
    lot: LotSchema
    cost_basis_total_gbp: str
    true_cost_total_gbp: str
    market_value_gbp: str | None = None
    market_value_native: str | None = None
    market_value_native_currency: str | None = None
    unrealised_gain_cgt_gbp: str | None = None
    unrealised_gain_economic_gbp: str | None = None
    est_cgt_on_lot_gbp: str | None = None
    est_net_proceeds_gbp: str | None = None
    sell_now_economic_gbp: str | None = None
    est_net_proceeds_reason: str | None = None
    sellability_status: str
    sellability_unlock_date: date | None = None

    @classmethod
    def from_service(cls, ls: LotSummary) -> "LotSummarySchema":
        return cls(
            lot=LotSchema.from_orm(ls.lot),
            cost_basis_total_gbp=str(ls.cost_basis_total_gbp),
            true_cost_total_gbp=str(ls.true_cost_total_gbp),
            market_value_gbp=(
                str(ls.market_value_gbp) if ls.market_value_gbp is not None else None
            ),
            market_value_native=(
                str(ls.market_value_native)
                if ls.market_value_native is not None
                else None
            ),
            market_value_native_currency=ls.market_value_native_currency,
            unrealised_gain_cgt_gbp=(
                str(ls.unrealised_gain_cgt_gbp)
                if ls.unrealised_gain_cgt_gbp is not None
                else None
            ),
            unrealised_gain_economic_gbp=(
                str(ls.unrealised_gain_economic_gbp)
                if ls.unrealised_gain_economic_gbp is not None
                else None
            ),
            est_cgt_on_lot_gbp=(
                str(ls.est_cgt_on_lot_gbp) if ls.est_cgt_on_lot_gbp is not None else None
            ),
            est_net_proceeds_gbp=(
                str(ls.est_net_proceeds_gbp)
                if ls.est_net_proceeds_gbp is not None
                else None
            ),
            sell_now_economic_gbp=(
                str(ls.sell_now_economic_gbp)
                if ls.sell_now_economic_gbp is not None
                else None
            ),
            est_net_proceeds_reason=ls.est_net_proceeds_reason,
            sellability_status=ls.sellability_status,
            sellability_unlock_date=ls.sellability_unlock_date,
        )


class SecuritySummarySchema(BaseModel):
    security: SecuritySchema
    active_lots: list[LotSummarySchema]
    total_quantity: str
    total_cost_basis_gbp: str
    total_true_cost_gbp: str
    # Phase L: live price fields (None when no price has been fetched)
    current_price_native: str | None = None
    current_price_gbp: str | None = None
    market_value_native: str | None = None
    market_value_native_currency: str | None = None
    market_value_gbp: str | None = None
    unrealised_gain_cgt_gbp: str | None = None
    unrealised_gain_economic_gbp: str | None = None
    price_as_of: date | None = None
    price_is_stale: bool = False
    price_refreshed_at: str | None = None
    fx_as_of: str | None = None
    fx_is_stale: bool = False
    est_cgt_gbp: str | None = None
    est_net_proceeds_gbp: str | None = None
    refresh_last_success_at: str | None = None
    refresh_last_error: str | None = None
    refresh_next_due_at: str | None = None

    @classmethod
    def from_service(cls, ss: SecuritySummary) -> "SecuritySummarySchema":
        return cls(
            security=SecuritySchema.from_orm(ss.security),
            active_lots=[LotSummarySchema.from_service(ls) for ls in ss.active_lots],
            total_quantity=str(ss.total_quantity),
            total_cost_basis_gbp=str(ss.total_cost_basis_gbp),
            total_true_cost_gbp=str(ss.total_true_cost_gbp),
            current_price_native=str(ss.current_price_native) if ss.current_price_native is not None else None,
            current_price_gbp=str(ss.current_price_gbp) if ss.current_price_gbp is not None else None,
            market_value_native=str(ss.market_value_native) if ss.market_value_native is not None else None,
            market_value_native_currency=ss.market_value_native_currency,
            market_value_gbp=str(ss.market_value_gbp) if ss.market_value_gbp is not None else None,
            unrealised_gain_cgt_gbp=str(ss.unrealised_gain_cgt_gbp) if ss.unrealised_gain_cgt_gbp is not None else None,
            unrealised_gain_economic_gbp=str(ss.unrealised_gain_economic_gbp) if ss.unrealised_gain_economic_gbp is not None else None,
            price_as_of=ss.price_as_of,
            price_is_stale=ss.price_is_stale,
            price_refreshed_at=ss.price_refreshed_at,
            fx_as_of=ss.fx_as_of,
            fx_is_stale=ss.fx_is_stale,
            est_cgt_gbp=str(ss.est_cgt_gbp) if ss.est_cgt_gbp is not None else None,
            est_net_proceeds_gbp=(
                str(ss.est_net_proceeds_gbp)
                if ss.est_net_proceeds_gbp is not None
                else None
            ),
            refresh_last_success_at=ss.refresh_last_success_at,
            refresh_last_error=ss.refresh_last_error,
            refresh_next_due_at=ss.refresh_next_due_at,
        )


class PortfolioSummarySchema(BaseModel):
    securities: list[SecuritySummarySchema]
    total_cost_basis_gbp: str
    total_true_cost_gbp: str
    # Phase L: total market value (None when no prices fetched yet)
    total_market_value_gbp: str | None = None
    fx_as_of: str | None = None
    fx_is_stale: bool = False
    valuation_currency: str = "GBP"
    fx_conversion_basis: str | None = None
    est_total_cgt_liability_gbp: str | None = None
    est_total_net_liquidation_gbp: str | None = None

    @classmethod
    def from_service(cls, ps: PortfolioSummary) -> "PortfolioSummarySchema":
        return cls(
            securities=[
                SecuritySummarySchema.from_service(ss) for ss in ps.securities
            ],
            total_cost_basis_gbp=str(ps.total_cost_basis_gbp),
            total_true_cost_gbp=str(ps.total_true_cost_gbp),
            total_market_value_gbp=str(ps.total_market_value_gbp) if ps.total_market_value_gbp is not None else None,
            fx_as_of=ps.fx_as_of,
            fx_is_stale=ps.fx_is_stale,
            valuation_currency=ps.valuation_currency,
            fx_conversion_basis=ps.fx_conversion_basis,
            est_total_cgt_liability_gbp=(
                str(ps.est_total_cgt_liability_gbp)
                if ps.est_total_cgt_liability_gbp is not None
                else None
            ),
            est_total_net_liquidation_gbp=(
                str(ps.est_total_net_liquidation_gbp)
                if ps.est_total_net_liquidation_gbp is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# ── Request schemas (write, added Phase WB) ────────────────────────────────
# ---------------------------------------------------------------------------

class AddSecurityRequest(BaseModel):
    """Request body for POST /portfolio/securities."""

    ticker: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=200)
    currency: str = Field(..., description="ISO 4217, e.g. 'GBP' or 'USD'")
    isin: str | None = Field(None, max_length=12)
    exchange: str | None = Field(None, max_length=20)
    units_precision: int = Field(0, ge=0, le=10)
    catalog_id: str | None = Field(
        None, description="UUID of the security_catalog entry (Phase S)."
    )
    is_manual_override: bool = Field(
        False,
        description=(
            "Set to true to bypass catalogue validation for unlisted instruments."
        ),
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3:
            raise ValueError("currency must be a 3-character ISO 4217 code (e.g. 'GBP')")
        return v

    @field_validator("ticker")
    @classmethod
    def normalise_ticker(cls, v: str) -> str:
        return v.strip().upper()

    model_config = {
        "json_schema_extra": {
            "example": {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "currency": "USD",
                "isin": "US0378331005",
                "exchange": "NASDAQ",
                "units_precision": 0,
            }
        }
    }


class AddLotRequest(BaseModel):
    """Request body for POST /portfolio/lots."""

    security_id: str
    scheme_type: str = Field(..., description=f"One of: {VALID_SCHEME_TYPES}")
    acquisition_date: date
    quantity: Decimal = Field(..., gt=0)
    acquisition_price_gbp: Decimal = Field(..., ge=0, description="CGT cost basis per share")
    true_cost_per_share_gbp: Decimal = Field(..., ge=0, description="Economic cost per share")
    fmv_at_acquisition_gbp: Decimal | None = Field(None, ge=0)
    broker_currency: str | None = Field(
        None,
        description="Optional broker holding currency for BROKERAGE/ISA lots (3-letter ISO).",
    )
    tax_year: str | None = Field(None, description="UK tax year e.g. '2024-25'; auto-derived if omitted")
    grant_id: str | None = None
    external_id: str | None = Field(None, description="Idempotency key; must be unique across all lots")
    broker_reference: str | None = None
    import_source: str | None = None
    notes: str | None = None

    @field_validator("scheme_type")
    @classmethod
    def validate_scheme_type(cls, v: str) -> str:
        if v not in VALID_SCHEME_TYPES:
            raise ValueError(
                f"scheme_type must be one of: {list(VALID_SCHEME_TYPES)}"
            )
        return v

    @field_validator("broker_currency")
    @classmethod
    def validate_broker_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise ValueError("broker_currency must be a 3-letter ISO currency code.")
        return cleaned

    model_config = {
        "json_schema_extra": {
            "example": {
                "security_id": "uuid-here",
                "scheme_type": "RSU",
                "acquisition_date": "2024-06-15",
                "quantity": "100",
                "acquisition_price_gbp": "145.32",
                "true_cost_per_share_gbp": "89.44",
                "tax_year": "2024-25",
            }
        }
    }


class EditLotRequest(BaseModel):
    """Request body for PATCH /portfolio/lots/{lot_id}."""

    acquisition_date: date
    quantity: Decimal = Field(..., gt=0)
    acquisition_price_gbp: Decimal = Field(..., ge=0)
    true_cost_per_share_gbp: Decimal = Field(..., ge=0)
    tax_year: str = Field(..., min_length=1, max_length=7)
    fmv_at_acquisition_gbp: Decimal | None = Field(None, ge=0)
    broker_currency: str | None = None
    notes: str | None = None

    @field_validator("broker_currency")
    @classmethod
    def validate_edit_broker_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise ValueError("broker_currency must be a 3-letter ISO currency code.")
        return cleaned


class SimulateDisposalRequest(BaseModel):
    """Request body for POST /portfolio/disposals/simulate (no DB write)."""

    security_id: str
    quantity: Decimal = Field(..., gt=0)
    price_per_share_gbp: Decimal = Field(..., ge=0)
    broker_fees_gbp: Decimal | None = Field(None, ge=0)
    scheme_type: str | None = Field(
        None, description="Restrict FIFO to a single scheme type (optional)"
    )
    as_of_date: date | None = Field(
        None, description="Only include lots acquired on or before this date (optional)"
    )


class CommitDisposalRequest(BaseModel):
    """Request body for POST /portfolio/disposals/commit (writes to DB)."""

    security_id: str
    quantity: Decimal = Field(..., gt=0)
    price_per_share_gbp: Decimal = Field(..., ge=0)
    transaction_date: date
    scheme_type: str | None = None
    broker_fees_gbp: Decimal | None = Field(None, ge=0)
    broker_reference: str | None = None
    import_source: str | None = None
    external_id: str | None = Field(
        None, description="Idempotency key; duplicate calls with same external_id return 409"
    )
    notes: str | None = None


# ---------------------------------------------------------------------------
# ── Response schemas for FIFO and disposal commit (added Phase WB) ─────────
# ---------------------------------------------------------------------------

class FIFOAllocationSchema(BaseModel):
    """Per-lot FIFO allocation from a simulate or commit call.  All monetary = str."""

    lot_id: str
    acquisition_date: date
    quantity_allocated: str
    cost_basis_gbp: str
    true_cost_gbp: str
    proceeds_gbp: str
    realised_gain_gbp: str
    realised_gain_economic_gbp: str


class SimulateDisposalResponse(BaseModel):
    """
    FIFO simulation result — no DB writes performed.

    Check ``is_fully_allocated`` before proceeding to commit.
    If ``shortfall`` > 0 (as a string Decimal), there are insufficient lots.
    """

    is_fully_allocated: bool
    quantity_requested: str
    quantity_sold: str
    shortfall: str
    disposal_price_gbp: str
    total_proceeds_gbp: str
    total_cost_basis_gbp: str
    total_true_cost_gbp: str
    total_realised_gain_gbp: str
    total_realised_gain_economic_gbp: str
    allocations: list[FIFOAllocationSchema]

    @classmethod
    def from_fifo_result(cls, r) -> "SimulateDisposalResponse":  # r: FIFOResult
        return cls(
            is_fully_allocated=r.is_fully_allocated,
            quantity_requested=str(r.quantity_requested),
            quantity_sold=str(r.quantity_sold),
            shortfall=str(r.shortfall),
            disposal_price_gbp=str(r.disposal_price_gbp),
            total_proceeds_gbp=str(r.total_proceeds_gbp),
            total_cost_basis_gbp=str(r.total_cost_basis_gbp),
            total_true_cost_gbp=str(r.total_true_cost_gbp),
            total_realised_gain_gbp=str(r.total_realised_gain_gbp),
            total_realised_gain_economic_gbp=str(r.total_realised_gain_economic_gbp),
            allocations=[
                FIFOAllocationSchema(
                    lot_id=a.lot_id,
                    acquisition_date=a.acquisition_date,
                    quantity_allocated=str(a.quantity_allocated),
                    cost_basis_gbp=str(a.cost_basis_gbp),
                    true_cost_gbp=str(a.true_cost_gbp),
                    proceeds_gbp=str(a.proceeds_gbp),
                    realised_gain_gbp=str(a.realised_gain_gbp),
                    realised_gain_economic_gbp=str(a.realised_gain_economic_gbp),
                )
                for a in r.allocations
            ],
        )


class TransactionSchema(BaseModel):
    """A persisted disposal transaction."""

    id: str
    security_id: str
    transaction_type: str
    transaction_date: date
    quantity: str
    price_per_share_gbp: str
    total_proceeds_gbp: str
    broker_fees_gbp: str | None = None
    broker_reference: str | None = None
    external_id: str | None = None
    notes: str | None = None

    @classmethod
    def from_orm(cls, tx: Transaction) -> "TransactionSchema":
        return cls(
            id=tx.id,
            security_id=tx.security_id,
            transaction_type=tx.transaction_type,
            transaction_date=tx.transaction_date,
            quantity=tx.quantity,
            price_per_share_gbp=tx.price_per_share_gbp,
            total_proceeds_gbp=tx.total_proceeds_gbp,
            broker_fees_gbp=tx.broker_fees_gbp,
            broker_reference=tx.broker_reference,
            external_id=tx.external_id,
            notes=tx.notes,
        )


class CommitDisposalResponse(BaseModel):
    """Confirmed disposal — transaction record plus per-lot allocation detail."""

    transaction: TransactionSchema
    lot_disposals: list[dict]   # LotDisposalSchema fields inlined to avoid circular import

    @classmethod
    def from_service(cls, transaction: Transaction, lot_disposals) -> "CommitDisposalResponse":
        return cls(
            transaction=TransactionSchema.from_orm(transaction),
            lot_disposals=[
                {
                    "id": d.id,
                    "lot_id": d.lot_id,
                    "quantity_allocated": d.quantity_allocated,
                    "cost_basis_gbp": d.cost_basis_gbp,
                    "true_cost_gbp": d.true_cost_gbp,
                    "proceeds_gbp": d.proceeds_gbp,
                    "realised_gain_gbp": d.realised_gain_gbp,
                    "realised_gain_economic_gbp": d.realised_gain_economic_gbp,
                }
                for d in lot_disposals
            ],
        )


class EditLotResponse(BaseModel):
    """Lot edit result with optional audit reference."""

    lot: LotSchema
    audit_id: str | None = None


class TransferLotRequest(BaseModel):
    """Request body for POST /portfolio/lots/{lot_id}/transfer."""

    destination_scheme: str = Field(default="BROKERAGE")
    quantity: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional quantity to transfer. ESPP supports partial FIFO transfer "
            "with whole-share constraints; RSU/ESPP_PLUS require full lot transfer."
        ),
    )
    broker_currency: str | None = Field(
        default=None,
        description="Optional destination broker holding currency (3-letter ISO).",
    )
    notes: str | None = None

    @field_validator("destination_scheme")
    @classmethod
    def validate_destination_scheme(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if cleaned != "BROKERAGE":
            raise ValueError(
                "destination_scheme must be BROKERAGE. "
                "ISA requires disposal then Add Lot."
            )
        return cleaned

    @field_validator("broker_currency")
    @classmethod
    def validate_transfer_broker_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise ValueError("broker_currency must be a 3-letter ISO currency code.")
        return cleaned


class TransferLotResponse(BaseModel):
    """Transfer result with mandatory audit reference."""

    lot: LotSchema
    audit_id: str
