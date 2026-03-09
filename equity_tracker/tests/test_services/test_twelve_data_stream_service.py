from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.services.twelve_data_stream_service import TwelveDataStreamService


@dataclass
class _FakeSecurity:
    id: str
    ticker: str
    exchange: str | None
    currency: str


@dataclass
class _FakeSummary:
    security: _FakeSecurity
    total_quantity: Decimal
    market_value_gbp: Decimal | None
    total_true_cost_gbp: Decimal
    total_cost_basis_gbp: Decimal


@dataclass
class _FakePortfolioSummary:
    securities: list[_FakeSummary]


def _reset_stream_state() -> None:
    TwelveDataStreamService._active_security_ids = set()
    TwelveDataStreamService._active_symbols = set()
    TwelveDataStreamService._symbol_meta = {}
    TwelveDataStreamService._desired_symbols = set()
    TwelveDataStreamService._last_error = None
    TwelveDataStreamService._last_message_at = None
    TwelveDataStreamService._connected = False
    TwelveDataStreamService._eligibility_cache = None


def test_subscription_failure_enters_cooldown_and_persists(monkeypatch, tmp_path):
    _reset_stream_state()
    monkeypatch.setenv("EQUITY_DB_PATH", str(tmp_path / "portfolio.db"))
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(TwelveDataStreamService, "_utc_now", staticmethod(lambda: now))

    TwelveDataStreamService._handle_status_event(
        {
            "event": "subscribe-status",
            "fails": [{"symbol": "IBM", "exchange": "NYSE", "reason": "not entitled"}],
        }
    )

    health = TwelveDataStreamService.health_snapshot()
    assert health["rejected_symbols"][0]["symbol"] == "IBM:NYSE"
    assert health["rejected_symbols"][0]["status"] == "cooldown"
    assert "IBM:NYSE" not in health["symbols"]
    assert TwelveDataStreamService._is_symbol_eligible("IBM:NYSE", now_utc=now) is False


def test_subscription_success_clears_cooldown(monkeypatch, tmp_path):
    _reset_stream_state()
    monkeypatch.setenv("EQUITY_DB_PATH", str(tmp_path / "portfolio.db"))
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(TwelveDataStreamService, "_utc_now", staticmethod(lambda: now))

    TwelveDataStreamService._mark_subscription_failure("IBM:NYSE", reason="not entitled", now_utc=now)
    TwelveDataStreamService._handle_status_event(
        {
            "event": "subscribe-status",
            "success": [{"symbol": "IBM", "exchange": "NYSE", "type": "COMMON_STOCK"}],
        }
    )

    health = TwelveDataStreamService.health_snapshot()
    assert health["rejected_symbols"] == []
    assert "IBM:NYSE" in health["last_successful_subscription"]
    assert TwelveDataStreamService._is_symbol_eligible("IBM:NYSE", now_utc=now) is True


def test_build_candidates_skips_symbols_in_cooldown(monkeypatch, tmp_path):
    _reset_stream_state()
    monkeypatch.setenv("EQUITY_DB_PATH", str(tmp_path / "portfolio.db"))
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(TwelveDataStreamService, "_utc_now", staticmethod(lambda: now))
    monkeypatch.setattr(TwelveDataStreamService, "max_streams", staticmethod(lambda: 8))

    portfolio = _FakePortfolioSummary(
        securities=[
            _FakeSummary(
                security=_FakeSecurity("1", "IBM", "NYSE", "USD"),
                total_quantity=Decimal("10"),
                market_value_gbp=Decimal("1000"),
                total_true_cost_gbp=Decimal("900"),
                total_cost_basis_gbp=Decimal("900"),
            ),
            _FakeSummary(
                security=_FakeSecurity("2", "AAPL", "NASDAQ", "USD"),
                total_quantity=Decimal("5"),
                market_value_gbp=Decimal("800"),
                total_true_cost_gbp=Decimal("700"),
                total_cost_basis_gbp=Decimal("700"),
            ),
        ]
    )
    monkeypatch.setattr(
        "src.services.twelve_data_stream_service.PortfolioService.get_portfolio_summary",
        lambda as_of=None: portfolio,
    )

    TwelveDataStreamService._mark_subscription_failure("IBM:NYSE", reason="not entitled", now_utc=now)
    candidates = TwelveDataStreamService._build_candidates()

    request_symbols = [candidate.request_symbol for candidate in candidates]
    assert "IBM:NYSE" not in request_symbols
    assert "AAPL:NASDAQ" in request_symbols
    assert "USD/GBP" in request_symbols


def test_build_candidates_retries_symbol_after_cooldown(monkeypatch, tmp_path):
    _reset_stream_state()
    monkeypatch.setenv("EQUITY_DB_PATH", str(tmp_path / "portfolio.db"))
    failed_at = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    retry_at = failed_at + timedelta(hours=25)
    monkeypatch.setattr(TwelveDataStreamService, "_utc_now", staticmethod(lambda: retry_at))

    portfolio = _FakePortfolioSummary(
        securities=[
            _FakeSummary(
                security=_FakeSecurity("1", "IBM", "NYSE", "USD"),
                total_quantity=Decimal("10"),
                market_value_gbp=Decimal("1000"),
                total_true_cost_gbp=Decimal("900"),
                total_cost_basis_gbp=Decimal("900"),
            ),
        ]
    )
    monkeypatch.setattr(
        "src.services.twelve_data_stream_service.PortfolioService.get_portfolio_summary",
        lambda as_of=None: portfolio,
    )

    TwelveDataStreamService._mark_subscription_failure("IBM:NYSE", reason="not entitled", now_utc=failed_at)
    candidates = TwelveDataStreamService._build_candidates()

    assert any(candidate.request_symbol == "IBM:NYSE" for candidate in candidates)


def test_health_snapshot_reports_partial_streaming_with_polling_fallback(monkeypatch, tmp_path):
    _reset_stream_state()
    monkeypatch.setenv("EQUITY_DB_PATH", str(tmp_path / "portfolio.db"))
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(TwelveDataStreamService, "_utc_now", staticmethod(lambda: now))

    TwelveDataStreamService._connected = True
    TwelveDataStreamService._active_symbols = {"AAPL:NASDAQ"}
    TwelveDataStreamService._desired_symbols = {"AAPL:NASDAQ", "IBM:NYSE"}
    TwelveDataStreamService._mark_subscription_failure("IBM:NYSE", reason="not entitled", now_utc=now)
    TwelveDataStreamService._mark_subscription_success("AAPL:NASDAQ", now_utc=now)

    health = TwelveDataStreamService.health_snapshot()

    assert health["status"] == "partial_streaming"
    assert health["symbols"] == ["AAPL:NASDAQ"]
    assert health["desired_symbols"] == ["AAPL:NASDAQ", "IBM:NYSE"]
    assert health["rejected_symbols"][0]["symbol"] == "IBM:NYSE"
