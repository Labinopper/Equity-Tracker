"""
CashLedgerService - deterministic multi-currency cash ledger sidecar.

Scope:
- Append-only cash transactions by container and currency.
- Deterministic balance views per container/currency.
- GBP-only ISA transfer workflow with mandatory FX conversion metadata for
  non-GBP sources.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

_MONEY_Q = Decimal("0.01")
_FX_Q = Decimal("0.0001")

CONTAINER_BROKER = "BROKER"
CONTAINER_ISA = "ISA"
CONTAINER_BANK = "BANK"
VALID_CONTAINERS = frozenset({CONTAINER_BROKER, CONTAINER_ISA, CONTAINER_BANK})

ENTRY_TYPE_MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
ENTRY_TYPE_FX_CONVERSION_OUT = "FX_CONVERSION_OUT"
ENTRY_TYPE_FX_CONVERSION_IN = "FX_CONVERSION_IN"
ENTRY_TYPE_FX_FEE = "FX_FEE"
ENTRY_TYPE_ISA_TRANSFER_OUT = "ISA_TRANSFER_OUT"
ENTRY_TYPE_ISA_TRANSFER_IN = "ISA_TRANSFER_IN"


def _ledger_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".cash_ledger.json")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_fx(value: Decimal) -> Decimal:
    return value.quantize(_FX_Q, rounding=ROUND_HALF_UP)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _normalize_container(value: object) -> str:
    container = str(value or "").strip().upper()
    if container not in VALID_CONTAINERS:
        raise ValueError("Container must be one of BROKER, ISA, BANK.")
    return container


def _normalize_currency(value: object) -> str:
    currency = str(value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Currency must be a 3-letter ISO code.")
    return currency


def _load_payload(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": []}

    if isinstance(data, list):
        return {"version": 1, "entries": data}
    if isinstance(data, dict):
        entries = data.get("entries", [])
        if isinstance(entries, list):
            return {"version": int(data.get("version", 1)), "entries": entries}
    return {"version": 1, "entries": []}


def _save_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _entry_sort_key(entry: dict) -> tuple[str, str, str]:
    return (
        str(entry.get("entry_date") or ""),
        str(entry.get("created_at_utc") or ""),
        str(entry.get("entry_id") or ""),
    )


def _entry_amount(entry: dict) -> Decimal:
    return _safe_decimal(entry.get("amount"))


def _balance_map_from_entries(entries: list[dict]) -> dict[str, dict[str, Decimal]]:
    balances: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for entry in entries:
        container = str(entry.get("container") or "").upper()
        currency = str(entry.get("currency") or "").upper()
        if not container or not currency:
            continue
        balances[container][currency] += _entry_amount(entry)
    return balances


def _apply_with_non_negative_check(
    *,
    balances: dict[str, dict[str, Decimal]],
    entry: dict,
) -> None:
    container = _normalize_container(entry.get("container"))
    currency = _normalize_currency(entry.get("currency"))
    amount = _q_money(_entry_amount(entry))
    next_balance = _q_money(balances[container][currency] + amount)
    if next_balance < Decimal("0"):
        raise ValueError(
            f"Insufficient cash balance: {container} {currency} would be {next_balance}."
        )
    balances[container][currency] = next_balance


class CashLedgerService:
    @staticmethod
    def load_entries(db_path: Path | None) -> list[dict]:
        if db_path is None:
            return []
        payload = _load_payload(_ledger_path(db_path))
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            return []
        clean = [dict(entry) for entry in entries if isinstance(entry, dict)]
        clean.sort(key=_entry_sort_key)
        return clean

    @staticmethod
    def save_entries(db_path: Path | None, entries: list[dict]) -> None:
        if db_path is None:
            raise ValueError("Database path is required to save cash ledger.")
        payload = {"version": 1, "entries": entries}
        _save_payload(_ledger_path(db_path), payload)

    @staticmethod
    def record_entry(
        *,
        db_path: Path | None,
        entry_date: date_type,
        container: str,
        currency: str,
        amount: Decimal,
        entry_type: str = ENTRY_TYPE_MANUAL_ADJUSTMENT,
        source: str = "manual",
        notes: str | None = None,
        metadata: dict | None = None,
        group_id: str | None = None,
    ) -> dict:
        if db_path is None:
            raise ValueError("Database path is required.")

        amount_q = _q_money(amount)
        if amount_q == Decimal("0"):
            raise ValueError("Cash amount must be non-zero.")

        entry = {
            "entry_id": uuid4().hex,
            "group_id": group_id or uuid4().hex,
            "entry_date": entry_date.isoformat(),
            "container": _normalize_container(container),
            "currency": _normalize_currency(currency),
            "amount": str(amount_q),
            "entry_type": str(entry_type or ENTRY_TYPE_MANUAL_ADJUSTMENT).strip().upper(),
            "source": str(source or "manual").strip(),
            "notes": (notes or "").strip() or None,
            "metadata": metadata or {},
            "created_at_utc": _now_utc_iso(),
        }

        entries = CashLedgerService.load_entries(db_path)
        balances = _balance_map_from_entries(entries)
        _apply_with_non_negative_check(balances=balances, entry=entry)
        entries.append(entry)
        entries.sort(key=_entry_sort_key)
        CashLedgerService.save_entries(db_path, entries)
        return entry

    @staticmethod
    def balances(db_path: Path | None) -> dict[str, dict[str, Decimal]]:
        return _balance_map_from_entries(CashLedgerService.load_entries(db_path))

    @staticmethod
    def balance_for(
        *,
        db_path: Path | None,
        container: str,
        currency: str,
    ) -> Decimal:
        balances = CashLedgerService.balances(db_path)
        return _q_money(
            balances[_normalize_container(container)][_normalize_currency(currency)]
        )

    @staticmethod
    def dashboard(
        *,
        db_path: Path | None,
        entry_limit: int = 200,
    ) -> dict:
        entries = CashLedgerService.load_entries(db_path)
        balances = _balance_map_from_entries(entries)

        balance_rows: list[dict] = []
        totals_by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for container in sorted(balances.keys()):
            for currency in sorted(balances[container].keys()):
                bal = _q_money(balances[container][currency])
                if bal == Decimal("0"):
                    continue
                balance_rows.append(
                    {
                        "container": container,
                        "currency": currency,
                        "balance": str(bal),
                    }
                )
                totals_by_currency[currency] = _q_money(
                    totals_by_currency[currency] + bal
                )

        totals_rows = [
            {"currency": currency, "balance": str(_q_money(balance))}
            for currency, balance in sorted(totals_by_currency.items(), key=lambda kv: kv[0])
        ]

        recent_entries = list(reversed(entries))[: max(1, entry_limit)]
        return {
            "balances": balance_rows,
            "totals_by_currency": totals_rows,
            "entries": recent_entries,
            "entry_count": len(entries),
        }

    @staticmethod
    def create_isa_transfer(
        *,
        db_path: Path | None,
        entry_date: date_type,
        source_container: str,
        source_currency: str,
        source_amount: Decimal,
        fx_rate: Decimal | None = None,
        fx_fee_gbp: Decimal | None = None,
        fx_source: str | None = None,
        notes: str | None = None,
    ) -> dict:
        if db_path is None:
            raise ValueError("Database path is required.")

        container = _normalize_container(source_container)
        if container == CONTAINER_ISA:
            raise ValueError("ISA transfer source container cannot be ISA.")

        currency = _normalize_currency(source_currency)
        amount = _q_money(source_amount)
        if amount <= Decimal("0"):
            raise ValueError("Transfer amount must be greater than zero.")

        fee = _q_money(fx_fee_gbp or Decimal("0"))
        if fee < Decimal("0"):
            raise ValueError("FX fee cannot be negative.")

        entries_existing = CashLedgerService.load_entries(db_path)
        balances = _balance_map_from_entries(entries_existing)
        group_id = uuid4().hex
        created_at = _now_utc_iso()

        planned_entries: list[dict] = []

        def _entry(
            *,
            container_code: str,
            ccy: str,
            amt: Decimal,
            entry_type: str,
            metadata: dict | None = None,
        ) -> dict:
            return {
                "entry_id": uuid4().hex,
                "group_id": group_id,
                "entry_date": entry_date.isoformat(),
                "container": container_code,
                "currency": ccy,
                "amount": str(_q_money(amt)),
                "entry_type": entry_type,
                "source": "isa_transfer_workflow",
                "notes": (notes or "").strip() or None,
                "metadata": metadata or {},
                "created_at_utc": created_at,
            }

        if currency == "GBP":
            planned_entries.append(
                _entry(
                    container_code=container,
                    ccy="GBP",
                    amt=-amount,
                    entry_type=ENTRY_TYPE_ISA_TRANSFER_OUT,
                )
            )
            planned_entries.append(
                _entry(
                    container_code=CONTAINER_ISA,
                    ccy="GBP",
                    amt=amount,
                    entry_type=ENTRY_TYPE_ISA_TRANSFER_IN,
                )
            )
            converted_gbp = amount
        else:
            if fx_rate is None or fx_rate <= Decimal("0"):
                raise ValueError("Non-GBP ISA transfer requires a positive FX rate.")
            fx_source_clean = (fx_source or "").strip()
            if not fx_source_clean:
                raise ValueError("Non-GBP ISA transfer requires FX source provenance.")

            gross_gbp = _q_money(amount * fx_rate)
            net_gbp = _q_money(gross_gbp - fee)
            if net_gbp <= Decimal("0"):
                raise ValueError("FX fee must be less than converted GBP amount.")

            fx_meta = {
                "fx_rate": str(_q_fx(fx_rate)),
                "fx_source": fx_source_clean,
                "source_currency_amount": str(amount),
                "gross_converted_gbp": str(gross_gbp),
                "fx_fee_gbp": str(fee),
            }
            planned_entries.append(
                _entry(
                    container_code=container,
                    ccy=currency,
                    amt=-amount,
                    entry_type=ENTRY_TYPE_FX_CONVERSION_OUT,
                    metadata=fx_meta,
                )
            )
            planned_entries.append(
                _entry(
                    container_code=container,
                    ccy="GBP",
                    amt=gross_gbp,
                    entry_type=ENTRY_TYPE_FX_CONVERSION_IN,
                    metadata=fx_meta,
                )
            )
            if fee > Decimal("0"):
                planned_entries.append(
                    _entry(
                        container_code=container,
                        ccy="GBP",
                        amt=-fee,
                        entry_type=ENTRY_TYPE_FX_FEE,
                        metadata=fx_meta,
                    )
                )
            planned_entries.append(
                _entry(
                    container_code=container,
                    ccy="GBP",
                    amt=-net_gbp,
                    entry_type=ENTRY_TYPE_ISA_TRANSFER_OUT,
                    metadata=fx_meta,
                )
            )
            planned_entries.append(
                _entry(
                    container_code=CONTAINER_ISA,
                    ccy="GBP",
                    amt=net_gbp,
                    entry_type=ENTRY_TYPE_ISA_TRANSFER_IN,
                    metadata=fx_meta,
                )
            )
            converted_gbp = net_gbp

        for entry in planned_entries:
            _apply_with_non_negative_check(balances=balances, entry=entry)

        all_entries = entries_existing + planned_entries
        all_entries.sort(key=_entry_sort_key)
        CashLedgerService.save_entries(db_path, all_entries)
        return {
            "group_id": group_id,
            "entry_count": len(planned_entries),
            "transferred_gbp": str(_q_money(converted_gbp)),
        }
