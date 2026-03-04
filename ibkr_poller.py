#!/usr/bin/env python3
"""
ibkr_poller.py  —  IB Market Data Poller for Equity Tracker
=============================================================

CONFIGURATION: Edit the block below.

  HOST, PORT, CLIENT_ID  — TWS/Gateway connection
    TWS Paper    : PORT=4002  (default)
    TWS Live     : PORT=7496
    Gateway Paper: PORT=4002
    Gateway Live : PORT=4001

  TRACKED        — list of ticker symbols to poll
                   Polling interval auto-adjusts as you add more.

  DB_PATH        — SQLite database file path

HOW TO RUN:
  pip install ibapi
  python ibkr_poller.py                  # fetch 1 Y history, then poll indefinitely
  python ibkr_poller.py --history-only   # fetch history then exit
  python ibkr_poller.py --poll-only      # skip history, poll snapshots only

ADDING MORE TICKERS:
  TRACKED = ["IBM", "AAPL", "MSFT"]     # interval auto-adjusts to max(30, n*15) s
"""

from __future__ import annotations

import argparse
import itertools
import logging
import signal
import sqlite3
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Generator

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

# ── Configuration ──────────────────────────────────────────────────────────────

HOST      = "127.0.0.1"
PORT      = 4002
CLIENT_ID = 1
TRACKED   = ["IBM"]          # ← add symbols here
DB_PATH   = "ib_market_data.db"

# ── Precision ──────────────────────────────────────────────────────────────────

PRICE_PLACES = Decimal("0.0001")   # OHLC bars — 4 dp
MONEY_PLACES = Decimal("0.01")     # snapshot bid / ask / last / mid — 2 dp

# ── IB error codes ─────────────────────────────────────────────────────────────

_INFORMATIONAL = {2104, 2106, 2107, 2108, 2158, 2176, 10167}
ERR_PACING     = 162    # Historical pacing violation — back off and retry
ERR_NO_PERMS   = 10089  # No market data subscription — switch to delayed
ERR_COMPETING  = 10197  # Competing live session — switch to delayed

# ── IB tick types (verified against ibapi.ticktype.TickTypeEnum) ───────────────

TICK_BID          = 1
TICK_ASK          = 2
TICK_LAST         = 4
TICK_DELAYED_BID  = 66
TICK_DELAYED_ASK  = 67
TICK_DELAYED_LAST = 68

# ── Polling / rate-limit tuning ────────────────────────────────────────────────

BASE_POLL_SECS   = 30    # minimum poll interval (1 ticker)
PER_TICKER_SECS  = 15    # added per additional ticker
MAX_POLL_SECS    = 300   # hard cap (5 min)
RATE_LIMIT_REQ   = 40    # max requests per 60-second rolling window
MAX_CONCURRENT   = 5     # max simultaneous snapshot requests (semaphore)

# ── Timeout / backoff ──────────────────────────────────────────────────────────

HIST_TIMEOUT  = 60.0    # seconds to wait for historicalDataEnd
SNAP_TIMEOUT  = 15.0    # seconds to wait for tickSnapshotEnd
BACKOFF_BASE  = 5.0     # first retry wait on pacing error
BACKOFF_MULT  = 2.0
BACKOFF_MAX   = 300.0   # cap
HIST_ATTEMPTS = 3       # max retries for historical requests
SNAP_ATTEMPTS = 2       # max retries for snapshot (live → delayed)

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = time.gmtime   # all log timestamps in UTC
log = logging.getLogger("ibkr_poller")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ib_date_to_iso(ib_date: str) -> str:
    """Convert IB daily-bar date "YYYYMMDD[...]" → "YYYY-MM-DD"."""
    d = ib_date.strip()[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _q(value: float, places: Decimal) -> str | None:
    """Quantize a float to a Decimal string; return None for sentinel values."""
    if value is None or value <= 0:
        return None
    return str(Decimal(str(value)).quantize(places, rounding=ROUND_HALF_EVEN))


# ──────────────────────────────────────────────────────────────────────────────
# Rate limiter  (sliding-window, thread-safe)
# ──────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    """Blocks acquire() until a request slot is available within the rolling window."""

    def __init__(self, limit: int = RATE_LIMIT_REQ, window: float = 60.0) -> None:
        self._limit  = limit
        self._window = window
        self._calls: deque[float] = deque()
        self._lock   = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self._window:
                    self._calls.popleft()
                if len(self._calls) < self._limit:
                    self._calls.append(now)
                    return
                sleep_for = self._window - (now - self._calls[0])
            time.sleep(max(0.05, sleep_for))


# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history_daily (
    symbol      TEXT    NOT NULL,
    date_utc    TEXT    NOT NULL,
    open        TEXT    NOT NULL,
    high        TEXT    NOT NULL,
    low         TEXT    NOT NULL,
    close       TEXT    NOT NULL,
    volume      INTEGER,
    source      TEXT    NOT NULL,
    inserted_at_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, date_utc)
);

CREATE TABLE IF NOT EXISTS price_snapshot (
    symbol      TEXT    NOT NULL,
    as_of_utc   TEXT    NOT NULL,
    last        TEXT,
    bid         TEXT,
    ask         TEXT,
    mid         TEXT,
    source      TEXT    NOT NULL,
    inserted_at_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of_utc)
);

CREATE TABLE IF NOT EXISTS request_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    req_id      INTEGER NOT NULL,
    symbol      TEXT    NOT NULL,
    req_type    TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'sent'
);

CREATE TABLE IF NOT EXISTS error_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    req_id      INTEGER,
    error_code  INTEGER NOT NULL,
    error_msg   TEXT    NOT NULL,
    symbol      TEXT,
    occurred_at TEXT    NOT NULL
);
"""


class DB:
    """Thread-safe SQLite wrapper with WAL mode and auto-migration."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous  = NORMAL")
        self._lock = threading.Lock()
        with self._write():
            self._conn.executescript(_SCHEMA)

    @contextmanager
    def _write(self) -> Generator:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def upsert_bar(self, symbol: str, date_utc: str,
                   open_: str, high: str, low: str, close: str,
                   volume: int | None, source: str) -> None:
        with self._write() as c:
            c.execute(
                """
                INSERT INTO price_history_daily
                    (symbol, date_utc, open, high, low, close, volume, source, inserted_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, date_utc) DO UPDATE SET
                    open=excluded.open, high=excluded.high,
                    low=excluded.low,   close=excluded.close,
                    volume=excluded.volume, source=excluded.source,
                    inserted_at_utc=excluded.inserted_at_utc
                """,
                (symbol, date_utc, open_, high, low, close, volume, source, _utcnow()),
            )

    def upsert_snapshot(self, symbol: str, as_of: str,
                        last: str | None, bid: str | None,
                        ask: str | None, mid: str | None,
                        source: str) -> None:
        with self._write() as c:
            c.execute(
                """
                INSERT INTO price_snapshot
                    (symbol, as_of_utc, last, bid, ask, mid, source, inserted_at_utc)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, as_of_utc) DO UPDATE SET
                    last=excluded.last, bid=excluded.bid,
                    ask=excluded.ask,   mid=excluded.mid,
                    source=excluded.source,
                    inserted_at_utc=excluded.inserted_at_utc
                """,
                (symbol, as_of, last, bid, ask, mid, source, _utcnow()),
            )

    def log_request(self, req_id: int, symbol: str, req_type: str) -> None:
        with self._write() as c:
            c.execute(
                "INSERT INTO request_log (req_id, symbol, req_type, sent_at) VALUES (?,?,?,?)",
                (req_id, symbol, req_type, _utcnow()),
            )

    def set_status(self, req_id: int, status: str) -> None:
        with self._write() as c:
            c.execute("UPDATE request_log SET status=? WHERE req_id=?", (status, req_id))

    def log_error(self, req_id: int | None, code: int, msg: str, symbol: str | None) -> None:
        with self._write() as c:
            c.execute(
                "INSERT INTO error_log (req_id, error_code, error_msg, symbol, occurred_at)"
                " VALUES (?,?,?,?,?)",
                (req_id, code, msg, symbol, _utcnow()),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# In-flight request state
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _Req:
    req_id:    int
    symbol:    str
    req_type:  str                                    # "historical" | "snapshot"
    event:     threading.Event = field(default_factory=threading.Event)
    is_error:  bool = False
    err_code:  int  = 0
    err_msg:   str  = ""
    # Historical accumulation
    bars:      list = field(default_factory=list)
    # Snapshot accumulation: tick_type → price (float)
    ticks:     dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# IBPoller  (EWrapper + EClient)
# ──────────────────────────────────────────────────────────────────────────────

class IBPoller(EWrapper, EClient):
    """
    Minimal IB market data poller.

    Thread model
    ────────────
    ib-reader  — daemon thread running EClient.run(); dispatches all callbacks.
    main       — runs poll_loop(); launches snapshot worker threads.
    snap-*     — one daemon thread per symbol per cycle (bounded by semaphore).

    All EWrapper callbacks are called on ib-reader. They must not block.
    reqHistoricalData / reqMktData are called from main / snap-* threads;
    EClient's socket send is internally thread-safe.
    """

    def __init__(self, db: DB) -> None:
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self._db              = db
        self._connected       = threading.Event()
        self._shutdown        = threading.Event()

        # Request registry
        self._pending: dict[int, _Req] = {}
        self._pending_lock = threading.Lock()
        self._id_iter      = itertools.count(1)
        self._id_lock      = threading.Lock()

        # Delayed-mode flag (set once on first 10089)
        self._delayed       = False
        self._delayed_lock  = threading.Lock()

        self._rate  = RateLimiter()
        self._sem   = threading.Semaphore(MAX_CONCURRENT)

        # Stats (main thread only after startup, no lock needed)
        self.stats = dict(hist_ok=0, hist_fail=0, snap_ok=0, snap_fail=0,
                          bars=0, snaps=0, errors=0, backoffs=0)

    # ── reqId allocation ───────────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._id_lock:
            return next(self._id_iter)

    def _reg(self, req: _Req) -> None:
        with self._pending_lock:
            self._pending[req.req_id] = req
        self._db.log_request(req.req_id, req.symbol, req.req_type)
        log.info("REQ  id=%-4d  type=%-12s  symbol=%s", req.req_id, req.req_type, req.symbol)

    def _unreg(self, req_id: int) -> None:
        with self._pending_lock:
            self._pending.pop(req_id, None)

    def _get(self, req_id: int) -> _Req | None:
        with self._pending_lock:
            return self._pending.get(req_id)

    # ── EWrapper: connection ───────────────────────────────────────────────────

    def nextValidId(self, orderId: int) -> None:
        log.info("Connected  nextValidId=%d", orderId)
        self._connected.set()

    def error(self, reqId: int, errorCode: int, errorString: str,
              advancedOrderRejectJson: str = "") -> None:
        if errorCode in _INFORMATIONAL:
            log.info("IB_INFO  [%d] %d: %s", reqId, errorCode, errorString)
            return

        req    = self._get(reqId)
        symbol = req.symbol if req else None
        log.warning("IB_ERR   id=%-4d  code=%-6d  symbol=%-6s  %s",
                    reqId, errorCode, symbol or "-", errorString)
        self.stats["errors"] += 1
        self._db.log_error(reqId if reqId > 0 else None, errorCode, errorString, symbol)

        # Switch to delayed mode on 10089 (no subscription) or 10197 (competing session)
        if errorCode in (ERR_NO_PERMS, ERR_COMPETING):
            with self._delayed_lock:
                if not self._delayed:
                    self._delayed = True
                    reason = ("no subscription" if errorCode == ERR_NO_PERMS
                              else "competing live session")
                    log.warning("Switching to delayed data (%s) - reqMarketDataType=3", reason)
                    self.reqMarketDataType(3)

        # Signal waiting thread so it can handle the error
        if req is not None:
            req.is_error = True
            req.err_code = errorCode
            req.err_msg  = errorString
            req.event.set()

    # ── EWrapper: historical data ──────────────────────────────────────────────

    def historicalData(self, reqId: int, bar) -> None:
        req = self._get(reqId)
        if req is not None:
            req.bars.append(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:
        req = self._get(reqId)
        if req is not None:
            log.debug("HIST_END  id=%d  symbol=%s  bars=%d", reqId, req.symbol, len(req.bars))
            req.event.set()

    # ── EWrapper: market data (snapshots) ─────────────────────────────────────

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:
        if price > 0:
            req = self._get(reqId)
            if req is not None:
                req.ticks[tickType] = price

    def tickSize(self, reqId: int, tickType: int, size) -> None:
        pass  # not needed for snapshots; logged at DEBUG if desired

    def tickSnapshotEnd(self, reqId: int) -> None:
        req = self._get(reqId)
        if req is not None:
            log.debug("SNAP_END  id=%d  symbol=%s  ticks=%s", reqId, req.symbol, req.ticks)
            req.event.set()

    # ── Contract builder ───────────────────────────────────────────────────────

    @staticmethod
    def _contract(symbol: str) -> Contract:
        c = Contract()
        c.symbol   = symbol
        c.secType  = "STK"
        c.exchange = "SMART"
        c.currency = "USD"
        return c

    # ── Historical fetch ───────────────────────────────────────────────────────

    def fetch_history(self, symbol: str) -> bool:
        """
        Request 1 Y of daily TRADES bars for symbol.
        Retries up to HIST_ATTEMPTS times on pacing errors (code 162).
        Returns True on success.
        """
        backoff = BACKOFF_BASE
        for attempt in range(1, HIST_ATTEMPTS + 1):
            req = _Req(req_id=self._next_id(), symbol=symbol, req_type="historical")
            self._reg(req)
            self._rate.acquire()

            self.reqHistoricalData(
                reqId          = req.req_id,
                contract       = self._contract(symbol),
                endDateTime    = "",
                durationStr    = "1 Y",
                barSizeSetting = "1 day",
                whatToShow     = "TRADES",
                useRTH         = 1,
                formatDate     = 1,
                keepUpToDate   = False,
                chartOptions   = [],
            )

            done = req.event.wait(timeout=HIST_TIMEOUT)
            if not done:
                self.cancelHistoricalData(req.req_id)
                self._unreg(req.req_id)
                self._db.set_status(req.req_id, "timeout")
                log.warning("HIST_TIMEOUT  symbol=%s  id=%d", symbol, req.req_id)
                self.stats["hist_fail"] += 1
                return False

            if req.is_error:
                self._unreg(req.req_id)
                self._db.set_status(req.req_id, "error")
                is_pacing = (req.err_code == ERR_PACING
                             and "pacing" in req.err_msg.lower())
                if is_pacing and attempt < HIST_ATTEMPTS:
                    self.stats["backoffs"] += 1
                    log.warning("PACING  symbol=%s  backoff=%.0fs  (attempt %d/%d)",
                                symbol, backoff, attempt, HIST_ATTEMPTS)
                    time.sleep(backoff)
                    backoff = min(backoff * BACKOFF_MULT, BACKOFF_MAX)
                    continue
                self.stats["hist_fail"] += 1
                return False

            # Success
            count = self._save_bars(symbol, req.bars)
            self._unreg(req.req_id)
            self._db.set_status(req.req_id, "ok")
            self.stats["hist_ok"] += 1
            self.stats["bars"] += count
            log.info("HIST_SAVED  symbol=%s  bars=%d", symbol, count)
            return True

        self.stats["hist_fail"] += 1
        return False

    def _save_bars(self, symbol: str, bars: list) -> int:
        source = "ibkr_delayed" if self._delayed else "ibkr"
        count  = 0
        for bar in bars:
            try:
                o = _q(bar.open,  PRICE_PLACES)
                h = _q(bar.high,  PRICE_PLACES)
                l = _q(bar.low,   PRICE_PLACES)
                c = _q(bar.close, PRICE_PLACES)
                if not all([o, h, l, c]):
                    continue
                v = int(bar.volume) if bar.volume else None
                self._db.upsert_bar(symbol, _ib_date_to_iso(bar.date),
                                    o, h, l, c, v, source)
                count += 1
            except Exception as exc:
                log.warning("BAR_ERR  symbol=%s  date=%s  %s", symbol, bar.date, exc)
        return count

    # ── Snapshot fetch ─────────────────────────────────────────────────────────

    def fetch_snapshot(self, symbol: str) -> bool:
        """Thread entry point — bounded by semaphore."""
        with self._sem:
            return self._snapshot(symbol)

    def _snapshot(self, symbol: str) -> bool:
        """
        Request a market data snapshot (snapshot=True).
        On 10089: delayed mode is set globally; retries once.
        """
        for attempt in range(1, SNAP_ATTEMPTS + 1):
            req = _Req(req_id=self._next_id(), symbol=symbol, req_type="snapshot")
            self._reg(req)
            self._rate.acquire()

            self.reqMktData(
                reqId              = req.req_id,
                contract           = self._contract(symbol),
                genericTickList    = "",
                snapshot           = True,
                regulatorySnapshot = False,
                mktDataOptions     = [],
            )

            done = req.event.wait(timeout=SNAP_TIMEOUT)
            if not done:
                self.cancelMktData(req.req_id)
                self._unreg(req.req_id)
                self._db.set_status(req.req_id, "timeout")
                log.warning("SNAP_TIMEOUT  symbol=%s  id=%d", symbol, req.req_id)
                self.stats["snap_fail"] += 1
                return False

            if req.is_error:
                self._unreg(req.req_id)
                self._db.set_status(req.req_id, "error")
                # 10089 / 10197 → delayed mode was switched; retry once
                if req.err_code in (ERR_NO_PERMS, ERR_COMPETING) and attempt < SNAP_ATTEMPTS:
                    log.info("SNAP_RETRY  symbol=%s  (delayed mode)", symbol)
                    continue
                self.stats["snap_fail"] += 1
                return False

            self._save_snapshot(symbol, req.ticks)
            self._unreg(req.req_id)
            self._db.set_status(req.req_id, "ok")
            self.stats["snap_ok"] += 1
            self.stats["snaps"] += 1
            return True

        self.stats["snap_fail"] += 1
        return False

    def _save_snapshot(self, symbol: str, ticks: dict) -> None:
        # Support both live and delayed tick types
        bid  = _q(ticks.get(TICK_BID)  or ticks.get(TICK_DELAYED_BID),  MONEY_PLACES)
        ask  = _q(ticks.get(TICK_ASK)  or ticks.get(TICK_DELAYED_ASK),  MONEY_PLACES)
        last = _q(ticks.get(TICK_LAST) or ticks.get(TICK_DELAYED_LAST), MONEY_PLACES)

        mid: str | None = None
        if bid and ask:
            mid = str(
                ((Decimal(bid) + Decimal(ask)) / 2).quantize(MONEY_PLACES, rounding=ROUND_HALF_EVEN)
            )

        if not any([last, bid, ask]):
            log.warning("SNAP_EMPTY  symbol=%s  (no price data; check subscription)", symbol)
            return

        source = "delayed" if self._delayed else "ibkr"
        as_of  = _utcnow()
        self._db.upsert_snapshot(symbol, as_of, last, bid, ask, mid, source)
        log.info("SNAP_SAVED  symbol=%-6s  last=%s  bid=%s  ask=%s  mid=%s  [%s]",
                 symbol, last, bid, ask, mid, source)

    # ── Shutdown ───────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._shutdown.set()
        self.disconnect()


# ──────────────────────────────────────────────────────────────────────────────
# Poll loop
# ──────────────────────────────────────────────────────────────────────────────

def _poll_interval(n: int) -> tuple[int, float]:
    """Return (interval_secs, stagger_secs) for n tickers."""
    interval = min(max(BASE_POLL_SECS, n * PER_TICKER_SECS), MAX_POLL_SECS)
    stagger  = interval / n if n > 0 else 0.0
    return interval, stagger


def poll_loop(app: IBPoller, tickers: list[str], mode: str) -> None:
    """
    mode: "both" | "history" | "poll"
    Runs on the main thread; blocks until shutdown.
    """
    n        = len(tickers)
    interval, stagger = _poll_interval(n)

    # ── History phase ──────────────────────────────────────────────────────────
    if mode in ("both", "history"):
        log.info("=== HISTORY  tickers=%d ===", n)
        for symbol in tickers:
            if app._shutdown.is_set():
                break
            ok = app.fetch_history(symbol)
            log.info("HIST  symbol=%s  result=%s", symbol, "OK" if ok else "FAIL")
            if stagger and not app._shutdown.is_set():
                time.sleep(stagger)   # pace between consecutive historical requests
        log.info("=== HISTORY COMPLETE ===")

    if mode == "history":
        return

    # ── Snapshot poll loop ─────────────────────────────────────────────────────
    log.info("=== POLLING  tickers=%d  interval=%ds  stagger=%.1fs ===",
             n, interval, stagger)

    while not app._shutdown.is_set():
        cycle_start = time.monotonic()
        threads: list[threading.Thread] = []

        for i, symbol in enumerate(tickers):
            if app._shutdown.is_set():
                break
            t = threading.Thread(
                target=app.fetch_snapshot,
                args=(symbol,),
                name=f"snap-{symbol}",
                daemon=True,
            )
            t.start()
            threads.append(t)
            if stagger and i < n - 1:
                time.sleep(stagger)

        for t in threads:
            t.join(timeout=SNAP_TIMEOUT + 5)

        # Sleep for the remainder of the interval
        elapsed   = time.monotonic() - cycle_start
        remaining = interval - elapsed
        deadline  = time.monotonic() + remaining
        while time.monotonic() < deadline and not app._shutdown.is_set():
            time.sleep(min(1.0, deadline - time.monotonic()))


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def _print_summary(app: IBPoller, tickers: list[str], mode: str) -> None:
    n        = len(tickers)
    interval, stagger = _poll_interval(n)
    s = app.stats
    print()
    print("-- Exit summary -------------------------------------")
    print(f"  Tickers tracked   : {', '.join(tickers)}")
    print(f"  Mode              : {mode}")
    print(f"  Poll interval     : {interval}s  (stagger {stagger:.0f}s)")
    print(f"  Data source       : {'delayed' if app._delayed else 'live'}")
    print(f"  Historical OK     : {s['hist_ok']}")
    print(f"  Historical fail   : {s['hist_fail']}")
    print(f"  Bars persisted    : {s['bars']}")
    print(f"  Snapshots OK      : {s['snap_ok']}")
    print(f"  Snapshots fail    : {s['snap_fail']}")
    print(f"  Snaps persisted   : {s['snaps']}")
    print(f"  IB errors logged  : {s['errors']}")
    print(f"  Pacing backoffs   : {s['backoffs']}")
    print("-----------------------------------------------------")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="IB Market Data Poller for Equity Tracker")
    parser.add_argument("--history-only", action="store_true",
                        help="Fetch 1 Y daily bars then exit (no polling)")
    parser.add_argument("--poll-only",    action="store_true",
                        help="Skip historical fetch; poll snapshots only")
    args = parser.parse_args()

    if args.history_only and args.poll_only:
        parser.error("--history-only and --poll-only are mutually exclusive")

    mode = "history" if args.history_only else ("poll" if args.poll_only else "both")

    db  = DB(DB_PATH)
    app = IBPoller(db)

    def _shutdown(signum=None, frame=None) -> None:
        log.info("Shutdown requested (signal %s)", signum)
        app.stop()

    # Register signal handlers (wrapped for Windows where SIGTERM is unavailable)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (OSError, ValueError):
            pass

    # Connect
    try:
        app.connect(HOST, PORT, clientId=CLIENT_ID)
    except Exception as exc:
        log.error("Connection failed: %s", exc)
        db.close()
        sys.exit(1)

    ib_thread = threading.Thread(target=app.run, daemon=True, name="ib-reader")
    ib_thread.start()

    if not app._connected.wait(timeout=10.0):
        log.error("Timed out waiting for nextValidId — is TWS running on %s:%d?", HOST, PORT)
        app.stop()
        db.close()
        sys.exit(1)

    try:
        poll_loop(app, TRACKED, mode)
    except KeyboardInterrupt:
        _shutdown()
    finally:
        log.info("Disconnecting...")
        app.stop()
        ib_thread.join(timeout=5.0)
        _print_summary(app, TRACKED, mode)
        db.close()
        log.info("Done.")


if __name__ == "__main__":
    main()
