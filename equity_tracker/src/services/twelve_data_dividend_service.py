from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from ..app_context import AppContext
from ..db.repository import DividendReferenceEventRepository, SecurityRepository
from .twelve_data_price_service import TwelveDataPriceService

logger = logging.getLogger(__name__)

_SYNC_INTERVAL = timedelta(hours=24)
_SYNC_TIMEOUT_SECS = 30.0
_SYNC_META_SUFFIX = ".dividend_sync.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sync_meta_path() -> Path | None:
    db_path = os.environ.get("EQUITY_DB_PATH", "").strip()
    if not db_path:
        return None
    return Path(f"{db_path}{_SYNC_META_SUFFIX}")


def _load_sync_meta() -> dict[str, object]:
    path = _sync_meta_path()
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_sync_meta(payload: dict[str, object]) -> None:
    path = _sync_meta_path()
    if path is None:
        return
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _normalize_meta(payload: dict[str, object]) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}
    for security_id, raw_value in payload.items():
        if not isinstance(raw_value, dict):
            continue
        last_synced_at = str(raw_value.get("last_synced_at") or "").strip()
        normalized[security_id] = {
            "last_synced_at": last_synced_at,
        }
    return normalized


class TwelveDataDividendService:
    @staticmethod
    def is_configured() -> bool:
        return TwelveDataPriceService.is_configured()

    @staticmethod
    def _last_synced_at_by_security() -> dict[str, datetime]:
        result: dict[str, datetime] = {}
        for security_id, raw_row in _normalize_meta(_load_sync_meta()).items():
            raw_ts = str(raw_row.get("last_synced_at") or "").strip()
            if not raw_ts:
                continue
            try:
                result[security_id] = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                continue
        return result

    @staticmethod
    def sync_due_for_security(security_id: str) -> bool:
        last_synced_at = TwelveDataDividendService._last_synced_at_by_security().get(security_id)
        if last_synced_at is None:
            return True
        return _utc_now() - last_synced_at >= _SYNC_INTERVAL

    @staticmethod
    def _symbol_for_security(security) -> str:
        ticker = str(security.ticker or "").strip().upper()
        exchange = str(security.exchange or "").strip().upper()
        if ticker and exchange:
            return f"{ticker}:{exchange}"
        return ticker

    @staticmethod
    def fetch_dividend_events_for_symbol(symbol: str) -> dict[str, object]:
        config = TwelveDataPriceService.load_config()
        if config is None:
            raise RuntimeError("Twelve Data is not configured.")
        with httpx.Client(base_url="https://api.twelvedata.com", timeout=_SYNC_TIMEOUT_SECS) as client:
            response = client.get(
                "/dividends",
                params={
                    "symbol": symbol,
                    "range": "full",
                    "apikey": config.api_key,
                },
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected Twelve Data dividends payload for {symbol}.")
        if str(payload.get("status") or "").strip().lower() == "error":
            raise RuntimeError(str(payload.get("message") or f"Twelve Data dividend sync failed for {symbol}."))
        return payload

    @staticmethod
    def sync_security(security_id: str) -> dict[str, int | str]:
        if not AppContext.is_initialized():
            raise RuntimeError("Database is not initialized.")

        with AppContext.read_session() as sess:
            security = SecurityRepository(sess).require_by_id(security_id)
        symbol = TwelveDataDividendService._symbol_for_security(security)
        if not symbol:
            return {"security_id": security_id, "symbol": "", "inserted": 0, "updated": 0, "fetched": 0}

        payload = TwelveDataDividendService.fetch_dividend_events_for_symbol(symbol)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        original_currency = str(
            meta.get("currency") or getattr(security, "currency", "") or "GBP"
        ).strip().upper() or "GBP"
        raw_rows = payload.get("dividends") if isinstance(payload.get("dividends"), list) else []

        inserted = 0
        updated = 0
        fetched = 0
        with AppContext.write_session() as sess:
            repo = DividendReferenceEventRepository(sess)
            for raw_row in raw_rows:
                if not isinstance(raw_row, dict):
                    continue
                raw_ex_date = str(raw_row.get("ex_date") or "").strip()
                raw_amount = raw_row.get("amount")
                if not raw_ex_date:
                    continue
                try:
                    ex_dividend_date = date.fromisoformat(raw_ex_date)
                    amount_original_ccy = Decimal(str(raw_amount))
                except (ValueError, InvalidOperation, TypeError):
                    continue
                if amount_original_ccy <= Decimal("0"):
                    continue
                raw_payment_date = str(raw_row.get("payment_date") or raw_row.get("pay_date") or "").strip()
                payment_date = None
                if raw_payment_date:
                    try:
                        payment_date = date.fromisoformat(raw_payment_date)
                    except ValueError:
                        payment_date = None
                provider_event_key = f"twelvedata:{security_id}:{ex_dividend_date.isoformat()}"
                _, created = repo.upsert(
                    security_id=security_id,
                    ex_dividend_date=ex_dividend_date,
                    payment_date=payment_date,
                    amount_original_ccy=amount_original_ccy,
                    original_currency=original_currency,
                    source="twelvedata",
                    provider_event_key=provider_event_key,
                )
                fetched += 1
                if created:
                    inserted += 1
                else:
                    updated += 1

        meta_payload = _normalize_meta(_load_sync_meta())
        meta_payload[security_id] = {
            "last_synced_at": _utc_now().isoformat(),
            "symbol": symbol,
        }
        _write_sync_meta(meta_payload)
        return {
            "security_id": security_id,
            "symbol": symbol,
            "inserted": inserted,
            "updated": updated,
            "fetched": fetched,
        }

    @staticmethod
    def sync_tracked_if_due(*, security_ids: list[str]) -> dict[str, object]:
        if not TwelveDataDividendService.is_configured():
            return {"enabled": False, "synced": [], "errors": []}

        synced: list[dict[str, int | str]] = []
        errors: list[str] = []
        seen: set[str] = set()
        for security_id in security_ids:
            normalized = str(security_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if not TwelveDataDividendService.sync_due_for_security(normalized):
                continue
            try:
                synced.append(TwelveDataDividendService.sync_security(normalized))
            except Exception as exc:
                logger.warning("Dividend sync failed for %s: %s", normalized, exc)
                errors.append(f"{normalized}: {exc}")
        return {"enabled": True, "synced": synced, "errors": errors}
