"""
DividendService - additive dividend workflow and dashboard payloads.

Scope:
- Dividend entry creation.
- Trailing/forecast totals.
- Tax-year dividend tax estimation and net-return view.

No changes to existing portfolio/tax engines.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..app_context import AppContext
from ..core.tax_engine import calculate_dividend_tax, get_bands, tax_year_for_date
from ..core.tax_engine.income_tax import personal_allowance
from ..db.repository import DividendEntryRepository, SecurityRepository
from ..settings import AppSettings

_GBP_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_VALID_TREATMENTS = frozenset({"TAXABLE", "ISA_EXEMPT"})


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(_q_money(value))


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


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
    def add_dividend_entry(
        *,
        security_id: str,
        dividend_date: date,
        amount_gbp: Decimal,
        tax_treatment: str = "TAXABLE",
        source: str | None = "manual",
        notes: str | None = None,
    ) -> dict[str, str]:
        if amount_gbp <= Decimal("0"):
            raise ValueError("amount_gbp must be greater than zero.")
        normalized_treatment = (tax_treatment or "").strip().upper()
        if normalized_treatment not in _VALID_TREATMENTS:
            raise ValueError("tax_treatment must be one of ['TAXABLE', 'ISA_EXEMPT'].")

        with AppContext.write_session() as sess:
            sec_repo = SecurityRepository(sess)
            sec = sec_repo.require_by_id(security_id)
            entry = DividendEntryRepository(sess).add(
                security_id=security_id,
                dividend_date=dividend_date,
                amount_gbp=_q_money(amount_gbp),
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
                "amount_gbp": str(_q_money(amount_gbp)),
                "tax_treatment": normalized_treatment,
                "source": entry.source or "",
                "notes": entry.notes or "",
            }

    @staticmethod
    def get_summary(
        *,
        settings: AppSettings | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        generated_at_utc = datetime.now(timezone.utc).isoformat()

        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            div_repo = DividendEntryRepository(sess)
            securities = sec_repo.list_all()
            entries = div_repo.list_all()

        security_map = {sec.id: sec for sec in securities}
        security_options = [
            {"id": sec.id, "ticker": sec.ticker, "name": sec.name}
            for sec in sorted(securities, key=lambda s: (s.ticker, s.id))
        ]

        hide_values = bool(settings and settings.hide_values)
        if hide_values:
            return {
                "generated_at_utc": generated_at_utc,
                "as_of_date": as_of_date.isoformat(),
                "hide_values": True,
                "security_options": security_options,
                "summary": {
                    "trailing_12m_total_gbp": None,
                    "forecast_12m_total_gbp": None,
                    "all_time_total_gbp": None,
                    "estimated_tax_gbp": None,
                    "estimated_net_dividends_gbp": None,
                    "tax_drag_pct": None,
                },
                "tax_years": [],
                "entries": [],
                "notes": ["Dividend values are hidden while privacy mode is enabled."],
            }

        trailing_start = as_of_date - timedelta(days=365)
        forecast_end = as_of_date + timedelta(days=365)

        entry_rows: list[dict[str, Any]] = []
        all_time_total = Decimal("0")
        all_time_taxable = Decimal("0")
        all_time_isa = Decimal("0")
        trailing_total = Decimal("0")
        forecast_total = Decimal("0")

        buckets: dict[str, dict[str, Decimal | int]] = {}

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

            all_time_total += amount
            if is_taxable:
                all_time_taxable += amount
            else:
                all_time_isa += amount

            if trailing_start <= entry.dividend_date <= as_of_date:
                trailing_total += amount
            if as_of_date < entry.dividend_date <= forecast_end:
                forecast_total += amount

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
            else:
                bucket["isa_exempt_dividends"] = (
                    Decimal(bucket["isa_exempt_dividends"]) + amount
                )

            entry_rows.append(
                {
                    "id": entry.id,
                    "security_id": entry.security_id,
                    "ticker": ticker,
                    "dividend_date": entry.dividend_date.isoformat(),
                    "tax_year": tax_year,
                    "amount_gbp": _money_str(amount),
                    "tax_treatment": treatment,
                    "source": entry.source,
                    "notes": entry.notes,
                    "is_forecast": entry.dividend_date > as_of_date,
                }
            )

        entry_rows.sort(key=lambda row: (row["dividend_date"], row["id"]), reverse=True)

        tax_year_rows: list[dict[str, Any]] = []
        estimated_tax_total = Decimal("0")
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

        return {
            "generated_at_utc": generated_at_utc,
            "as_of_date": as_of_date.isoformat(),
            "hide_values": False,
            "security_options": security_options,
            "summary": {
                "trailing_12m_total_gbp": _money_str(trailing_total),
                "forecast_12m_total_gbp": _money_str(forecast_total),
                "all_time_total_gbp": _money_str(all_time_total),
                "all_time_taxable_dividends_gbp": _money_str(all_time_taxable),
                "all_time_isa_exempt_dividends_gbp": _money_str(all_time_isa),
                "estimated_tax_gbp": _money_str(estimated_tax_total),
                "estimated_net_dividends_gbp": _money_str(estimated_net_total),
                "tax_drag_pct": str(tax_drag_pct),
            },
            "tax_years": tax_year_rows,
            "entries": entry_rows,
            "notes": notes,
        }
