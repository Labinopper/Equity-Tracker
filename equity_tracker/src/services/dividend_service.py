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
from .portfolio_service import PortfolioService

_GBP_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_FX_Q = Decimal("0.000001")
_VALID_TREATMENTS = frozenset({"TAXABLE", "ISA_EXEMPT"})
_GBP = "GBP"


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


def _normalize_currency(value: str | None) -> str:
    cleaned = (value or "").strip().upper()
    if not cleaned:
        return _GBP
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise ValueError("original_currency must be a 3-letter ISO currency code.")
    return cleaned


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
                    raise ValueError(
                        "Non-GBP dividends require fx_rate_to_gbp or amount_gbp for conversion."
                    )
                normalized_fx_rate = _q_fx(normalized_amount_gbp / normalized_amount_original)
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
                    "actual_to_date_total_gbp": None,
                    "forecast_entry_total_gbp": None,
                    "actual_entry_count": None,
                    "forecast_entry_count": None,
                    "all_time_total_gbp": None,
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
                    "original_currency": original_currency,
                    "fx_rate_to_gbp": str(fx_rate_to_gbp) if fx_rate_to_gbp is not None else None,
                    "fx_rate_source": entry.fx_rate_source,
                    "tax_treatment": treatment,
                    "source": entry.source,
                    "notes": entry.notes,
                    "is_forecast": entry.dividend_date > as_of_date,
                }
            )

        entry_rows.sort(key=lambda row: (row["dividend_date"], row["id"]), reverse=True)

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
            if taxable_by_security and taxable > Decimal("0") and tax_result.total_dividend_tax > Decimal("0"):
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

        portfolio_summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
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
                "Security-level dividend allocation reconciles entry totals to current held capital."
            )

        return {
            "generated_at_utc": generated_at_utc,
            "as_of_date": as_of_date.isoformat(),
            "hide_values": False,
            "security_options": security_options,
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
                    "Allocated estimated tax uses tax-year taxable-dividend proportions by security.",
                    "Capital at risk after dividends = max(0, active true cost - allocated net dividends).",
                ],
            },
            "notes": notes,
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
