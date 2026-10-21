#!/usr/bin/env python3
"""Fetch and cache public, unauthenticated Kalshi data for the KXBTC15M backtest.

Standard library only. Raw JSON is cached under data/ so reruns are offline and an
interrupted fetch resumes where it stopped (one file per market; files are written
atomically, so a partial file is never left behind).

  python fetch.py markets   # settled KXBTC15M markets closing in the window -> data/markets/
  python fetch.py candles   # 1-minute candlesticks per market             -> data/candles/
  python fetch.py trades    # trade prints, last 9 min, Tier B sample       -> data/trades/
  python fetch.py all       # all three in order

Requests are spaced to at most --rps per second (default 6.5, under the ~8/s budget)
with exponential backoff on HTTP 429, 5xx and network errors. On 2026-09-22 the candle
fetch at 6.5/s drew 380 HTTP 429s in 3228 requests; the trades fetch ran with
`--rps 4 --workers 3` and drew none.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sim  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"
DEFAULT_END = "2026-09-22T22:00:00Z"
DEFAULT_DAYS = 30
TRADES_LOOKBACK_S = sim.TIERB_LOOKBACK_S


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def write_json_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class Client:
    """GET-only JSON client with request spacing and backoff. Thread-safe: the spacing
    is shared, so parallel workers together stay under `rps` requests per second."""

    def __init__(self, rps: float = 6.5, max_retries: int = 8):
        self.min_interval = 1.0 / rps
        self.max_retries = max_retries
        self.next_slot = 0.0
        self.lock = threading.Lock()
        self.requests = 0
        self.retries = 0

    def _wait_slot(self) -> None:
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.min_interval
            self.requests += 1
        if slot > now:
            time.sleep(slot - now)

    def get(self, path: str, params: dict):
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        for attempt in range(self.max_retries + 1):
            self._wait_slot()
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "kalshi-15m-backtester/1.0 (public market data)",
                    "Accept": "application/json",
                })
                with urllib.request.urlopen(req, timeout=40) as r:
                    return r.status, json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    with self.lock:
                        self.retries += 1
                    back = min(60.0, 2.0 ** attempt) + random.random()
                    log("HTTP %d on %s; retry in %.1fs" % (e.code, path, back))
                    time.sleep(back)
                    continue
                body = e.read().decode("utf-8", "replace")[:300]
                return e.code, {"error_status": e.code, "error_body": body}
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                with self.lock:
                    self.retries += 1
                back = min(60.0, 2.0 ** attempt) + random.random()
                log("network error %r on %s; retry in %.1fs" % (e, path, back))
                time.sleep(back)
        raise RuntimeError("giving up after %d retries: %s" % (self.max_retries, url))


def window_bounds(end_iso: str, days: int):
    end = int(sim.iso_to_ts(end_iso))
    return end - days * 86400, end


def fetch_markets(c: Client, data: str, end_iso: str, days: int, refresh: bool = False) -> list:
    """All settled KXBTC15M markets with start < close_ts <= end. The API filter is used
    with a 15-minute margin on each side and the exact window is applied locally."""
    index_path = os.path.join(data, "markets_index.json")
    if os.path.exists(index_path) and not refresh:
        idx = read_json(index_path)
        if idx.get("end") == end_iso and idx.get("days") == days:
            log("markets: cached index has %d markets" % len(idx["markets"]))
            return idx["markets"]
    start, end = window_bounds(end_iso, days)
    cursor = ""
    page = 0
    rows = []
    while True:
        params = {"series_ticker": SERIES, "status": "settled", "limit": 1000,
                  "min_close_ts": start - 900, "max_close_ts": end + 900}
        if cursor:
            params["cursor"] = cursor
        status, body = c.get("/markets", params)
        if status != 200:
            raise RuntimeError("markets page %d failed: %s" % (page, body))
        write_json_atomic(os.path.join(data, "markets", "page_%03d.json" % page), body)
        rows.extend(body.get("markets", []))
        log("markets page %d: %d rows" % (page, len(body.get("markets", []))))
        cursor = body.get("cursor") or ""
        page += 1
        if not cursor:
            break
    keep = {}
    for m in rows:
        ct = int(sim.iso_to_ts(m["close_time"]))
        if start < ct <= end:
            keep[m["ticker"]] = {k: m.get(k) for k in (
                "ticker", "event_ticker", "open_time", "close_time", "result", "status",
                "floor_strike", "expiration_value", "exchange_index", "volume_fp",
                "settlement_ts", "price_level_structure")}
    markets = sorted(keep.values(), key=lambda m: m["close_time"])
    write_json_atomic(index_path, {"end": end_iso, "days": days, "start_ts": start, "end_ts": end,
                                   "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                   "pages": page, "raw_rows": len(rows), "markets": markets})
    log("markets: %d in window (from %d raw rows)" % (len(markets), len(rows)))
    return markets


def fetch_candles(c: Client, data: str, markets: list) -> None:
    todo = [m for m in markets if not os.path.exists(os.path.join(data, "candles", m["ticker"] + ".json"))]
    log("candles: %d cached, %d to fetch" % (len(markets) - len(todo), len(todo)))
    for i, m in enumerate(todo):
        o = int(sim.iso_to_ts(m["open_time"]))
        cl = int(sim.iso_to_ts(m["close_time"]))
        status, body = c.get("/series/%s/markets/%s/candlesticks" % (SERIES, m["ticker"]),
                             {"start_ts": o, "end_ts": cl, "period_interval": 1})
        if status != 200:
            body = {"candlesticks": [], "fetch_status": status, "fetch_error": body}
        write_json_atomic(os.path.join(data, "candles", m["ticker"] + ".json"), body)
        if (i + 1) % 100 == 0 or i + 1 == len(todo):
            log("candles: %d/%d (requests %d, retries %d)" % (i + 1, len(todo), c.requests, c.retries))


def fetch_trades_for(c: Client, data: str, meta: dict) -> dict:
    path = os.path.join(data, "trades", meta["ticker"] + ".json")
    if os.path.exists(path):
        return read_json(path)
    cl = int(sim.iso_to_ts(meta["close_time"]))
    lo = cl - TRADES_LOOKBACK_S
    trades = []
    cursor = ""
    pages = 0
    while True:
        params = {"ticker": meta["ticker"], "limit": 1000, "min_ts": lo, "max_ts": cl}
        if cursor:
            params["cursor"] = cursor
        status, body = c.get("/markets/trades", params)
        if status != 200:
            raise RuntimeError("trades %s page %d failed: %s" % (meta["ticker"], pages, body))
        trades.extend(body.get("trades", []))
        pages += 1
        nxt = body.get("cursor") or ""
        if not nxt or nxt == cursor or pages >= 500:
            break
        cursor = nxt
    obj = {"ticker": meta["ticker"], "min_ts": lo, "max_ts": cl, "pages": pages,
           "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "trades": trades}
    write_json_atomic(path, obj)
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["markets", "candles", "trades", "all"])
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--rps", type=float, default=6.5)
    ap.add_argument("--workers", type=int, default=3, help="parallel markets for the trades fetch (shared rps cap)")
    ap.add_argument("--refresh-markets", action="store_true")
    ap.add_argument("--resample", action="store_true", help="recompute data/tierb_sample.json even if it exists")
    ap.add_argument("--sample-step", type=int, default=None, help="Tier B systematic sample: every Nth market")
    ap.add_argument("--supplement", type=int, default=None, help="Tier B extra markets whose Tier A bounds differ")
    ap.add_argument("--series", default=SERIES,
                    help="15-minute series to fetch (default KXBTC15M). KXETH15M/KXSOL15M/KXXRP15M share its fee "
                         "schedule (quadratic, multiplier 1, checked 2026-09-23). Use a separate --data-dir per series.")
    args = ap.parse_args(argv)
    globals()["SERIES"] = args.series
    c = Client(rps=args.rps)
    data = args.data_dir
    markets = None
    if args.what in ("markets", "all"):
        markets = fetch_markets(c, data, args.end, args.days, args.refresh_markets)
    if args.what in ("candles", "all"):
        markets = markets or fetch_markets(c, data, args.end, args.days)
        fetch_candles(c, data, markets)
    if args.what in ("trades", "all"):
        markets = markets or fetch_markets(c, data, args.end, args.days)
        import run_backtest  # local module; selects the sample from cached candles
        sample_path = os.path.join(data, "tierb_sample.json")
        if os.path.exists(sample_path) and not args.resample:
            sample = read_json(sample_path)
        else:
            step = args.sample_step or run_backtest.TIERB_STEP
            sup = run_backtest.TIERB_SUPPLEMENT if args.supplement is None else args.supplement
            sample = run_backtest.tierb_sample(data, markets, sim.Params(), step, sup)
            write_json_atomic(sample_path, sample)
        by_ticker = {m["ticker"]: m for m in markets}
        tickers = [s["ticker"] for s in sample]
        log("trades: Tier B sample %d markets, %d workers" % (len(tickers), args.workers))
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {ex.submit(fetch_trades_for, c, data, by_ticker[t]): t for t in tickers}
            for f in as_completed(futs):
                obj = f.result()
                done += 1
                log("trades %d/%d %s: %d prints, %d pages (requests %d, retries %d)" % (
                    done, len(tickers), futs[f], len(obj["trades"]), obj["pages"], c.requests, c.retries))
    log("done: %d requests, %d retries" % (c.requests, c.retries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
