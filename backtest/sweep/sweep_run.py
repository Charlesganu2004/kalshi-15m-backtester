#!/usr/bin/env python3
"""Exploratory parameter sweep of the entry, stop and take-profit rules on cached KXBTC15M
data (Tier A, live model).

Imports sim.py unchanged and drives sim.tier_a_trade exactly as run_backtest.run_tier_a
does. Chronological train/test holdout: train = markets closing in the first 20 days of the
cached period, test = the last 10 days (one split, not rolling windows). Writes
results/sweep_grid.json (raw per-config stats), which sweep_report.py turns into
results/sweep.json and results/sweep.md.

  python sweep/sweep_run.py            # full grid, 7 workers
  python sweep/sweep_run.py --workers 4 --limit 20   # smoke run: the first 19 configs plus the baseline
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import multiprocessing as mp
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)

import sim  # noqa: E402
import run_backtest as rb  # noqa: E402

DATA = os.path.join(BACKTEST, "data")
OUT = os.path.join(BACKTEST, "results", "sweep_grid.json")
LOG = os.path.join(BACKTEST, "results", "sweep_run.log")

ENTRY_MIN = [0.70, 0.75, 0.80, 0.85]
ENTRY_MAX = [0.90, 0.93, 0.95, 0.97]
EXIT = [None, 0.20, 0.30, 0.40, 0.50]          # None = no stop-loss (exit_below 0.0: ask < 0 never)
TP = [0.05, 0.10, None]                        # None = hold to settlement (take_profit_pts 1.0: target > 1 always)
DELAY = [2, 3, 5, 8, 12]
TRAIN_DAYS = 20
BASELINE = (0.80, 0.93, 0.40, 0.10, 8)
BOUNDS = ("pes", "opt")
MODEL = "live"

LOAD_DELAY = 12.0  # load/validate windows with the widest delay in the grid (superset of the others)

MARKETS = None
CUT = None


def grid():
    out = []
    for emin, emax, ex, tp, d in itertools.product(ENTRY_MIN, ENTRY_MAX, EXIT, TP, DELAY):
        if emin < emax:
            out.append((emin, emax, ex, tp, d))
    return out


def params_for(cfg) -> sim.Params:
    emin, emax, ex, tp, d = cfg
    return sim.Params(entry_min=emin, entry_max=emax,
                      exit_below=(0.0 if ex is None else ex),
                      take_profit_pts=(1.0 if tp is None else tp),
                      time_delay_min=float(d))


def cfg_key(cfg) -> str:
    emin, emax, ex, tp, d = cfg
    return "%.2f|%.2f|%s|%s|%d" % (emin, emax, "none" if ex is None else "%.2f" % ex,
                                    "hold" if tp is None else "%.2f" % tp, d)


def cfg_label(cfg) -> str:
    emin, emax, ex, tp, d = cfg
    return "entry range [%.2f, %.2f], stop %s, take-profit %s, window %d min" % (
        emin, emax, "none" if ex is None else "%.2f" % ex, "hold" if tp is None else "%.2f" % tp, d)


def _init(cut: int):
    global MARKETS, CUT
    idx = rb.load_index(DATA)
    MARKETS, _ = rb.load_markets(DATA, idx["markets"], sim.Params(time_delay_min=LOAD_DELAY))
    CUT = cut


def slim(trades) -> dict:
    s = sim.stats(trades)
    by = {}
    for t in trades:
        by[t["exit_type"]] = by.get(t["exit_type"], 0) + 1
    ups = sum(1 for t in trades if t["side"] == sim.UP)
    return {
        "trades": s["trades"],
        "win_rate": s["win_rate"],
        "avg_win": s["avg_win"],
        "avg_loss": s["avg_loss"],
        "pnl_per_trade": s["pnl_per_trade"],
        "total_pnl": s["total_pnl"],
        "max_drawdown": s["max_drawdown"],
        "pnl_per_trade_se": s["pnl_per_trade_se"],
        "breakeven_win_rate": s["breakeven_win_rate"],
        "exits": by,
        "up_trades": ups,
        "avg_entry": (sum(t["entry"] for t in trades) / 1000.0 / len(trades)) if trades else None,
    }


def run_config(cfg) -> dict:
    p = params_for(cfg)
    t0 = time.time()
    rec = {"key": cfg_key(cfg), "label": cfg_label(cfg),
           "entry_min": cfg[0], "entry_max": cfg[1], "exit": cfg[2], "tp": cfg[3], "delay": cfg[4]}
    for b in BOUNDS:
        trades = [t for t in (sim.tier_a_trade(m, p, MODEL, b) for m in MARKETS) if t is not None]
        train = [t for t in trades if t["close_ts"] <= CUT]
        test = [t for t in trades if t["close_ts"] > CUT]
        rec[b] = {"train": slim(train), "test": slim(test), "all": slim(trades)}
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    idx = rb.load_index(DATA)
    cut = int(idx["start_ts"]) + TRAIN_DAYS * 86400
    markets, v = rb.load_markets(DATA, idx["markets"], sim.Params(time_delay_min=LOAD_DELAY))
    train_n = sum(1 for m in markets if m.close_ts <= cut)
    test_n = len(markets) - train_n
    cfgs = grid()
    if args.limit:
        cfgs = cfgs[:args.limit]
        if BASELINE not in cfgs:  # sweep_report compares every config with the baseline, so it always runs
            cfgs[-1] = BASELINE
    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": MODEL,
        "bounds": list(BOUNDS),
        "tier": "A (1-minute candles, all markets)",
        "data_first_close": dt.datetime.fromtimestamp(markets[0].close_ts, dt.timezone.utc).isoformat(),
        "data_last_close": dt.datetime.fromtimestamp(markets[-1].close_ts, dt.timezone.utc).isoformat(),
        "train_cut_ts": cut,
        "train_cut_iso": dt.datetime.fromtimestamp(cut, dt.timezone.utc).isoformat(),
        "train_markets": train_n,
        "test_markets": test_n,
        "markets_usable": len(markets),
        "markets_excluded_bad_window": len(v["excluded_bad_window"]),
        "markets_excluded_no_result": len(v["excluded_no_result"]),
        "grid": {"entry_min": ENTRY_MIN, "entry_max": ENTRY_MAX, "exit": ["none" if e is None else e for e in EXIT],
                 "tp": ["hold" if t is None else t for t in TP], "delay": DELAY},
        "grid_size": len(cfgs),
        "full_grid_size": len(grid()),
        "workers": args.workers,
        "baseline_key": cfg_key(BASELINE),
        "fee_precision": "centicent",
        "daily_limits_applied": False,
        "sim_md5": None,
    }
    import hashlib
    with open(os.path.join(BACKTEST, "sim.py"), "rb") as f:
        meta["sim_md5"] = hashlib.md5(f.read()).hexdigest()
    with open(LOG, "a", encoding="utf-8") as lg:
        lg.write("%s start grid=%d workers=%d cut=%s train=%d test=%d\n" % (
            meta["generated_at"], len(cfgs), args.workers, meta["train_cut_iso"], train_n, test_n))
    results = []
    t0 = time.time()
    with mp.Pool(processes=args.workers, initializer=_init, initargs=(cut,)) as pool:
        for i, rec in enumerate(pool.imap_unordered(run_config, cfgs, chunksize=2), 1):
            results.append(rec)
            if i % 25 == 0 or i == len(cfgs):
                with open(LOG, "a", encoding="utf-8") as lg:
                    lg.write("%d/%d done, %.0f s elapsed\n" % (i, len(cfgs), time.time() - t0))
    results.sort(key=lambda r: r["key"])
    meta["elapsed_s"] = round(time.time() - t0, 1)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "configs": results}, f, indent=1)
    with open(LOG, "a", encoding="utf-8") as lg:
        lg.write("wrote %s (%d configs) in %.0f s\n" % (args.out, len(results), meta["elapsed_s"]))
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
