"""
FxService - provider-agnostic FX quote resolution.

Default provider is the existing Google Sheets FX tab reader.

Capabilities:
- Direct pair lookup (e.g. USD2GBP)
- Inverse lookup (e.g. derive USD2GBP from GBP2USD)
- Multi-hop path lookup for generalized currency conversion
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping

from .sheets_fx_service import FxRow, SheetsFxService

_FX_TS_FMT = "%Y-%m-%d %H:%M:%S"
_SHEETS_SOURCE = "google_sheets_fx_tab"
_MAX_PATH_HOPS = 4


@dataclass(frozen=True)
class FxQuote:
    """Resolved FX quote from one currency to another."""

    from_currency: str
    to_currency: str
    rate: Decimal
    as_of: str | None
    source: str
    path: tuple[str, ...]


def _normalize_currency_code(raw_value: str) -> str:
    cleaned = (raw_value or "").strip().upper()
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise ValueError(f"currency must be a 3-letter ISO code: {raw_value!r}")
    return cleaned


def _parse_pair(pair: str) -> tuple[str, str] | None:
    cleaned = (pair or "").strip().upper()
    if len(cleaned) != 7 or cleaned[3] != "2":
        return None
    base = cleaned[:3]
    quote = cleaned[4:]
    if not base.isalpha() or not quote.isalpha():
        return None
    return base, quote


def _oldest_as_of(values: Iterable[str | None]) -> str | None:
    raw_vals = [v for v in values if v]
    if not raw_vals:
        return None

    parsed: list[datetime] = []
    for v in raw_vals:
        try:
            parsed.append(datetime.strptime(v, _FX_TS_FMT))
        except ValueError:
            continue
    if parsed:
        return min(parsed).strftime(_FX_TS_FMT)
    return raw_vals[0]


class FxService:
    """FX conversion service with pluggable provider-backed rates."""

    @staticmethod
    def read_rates() -> dict[str, FxRow]:
        """Read provider FX rows (pair -> FxRow)."""
        return SheetsFxService.read_fx_rates()

    @staticmethod
    def _resolve_direct_or_inverse(
        *,
        from_currency: str,
        to_currency: str,
        rates: Mapping[str, FxRow],
    ) -> FxQuote | None:
        direct_key = f"{from_currency}2{to_currency}"
        direct = rates.get(direct_key)
        if direct is not None and direct.rate > 0:
            return FxQuote(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=direct.rate,
                as_of=direct.as_of or None,
                source=_SHEETS_SOURCE,
                path=(direct_key,),
            )

        inverse_key = f"{to_currency}2{from_currency}"
        inverse = rates.get(inverse_key)
        if inverse is not None and inverse.rate > 0:
            return FxQuote(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=Decimal("1") / inverse.rate,
                as_of=inverse.as_of or None,
                source=_SHEETS_SOURCE,
                path=(inverse_key,),
            )
        return None

    @staticmethod
    def _build_graph(
        rates: Mapping[str, FxRow],
    ) -> dict[str, list[tuple[str, Decimal, str, str | None]]]:
        graph: dict[str, list[tuple[str, Decimal, str, str | None]]] = {}
        for key, row in rates.items():
            pair_name = (row.pair or key).strip().upper()
            pair = _parse_pair(pair_name)
            if pair is None:
                continue
            base, quote = pair
            rate = row.rate
            if rate <= 0:
                continue
            graph.setdefault(base, []).append((quote, rate, pair_name, row.as_of or None))
            graph.setdefault(quote, []).append(
                (base, Decimal("1") / rate, f"{quote}2{base}", row.as_of or None)
            )
        return graph

    @staticmethod
    def _resolve_via_path(
        *,
        from_currency: str,
        to_currency: str,
        rates: Mapping[str, FxRow],
    ) -> FxQuote | None:
        graph = FxService._build_graph(rates)
        if from_currency not in graph:
            return None

        queue = deque(
            [
                (
                    from_currency,
                    Decimal("1"),
                    tuple(),
                    tuple(),
                    0,
                )
            ]
        )
        # Track shallowest hop depth seen for each node.
        depth_seen: dict[str, int] = {from_currency: 0}

        while queue:
            current, acc_rate, path, as_of_path, hops = queue.popleft()
            if current == to_currency and path:
                return FxQuote(
                    from_currency=from_currency,
                    to_currency=to_currency,
                    rate=acc_rate,
                    as_of=_oldest_as_of(as_of_path),
                    source=_SHEETS_SOURCE,
                    path=path,
                )
            if hops >= _MAX_PATH_HOPS:
                continue

            for nxt, edge_rate, edge_label, edge_as_of in graph.get(current, []):
                next_depth = hops + 1
                prior_depth = depth_seen.get(nxt)
                if prior_depth is not None and prior_depth < next_depth:
                    continue
                depth_seen[nxt] = next_depth
                queue.append(
                    (
                        nxt,
                        acc_rate * edge_rate,
                        (*path, edge_label),
                        (*as_of_path, edge_as_of),
                        next_depth,
                    )
                )
        return None

    @staticmethod
    def get_rate(
        from_currency: str,
        to_currency: str,
        *,
        rates: Mapping[str, FxRow] | None = None,
    ) -> FxQuote:
        """
        Resolve FX quote from from_currency to to_currency.

        Args:
            from_currency: ISO currency code (e.g. USD).
            to_currency: ISO currency code (e.g. GBP).
            rates: Optional pre-loaded provider rates for batch use.
        """
        frm = _normalize_currency_code(from_currency)
        to = _normalize_currency_code(to_currency)
        if frm == to:
            return FxQuote(
                from_currency=frm,
                to_currency=to,
                rate=Decimal("1"),
                as_of=None,
                source="identity",
                path=(f"{frm}2{to}",),
            )

        fx_rows = rates if rates is not None else FxService.read_rates()

        direct = FxService._resolve_direct_or_inverse(
            from_currency=frm,
            to_currency=to,
            rates=fx_rows,
        )
        if direct is not None:
            return direct

        path_quote = FxService._resolve_via_path(
            from_currency=frm,
            to_currency=to,
            rates=fx_rows,
        )
        if path_quote is not None:
            return path_quote

        pair_hint = f"{frm}2{to}"
        raise RuntimeError(
            f"FX rate '{pair_hint}' was not found in the provider and no conversion path was available."
        )

    @staticmethod
    def get_rates_to_currency(
        currencies: Iterable[str],
        to_currency: str,
        *,
        rates: Mapping[str, FxRow] | None = None,
    ) -> dict[str, FxQuote]:
        """Resolve a set of currencies to one target currency."""
        target = _normalize_currency_code(to_currency)
        fx_rows = rates if rates is not None else FxService.read_rates()
        out: dict[str, FxQuote] = {}
        for currency in currencies:
            code = _normalize_currency_code(currency)
            out[code] = FxService.get_rate(code, target, rates=fx_rows)
        return out
