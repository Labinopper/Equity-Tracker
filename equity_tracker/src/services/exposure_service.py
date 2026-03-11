"""
ExposureService - concentration and deployable-capital diagnostics.

Scope:
- Deterministic gross/sellable concentration metrics.
- Deterministic locked vs forfeitable value split.
- Deployable-capital blend: sellable holdings + GBP BROKER/BANK cash.
- Employer dependence ratio with explicit component breakdown.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..settings import AppSettings
from .cash_ledger_service import CONTAINER_BANK, CONTAINER_BROKER, CashLedgerService
from .fx_service import FxService

_MONEY_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_HUNDRED = Decimal("100")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= Decimal("0"):
        return Decimal("0.00")
    return _q_pct((part / whole) * _HUNDRED)


def _normalize_ticker(value: object) -> str | None:
    raw = str(value or "").strip().upper()
    return raw or None


def _settings_employer_ticker(settings: AppSettings | None) -> str | None:
    if settings is None:
        return None
    return _normalize_ticker(getattr(settings, "employer_ticker", ""))


def _settings_income_dependency_pct(settings: AppSettings | None) -> Decimal:
    if settings is None:
        return Decimal("0.00")
    raw = _to_decimal(getattr(settings, "employer_income_dependency_pct", Decimal("0")))
    if raw is None:
        return Decimal("0.00")
    if raw < Decimal("0"):
        return Decimal("0.00")
    if raw > Decimal("100"):
        return Decimal("100.00")
    return _q_pct(raw)


def _deployable_cash_gbp(db_path) -> tuple[Decimal, list[str]]:
    dashboard = CashLedgerService.dashboard(db_path=db_path)
    total = Decimal("0")
    fx_rates: dict[str, Decimal] = {"GBP": Decimal("1")}
    fx_converted: set[str] = set()
    fx_missing: set[str] = set()
    for row in dashboard.get("balances", []):
        if row.get("container") not in {CONTAINER_BROKER, CONTAINER_BANK}:
            continue
        currency = str(row.get("currency") or "").strip().upper()
        if not currency:
            continue
        amount = _to_decimal(row.get("balance"))
        if amount is None:
            continue
        if currency == "GBP":
            total += amount
            continue
        fx_rate = fx_rates.get(currency)
        if fx_rate is None:
            try:
                quote = FxService.get_rate(currency, "GBP")
            except Exception:
                fx_missing.add(currency)
                continue
            fx_rate = quote.rate
            fx_rates[currency] = fx_rate
            fx_converted.add(currency)
        total += amount * fx_rate

    notes: list[str] = []
    if fx_converted:
        notes.append(
            "Converted deployable non-GBP cash to GBP using live FX: "
            + ", ".join(sorted(fx_converted))
            + "."
        )
    if fx_missing:
        notes.append(
            "Excluded deployable non-GBP cash with unavailable FX to GBP: "
            + ", ".join(sorted(fx_missing))
            + "."
        )
    return _q_money(total), notes


def _top_bucket(values_by_ticker: dict[str, Decimal]) -> tuple[str | None, Decimal]:
    if not values_by_ticker:
        return None, Decimal("0.00")
    top_ticker, top_value = max(values_by_ticker.items(), key=lambda item: item[1])
    return top_ticker, _q_money(top_value)


class ExposureService:
    @staticmethod
    def get_snapshot(
        *,
        settings: AppSettings | None = None,
        db_path=None,
        summary,
    ) -> dict[str, Any]:
        gross_by_ticker: dict[str, Decimal] = {}
        sellable_by_ticker: dict[str, Decimal] = {}
        total_gross_market = Decimal("0")
        total_sellable_market = Decimal("0")
        locked_capital = Decimal("0")
        forfeitable_capital = Decimal("0")
        isa_wrapper_market = Decimal("0")
        taxable_wrapper_market = Decimal("0")
        unpriced_lot_count = 0

        for security_summary in summary.securities:
            ticker = _normalize_ticker(security_summary.security.ticker) or security_summary.security.id
            if security_summary.market_value_gbp is not None:
                gross_mv = _q_money(security_summary.market_value_gbp)
                total_gross_market += gross_mv
                gross_by_ticker[ticker] = gross_by_ticker.get(ticker, Decimal("0")) + gross_mv

            for lot_summary in security_summary.active_lots:
                mv = lot_summary.market_value_gbp
                if mv is None:
                    unpriced_lot_count += 1
                    continue
                lot_mv = _q_money(mv)
                if (lot_summary.lot.scheme_type or "").upper() == "ISA":
                    isa_wrapper_market += lot_mv
                else:
                    taxable_wrapper_market += lot_mv

                is_forfeitable_match = (
                    lot_summary.forfeiture_risk is not None
                    and lot_summary.forfeiture_risk.in_window
                    and lot_summary.lot.matching_lot_id is not None
                )
                if is_forfeitable_match:
                    forfeitable_capital += lot_mv
                    continue

                if (lot_summary.sellability_status or "").upper() == "LOCKED":
                    locked_capital += lot_mv
                    continue

                total_sellable_market += lot_mv
                sellable_by_ticker[ticker] = (
                    sellable_by_ticker.get(ticker, Decimal("0")) + lot_mv
                )

        sellable_total_q = _q_money(total_sellable_market)
        locked_q = _q_money(locked_capital)
        forfeitable_q = _q_money(forfeitable_capital)
        gross_total_q = _q_money(locked_q + forfeitable_q + sellable_total_q)
        wrapper_total = _q_money(isa_wrapper_market + taxable_wrapper_market)

        top_gross_ticker, top_gross_value = _top_bucket(gross_by_ticker)
        top_sellable_ticker, top_sellable_value = _top_bucket(sellable_by_ticker)

        employer_ticker = _settings_employer_ticker(settings)
        employer_gross = _q_money(gross_by_ticker.get(employer_ticker or "", Decimal("0")))
        employer_sellable = _q_money(sellable_by_ticker.get(employer_ticker or "", Decimal("0")))

        deployable_cash, deployable_cash_notes = _deployable_cash_gbp(db_path)
        deployable_capital = _q_money(sellable_total_q + deployable_cash)

        employer_income_dependency_pct = _settings_income_dependency_pct(settings)
        gross_income = (
            settings.default_gross_income
            if settings is not None
            else Decimal("0")
        )
        pension_sacrifice = (
            settings.default_pension_sacrifice
            if settings is not None
            else Decimal("0")
        )
        income_base = max(Decimal("0"), gross_income - pension_sacrifice)
        employer_income_dependency_proxy = _q_money(
            income_base * (employer_income_dependency_pct / _HUNDRED)
        )

        employer_dependence_denominator = _q_money(
            gross_total_q + deployable_cash + employer_income_dependency_proxy
        )
        employer_dependence_numerator = _q_money(
            employer_gross + employer_income_dependency_proxy
        )

        notes: list[str] = []
        if employer_ticker is None:
            notes.append("Employer ticker not configured; employer concentration defaults to 0%.")
        if unpriced_lot_count > 0:
            notes.append(
                f"{unpriced_lot_count} lot(s) excluded from concentration splits due to missing prices."
            )
        notes.extend(deployable_cash_notes)

        return {
            "top_holding_ticker_gross": top_gross_ticker,
            "top_holding_value_gross_gbp": top_gross_value,
            "top_holding_pct_gross": _pct(top_gross_value, gross_total_q),
            "top_holding_ticker_sellable": top_sellable_ticker,
            "top_holding_value_sellable_gbp": top_sellable_value,
            "top_holding_pct_sellable": _pct(top_sellable_value, sellable_total_q),
            "total_gross_market_value_gbp": gross_total_q,
            "total_sellable_market_value_gbp": sellable_total_q,
            "locked_capital_gbp": locked_q,
            "forfeitable_capital_gbp": forfeitable_q,
            "isa_wrapper_market_value_gbp": _q_money(isa_wrapper_market),
            "taxable_wrapper_market_value_gbp": _q_money(taxable_wrapper_market),
            "isa_wrapper_pct_of_total": _pct(isa_wrapper_market, wrapper_total),
            "taxable_wrapper_pct_of_total": _pct(taxable_wrapper_market, wrapper_total),
            "employer_ticker": employer_ticker,
            "employer_market_value_gbp": employer_gross,
            "employer_sellable_market_value_gbp": employer_sellable,
            "employer_pct_of_gross": _pct(employer_gross, gross_total_q),
            "employer_pct_of_sellable": _pct(employer_sellable, sellable_total_q),
            "deployable_cash_gbp": deployable_cash,
            "deployable_capital_gbp": deployable_capital,
            "employer_share_of_deployable_pct": _pct(employer_sellable, deployable_capital),
            "employer_income_dependency_pct": employer_income_dependency_pct,
            "employer_income_dependency_proxy_gbp": employer_income_dependency_proxy,
            "employer_dependence_denominator_gbp": employer_dependence_denominator,
            "employer_dependence_numerator_gbp": employer_dependence_numerator,
            "employer_dependence_ratio_pct": _pct(
                employer_dependence_numerator,
                employer_dependence_denominator,
            ),
            "notes": notes,
        }
