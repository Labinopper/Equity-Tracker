"""
FxService - provider-agnostic FX quote resolution.

Live provider follows EQUITY_FX_PROVIDER when set; otherwise it follows
EQUITY_PRICE_PROVIDER. Twelve Data can be used for live FX when configured,
with optional fallback to yfinance.

Capabilities:
- Direct pair lookup (e.g. USD2GBP)
- Inverse lookup (e.g. derive USD2GBP from GBP2USD)
- Multi-hop path lookup for generalized currency conversion (for explicit
  pre-loaded rates only).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from typing import Iterable, Mapping

from .sheets_fx_service import FxRow
from .twelve_data_price_service import TwelveDataPriceService

_FX_TS_FMT = "%Y-%m-%d %H:%M:%S"
_LIVE_SOURCE = "yfinance_fx"
_TWELVE_DATA_LIVE_SOURCE = "twelvedata_fx"
_MAX_PATH_HOPS = 4
_LIVE_CACHE_TTL = timedelta(minutes=1)
_TWELVE_DATA_LIVE_CACHE_TTL = timedelta(seconds=5)
_LIVE_FX_CACHE: dict[tuple[str, str, str], tuple[datetime, FxQuote]] = {}


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


def _current_live_fx_provider() -> str:
    explicit = (os.environ.get("EQUITY_FX_PROVIDER", "").strip().lower())
    if explicit in {"twelve_data", "yfinance"}:
        return explicit
    live_price_provider = os.environ.get("EQUITY_PRICE_PROVIDER", "yfinance").strip().lower()
    if live_price_provider == "twelve_data" and TwelveDataPriceService.is_configured():
        return "twelve_data"
    return "yfinance"


def _allow_twelve_data_fallback_to_yfinance() -> bool:
    return os.environ.get("EQUITY_TWELVE_DATA_FALLBACK_TO_YFINANCE", "false").strip().lower() == "true"


def _cache_key(provider: str, from_currency: str, to_currency: str) -> tuple[str, str, str]:
    return provider, from_currency, to_currency


def _get_cached_live_quote(provider: str, from_currency: str, to_currency: str) -> FxQuote | None:
    entry = _LIVE_FX_CACHE.get(_cache_key(provider, from_currency, to_currency))
    if entry is None:
        return None
    observed_at, quote = entry
    ttl = _TWELVE_DATA_LIVE_CACHE_TTL if provider == "twelve_data" else _LIVE_CACHE_TTL
    if datetime.now(timezone.utc) - observed_at > ttl:
        _LIVE_FX_CACHE.pop(_cache_key(provider, from_currency, to_currency), None)
        return None
    return quote


def _store_cached_live_quote(provider: str, quote: FxQuote) -> FxQuote:
    _LIVE_FX_CACHE[_cache_key(provider, quote.from_currency, quote.to_currency)] = (
        datetime.now(timezone.utc),
        quote,
    )
    return quote


class FxService:
    """FX conversion service with pluggable provider-backed rates."""

    @staticmethod
    def read_rates() -> dict[str, FxRow]:
        """
        Legacy bulk-rates hook.

        The runtime provider now resolves FX on-demand via yfinance, so no
        pre-loaded table is required by default.
        """
        return {}

    @staticmethod
    def current_live_provider() -> str:
        return _current_live_fx_provider()

    @staticmethod
    def uses_twelve_data() -> bool:
        return FxService.current_live_provider() == "twelve_data" and TwelveDataPriceService.is_configured()

    @staticmethod
    def _read_live_pair_yfinance(
        *,
        from_currency: str,
        to_currency: str,
    ) -> FxQuote | None:
        """
        Read an FX rate from yfinance using the canonical pair symbol.

        Example: USD->GBP uses symbol USDGBP=X.
        """
        symbol = f"{from_currency}{to_currency}=X"
        try:
            import yfinance as yf  # noqa: PLC0415
        except Exception as exc:  # pragma: no cover - dependency is present in app env
            raise RuntimeError("yfinance is unavailable for FX conversion.") from exc

        try:
            hist = yf.Ticker(symbol).history(
                period="5d",
                interval="1d",
                auto_adjust=False,
                actions=False,
            )
        except Exception:
            return None

        if hist is None or hist.empty or "Close" not in hist.columns:
            return None

        close_series = hist["Close"].dropna()
        if close_series.empty:
            return None

        try:
            rate = Decimal(str(close_series.iloc[-1]))
        except Exception:
            return None

        if rate <= 0:
            return None

        as_of = datetime.now(timezone.utc).strftime(_FX_TS_FMT)
        pair_label = f"{from_currency}2{to_currency}"
        return FxQuote(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate,
            as_of=as_of,
            source=_LIVE_SOURCE,
            path=(pair_label,),
        )

    @staticmethod
    def _read_live_pair_twelve_data(
        *,
        from_currency: str,
        to_currency: str,
    ) -> FxQuote | None:
        config = TwelveDataPriceService.load_config()
        if config is None:
            return None

        pair_symbol = f"{from_currency}/{to_currency}"
        try:
            quote = TwelveDataPriceService.fetch_quote(
                ticker=pair_symbol,
                exchange=None,
                api_key=config.api_key,
                extended_hours=config.extended_hours,
            )
        except Exception:
            return None

        if quote.close <= 0:
            return None

        TwelveDataPriceService.increment_credit_usage(1)
        return FxQuote(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=quote.close,
            as_of=quote.timestamp_text,
            source=_TWELVE_DATA_LIVE_SOURCE,
            path=(f"{from_currency}2{to_currency}",),
        )

    @staticmethod
    def _read_live_pair(
        *,
        from_currency: str,
        to_currency: str,
        provider: str,
    ) -> FxQuote | None:
        cached = _get_cached_live_quote(provider, from_currency, to_currency)
        if cached is not None:
            return cached

        if provider == "twelve_data":
            quote = FxService._read_live_pair_twelve_data(
                from_currency=from_currency,
                to_currency=to_currency,
            )
        else:
            quote = FxService._read_live_pair_yfinance(
                from_currency=from_currency,
                to_currency=to_currency,
            )
        if quote is None:
            return None
        return _store_cached_live_quote(provider, quote)

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
                source="provided_rates",
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
                source="provided_rates",
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
                    source="provided_rates",
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

        # If rates are explicitly provided (including {}), stay fully deterministic
        # and do not call any external provider.
        if rates is not None:
            direct = FxService._resolve_direct_or_inverse(
                from_currency=frm,
                to_currency=to,
                rates=rates,
            )
            if direct is not None:
                return direct

            path_quote = FxService._resolve_via_path(
                from_currency=frm,
                to_currency=to,
                rates=rates,
            )
            if path_quote is not None:
                return path_quote
        else:
            provider = FxService.current_live_provider()
            live_direct = FxService._read_live_pair(
                from_currency=frm,
                to_currency=to,
                provider=provider,
            )
            if live_direct is not None:
                return live_direct

            live_inverse = FxService._read_live_pair(
                from_currency=to,
                to_currency=frm,
                provider=provider,
            )
            if live_inverse is not None and live_inverse.rate > 0:
                return FxQuote(
                    from_currency=frm,
                    to_currency=to,
                    rate=Decimal("1") / live_inverse.rate,
                    as_of=live_inverse.as_of,
                    source=live_inverse.source,
                    path=live_inverse.path,
                )

            if provider == "twelve_data" and _allow_twelve_data_fallback_to_yfinance():
                live_direct = FxService._read_live_pair(
                    from_currency=frm,
                    to_currency=to,
                    provider="yfinance",
                )
                if live_direct is not None:
                    return live_direct

                live_inverse = FxService._read_live_pair(
                    from_currency=to,
                    to_currency=frm,
                    provider="yfinance",
                )
                if live_inverse is not None and live_inverse.rate > 0:
                    return FxQuote(
                        from_currency=frm,
                        to_currency=to,
                        rate=Decimal("1") / live_inverse.rate,
                        as_of=live_inverse.as_of,
                        source=live_inverse.source,
                        path=live_inverse.path,
                    )

            # Conservative two-leg fallback via USD.
            if frm != "USD" and to != "USD":
                leg1 = FxService._read_live_pair(
                    from_currency=frm,
                    to_currency="USD",
                    provider=provider,
                )
                leg2 = FxService._read_live_pair(
                    from_currency="USD",
                    to_currency=to,
                    provider=provider,
                )
                if leg1 is not None and leg2 is not None:
                    return FxQuote(
                        from_currency=frm,
                        to_currency=to,
                        rate=leg1.rate * leg2.rate,
                        as_of=_oldest_as_of((leg1.as_of, leg2.as_of)),
                        source=leg1.source,
                        path=(f"{frm}2USD", f"USD2{to}"),
                    )

                if provider == "twelve_data" and _allow_twelve_data_fallback_to_yfinance():
                    leg1 = FxService._read_live_pair(
                        from_currency=frm,
                        to_currency="USD",
                        provider="yfinance",
                    )
                    leg2 = FxService._read_live_pair(
                        from_currency="USD",
                        to_currency=to,
                        provider="yfinance",
                    )
                    if leg1 is not None and leg2 is not None:
                        return FxQuote(
                            from_currency=frm,
                            to_currency=to,
                            rate=leg1.rate * leg2.rate,
                            as_of=_oldest_as_of((leg1.as_of, leg2.as_of)),
                            source=leg1.source,
                            path=(f"{frm}2USD", f"USD2{to}"),
                        )

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
