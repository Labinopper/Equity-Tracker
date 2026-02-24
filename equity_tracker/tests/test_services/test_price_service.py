"""
Tests for PriceRepository and PriceService (Phase L — Google Sheets source).

PriceRepository tests use the in-memory DB directly via AppContext.
PriceService.fetch_and_store() and fetch_all() tests monkeypatch both
SheetsPriceService.read_prices and SheetsFxService.read_fx_rates so no
network calls are made.

FX conversion rules under test:
  - GBP security  → price stored as-is, no FX call required.
  - GBX security  → already divided by 100 and ccy="GBP" by SheetsPriceService,
                     so treated same as GBP here.
  - USD security  → price_gbp = USD_price × USD2GBP_rate (from fx tab).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.app_context import AppContext
from src.db.models import Security
from src.db.repository import PriceRepository, SecurityRepository
from src.services.price_service import (
    PriceService,
    _build_source,
    _build_source_with_fx,
    _parse_fx_timestamp,
    _parse_sheets_timestamp,
)
from src.services.sheets_fx_service import FxRow
from src.services.sheets_price_service import SheetRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_security(ticker: str = "IBM", currency: str = "USD") -> Security:
    from src.services.portfolio_service import PortfolioService
    return PortfolioService.add_security(ticker, f"{ticker} Corp", currency, is_manual_override=True)


def _sheet_prices(*rows: SheetRow) -> dict[str, SheetRow]:
    return {r.ticker.upper(): r for r in rows}


def _fx_rates(*rows: FxRow) -> dict[str, FxRow]:
    return {r.pair.upper(): r for r in rows}


def _usd2gbp(rate: str = "0.79", as_of: str = "2025-01-15 09:58:00") -> dict[str, FxRow]:
    return _fx_rates(FxRow(pair="USD2GBP", rate=Decimal(rate), as_of=as_of))


# ---------------------------------------------------------------------------
# Source-field helpers
# ---------------------------------------------------------------------------

class TestSourceHelpers:
    def test_build_source_with_timestamp(self):
        assert _build_source("2024-01-15 10:30:00") == "google_sheets:2024-01-15 10:30:00"

    def test_build_source_empty_timestamp(self):
        assert _build_source("") == "google_sheets:"

    def test_build_source_with_fx(self):
        src = _build_source_with_fx("2025-01-15 10:00:00", "2025-01-15 09:58:00")
        assert src == "google_sheets:2025-01-15 10:00:00|fx:2025-01-15 09:58:00"

    def test_parse_sheets_timestamp_extracts_value(self):
        assert _parse_sheets_timestamp("google_sheets:2024-01-15 10:30:00") == "2024-01-15 10:30:00"

    def test_parse_sheets_timestamp_strips_fx_suffix(self):
        src = "google_sheets:2025-01-15 10:00:00|fx:2025-01-15 09:58:00"
        assert _parse_sheets_timestamp(src) == "2025-01-15 10:00:00"

    def test_parse_sheets_timestamp_empty_suffix(self):
        assert _parse_sheets_timestamp("google_sheets:") is None

    def test_parse_sheets_timestamp_non_sheets_source(self):
        assert _parse_sheets_timestamp("yfinance") is None
        assert _parse_sheets_timestamp("manual") is None
        assert _parse_sheets_timestamp("") is None

    def test_parse_fx_timestamp_present(self):
        src = "google_sheets:2025-01-15 10:00:00|fx:2025-01-15 09:58:00"
        assert _parse_fx_timestamp(src) == "2025-01-15 09:58:00"

    def test_parse_fx_timestamp_absent(self):
        assert _parse_fx_timestamp("google_sheets:2025-01-15 10:00:00") is None
        assert _parse_fx_timestamp("") is None

    def test_parse_fx_timestamp_empty_suffix(self):
        assert _parse_fx_timestamp("google_sheets:2025-01-15 10:00:00|fx:") is None


# ---------------------------------------------------------------------------
# PriceRepository — direct DB tests
# ---------------------------------------------------------------------------

class TestPriceRepository:
    def test_upsert_inserts_new_row(self, app_context):
        sec = _add_security("AAPL", "USD")
        with AppContext.write_session() as sess:
            repo = PriceRepository(sess)
            row = repo.upsert(
                security_id=sec.id,
                price_date=date(2025, 1, 10),
                close_price_original_ccy="150.00",
                currency="USD",
                source="google_sheets:",
                close_price_gbp="118.50",
            )
        assert row.id is not None
        assert row.close_price_gbp == "118.50"
        assert row.currency == "USD"

    def test_upsert_updates_existing_row(self, app_context):
        sec = _add_security("AAPL", "USD")
        today = date(2025, 1, 10)
        src = "google_sheets:"
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="150.00",
                currency="USD",
                source=src,
                close_price_gbp="118.50",
            )
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="155.00",
                currency="USD",
                source=src,
                close_price_gbp="122.00",
            )
        with AppContext.read_session() as sess:
            from sqlalchemy import select
            from src.db.models import PriceHistory
            rows = list(sess.scalars(
                select(PriceHistory).where(PriceHistory.security_id == sec.id)
            ))
        assert len(rows) == 1
        assert rows[0].close_price_gbp == "122.00"

    def test_get_latest_returns_none_when_empty(self, app_context):
        sec = _add_security("MSFT", "USD")
        with AppContext.read_session() as sess:
            row = PriceRepository(sess).get_latest(sec.id)
        assert row is None

    def test_get_latest_returns_most_recent(self, app_context):
        sec = _add_security("MSFT", "USD")
        older = date(2025, 1, 1)
        newer = date(2025, 1, 10)
        with AppContext.write_session() as sess:
            repo = PriceRepository(sess)
            repo.upsert(
                security_id=sec.id,
                price_date=older,
                close_price_original_ccy="100.00",
                currency="USD",
                source="google_sheets:",
                close_price_gbp="79.00",
            )
            repo.upsert(
                security_id=sec.id,
                price_date=newer,
                close_price_original_ccy="110.00",
                currency="USD",
                source="google_sheets:",
                close_price_gbp="87.00",
            )
        with AppContext.read_session() as sess:
            row = PriceRepository(sess).get_latest(sec.id)
        assert row is not None
        assert row.price_date == newer
        assert row.close_price_gbp == "87.00"

    def test_list_latest_all_returns_one_per_security(self, app_context):
        sec_a = _add_security("AAPL", "USD")
        sec_b = _add_security("MSFT", "USD")
        today = date(2025, 1, 10)
        with AppContext.write_session() as sess:
            repo = PriceRepository(sess)
            repo.upsert(sec_a.id, today, "150.00", "USD", "google_sheets:", close_price_gbp="118.50")
            repo.upsert(sec_b.id, today, "300.00", "USD", "google_sheets:", close_price_gbp="237.00")
        with AppContext.read_session() as sess:
            rows = PriceRepository(sess).list_latest_all()
        security_ids = {r.security_id for r in rows}
        assert sec_a.id in security_ids
        assert sec_b.id in security_ids

    def test_get_latest_before_returns_previous_date(self, app_context):
        sec = _add_security("NVDA", "USD")
        older = date(2025, 1, 5)
        newer = date(2025, 1, 10)
        with AppContext.write_session() as sess:
            repo = PriceRepository(sess)
            repo.upsert(sec.id, older, "100.00", "USD", "google_sheets:", close_price_gbp="79.00")
            repo.upsert(sec.id, newer, "120.00", "USD", "google_sheets:", close_price_gbp="94.80")
        with AppContext.read_session() as sess:
            row = PriceRepository(sess).get_latest_before(sec.id, newer)
        assert row is not None
        assert row.price_date == older
        assert row.close_price_gbp == "79.00"

    def test_get_earliest_price_date(self, app_context):
        sec = _add_security("AMD", "USD")
        with AppContext.write_session() as sess:
            repo = PriceRepository(sess)
            repo.upsert(sec.id, date(2025, 1, 5), "100.00", "USD", "google_sheets:", close_price_gbp="79.00")
            repo.upsert(sec.id, date(2025, 1, 10), "120.00", "USD", "google_sheets:", close_price_gbp="94.80")
        with AppContext.read_session() as sess:
            earliest = PriceRepository(sess).get_earliest_price_date(sec.id)
        assert earliest == date(2025, 1, 5)

    def test_ticker_snapshot_latest_and_run_start(self, app_context):
        sec = _add_security("RUNSTART", "USD")
        t0 = datetime(2026, 2, 24, 14, 0, 0, tzinfo=timezone.utc)
        with AppContext.write_session() as sess:
            repo = PriceRepository(sess)
            repo.add_ticker_snapshot(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                price_gbp="10.0000",
                observed_at=t0,
            )
            repo.add_ticker_snapshot(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                price_gbp="10.0000",
                observed_at=t0 + timedelta(minutes=10),
            )
            repo.add_ticker_snapshot(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                price_gbp="11.0000",
                observed_at=t0 + timedelta(minutes=20),
            )
            repo.add_ticker_snapshot(
                security_id=sec.id,
                price_date=date(2026, 2, 24),
                price_gbp="11.0000",
                observed_at=t0 + timedelta(minutes=30),
            )

        with AppContext.read_session() as sess:
            repo = PriceRepository(sess)
            latest = repo.get_latest_ticker_snapshot(sec.id)
            run_start = repo.get_current_price_run_started_at(sec.id)

        assert latest is not None
        assert latest.price_gbp == "11.0000"
        assert run_start == (t0 + timedelta(minutes=20)).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# PriceService.get_latest — service-level read
# ---------------------------------------------------------------------------

class TestPriceServiceGetLatest:
    def test_returns_none_for_security_with_no_prices(self, app_context):
        sec = _add_security("TSLA", "USD")
        result = PriceService.get_latest(sec.id)
        assert result is None

    def test_returns_snapshot_after_upsert(self, app_context):
        sec = _add_security("TSLA", "USD")
        today = date(2025, 1, 15)
        price_ts = "2025-01-15 09:00:00"
        fx_ts = "2025-01-15 08:58:00"
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="250.00",
                currency="USD",
                source=_build_source_with_fx(price_ts, fx_ts),
                close_price_gbp="197.50",
            )
        snap = PriceService.get_latest(sec.id)
        assert snap is not None
        assert snap.security_id == sec.id
        assert snap.price_gbp == Decimal("197.50")
        assert snap.close_price_original_ccy == Decimal("250.00")
        assert snap.currency == "USD"
        assert snap.as_of == today
        assert snap.sheets_timestamp == price_ts
        assert snap.fx_as_of == fx_ts

    def test_fx_as_of_none_for_gbp_security(self, app_context):
        sec = _add_security("X", "GBP")
        today = date(2025, 1, 15)
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="10.00",
                currency="GBP",
                source=_build_source("2025-01-15 10:00:00"),
                close_price_gbp="10.00",
            )
        snap = PriceService.get_latest(sec.id)
        assert snap is not None
        assert snap.fx_as_of is None

    def test_sheets_timestamp_none_for_non_sheets_source(self, app_context):
        sec = _add_security("X", "GBP")
        today = date(2025, 1, 15)
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="10.00",
                currency="GBP",
                source="manual",
                close_price_gbp="10.00",
            )
        snap = PriceService.get_latest(sec.id)
        assert snap is not None
        assert snap.sheets_timestamp is None
        assert snap.fx_as_of is None


# ---------------------------------------------------------------------------
# PriceService.fetch_and_store — mocked SheetsPriceService + SheetsFxService
# ---------------------------------------------------------------------------

class TestPriceServiceFetchAndStore:
    def _mock_sheet(self, *rows: SheetRow):
        return patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=_sheet_prices(*rows),
        )

    def _mock_fx(self, *rows: FxRow):
        return patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value=_fx_rates(*rows),
        )

    def test_gbp_security_stored_correctly(self, app_context):
        """GBP price from sheet stored as-is — no FX call needed."""
        sec = _add_security("BARC", "GBP")
        sheet_row = SheetRow(ticker="BARC", price=Decimal("150.50"), currency="GBP", last_refresh="2025-01-15 10:00:00")
        with self._mock_sheet(sheet_row):
            snap = PriceService.fetch_and_store(sec.id)

        assert snap.security_id == sec.id
        assert snap.currency == "GBP"
        assert snap.price_gbp == Decimal("150.5000")
        assert snap.fx_as_of is None
        assert snap.sheets_timestamp == "2025-01-15 10:00:00"

    def test_usd_security_converted_to_gbp(self, app_context):
        """USD price from sheet is multiplied by USD2GBP rate to produce GBP price."""
        sec = _add_security("IBM", "USD")
        sheet_row = SheetRow(ticker="IBM", price=Decimal("257.16"), currency="USD", last_refresh="2025-01-15 10:00:00")
        fx_row = FxRow(pair="USD2GBP", rate=Decimal("0.79"), as_of="2025-01-15 09:58:00")
        with self._mock_sheet(sheet_row), self._mock_fx(fx_row):
            snap = PriceService.fetch_and_store(sec.id)

        assert snap.currency == "USD"
        assert snap.close_price_original_ccy == Decimal("257.16")
        # 257.16 × 0.79 = 203.1564
        assert snap.price_gbp == Decimal("203.1564")
        assert snap.fx_as_of == "2025-01-15 09:58:00"
        assert snap.sheets_timestamp == "2025-01-15 10:00:00"

    def test_usd_price_persisted_as_gbp(self, app_context):
        """DB row stores original USD in close_price_original_ccy and GBP in close_price_gbp."""
        sec = _add_security("IBM", "USD")
        sheet_row = SheetRow(ticker="IBM", price=Decimal("257.16"), currency="USD", last_refresh="")
        fx_row = FxRow(pair="USD2GBP", rate=Decimal("0.79"), as_of="")
        with self._mock_sheet(sheet_row), self._mock_fx(fx_row):
            PriceService.fetch_and_store(sec.id)

        with AppContext.read_session() as sess:
            row = PriceRepository(sess).get_latest(sec.id)
        assert row is not None
        assert row.close_price_original_ccy == "257.16"
        assert row.close_price_gbp == "203.1564"
        assert row.currency == "USD"

    def test_gbp_price_persisted_correctly(self, app_context):
        """fetch_and_store writes to price_history; get_latest returns it."""
        sec = _add_security("GOOG", "USD")
        sheet_row = SheetRow(ticker="GOOG", price=Decimal("170.00"), currency="USD", last_refresh="2025-01-15 11:00:00")
        fx_row = FxRow(pair="USD2GBP", rate=Decimal("0.80"), as_of="2025-01-15 10:58:00")
        with self._mock_sheet(sheet_row), self._mock_fx(fx_row):
            PriceService.fetch_and_store(sec.id)

        snap = PriceService.get_latest(sec.id)
        assert snap is not None
        # 170.00 × 0.80 = 136.0000
        assert snap.price_gbp == Decimal("136.0000")
        assert snap.sheets_timestamp == "2025-01-15 11:00:00"
        assert snap.fx_as_of == "2025-01-15 10:58:00"

    def test_case_insensitive_ticker_match(self, app_context):
        """Ticker lookup in sheet is case-insensitive."""
        sec = _add_security("msft", "USD")  # stored lowercase
        sheet_row = SheetRow(ticker="MSFT", price=Decimal("300.00"), currency="USD", last_refresh="")
        fx_row = FxRow(pair="USD2GBP", rate=Decimal("0.80"), as_of="")
        with self._mock_sheet(sheet_row), self._mock_fx(fx_row):
            snap = PriceService.fetch_and_store(sec.id)

        # 300.00 × 0.80 = 240.0000
        assert snap.price_gbp == Decimal("240.0000")

    def test_raises_on_unknown_security(self, app_context):
        with pytest.raises(ValueError, match="not found"):
            PriceService.fetch_and_store("nonexistent-id")

    def test_raises_when_ticker_not_in_sheet(self, app_context):
        sec = _add_security("NOTINSHEET", "USD")
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value={},
        ):
            with pytest.raises(RuntimeError, match="not found in Google Sheets"):
                PriceService.fetch_and_store(sec.id)

    def test_raises_when_usd2gbp_not_in_fx_tab(self, app_context):
        """USD security fails gracefully if USD2GBP pair is missing from fx tab."""
        sec = _add_security("IBM", "USD")
        sheet_row = SheetRow(ticker="IBM", price=Decimal("257.16"), currency="USD", last_refresh="")
        with self._mock_sheet(sheet_row), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value={},  # empty — no USD2GBP
        ):
            with pytest.raises(RuntimeError, match="USD2GBP"):
                PriceService.fetch_and_store(sec.id)

    def test_empty_timestamp_gives_none_fx_as_of(self, app_context):
        sec = _add_security("AMZN", "USD")
        sheet_row = SheetRow(ticker="AMZN", price=Decimal("180.00"), currency="USD", last_refresh="")
        fx_row = FxRow(pair="USD2GBP", rate=Decimal("0.79"), as_of="")
        with self._mock_sheet(sheet_row), self._mock_fx(fx_row):
            snap = PriceService.fetch_and_store(sec.id)
        assert snap.fx_as_of is None
        assert snap.sheets_timestamp is None


# ---------------------------------------------------------------------------
# GBX normalisation (handled by SheetsPriceService, verified via PriceService)
# ---------------------------------------------------------------------------

class TestGbxNormalisation:
    """
    GBX→GBP normalisation happens inside SheetsPriceService.read_prices.
    These tests verify that PriceService stores the already-normalised GBP
    value correctly (no further FX conversion needed).
    """

    def test_gbx_stock_stored_as_gbp(self, app_context):
        sec = _add_security("LLOY", "GBP")
        # SheetsPriceService normalises GBX 64.78 → GBP 0.6478
        sheet_row = SheetRow(ticker="LLOY", price=Decimal("0.6478"), currency="GBP", last_refresh="2025-01-15 10:00:00")
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=_sheet_prices(sheet_row),
        ):
            snap = PriceService.fetch_and_store(sec.id)

        assert snap.currency == "GBP"
        assert snap.price_gbp == Decimal("0.6478")
        assert snap.fx_as_of is None


# ---------------------------------------------------------------------------
# PriceService.fetch_all — mocked, counts fetched/failed
# ---------------------------------------------------------------------------

class TestPriceServiceFetchAll:
    def test_fetch_all_counts(self, app_context):
        """fetch_all returns correct fetched/failed counts for matched tickers."""
        _add_security("AAPL", "USD")
        _add_security("MSFT", "USD")
        sheet_data = _sheet_prices(
            SheetRow("AAPL", Decimal("170.00"), "USD", "2025-01-15 09:00:00"),
            SheetRow("MSFT", Decimal("300.00"), "USD", "2025-01-15 09:00:00"),
        )
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value=_usd2gbp("0.79"),
        ):
            result = PriceService.fetch_all()

        assert result["fetched"] == 2
        assert result["failed"] == 0
        assert result["errors"] == []

    def test_fetch_all_stores_gbp_converted_price(self, app_context):
        """USD prices are converted to GBP in the DB after fetch_all."""
        sec = _add_security("IBM", "USD")
        sheet_data = _sheet_prices(
            SheetRow("IBM", Decimal("257.16"), "USD", "2025-01-15 09:00:00"),
        )
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value=_usd2gbp("0.79", "2025-01-15 08:58:00"),
        ):
            PriceService.fetch_all()

        with AppContext.read_session() as sess:
            row = PriceRepository(sess).get_latest(sec.id)
        assert row is not None
        assert row.close_price_original_ccy == "257.16"
        # 257.16 × 0.79 = 203.1564
        assert row.close_price_gbp == "203.1564"
        assert "|fx:2025-01-15 08:58:00" in row.source

    def test_fetch_all_writes_ticker_snapshot_for_freshness_tracking(self, app_context):
        sec = _add_security("SNAP", "USD")
        prev_date = date.today() - timedelta(days=1)
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=prev_date,
                close_price_original_ccy="250.00",
                currency="USD",
                source="yfinance_history",
                close_price_gbp="200.0000",
            )

        sheet_data = _sheet_prices(
            SheetRow("SNAP", Decimal("260.00"), "USD", "2026-02-24 12:00:00"),
        )
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value=_usd2gbp("0.80", "2026-02-24 11:58:00"),
        ):
            result = PriceService.fetch_all()

        assert result["fetched"] == 1
        with AppContext.read_session() as sess:
            snapshot = PriceRepository(sess).get_latest_ticker_snapshot(sec.id)
        assert snapshot is not None
        assert snapshot.price_gbp == "208.0000"
        assert snapshot.direction == "up"
        assert snapshot.percent_change == "4.00"

    def test_fetch_all_records_missing_ticker(self, app_context):
        """Security not in the sheet is recorded as a failure, not an exception."""
        _add_security("AAPL", "USD")
        _add_security("MISSING", "USD")
        sheet_data = _sheet_prices(
            SheetRow("AAPL", Decimal("170.00"), "USD", ""),
        )
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value=_usd2gbp(),
        ):
            result = PriceService.fetch_all()

        assert result["fetched"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert "MISSING" in result["errors"][0]["ticker"]

    def test_fetch_all_usd_fails_when_fx_pair_missing(self, app_context):
        """USD security fails if USD2GBP pair is absent from the fx tab."""
        _add_security("IBM", "USD")
        sheet_data = _sheet_prices(
            SheetRow("IBM", Decimal("257.16"), "USD", ""),
        )
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value={},  # empty fx tab
        ):
            result = PriceService.fetch_all()

        assert result["fetched"] == 0
        assert result["failed"] == 1
        assert "USD2GBP" in result["errors"][0]["error"]

    def test_fetch_all_gbp_succeeds_when_fx_tab_unavailable(self, app_context):
        """GBP securities store correctly even if the fx tab is unavailable."""
        _add_security("BARC", "GBP")
        sheet_data = _sheet_prices(
            SheetRow("BARC", Decimal("150.50"), "GBP", ""),
        )
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            side_effect=RuntimeError("fx tab unavailable"),
        ):
            result = PriceService.fetch_all()

        # GBP security should succeed because it doesn't need FX conversion
        assert result["fetched"] == 1
        assert result["failed"] == 0

    def test_fetch_all_sheet_unavailable(self, app_context):
        """If prices Sheet raises, all securities are returned as failed gracefully."""
        _add_security("AAPL", "USD")
        _add_security("MSFT", "USD")
        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            side_effect=RuntimeError("Sheet unavailable"),
        ):
            result = PriceService.fetch_all()

        assert result["fetched"] == 0
        assert result["failed"] == 2
        assert all("Sheet unavailable" in e["error"] for e in result["errors"])

    def test_fetch_all_partial_failure(self, app_context):
        """A DB write failure for one ticker does not abort the loop."""
        _add_security("AAPL", "USD")
        _add_security("MSFT", "USD")
        sheet_data = _sheet_prices(
            SheetRow("AAPL", Decimal("170.00"), "USD", ""),
            SheetRow("MSFT", Decimal("300.00"), "USD", ""),
        )

        original_upsert = PriceRepository.upsert
        call_count = [0]

        def failing_upsert(self, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("DB write error")
            return original_upsert(self, *args, **kwargs)

        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value=_usd2gbp(),
        ), patch.object(PriceRepository, "upsert", failing_upsert):
            result = PriceService.fetch_all()

        assert result["fetched"] == 1
        assert result["failed"] == 1

    def test_fetch_all_backfills_history_from_acquisition_date(self, app_context):
        """When lots are added late, fetch_all backfills missing daily history."""
        from src.services.portfolio_service import PortfolioService

        sec = _add_security("BTI", "GBP")
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2025, 1, 2),
            quantity=Decimal("10"),
            acquisition_price_gbp=Decimal("10"),
            true_cost_per_share_gbp=Decimal("10"),
        )

        sheet_data = _sheet_prices(
            SheetRow("BTI", Decimal("11.00"), "GBP", "2025-01-10 09:00:00"),
        )
        history_rows = {
            date(2025, 1, 2): Decimal("10.10"),
            date(2025, 1, 3): Decimal("10.20"),
            date(2025, 1, 6): Decimal("10.40"),
        }

        with patch(
            "src.services.price_service.SheetsPriceService.read_prices",
            return_value=sheet_data,
        ), patch(
            "src.services.price_service.SheetsFxService.read_fx_rates",
            return_value={},
        ), patch(
            "src.services.price_service._read_history_closes",
            return_value=history_rows,
        ):
            result = PriceService.fetch_all()

        assert result["fetched"] == 1
        assert result["failed"] == 0
        assert result["backfilled_days"] == 3

        with AppContext.read_session() as sess:
            from sqlalchemy import select
            from src.db.models import PriceHistory

            rows = list(
                sess.scalars(
                    select(PriceHistory)
                    .where(PriceHistory.security_id == sec.id)
                    .order_by(PriceHistory.price_date.asc(), PriceHistory.source.asc())
                )
            )

        sources_by_date = {(r.price_date, r.source) for r in rows}
        assert (date(2025, 1, 2), "yfinance_history") in sources_by_date
        assert (date(2025, 1, 3), "yfinance_history") in sources_by_date
        assert (date(2025, 1, 6), "yfinance_history") in sources_by_date


# ---------------------------------------------------------------------------
# Integration: get_portfolio_summary includes price data + price_refreshed_at
# ---------------------------------------------------------------------------

class TestPortfolioSummaryWithPrices:
    def test_summary_includes_market_value_when_price_exists(self, app_context):
        from src.services.portfolio_service import PortfolioService
        sec = _add_security("IBM", "USD")
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("100"),
            acquisition_price_gbp=Decimal("140"),
            true_cost_per_share_gbp=Decimal("140"),
        )
        today = date(2025, 1, 10)
        price_ts = "2025-01-10 08:30:00"
        fx_ts = "2025-01-10 08:28:00"
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="180.00",   # USD
                currency="USD",
                source=_build_source_with_fx(price_ts, fx_ts),
                close_price_gbp="142.20",             # GBP after conversion
            )
        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.current_price_gbp == Decimal("142.20")
        assert ss.market_value_gbp == Decimal("14220.00")
        assert ss.unrealised_gain_cgt_gbp == Decimal("220.00")
        assert ss.price_as_of == today
        assert ss.price_refreshed_at == price_ts
        assert ss.fx_as_of == fx_ts
        assert summary.total_market_value_gbp == Decimal("14220.00")
        assert summary.fx_as_of == fx_ts

    def test_summary_price_refreshed_at_none_without_price(self, app_context):
        from src.services.portfolio_service import PortfolioService
        sec = _add_security("IBM", "USD")
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("50"),
            acquisition_price_gbp=Decimal("150"),
            true_cost_per_share_gbp=Decimal("150"),
        )
        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.current_price_gbp is None
        assert ss.price_refreshed_at is None
        assert ss.fx_as_of is None
        assert summary.fx_as_of is None

    def test_summary_fx_as_of_none_for_gbp_security(self, app_context):
        from src.services.portfolio_service import PortfolioService
        sec = _add_security("BARC", "GBP")
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("50"),
            acquisition_price_gbp=Decimal("1.50"),
            true_cost_per_share_gbp=Decimal("1.50"),
        )
        today = date(2025, 1, 10)
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="1.65",
                currency="GBP",
                source=_build_source("2025-01-10 08:30:00"),
                close_price_gbp="1.65",
            )
        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.fx_as_of is None
        assert summary.fx_as_of is None
        assert summary.fx_is_stale is False

    def test_summary_price_refreshed_at_none_for_non_sheets_source(self, app_context):
        from src.services.portfolio_service import PortfolioService
        sec = _add_security("IBM", "USD")
        PortfolioService.add_lot(
            security_id=sec.id,
            scheme_type="BROKERAGE",
            acquisition_date=date(2024, 1, 1),
            quantity=Decimal("50"),
            acquisition_price_gbp=Decimal("150"),
            true_cost_per_share_gbp=Decimal("150"),
        )
        today = date(2025, 1, 10)
        with AppContext.write_session() as sess:
            PriceRepository(sess).upsert(
                security_id=sec.id,
                price_date=today,
                close_price_original_ccy="190.00",
                currency="USD",
                source="manual",
                close_price_gbp="155.00",
            )
        summary = PortfolioService.get_portfolio_summary()
        ss = summary.securities[0]
        assert ss.price_refreshed_at is None
        assert ss.fx_as_of is None
