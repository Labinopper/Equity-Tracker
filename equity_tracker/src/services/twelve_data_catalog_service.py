from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..app_context import AppContext
from ..db.repository import SecurityCatalogRepository
from .twelve_data_price_service import TwelveDataPriceService

logger = logging.getLogger(__name__)

_CATALOG_COUNTRIES = ("United States", "United Kingdom")
_SYNC_INTERVAL = timedelta(days=7)
_SYNC_TIMEOUT_SECS = 30.0
_SYNC_META_SUFFIX = ".catalog_sync.json"


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


class TwelveDataCatalogService:
    @staticmethod
    def is_configured() -> bool:
        return TwelveDataPriceService.is_configured()

    @staticmethod
    def last_synced_at() -> datetime | None:
        raw_value = str(_load_sync_meta().get("last_synced_at") or "").strip()
        if not raw_value:
            return None
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def sync_due() -> bool:
        last_synced_at = TwelveDataCatalogService.last_synced_at()
        if last_synced_at is None:
            return True
        return _utc_now() - last_synced_at >= _SYNC_INTERVAL

    @staticmethod
    def fetch_catalog_entries() -> list[dict[str, str | None]]:
        config = TwelveDataPriceService.load_config()
        if config is None:
            raise RuntimeError("Twelve Data is not configured.")

        rows: list[dict[str, str | None]] = []
        with httpx.Client(base_url="https://api.twelvedata.com", timeout=_SYNC_TIMEOUT_SECS) as client:
            for country in _CATALOG_COUNTRIES:
                response = client.get(
                    "/stocks",
                    params={
                        "country": country,
                        "format": "JSON",
                        "apikey": config.api_key,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list):
                    raise RuntimeError(f"Unexpected Twelve Data catalog payload for {country}.")

                for raw_row in data:
                    if not isinstance(raw_row, dict):
                        continue
                    symbol = str(raw_row.get("symbol") or "").strip().upper()
                    name = str(raw_row.get("name") or "").strip()
                    exchange = str(raw_row.get("exchange") or "").strip().upper()
                    currency = str(raw_row.get("currency") or "").strip().upper()
                    if not symbol or not name or not exchange or len(currency) != 3:
                        continue

                    isin = str(raw_row.get("isin") or "").strip()
                    if not isin or isin == "request_access_via_add_ons":
                        isin = ""
                    figi = str(raw_row.get("figi_code") or raw_row.get("figi") or "").strip()

                    rows.append(
                        {
                            "symbol": symbol,
                            "name": name,
                            "exchange": exchange,
                            "currency": currency,
                            "isin": isin or None,
                            "figi": figi or None,
                        }
                    )

        logger.info("Fetched %d catalogue rows from Twelve Data.", len(rows))
        return rows

    @staticmethod
    def sync_now() -> dict[str, int]:
        if not AppContext.is_initialized():
            raise RuntimeError("Database is not initialized.")
        entries = TwelveDataCatalogService.fetch_catalog_entries()
        with AppContext.write_session() as sess:
            result = SecurityCatalogRepository(sess).sync_entries(entries)

        _write_sync_meta(
            {
                "last_synced_at": _utc_now().isoformat(),
                **result,
            }
        )
        return result

    @staticmethod
    def sync_if_due(*, force: bool = False) -> dict[str, int] | None:
        if not force and not TwelveDataCatalogService.sync_due():
            return None
        return TwelveDataCatalogService.sync_now()
