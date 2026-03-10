"""
DividendService - additive dividend workflow and dashboard payloads.

Scope:
- Dividend entry creation.
- Trailing/forecast totals.
- Tax-year dividend tax estimation and net-return view.

No changes to existing portfolio/tax engines.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from ..app_context import AppContext
from ..core.tax_engine import calculate_dividend_tax, get_bands, tax_year_for_date
from ..core.tax_engine.income_tax import personal_allowance
from ..db.models import LotDisposal, Transaction
from ..db.repository import (
    DividendEntryRepository,
    DividendReferenceEventRepository,
    LotTransferEventRepository,
    LotRepository,
    SecurityRepository,
)
from ..settings import AppSettings
from .fx_service import FxService
from .portfolio_service import PortfolioService
from .twelve_data_dividend_service import TwelveDataDividendService

_GBP_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_FX_Q = Decimal("0.000001")
_VALID_TREATMENTS = frozenset({"TAXABLE", "ISA_EXEMPT"})
_GBP = "GBP"
_IBKR_META_PREFIX = "IBKR_META:"
_LEGACY_TRANSFER_LINE_RE = re.compile(
    r"Transferred\s+(?P<qty>[0-9]+(?:\.[0-9]+)?)\s+shares\s+to\s+BROKERAGE\s+"
    r"\(FIFO\s+from\s+ESPP\s+source\s+lot\s+(?P<source_lot_id>[0-9a-f\-]{36})\s+on\s+"
    r"(?P<transfer_date>\d{4}-\d{2}-\d{2})\)\.",
    re.IGNORECASE,
)


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(_q_money(value))


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _q_fx(value: Decimal) -> Decimal:
    return value.quantize(_FX_Q, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _to_decimal_or_zero(value: object) -> Decimal:
    parsed = _to_decimal(value)
    return parsed if parsed is not None else Decimal("0")


def _decimal_to_plain_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _quantity_str(value: Decimal) -> str:
    plain = format(value, "f")
    trimmed = plain.rstrip("0").rstrip(".")
    return trimmed or "0"


def _append_ibkr_meta(notes: str | None, metadata: dict[str, object] | None) -> str | None:
    compact: dict[str, str] = {}
    for key, raw_value in (metadata or {}).items():
        if raw_value is None:
            continue
        if isinstance(raw_value, str):
            cleaned = raw_value.strip()
            if not cleaned:
                continue
            compact[key] = cleaned
            continue
        if isinstance(raw_value, Decimal):
            compact[key] = _decimal_to_plain_str(raw_value) or ""
            continue
        compact[key] = str(raw_value)

    clean_notes = (notes or "").strip()
    if not compact:
        return clean_notes or None

    payload = f"{_IBKR_META_PREFIX}{json.dumps(compact, sort_keys=True, separators=(',', ':'))}"
    if clean_notes:
        return f"{payload}\n{clean_notes}"
    return payload


def _extract_ibkr_meta(notes: str | None) -> tuple[dict[str, object], str | None]:
    if notes is None:
        return {}, None
    text = notes.strip()
    if not text:
        return {}, None
    lines = text.splitlines()
    if not lines:
        return {}, None
    header = lines[0].strip()
    if not header.startswith(_IBKR_META_PREFIX):
        return {}, text

    raw_payload = header[len(_IBKR_META_PREFIX) :].strip()
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}, text
    if not isinstance(parsed, dict):
        parsed = {}

    body = "\n".join(lines[1:]).strip()
    return parsed, (body or None)


def _resolve_cash_components(
    *,
    amount_gbp: Decimal,
    amount_original: Decimal,
    fx_rate_to_gbp: Decimal | None,
    ib_meta: dict[str, object],
) -> dict[str, Decimal]:
    tax_withheld_original = _q_money(
        max(Decimal("0"), _to_decimal_or_zero(ib_meta.get("tax_withheld_original_ccy")))
    )
    fee_original = _q_money(max(Decimal("0"), _to_decimal_or_zero(ib_meta.get("fee_original_ccy"))))

    gross_original = _to_decimal(ib_meta.get("gross_amount_original_ccy"))
    if gross_original is None or gross_original <= Decimal("0"):
        gross_original = amount_original
    gross_original = _q_money(gross_original)

    net_original = _to_decimal(ib_meta.get("net_amount_original_ccy"))
    if net_original is None or net_original <= Decimal("0"):
        derived_net = _q_money(gross_original - tax_withheld_original - fee_original)
        net_original = derived_net if derived_net > Decimal("0") else gross_original
    net_original = _q_money(net_original)

    resolved_fx = fx_rate_to_gbp
    if resolved_fx is None:
        if amount_original > Decimal("0"):
            resolved_fx = _q_fx(amount_gbp / amount_original)
        else:
            resolved_fx = Decimal("1.000000")
    resolved_fx = _q_fx(resolved_fx)

    gross_gbp = _q_money(gross_original * resolved_fx)
    net_gbp = _q_money(net_original * resolved_fx)
    tax_withheld_gbp = _q_money(tax_withheld_original * resolved_fx)
    fee_gbp = _q_money(fee_original * resolved_fx)

    return {
        "gross_original": gross_original,
        "net_original": net_original,
        "tax_withheld_original": tax_withheld_original,
        "fee_original": fee_original,
        "gross_gbp": gross_gbp,
        "net_gbp": net_gbp,
        "tax_withheld_gbp": tax_withheld_gbp,
        "fee_gbp": fee_gbp,
    }


def _auto_fx_rate_to_gbp(
    *,
    from_currency: str,
    dividend_date: date,
) -> tuple[Decimal, str]:
    """
    Resolve a GBP conversion rate for a dividend date.

    Priority:
    1) yfinance historical daily close on/before dividend_date.
    2) FxService live/provider fallback.
    """
    symbol = f"{from_currency}GBP=X"
    try:
        import yfinance as yf  # noqa: PLC0415

        hist = yf.Ticker(symbol).history(
            start=(dividend_date - timedelta(days=7)).isoformat(),
            end=(dividend_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        if hist is not None and not hist.empty and "Close" in hist.columns:
            close_series = hist["Close"].dropna()
            if not close_series.empty:
                dated_rows: list[tuple[date, Decimal]] = []
                for idx, close_value in close_series.items():
                    try:
                        quote_date = idx.date()
                    except Exception:
                        continue
                    try:
                        quote_rate = Decimal(str(close_value))
                    except (InvalidOperation, TypeError, ValueError):
                        continue
                    if quote_rate > Decimal("0"):
                        dated_rows.append((quote_date, quote_rate))
                dated_rows.sort(key=lambda row: row[0])
                eligible = [row for row in dated_rows if row[0] <= dividend_date]
                chosen = eligible[-1] if eligible else dated_rows[-1] if dated_rows else None
                if chosen is not None:
                    return _q_fx(chosen[1]), f"auto_yfinance:{chosen[0].isoformat()}"
    except Exception:
        pass

    try:
        quote = FxService.get_rate(from_currency, "GBP")
        return _q_fx(quote.rate), f"auto_{quote.source}:{quote.as_of or dividend_date.isoformat()}"
    except Exception as exc:
        raise ValueError(
            "Could not auto-resolve FX rate for "
            f"{from_currency}->GBP on {dividend_date.isoformat()}. "
            "Provide fx_rate_to_gbp or amount_gbp."
        ) from exc


def _normalize_currency(value: str | None) -> str:
    cleaned = (value or "").strip().upper()
    if not cleaned:
        return _GBP
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise ValueError("original_currency must be a 3-letter ISO currency code.")
    return cleaned


def _is_transfer_shadow_lot(lot: Any) -> bool:
    external_id = str(getattr(lot, "external_id", None) or "").strip()
    return external_id.startswith("transfer-origin-lot:")


def _ensure_transfer_event_backfill_for_security(security_id: str) -> None:
    with AppContext.write_session() as sess:
        lot_repo = LotRepository(sess)
        transfer_repo = LotTransferEventRepository(sess)
        lots = lot_repo.get_all_lots_for_security(security_id)
        if not lots:
            return
        existing_events = transfer_repo.list_for_security(security_id)
        existing_keys = {
            (
                str(event.source_lot_id),
                str(event.destination_lot_id or ""),
                str(event.source_scheme or "").strip().upper(),
                str(event.destination_scheme or "").strip().upper(),
                event.transfer_date,
                str(event.quantity),
            )
            for event in existing_events
        }

        for lot in lots:
            if not _is_transfer_shadow_lot(lot):
                continue
            notes = str(getattr(lot, "notes", None) or "")
            if not notes.strip():
                continue
            for index, match in enumerate(_LEGACY_TRANSFER_LINE_RE.finditer(notes), start=1):
                source_lot_id = match.group("source_lot_id").strip()
                try:
                    qty = Decimal(match.group("qty"))
                    transfer_date = date.fromisoformat(match.group("transfer_date"))
                except (InvalidOperation, ValueError):
                    continue
                dedupe_key = (
                    source_lot_id,
                    str(lot.id),
                    "ESPP",
                    "BROKERAGE",
                    transfer_date,
                    str(qty),
                )
                if dedupe_key in existing_keys:
                    continue
                external_id = (
                    f"legacy-transfer-backfill:{lot.id}:{source_lot_id}:"
                    f"{transfer_date.isoformat()}:{index}:{str(qty)}"
                )
                if transfer_repo.get_by_external_id(external_id) is not None:
                    continue
                transfer_repo.add(
                    security_id=security_id,
                    source_lot_id=source_lot_id,
                    destination_lot_id=lot.id,
                    source_scheme="ESPP",
                    destination_scheme="BROKERAGE",
                    transfer_date=transfer_date,
                    quantity=qty,
                    source="legacy_note_backfill",
                    external_id=external_id,
                    notes=match.group(0).strip(),
                )
                existing_keys.add(dedupe_key)


def _eligible_quantity_on_ex_date(
    *,
    security_id: str,
    ex_dividend_date: date,
    holding_scope: str | None = None,
) -> Decimal:
    quantities = _eligible_quantities_by_holding_bucket_on_ex_date(
        security_id=security_id,
        ex_dividend_date=ex_dividend_date,
    )
    normalized_scope = (holding_scope or "").strip().upper()
    if not normalized_scope:
        return sum(quantities.values(), Decimal("0"))
    return quantities.get(normalized_scope, Decimal("0"))


def _holding_bucket_for_lot(lot: Any) -> str:
    scheme = str(getattr(lot, "scheme_type", "") or "").strip().upper()
    return scheme or "UNKNOWN"


def _holding_bucket_label(bucket: str) -> str:
    normalized = (bucket or "").strip().upper()
    if normalized == "ESPP_PLUS":
        return "ESPP+"
    if normalized == "ESPP":
        return "ESPP"
    if normalized == "BROKERAGE":
        return "Brokerage"
    if normalized == "ISA":
        return "ISA"
    if normalized == "RSU":
        return "RSU"
    if not normalized:
        return "Unknown"
    return normalized.replace("_", " ").title()


def _holding_bucket_tax_treatment(bucket: str) -> str:
    return "ISA_EXEMPT" if (bucket or "").strip().upper() == "ISA" else "TAXABLE"


def _holding_bucket_lot_group(bucket: str) -> str:
    normalized = (bucket or "").strip().upper()
    if not normalized:
        raise ValueError("holding bucket is required.")
    return f"SCHEME:{normalized}"


def _eligible_quantities_by_holding_bucket_on_ex_date(
    *,
    security_id: str,
    ex_dividend_date: date,
) -> dict[str, Decimal]:
    _ensure_transfer_event_backfill_for_security(security_id)

    with AppContext.read_session() as sess:
        lot_repo = LotRepository(sess)
        transfer_repo = LotTransferEventRepository(sess)
        lots = lot_repo.get_all_lots_for_security(security_id)
        if not lots:
            return {}

        lot_ids = [lot.id for lot in lots]
        transfer_events = transfer_repo.list_for_security(security_id)
        disposal_events_by_lot: dict[str, list[tuple[date, Decimal]]] = {}
        transfer_events_by_source_lot: dict[str, list[Any]] = {}
        transfer_events_by_destination_lot: dict[str, list[Any]] = {}
        if lot_ids:
            rows = sess.execute(
                select(
                    LotDisposal.lot_id,
                    LotDisposal.quantity_allocated,
                    Transaction.transaction_date,
                )
                .join(Transaction, LotDisposal.transaction_id == Transaction.id)
                .where(LotDisposal.lot_id.in_(lot_ids))
            ).all()
            for lot_id, quantity_allocated, transaction_date in rows:
                try:
                    qty = Decimal(str(quantity_allocated))
                except (InvalidOperation, TypeError, ValueError):
                    continue
                if qty <= Decimal("0"):
                    continue
                disposal_events_by_lot.setdefault(str(lot_id), []).append((transaction_date, qty))
        for event in transfer_events:
            transfer_events_by_source_lot.setdefault(event.source_lot_id, []).append(event)
            if event.destination_lot_id:
                transfer_events_by_destination_lot.setdefault(event.destination_lot_id, []).append(event)

    eligible_by_bucket: dict[str, Decimal] = {}
    for lot in lots:
        if lot.acquisition_date >= ex_dividend_date:
            continue
        current_qty = _to_decimal(lot.quantity_remaining)
        if current_qty is None:
            continue
        reconstructed_qty = current_qty
        for transaction_date, qty in disposal_events_by_lot.get(lot.id, []):
            if transaction_date > ex_dividend_date:
                reconstructed_qty += qty
        for event in transfer_events_by_source_lot.get(lot.id, []):
            event_qty = _to_decimal(event.quantity)
            if event_qty is None or event_qty <= Decimal("0"):
                continue
            if event.transfer_date > ex_dividend_date:
                reconstructed_qty += event_qty
        for event in transfer_events_by_destination_lot.get(lot.id, []):
            if event.destination_lot_id == event.source_lot_id:
                continue
            event_qty = _to_decimal(event.quantity)
            if event_qty is None or event_qty <= Decimal("0"):
                continue
            if event.transfer_date > ex_dividend_date:
                reconstructed_qty -= event_qty
        if reconstructed_qty <= Decimal("0"):
            continue
        bucket = _holding_bucket_for_lot(lot)
        self_transfer_events = sorted(
            (
                event
                for event in transfer_events_by_source_lot.get(lot.id, [])
                if event.destination_lot_id == lot.id
            ),
            key=lambda event: (event.transfer_date, event.created_at, event.id),
        )
        if self_transfer_events:
            bucket = self_transfer_events[0].source_scheme
            for event in self_transfer_events:
                if event.transfer_date <= ex_dividend_date:
                    bucket = event.destination_scheme
                else:
                    break
        eligible_by_bucket[bucket] = eligible_by_bucket.get(bucket, Decimal("0")) + reconstructed_qty

    return {
        bucket: qty
        for bucket, qty in eligible_by_bucket.items()
        if qty > Decimal("0")
    }


def _build_actual_dividend_match_index(entry_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in entry_rows:
        security_id = str(row.get("security_id") or "").strip()
        if not security_id:
            continue
        buckets.setdefault(security_id, []).append(row)
    return buckets


def _match_reference_event_to_actual_entry(
    *,
    reference_event: Any,
    entry_rows: list[dict[str, Any]],
    expected_total_original: Decimal,
) -> dict[str, Any] | None:
    ex_date_iso = reference_event.ex_dividend_date.isoformat()
    candidates: list[dict[str, Any]] = []
    for row in entry_rows:
        row_ex_date = str(row.get("ex_date") or "").strip()
        if row_ex_date and row_ex_date == ex_date_iso:
            candidates.append(row)
    if candidates:
        return sorted(
            candidates,
            key=lambda row: (str(row.get("dividend_date") or ""), str(row.get("id") or "")),
        )[0]

    event_currency = _normalize_currency(reference_event.original_currency)
    window_end = reference_event.ex_dividend_date + timedelta(days=60)
    for row in entry_rows:
        if _normalize_currency(row.get("original_currency")) != event_currency:
            continue
        try:
            dividend_date = date.fromisoformat(str(row.get("dividend_date") or ""))
        except ValueError:
            continue
        if dividend_date < reference_event.ex_dividend_date or dividend_date > window_end:
            continue
        row_amount = _to_decimal(row.get("amount_original_ccy"))
        if row_amount is None:
            continue
        if abs(row_amount - expected_total_original) <= Decimal("0.05"):
            candidates.append(row)

    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda row: (str(row.get("dividend_date") or ""), str(row.get("id") or "")),
    )[0]


def _estimate_reference_dividend_gbp(
    *,
    original_currency: str,
    amount_original: Decimal,
    ex_dividend_date: date,
) -> tuple[Decimal | None, str | None]:
    normalized_currency = _normalize_currency(original_currency)
    if normalized_currency == _GBP:
        return _q_money(amount_original), "native_gbp"

    try:
        if ex_dividend_date <= date.today():
            fx_rate, fx_source = _auto_fx_rate_to_gbp(
                from_currency=normalized_currency,
                dividend_date=ex_dividend_date,
            )
        else:
            quote = FxService.get_rate(normalized_currency, "GBP")
            fx_rate = _q_fx(quote.rate)
            fx_source = f"estimate_{quote.source}:{quote.as_of or ex_dividend_date.isoformat()}"
    except Exception:
        return None, None

    return _q_money(amount_original * fx_rate), fx_source


def _build_reference_dividend_rows(
    *,
    reference_events: list[Any],
    entry_rows: list[dict[str, Any]],
    security_map: dict[str, Any],
    as_of_date: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matched_entries_by_security = _build_actual_dividend_match_index(entry_rows)
    rows: list[dict[str, Any]] = []
    upcoming_count = 0
    awaiting_count = 0
    recorded_count = 0
    total_expected_gbp = Decimal("0")
    total_expected_gbp_known = 0

    for event in sorted(
        reference_events,
        key=lambda row: (row.ex_dividend_date, row.id),
        reverse=True,
    ):
        security = security_map.get(event.security_id)
        if security is None:
            continue
        amount_per_share_original = _to_decimal(event.amount_original_ccy)
        if amount_per_share_original is None or amount_per_share_original <= Decimal("0"):
            continue
        eligible_quantities = _eligible_quantities_by_holding_bucket_on_ex_date(
            security_id=event.security_id,
            ex_dividend_date=event.ex_dividend_date,
        )
        for scope_id, eligible_quantity in sorted(eligible_quantities.items()):
            if eligible_quantity <= Decimal("0"):
                continue

            expected_total_original = _q_money(amount_per_share_original * eligible_quantity)
            expected_treatment = _holding_bucket_tax_treatment(scope_id)
            expected_lot_group = _holding_bucket_lot_group(scope_id)
            matched_entry = _match_reference_event_to_actual_entry(
                reference_event=event,
                entry_rows=[
                    row
                    for row in matched_entries_by_security.get(event.security_id, [])
                    if (
                        str(row.get("lot_group") or "").strip().upper() == expected_lot_group
                        or (
                            not str(row.get("lot_group") or "").strip()
                            and str(row.get("tax_treatment") or "").strip().upper()
                            == expected_treatment
                        )
                    )
                ],
                expected_total_original=expected_total_original,
            )
            estimated_gbp, estimated_gbp_source = _estimate_reference_dividend_gbp(
                original_currency=event.original_currency,
                amount_original=expected_total_original,
                ex_dividend_date=event.ex_dividend_date,
            )
            if estimated_gbp is not None:
                total_expected_gbp += estimated_gbp
                total_expected_gbp_known += 1

            if matched_entry is not None:
                status = "Recorded"
                recorded_count += 1
            elif event.ex_dividend_date > as_of_date:
                status = "Upcoming ex-date"
                upcoming_count += 1
            else:
                status = "Awaiting confirmation"
                awaiting_count += 1

            rows.append(
                {
                    "security_id": event.security_id,
                    "ticker": security.ticker,
                    "name": security.name,
                    "holding_scope": scope_id,
                    "holding_scope_label": _holding_bucket_label(scope_id),
                    "ex_dividend_date": event.ex_dividend_date.isoformat(),
                    "payment_date": event.payment_date.isoformat() if event.payment_date else None,
                    "amount_per_share_original_ccy": _money_str(amount_per_share_original),
                    "expected_quantity": _quantity_str(eligible_quantity),
                    "expected_total_original_ccy": _money_str(expected_total_original),
                    "original_currency": _normalize_currency(event.original_currency),
                    "expected_total_gbp": _money_str(estimated_gbp),
                    "expected_total_gbp_source": estimated_gbp_source,
                    "status": status,
                    "source": event.source,
                    "matched_entry_id": matched_entry.get("id") if matched_entry else None,
                    "matched_dividend_date": matched_entry.get("dividend_date") if matched_entry else None,
                }
            )

    summary = {
        "row_count": len(rows),
        "upcoming_count": upcoming_count,
        "awaiting_count": awaiting_count,
        "recorded_count": recorded_count,
        "estimated_total_gbp": _money_str(total_expected_gbp) if total_expected_gbp_known else None,
        "estimated_total_known_count": total_expected_gbp_known,
    }
    return rows, summary


def _taxable_income_ex_dividends(
    *,
    settings: AppSettings | None,
    tax_year: str,
) -> Decimal:
    if settings is None:
        return Decimal("0")
    bands = get_bands(tax_year)
    adjusted_net_income = (
        settings.default_gross_income
        - settings.default_pension_sacrifice
        + settings.default_other_income
    )
    pa = personal_allowance(bands, adjusted_net_income)
    return max(Decimal("0"), adjusted_net_income - pa)


class DividendService:
    """
    Read/write service for manual dividend records and dashboard summaries.
    """

    @staticmethod
    def append_ibkr_metadata_to_notes(
        *,
        notes: str | None,
        metadata: dict[str, object] | None,
    ) -> str | None:
        return _append_ibkr_meta(notes, metadata)

    @staticmethod
    def extract_ibkr_metadata_from_notes(
        *,
        notes: str | None,
    ) -> tuple[dict[str, object], str | None]:
        return _extract_ibkr_meta(notes)

    @staticmethod
    def relink_dividend_entry_lots(
        *,
        entry_id: str,
        security_id: str,
        lot_ids: list[str],
        lot_group: str | None = None,
        linked_lot_quantity: str | None = None,
    ) -> dict[str, str]:
        normalized_lot_ids: list[str] = []
        seen: set[str] = set()
        for raw_lot_id in lot_ids:
            cleaned = str(raw_lot_id or "").strip()
            if not cleaned or cleaned in seen:
                continue
            normalized_lot_ids.append(cleaned)
            seen.add(cleaned)
        if not normalized_lot_ids:
            raise ValueError("Select at least one lot to relink the dividend entry.")

        expected_security_id = str(security_id or "").strip()
        if not expected_security_id:
            raise ValueError("security_id is required for dividend relinking.")

        with AppContext.write_session() as sess:
            div_repo = DividendEntryRepository(sess)
            sec_repo = SecurityRepository(sess)
            entry = div_repo.get_by_id(entry_id)
            if entry is None:
                raise KeyError(f"Dividend entry not found: {entry_id!r}")
            if str(entry.security_id) != expected_security_id:
                raise ValueError(
                    "Selected lots do not belong to the same security as the dividend entry."
                )

            existing_meta, clean_notes = _extract_ibkr_meta(entry.notes)
            merged_meta = dict(existing_meta)
            merged_meta["lot_ids"] = ",".join(normalized_lot_ids)
            merged_meta["lot_count"] = str(len(normalized_lot_ids))

            normalized_group = (lot_group or "").strip().upper()
            if normalized_group and normalized_group != "ALL":
                merged_meta["lot_group"] = normalized_group
            else:
                merged_meta.pop("lot_group", None)

            linked_qty = _to_decimal(linked_lot_quantity)
            if linked_qty is not None and linked_qty > Decimal("0"):
                merged_meta["linked_lot_quantity"] = _decimal_to_plain_str(linked_qty)

            entry.notes = _append_ibkr_meta(clean_notes, merged_meta)
            sec = sec_repo.require_by_id(entry.security_id)
            sess.flush()
            return {
                "id": entry.id,
                "security_id": entry.security_id,
                "ticker": sec.ticker,
                "lot_ids": ",".join(normalized_lot_ids),
            }

    @staticmethod
    def add_dividend_entry(
        *,
        security_id: str,
        dividend_date: date,
        amount_gbp: Decimal | None = None,
        amount_original_ccy: Decimal | None = None,
        original_currency: str | None = _GBP,
        fx_rate_to_gbp: Decimal | None = None,
        fx_rate_source: str | None = None,
        tax_treatment: str = "TAXABLE",
        source: str | None = "manual",
        notes: str | None = None,
    ) -> dict[str, str]:
        normalized_currency = _normalize_currency(original_currency)
        normalized_fx_source = (fx_rate_source or "").strip() or None

        normalized_amount_gbp = _q_money(amount_gbp) if amount_gbp is not None else None
        normalized_amount_original = (
            _q_money(amount_original_ccy)
            if amount_original_ccy is not None
            else None
        )
        normalized_fx_rate = _q_fx(fx_rate_to_gbp) if fx_rate_to_gbp is not None else None

        if normalized_amount_gbp is not None and normalized_amount_gbp <= Decimal("0"):
            raise ValueError("amount_gbp must be greater than zero.")
        if normalized_amount_original is not None and normalized_amount_original <= Decimal("0"):
            raise ValueError("amount_original_ccy must be greater than zero.")
        if normalized_fx_rate is not None and normalized_fx_rate <= Decimal("0"):
            raise ValueError("fx_rate_to_gbp must be greater than zero.")

        if normalized_currency == _GBP:
            if normalized_amount_gbp is None and normalized_amount_original is None:
                raise ValueError("Provide amount_gbp or amount_original_ccy for GBP dividends.")
            if normalized_amount_gbp is None:
                normalized_amount_gbp = normalized_amount_original
            if normalized_amount_original is None:
                normalized_amount_original = normalized_amount_gbp
            normalized_fx_rate = Decimal("1.000000")
            normalized_fx_source = normalized_fx_source or "identity_gbp"
        else:
            if normalized_amount_original is None:
                raise ValueError(
                    "Non-GBP dividends require amount_original_ccy in the native currency."
                )
            if normalized_fx_rate is None:
                if normalized_amount_gbp is None:
                    auto_rate, auto_source = _auto_fx_rate_to_gbp(
                        from_currency=normalized_currency,
                        dividend_date=dividend_date,
                    )
                    normalized_fx_rate = auto_rate
                    normalized_fx_source = normalized_fx_source or auto_source
                else:
                    normalized_fx_rate = _q_fx(
                        normalized_amount_gbp / normalized_amount_original
                    )
            if normalized_amount_gbp is None:
                normalized_amount_gbp = _q_money(normalized_amount_original * normalized_fx_rate)
            normalized_fx_source = normalized_fx_source or "manual_conversion"

        if normalized_amount_gbp is None or normalized_amount_gbp <= Decimal("0"):
            raise ValueError("Resolved GBP dividend amount must be greater than zero.")

        normalized_treatment = (tax_treatment or "").strip().upper()
        if normalized_treatment not in _VALID_TREATMENTS:
            raise ValueError("tax_treatment must be one of ['TAXABLE', 'ISA_EXEMPT'].")

        with AppContext.write_session() as sess:
            sec_repo = SecurityRepository(sess)
            sec = sec_repo.require_by_id(security_id)
            entry = DividendEntryRepository(sess).add(
                security_id=security_id,
                dividend_date=dividend_date,
                amount_gbp=normalized_amount_gbp,
                amount_original_ccy=normalized_amount_original,
                original_currency=normalized_currency,
                fx_rate_to_gbp=normalized_fx_rate,
                fx_rate_source=normalized_fx_source,
                tax_treatment=normalized_treatment,
                source=source,
                notes=notes,
            )
            sess.flush()
            return {
                "id": entry.id,
                "security_id": sec.id,
                "ticker": sec.ticker,
                "dividend_date": entry.dividend_date.isoformat(),
                "amount_gbp": str(normalized_amount_gbp),
                "amount_original_ccy": str(normalized_amount_original),
                "original_currency": normalized_currency,
                "fx_rate_to_gbp": str(normalized_fx_rate) if normalized_fx_rate is not None else "",
                "fx_rate_source": normalized_fx_source or "",
                "tax_treatment": normalized_treatment,
                "source": entry.source or "",
                "notes": entry.notes or "",
            }

    @staticmethod
    def confirm_reference_dividend(
        *,
        security_id: str,
        ex_dividend_date: date,
        dividend_date: date,
        holding_scope: str,
        confirmation_mode: str,
        amount_original_ccy: Decimal,
        original_currency: str,
        tax_withheld_original_ccy: Decimal | None = None,
        fee_original_ccy: Decimal | None = None,
        fx_rate_to_gbp: Decimal | None = None,
        fx_rate_source: str | None = None,
        stock_quantity_received: Decimal | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = (holding_scope or "").strip().upper()
        if not normalized_scope:
            raise ValueError("holding_scope is required.")
        tax_treatment = _holding_bucket_tax_treatment(normalized_scope)
        lot_group = _holding_bucket_lot_group(normalized_scope)

        normalized_mode = (confirmation_mode or "").strip().lower()
        if normalized_mode not in {"cash", "stock"}:
            raise ValueError("confirmation_mode must be cash or stock.")
        if amount_original_ccy <= Decimal("0"):
            raise ValueError("gross amount must be greater than zero.")
        if normalized_mode == "stock" and (
            stock_quantity_received is None or stock_quantity_received <= Decimal("0")
        ):
            raise ValueError("stock quantity received is required when confirming stock.")

        gross_amount_original_ccy = _q_money(amount_original_ccy)
        tax_withheld_original_ccy = _q_money(
            max(Decimal("0"), tax_withheld_original_ccy or Decimal("0"))
        )
        fee_original_ccy = _q_money(max(Decimal("0"), fee_original_ccy or Decimal("0")))
        net_amount_original_ccy = _q_money(
            max(
                Decimal("0"),
                gross_amount_original_ccy - tax_withheld_original_ccy - fee_original_ccy,
            )
        )

        metadata: dict[str, object] = {
            "ex_date": ex_dividend_date.isoformat(),
            "lot_group": lot_group,
            "confirmation_mode": normalized_mode,
            "gross_amount_original_ccy": gross_amount_original_ccy,
            "net_amount_original_ccy": net_amount_original_ccy,
            "tax_withheld_original_ccy": tax_withheld_original_ccy,
            "fee_original_ccy": fee_original_ccy,
        }
        if stock_quantity_received is not None and stock_quantity_received > Decimal("0"):
            metadata["stock_quantity_received"] = stock_quantity_received

        return DividendService.add_dividend_entry(
            security_id=security_id,
            dividend_date=dividend_date,
            amount_gbp=None,
            amount_original_ccy=gross_amount_original_ccy,
            original_currency=original_currency,
            fx_rate_to_gbp=fx_rate_to_gbp,
            fx_rate_source=fx_rate_source,
            tax_treatment=tax_treatment,
            source="dividend_confirmation",
            notes=DividendService.append_ibkr_metadata_to_notes(
                notes=notes,
                metadata=metadata,
            ),
        )

    @staticmethod
    def delete_dividend_entry(entry_id: str) -> bool:
        with AppContext.write_session() as sess:
            return DividendEntryRepository(sess).delete(entry_id)

    @staticmethod
    def get_summary(
        *,
        settings: AppSettings | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        generated_at_utc = datetime.now(UTC).isoformat()

        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            div_repo = DividendEntryRepository(sess)
            securities = sec_repo.list_all()
            entries = div_repo.list_all()

        security_map = {sec.id: sec for sec in securities}
        portfolio_summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )

        tracked_security_ids = sorted({sec.id for sec in securities})

        dividend_sync_result = TwelveDataDividendService.sync_tracked_if_due(
            security_ids=tracked_security_ids,
        )

        with AppContext.read_session() as sess:
            reference_events = DividendReferenceEventRepository(sess).list_for_security_ids(
                tracked_security_ids
            )

        active_schemes_by_security: dict[str, set[str]] = {}
        for sec_summary in portfolio_summary.securities:
            scheme_types = {
                ls.lot.scheme_type
                for ls in sec_summary.active_lots
                if ls.quantity_remaining > Decimal("0")
            }
            if scheme_types:
                active_schemes_by_security[sec_summary.security.id] = scheme_types

        security_options = []
        for sec in sorted(securities, key=lambda s: (s.ticker, s.id)):
            scheme_types = sorted(active_schemes_by_security.get(sec.id, set()))
            has_isa_lot = "ISA" in scheme_types
            has_taxable_lot = any(scheme != "ISA" for scheme in scheme_types)
            security_options.append(
                {
                    "id": sec.id,
                    "ticker": sec.ticker,
                    "name": sec.name,
                    "currency": _normalize_currency(sec.currency),
                    "dividend_reminder_date": (
                        sec.dividend_reminder_date.isoformat()
                        if sec.dividend_reminder_date is not None
                        else ""
                    ),
                    "scheme_types": scheme_types,
                    "has_isa_lot": has_isa_lot,
                    "has_taxable_lot": has_taxable_lot,
                }
            )

        active_lot_options: list[dict[str, str]] = []
        for sec_summary in sorted(
            portfolio_summary.securities,
            key=lambda row: (row.security.ticker, row.security.id),
        ):
            sec = sec_summary.security
            sec_currency = _normalize_currency(sec.currency)
            for lot_summary in sorted(
                sec_summary.active_lots,
                key=lambda row: (row.lot.acquisition_date, row.lot.id),
            ):
                if lot_summary.quantity_remaining <= Decimal("0"):
                    continue
                lot = lot_summary.lot
                qty = lot_summary.quantity_remaining
                active_lot_options.append(
                    {
                        "id": lot.id,
                        "security_id": sec.id,
                        "ticker": sec.ticker,
                        "security_name": sec.name,
                        "scheme_type": lot.scheme_type,
                        "currency": sec_currency,
                        "quantity_remaining": _quantity_str(qty),
                        "acquisition_date": lot.acquisition_date.isoformat(),
                        "label": (
                            f"{sec.ticker} | {lot.scheme_type} | "
                            f"{_quantity_str(qty)} sh | {lot.acquisition_date.isoformat()}"
                        ),
                    }
                )

        currency_options = sorted(
            {
                "GBP",
                "USD",
                "EUR",
                *[_normalize_currency(sec.currency) for sec in securities],
            }
        )

        unique_schemes = sorted(
            {
                scheme
                for schemes in active_schemes_by_security.values()
                for scheme in schemes
            }
        )
        has_isa_group = any("ISA" in schemes for schemes in active_schemes_by_security.values())
        has_taxable_group = any(
            any(scheme != "ISA" for scheme in schemes)
            for schemes in active_schemes_by_security.values()
        )
        lot_group_options: list[dict[str, str]] = [
            {
                "id": "ALL",
                "label": "All Holdings",
                "default_tax_treatment": "TAXABLE",
            }
        ]
        if has_isa_group:
            lot_group_options.append(
                {
                    "id": "ISA_ONLY",
                    "label": "ISA Lots",
                    "default_tax_treatment": "ISA_EXEMPT",
                }
            )
        if has_taxable_group:
            lot_group_options.append(
                {
                    "id": "TAXABLE_ONLY",
                    "label": "Taxable Lots",
                    "default_tax_treatment": "TAXABLE",
                }
            )
        for scheme in unique_schemes:
            label = "ESPP+ Lots" if scheme == "ESPP_PLUS" else f"{scheme} Lots"
            lot_group_options.append(
                {
                    "id": f"SCHEME:{scheme}",
                    "label": label,
                    "default_tax_treatment": (
                        "ISA_EXEMPT" if scheme == "ISA" else "TAXABLE"
                    ),
                }
            )

        hide_values = bool(settings and settings.hide_values)
        if hide_values:
            return {
                "generated_at_utc": generated_at_utc,
                "as_of_date": as_of_date.isoformat(),
                "hide_values": True,
                "security_options": security_options,
                "active_lot_options": active_lot_options,
                "lot_group_options": lot_group_options,
                "currency_options": currency_options,
                "reference_summary": {
                    "row_count": 0,
                    "upcoming_count": 0,
                    "awaiting_count": 0,
                    "recorded_count": 0,
                    "estimated_total_gbp": None,
                    "estimated_total_known_count": 0,
                },
                "reference_events": [],
                "reference_notes": [
                    "Reference dividend values are hidden while privacy mode is enabled."
                ],
                "summary": {
                    "trailing_12m_total_gbp": None,
                    "forecast_12m_total_gbp": None,
                    "actual_to_date_total_gbp": None,
                    "forecast_entry_total_gbp": None,
                    "actual_entry_count": None,
                    "forecast_entry_count": None,
                    "all_time_total_gbp": None,
                    "actual_gross_dividends_gbp": None,
                    "actual_withholding_tax_gbp": None,
                    "actual_fees_gbp": None,
                    "actual_net_paid_gbp": None,
                    "actual_withholding_drag_pct": None,
                    "actual_rows_with_ib_detail": None,
                    "actual_rows_with_withholding": None,
                    "estimated_tax_gbp": None,
                    "estimated_net_dividends_gbp": None,
                    "tax_drag_pct": None,
                },
                "tax_years": [],
                "entries": [],
                "allocation": {
                    "mode": "SECURITY_LEVEL",
                    "rows": [],
                    "totals": {
                        "allocated_total_dividends_gbp": None,
                        "allocated_estimated_tax_gbp": None,
                        "allocated_net_dividends_gbp": None,
                    },
                    "notes": ["Dividend values are hidden while privacy mode is enabled."],
                },
                "notes": ["Dividend values are hidden while privacy mode is enabled."],
                "sync": dividend_sync_result,
            }

        trailing_start = as_of_date - timedelta(days=365)
        forecast_end = as_of_date + timedelta(days=365)

        entry_rows: list[dict[str, Any]] = []
        all_time_total = Decimal("0")
        all_time_taxable = Decimal("0")
        all_time_isa = Decimal("0")
        trailing_total = Decimal("0")
        forecast_total = Decimal("0")
        actual_to_date_total = Decimal("0")
        forecast_entry_total = Decimal("0")
        actual_entry_count = 0
        forecast_entry_count = 0
        actual_gross_dividends = Decimal("0")
        actual_withholding_tax = Decimal("0")
        actual_fees = Decimal("0")
        actual_net_paid = Decimal("0")
        actual_rows_with_ib_detail = 0
        actual_rows_with_withholding = 0

        buckets: dict[str, dict[str, Decimal | int]] = {}
        security_buckets: dict[str, dict[str, Decimal | int | str]] = {}
        taxable_by_security_by_year: dict[str, dict[str, Decimal]] = {}

        for entry in entries:
            amount = _to_decimal(entry.amount_gbp)
            if amount is None:
                continue
            amount = _q_money(amount)
            treatment = (entry.tax_treatment or "TAXABLE").strip().upper()
            if treatment not in _VALID_TREATMENTS:
                treatment = "TAXABLE"
            is_taxable = treatment == "TAXABLE"
            tax_year = tax_year_for_date(entry.dividend_date)
            sec = security_map.get(entry.security_id)
            ticker = sec.ticker if sec is not None else "UNKNOWN"
            amount_original = _to_decimal(entry.amount_original_ccy)
            if amount_original is None:
                amount_original = amount
            amount_original = _q_money(amount_original)
            original_currency = _normalize_currency(entry.original_currency)
            fx_rate_to_gbp = _to_decimal(entry.fx_rate_to_gbp)
            if fx_rate_to_gbp is None:
                if original_currency == _GBP:
                    fx_rate_to_gbp = Decimal("1.000000")
                elif amount_original > Decimal("0"):
                    fx_rate_to_gbp = _q_fx(amount / amount_original)
            if fx_rate_to_gbp is not None:
                fx_rate_to_gbp = _q_fx(fx_rate_to_gbp)

            ib_meta, cleaned_notes = _extract_ibkr_meta(entry.notes)
            lot_ids_raw = str(ib_meta.get("lot_ids") or "").strip()
            lot_id_values = [
                lot_id.strip()
                for lot_id in lot_ids_raw.split(",")
                if lot_id and lot_id.strip()
            ]
            cash_components = _resolve_cash_components(
                amount_gbp=amount,
                amount_original=amount_original,
                fx_rate_to_gbp=fx_rate_to_gbp,
                ib_meta=ib_meta,
            )
            gross_original = cash_components["gross_original"]
            net_original = cash_components["net_original"]
            tax_withheld_original = cash_components["tax_withheld_original"]
            fee_original = cash_components["fee_original"]
            gross_gbp = cash_components["gross_gbp"]
            net_gbp = cash_components["net_gbp"]
            tax_withheld_gbp = cash_components["tax_withheld_gbp"]
            fee_gbp = cash_components["fee_gbp"]

            all_time_total += amount
            if is_taxable:
                all_time_taxable += amount
            else:
                all_time_isa += amount

            if trailing_start <= entry.dividend_date <= as_of_date:
                trailing_total += amount
            if as_of_date < entry.dividend_date <= forecast_end:
                forecast_total += amount
            if entry.dividend_date <= as_of_date:
                actual_to_date_total += amount
                actual_entry_count += 1
                actual_gross_dividends += gross_gbp
                actual_withholding_tax += tax_withheld_gbp
                actual_fees += fee_gbp
                actual_net_paid += net_gbp
                if ib_meta:
                    actual_rows_with_ib_detail += 1
                if tax_withheld_original > Decimal("0"):
                    actual_rows_with_withholding += 1
            else:
                forecast_entry_total += amount
                forecast_entry_count += 1

            bucket = buckets.setdefault(
                tax_year,
                {
                    "entry_count": 0,
                    "total_dividends": Decimal("0"),
                    "taxable_dividends": Decimal("0"),
                    "isa_exempt_dividends": Decimal("0"),
                },
            )
            bucket["entry_count"] = int(bucket["entry_count"]) + 1
            bucket["total_dividends"] = Decimal(bucket["total_dividends"]) + amount
            if is_taxable:
                bucket["taxable_dividends"] = Decimal(bucket["taxable_dividends"]) + amount
                taxable_for_year = taxable_by_security_by_year.setdefault(tax_year, {})
                taxable_for_year[entry.security_id] = (
                    taxable_for_year.get(entry.security_id, Decimal("0")) + amount
                )
            else:
                bucket["isa_exempt_dividends"] = (
                    Decimal(bucket["isa_exempt_dividends"]) + amount
                )

            security_bucket = security_buckets.setdefault(
                entry.security_id,
                {
                    "security_id": entry.security_id,
                    "ticker": ticker,
                    "entry_count": 0,
                    "total_dividends_gbp": Decimal("0"),
                    "taxable_dividends_gbp": Decimal("0"),
                    "isa_exempt_dividends_gbp": Decimal("0"),
                },
            )
            security_bucket["entry_count"] = int(security_bucket["entry_count"]) + 1
            security_bucket["total_dividends_gbp"] = (
                Decimal(security_bucket["total_dividends_gbp"]) + amount
            )
            if is_taxable:
                security_bucket["taxable_dividends_gbp"] = (
                    Decimal(security_bucket["taxable_dividends_gbp"]) + amount
                )
            else:
                security_bucket["isa_exempt_dividends_gbp"] = (
                    Decimal(security_bucket["isa_exempt_dividends_gbp"]) + amount
                )

            entry_rows.append(
                {
                    "id": entry.id,
                    "security_id": entry.security_id,
                    "ticker": ticker,
                    "dividend_date": entry.dividend_date.isoformat(),
                    "tax_year": tax_year,
                    "amount_gbp": _money_str(amount),
                    "amount_original_ccy": _money_str(amount_original),
                    "gross_amount_original_ccy": _money_str(gross_original),
                    "tax_withheld_original_ccy": _money_str(tax_withheld_original),
                    "fee_original_ccy": _money_str(fee_original),
                    "net_amount_original_ccy": _money_str(net_original),
                    "original_currency": original_currency,
                    "fx_rate_to_gbp": str(fx_rate_to_gbp) if fx_rate_to_gbp is not None else None,
                    "fx_rate_source": entry.fx_rate_source,
                    "gross_amount_gbp": _money_str(gross_gbp),
                    "tax_withheld_gbp": _money_str(tax_withheld_gbp),
                    "fee_gbp": _money_str(fee_gbp),
                    "net_amount_gbp": _money_str(net_gbp),
                    "quantity": ib_meta.get("quantity"),
                    "gross_rate_original_ccy": ib_meta.get("gross_rate_original_ccy"),
                    "ex_date": ib_meta.get("ex_date"),
                    "ib_code": ib_meta.get("ib_code"),
                    "lot_group": ib_meta.get("lot_group"),
                    "lot_ids": lot_id_values,
                    "lot_link_count": len(lot_id_values),
                    "has_lot_links": bool(lot_id_values),
                    "linked_lot_quantity": ib_meta.get("linked_lot_quantity"),
                    "tax_treatment": treatment,
                    "source": entry.source,
                    "notes": cleaned_notes,
                    "is_forecast": entry.dividend_date > as_of_date,
                }
            )

        entry_rows.sort(key=lambda row: (row["dividend_date"], row["id"]), reverse=True)
        reference_rows, reference_summary = _build_reference_dividend_rows(
            reference_events=reference_events,
            entry_rows=entry_rows,
            security_map=security_map,
            as_of_date=as_of_date,
        )

        tax_year_rows: list[dict[str, Any]] = []
        estimated_tax_total = Decimal("0")
        estimated_tax_by_security: dict[str, Decimal] = {}
        for tax_year in sorted(buckets):
            b = buckets[tax_year]
            taxable = _q_money(Decimal(b["taxable_dividends"]))
            isa_exempt = _q_money(Decimal(b["isa_exempt_dividends"]))
            taxable_income = _taxable_income_ex_dividends(
                settings=settings,
                tax_year=tax_year,
            )
            tax_result = calculate_dividend_tax(
                tax_year=tax_year,
                total_dividends=taxable,
                taxable_income_ex_dividends=taxable_income,
            )
            estimated_tax_total += tax_result.total_dividend_tax

            taxable_by_security = taxable_by_security_by_year.get(tax_year, {})
            if (
                taxable_by_security
                and taxable > Decimal("0")
                and tax_result.total_dividend_tax > Decimal("0")
            ):
                allocated_sum = Decimal("0")
                ranked = sorted(
                    taxable_by_security.items(),
                    key=lambda item: (item[1], item[0]),
                    reverse=True,
                )
                for security_id, taxable_amount in ranked:
                    allocated = _q_money(
                        (tax_result.total_dividend_tax * taxable_amount) / taxable
                    )
                    allocated_sum += allocated
                    estimated_tax_by_security[security_id] = (
                        estimated_tax_by_security.get(security_id, Decimal("0")) + allocated
                    )
                remainder = _q_money(tax_result.total_dividend_tax - allocated_sum)
                if remainder != Decimal("0") and ranked:
                    top_security_id = ranked[0][0]
                    estimated_tax_by_security[top_security_id] = (
                        estimated_tax_by_security.get(top_security_id, Decimal("0")) + remainder
                    )

            net_after_tax = _q_money((taxable - tax_result.total_dividend_tax) + isa_exempt)
            tax_year_rows.append(
                {
                    "tax_year": tax_year,
                    "entry_count": int(b["entry_count"]),
                    "total_dividends_gbp": _money_str(Decimal(b["total_dividends"])),
                    "taxable_dividends_gbp": _money_str(taxable),
                    "isa_exempt_dividends_gbp": _money_str(isa_exempt),
                    "dividend_allowance_gbp": _money_str(tax_result.dividend_allowance_used),
                    "taxable_after_allowance_gbp": _money_str(tax_result.taxable_dividends),
                    "estimated_dividend_tax_gbp": _money_str(tax_result.total_dividend_tax),
                    "estimated_net_after_tax_gbp": _money_str(net_after_tax),
                    "effective_tax_rate_pct": str(
                        _q_pct(tax_result.effective_rate * Decimal("100"))
                    ),
                }
            )

        estimated_tax_total = _q_money(estimated_tax_total)
        estimated_net_total = _q_money(all_time_total - estimated_tax_total)
        tax_drag_pct = (
            _q_pct((estimated_tax_total / all_time_taxable) * Decimal("100"))
            if all_time_taxable > Decimal("0")
            else Decimal("0.00")
        )
        actual_gross_dividends = _q_money(actual_gross_dividends)
        actual_withholding_tax = _q_money(actual_withholding_tax)
        actual_fees = _q_money(actual_fees)
        actual_net_paid = _q_money(actual_net_paid)
        actual_withholding_drag_pct = (
            _q_pct((actual_withholding_tax / actual_gross_dividends) * Decimal("100"))
            if actual_gross_dividends > Decimal("0")
            else Decimal("0.00")
        )

        active_true_cost_by_security = {
            ss.security.id: _q_money(Decimal(ss.total_true_cost_gbp))
            for ss in portfolio_summary.securities
        }
        economic_gain_by_security = {
            ss.security.id: _q_money(
                Decimal(ss.unrealised_gain_economic_gbp or Decimal("0"))
            )
            for ss in portfolio_summary.securities
        }

        allocation_rows: list[dict[str, Any]] = []
        for security_id, raw_row in sorted(
            security_buckets.items(),
            key=lambda item: (
                Decimal(item[1]["total_dividends_gbp"]),
                str(item[1]["ticker"]),
            ),
            reverse=True,
        ):
            total_dividends = _q_money(Decimal(raw_row["total_dividends_gbp"]))
            taxable_dividends = _q_money(Decimal(raw_row["taxable_dividends_gbp"]))
            isa_dividends = _q_money(Decimal(raw_row["isa_exempt_dividends_gbp"]))
            allocated_tax = _q_money(estimated_tax_by_security.get(security_id, Decimal("0")))
            allocated_net = _q_money(total_dividends - allocated_tax)
            active_true_cost = _q_money(active_true_cost_by_security.get(security_id, Decimal("0")))
            capital_at_risk_after_dividends = _q_money(
                max(Decimal("0"), active_true_cost - allocated_net)
            )
            economic_gain = _q_money(economic_gain_by_security.get(security_id, Decimal("0")))
            allocation_rows.append(
                {
                    "security_id": security_id,
                    "ticker": str(raw_row["ticker"]),
                    "entry_count": int(raw_row["entry_count"]),
                    "total_dividends_gbp": _money_str(total_dividends),
                    "taxable_dividends_gbp": _money_str(taxable_dividends),
                    "isa_exempt_dividends_gbp": _money_str(isa_dividends),
                    "allocated_estimated_tax_gbp": _money_str(allocated_tax),
                    "allocated_net_dividends_gbp": _money_str(allocated_net),
                    "active_true_cost_gbp": _money_str(active_true_cost),
                    "capital_at_risk_after_dividends_gbp": _money_str(
                        capital_at_risk_after_dividends
                    ),
                    "economic_gain_gbp": _money_str(economic_gain),
                    "economic_gain_plus_net_dividends_gbp": _money_str(
                        _q_money(economic_gain + allocated_net)
                    ),
                }
            )

        allocated_total_dividends = _q_money(
            sum(
                (Decimal(row["total_dividends_gbp"]) for row in allocation_rows),
                Decimal("0"),
            )
        )
        allocated_estimated_tax = _q_money(
            sum(
                (Decimal(row["allocated_estimated_tax_gbp"]) for row in allocation_rows),
                Decimal("0"),
            )
        )
        allocated_net_dividends = _q_money(
            sum(
                (Decimal(row["allocated_net_dividends_gbp"]) for row in allocation_rows),
                Decimal("0"),
            )
        )

        notes: list[str] = []
        if not entries:
            notes.append("No dividend entries recorded yet.")
        if settings is None:
            notes.append("Tax estimate uses zero income baseline until Settings are configured.")
        if forecast_total > Decimal("0"):
            notes.append(
                "Future-dated entries are treated as manual forecast values."
            )
        if all_time_isa > Decimal("0"):
            notes.append("ISA-exempt dividend flow is tracked separately from taxable flow.")
        if allocation_rows:
            notes.append(
                "Security-level dividend allocation reconciles entry totals to "
                "current held capital."
            )
        if actual_entry_count > 0 and actual_rows_with_ib_detail == 0:
            notes.append(
                "Cash payout stats use gross amount when withholding fields are not provided."
            )
        if actual_entry_count > 0 and actual_rows_with_withholding == 0:
            notes.append(
                "No withholding tax rows logged yet; add tax withheld to match broker payout slips."
            )
        reference_notes: list[str] = [
            "Reference dividends come from Twelve Data and are not auto-booked as received cash."
        ]
        if reference_rows:
            reference_notes.append(
                "Eligibility uses holdings on the ex-dividend date, so post-ex-date transfers remain attributed to the original holder."
            )
            reference_notes.append(
                "Unvested RSUs are excluded until the lot acquisition/vest date has passed."
            )
        else:
            reference_notes.append("No reference dividend events found for tracked holdings yet.")
        if dividend_sync_result.get("errors"):
            reference_notes.append("Some tracked securities failed dividend sync and may be incomplete.")

        return {
            "generated_at_utc": generated_at_utc,
            "as_of_date": as_of_date.isoformat(),
            "hide_values": False,
            "security_options": security_options,
            "active_lot_options": active_lot_options,
            "lot_group_options": lot_group_options,
            "currency_options": currency_options,
            "reference_summary": reference_summary,
            "reference_events": reference_rows,
            "reference_notes": reference_notes,
            "summary": {
                "trailing_12m_total_gbp": _money_str(trailing_total),
                "forecast_12m_total_gbp": _money_str(forecast_total),
                "actual_to_date_total_gbp": _money_str(actual_to_date_total),
                "forecast_entry_total_gbp": _money_str(forecast_entry_total),
                "actual_entry_count": actual_entry_count,
                "forecast_entry_count": forecast_entry_count,
                "all_time_total_gbp": _money_str(all_time_total),
                "all_time_taxable_dividends_gbp": _money_str(all_time_taxable),
                "all_time_isa_exempt_dividends_gbp": _money_str(all_time_isa),
                "actual_gross_dividends_gbp": _money_str(actual_gross_dividends),
                "actual_withholding_tax_gbp": _money_str(actual_withholding_tax),
                "actual_fees_gbp": _money_str(actual_fees),
                "actual_net_paid_gbp": _money_str(actual_net_paid),
                "actual_withholding_drag_pct": str(actual_withholding_drag_pct),
                "actual_rows_with_ib_detail": actual_rows_with_ib_detail,
                "actual_rows_with_withholding": actual_rows_with_withholding,
                "estimated_tax_gbp": _money_str(estimated_tax_total),
                "estimated_net_dividends_gbp": _money_str(estimated_net_total),
                "tax_drag_pct": str(tax_drag_pct),
            },
            "tax_years": tax_year_rows,
            "entries": entry_rows,
            "allocation": {
                "mode": "SECURITY_LEVEL",
                "rows": allocation_rows,
                "totals": {
                    "allocated_total_dividends_gbp": _money_str(allocated_total_dividends),
                    "allocated_estimated_tax_gbp": _money_str(allocated_estimated_tax),
                    "allocated_net_dividends_gbp": _money_str(allocated_net_dividends),
                },
                "notes": [
                    "Allocated estimated tax uses tax-year taxable-dividend "
                    "proportions by security.",
                    "Capital at risk after dividends = max(0, active true cost "
                    "- allocated net dividends).",
                ],
            },
            "notes": notes,
            "sync": dividend_sync_result,
        }

    @staticmethod
    def _build_net_entry_rows(
        *,
        entries: list[Any],
        settings: AppSettings | None,
        as_of_date: date,
    ) -> list[dict[str, Any]]:
        """
        Resolve per-entry net dividends (after estimated dividend tax allocation).

        Tax allocation is deterministic per tax year:
        - Taxable entries share that year's estimated dividend tax proportionally.
        - Rounding remainder is assigned to the largest taxable entry in that year.
        """
        rows: list[dict[str, Any]] = []
        taxable_rows_by_year: dict[str, list[dict[str, Any]]] = {}

        for entry in entries:
            if entry.dividend_date > as_of_date:
                continue
            amount = _to_decimal(entry.amount_gbp)
            if amount is None:
                continue
            amount = _q_money(amount)
            treatment = (entry.tax_treatment or "TAXABLE").strip().upper()
            if treatment not in _VALID_TREATMENTS:
                treatment = "TAXABLE"
            is_taxable = treatment == "TAXABLE"
            tax_year = tax_year_for_date(entry.dividend_date)
            row = {
                "id": str(entry.id),
                "security_id": str(entry.security_id),
                "dividend_date": entry.dividend_date,
                "tax_year": tax_year,
                "amount_gbp": amount,
                "is_taxable": is_taxable,
                "allocated_tax_gbp": Decimal("0.00"),
                "net_dividend_gbp": amount,
            }
            rows.append(row)
            if is_taxable:
                taxable_rows_by_year.setdefault(tax_year, []).append(row)

        for tax_year, taxable_rows in taxable_rows_by_year.items():
            taxable_total = _q_money(
                sum((row["amount_gbp"] for row in taxable_rows), Decimal("0"))
            )
            if taxable_total <= Decimal("0"):
                continue
            taxable_income = _taxable_income_ex_dividends(
                settings=settings,
                tax_year=tax_year,
            )
            tax_result = calculate_dividend_tax(
                tax_year=tax_year,
                total_dividends=taxable_total,
                taxable_income_ex_dividends=taxable_income,
            )
            total_tax = _q_money(tax_result.total_dividend_tax)
            if total_tax <= Decimal("0"):
                continue

            ranked = sorted(
                taxable_rows,
                key=lambda row: (
                    row["amount_gbp"],
                    row["dividend_date"],
                    row["id"],
                ),
                reverse=True,
            )
            allocated_sum = Decimal("0")
            for row in ranked:
                allocated = _q_money((total_tax * row["amount_gbp"]) / taxable_total)
                row["allocated_tax_gbp"] = allocated
                allocated_sum += allocated

            remainder = _q_money(total_tax - allocated_sum)
            if remainder != Decimal("0") and ranked:
                ranked[0]["allocated_tax_gbp"] = _q_money(
                    ranked[0]["allocated_tax_gbp"] + remainder
                )

        for row in rows:
            row["net_dividend_gbp"] = _q_money(row["amount_gbp"] - row["allocated_tax_gbp"])

        return rows

    @staticmethod
    def get_net_dividends_timeline(
        *,
        settings: AppSettings | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """
        Deterministic cumulative net-dividend timeline (portfolio + per-security).

        Future-dated entries after ``as_of`` are excluded.
        """
        as_of_date = as_of or date.today()

        with AppContext.read_session() as sess:
            entries = DividendEntryRepository(sess).list_all()

        net_rows = DividendService._build_net_entry_rows(
            entries=entries,
            settings=settings,
            as_of_date=as_of_date,
        )

        net_by_date: dict[date, Decimal] = {}
        net_by_security_by_date: dict[str, dict[date, Decimal]] = {}
        for row in net_rows:
            d = row["dividend_date"]
            security_id = row["security_id"]
            net = row["net_dividend_gbp"]
            net_by_date[d] = net_by_date.get(d, Decimal("0")) + net
            sec_bucket = net_by_security_by_date.setdefault(security_id, {})
            sec_bucket[d] = sec_bucket.get(d, Decimal("0")) + net

        cumulative_by_date: dict[str, str] = {}
        running_total = Decimal("0")
        for d in sorted(net_by_date):
            running_total = _q_money(running_total + net_by_date[d])
            cumulative_by_date[d.isoformat()] = str(running_total)

        cumulative_by_security: dict[str, dict[str, str]] = {}
        totals_by_security: dict[str, str] = {}
        for security_id, dated_values in net_by_security_by_date.items():
            running = Decimal("0")
            sec_map: dict[str, str] = {}
            for d in sorted(dated_values):
                running = _q_money(running + dated_values[d])
                sec_map[d.isoformat()] = str(running)
            cumulative_by_security[security_id] = sec_map
            totals_by_security[security_id] = str(running)

        return {
            "as_of_date": as_of_date.isoformat(),
            "total_net_dividends_gbp": str(_q_money(running_total)),
            "net_dividends_by_security_gbp": totals_by_security,
            "cumulative_net_dividends_by_date": cumulative_by_date,
            "cumulative_net_dividends_by_security": cumulative_by_security,
        }
