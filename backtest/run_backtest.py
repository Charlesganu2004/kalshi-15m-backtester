#!/usr/bin/env python3
"""Offline backtest of the entry, stop and take-profit rules on cached KXBTC15M data (see fetch.py).

  python run_backtest.py                      # default settings, writes results/
  python run_backtest.py --entry-min 0.85 --tp 0.05 --out-dir results_alt

Writes results/summary.json, results/report.md, results/tier_a_trades.csv and
results/tier_b_trades.csv. Dollar amounts are per `--contracts` (default 1) contracts.
Tier B needs cached trade prints for its sample. fetch.py caches them for the sample chosen
with the default settings, so with other settings the ambiguous-supplement markets that
differ from that sample have no prints; the run skips them, prints a warning and lists the
count in the report.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import json
import os
import statistics
import sys
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sim  # noqa: E402

MODELS = ("paper", "live")
TIERB_STEP = 10        # systematic Tier B sample: every 10th market by close time
TIERB_OFFSET = 0       # starting at the first market
TIERB_SUPPLEMENT = 60  # plus markets whose live Tier A bounds differ in side or exit type
BRACKET_TOL = 0.0105   # one cent of entry price plus the fee change it causes
POSITION_CLIP = (-0.5, 1.5)  # per-market position in the bounds is clipped to this range before averaging
BOUNDS = ("opt", "pes")
BOUND_NAME = {"opt": "optimistic", "pes": "pessimistic"}


# ---------------------------------------------------------------------------
# loading and validation
# ---------------------------------------------------------------------------
def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_index(data: str) -> dict:
    return read_json(os.path.join(data, "markets_index.json"))


def load_markets(data: str, metas: Sequence[dict], p: sim.Params):
    """Parse every cached market. Returns (usable markets, validation dict)."""
    usable: List[sim.Market] = []
    candle_counts: Dict[int, int] = {}
    v = {
        "markets_listed": len(metas),
        "results": {},
        "no_candle_file": [],
        "no_candles": [],
        "candle_fetch_errors": [],
        "duration_not_900s": [],
        "missing_minutes_total": 0,
        "missing_minutes_in_window": 0,
        "markets_with_missing_window_minutes": [],
        "null_ohlc_fields": 0,
        "excluded_no_result": [],
        "excluded_bad_window": [],
        "window_bars_with_empty_book_side": 0,
        "window_bars_total": 0,
    }
    ws = p.window_s
    for meta in metas:
        t = meta["ticker"]
        res = (meta.get("result") or "").lower()
        v["results"][res or "(empty)"] = v["results"].get(res or "(empty)", 0) + 1
        path = os.path.join(data, "candles", t + ".json")
        if not os.path.exists(path):
            v["no_candle_file"].append(t)
            continue
        cj = read_json(path)
        if cj.get("fetch_status"):
            v["candle_fetch_errors"].append({"ticker": t, "status": cj.get("fetch_status")})
        n = len(cj.get("candlesticks") or [])
        candle_counts[n] = candle_counts.get(n, 0) + 1
        if n == 0:
            v["no_candles"].append(t)
            continue
        m = sim.parse_market(meta, cj)
        if m.close_ts - m.open_ts != 900:
            v["duration_not_900s"].append(t)
        v["missing_minutes_total"] += len(m.missing_minutes)
        v["null_ohlc_fields"] += m.null_fields
        first_window_idx = (m.close_ts - ws - m.open_ts) // 60
        mw = [i for i in m.missing_minutes if i >= first_window_idx - 1]
        v["missing_minutes_in_window"] += len(mw)
        if mw:
            v["markets_with_missing_window_minutes"].append({"ticker": t, "minutes": mw})
        if res not in ("yes", "no"):
            v["excluded_no_result"].append(t)
            continue
        wb, _ = sim.window_bars(m, p)
        expected = int(round(ws / 60.0))
        if len(wb) < expected or any(b is None for b in m.bars[first_window_idx:]):
            v["excluded_bad_window"].append(t)
            continue
        for b in wb:
            v["window_bars_total"] += 1
            if max(b.ya) >= sim.FULL or min(b.yb) <= 0:
                v["window_bars_with_empty_book_side"] += 1
        usable.append(m)
    v["candles_per_market"] = {str(k): c for k, c in sorted(candle_counts.items())}
    v["markets_usable"] = len(usable)
    usable.sort(key=lambda m: m.close_ts)
    return usable, v


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------
def run_tier_a(markets: Sequence[sim.Market], p: sim.Params) -> Dict[str, Dict[str, List[dict]]]:
    out: Dict[str, Dict[str, List[dict]]] = {m: {b: [] for b in BOUNDS} for m in MODELS}
    for mk in markets:
        for b in BOUNDS:
            for model in MODELS:
                tr = sim.tier_a_trade(mk, p, model, b)
                if tr is not None:
                    out[model][b].append(tr)
    return out


def tierb_sample(data: str, metas: Sequence[dict], p: sim.Params, step: int, supplement: int,
                 offset: int = TIERB_OFFSET) -> List[dict]:
    markets, _ = load_markets(data, metas, p)
    return tierb_sample_from_markets(markets, p, step, supplement, offset)


def tierb_sample_from_markets(markets: Sequence[sim.Market], p: sim.Params, step: int, supplement: int,
                              offset: int = TIERB_OFFSET) -> List[dict]:
    """Systematic every-step-th market sample plus up to `supplement` markets where the
    live Tier A bounds disagree on side or exit type (structural intrabar ambiguity)."""
    tick_sorted = [m.ticker for m in markets]
    sys_sample = sim.select_tierb_sample(tick_sorted, step, offset)
    amb = []
    for m in markets:
        o = sim.tier_a_trade(m, p, "live", "opt")
        q = sim.tier_a_trade(m, p, "live", "pes")
        if o and q and (o["exit_type"] != q["exit_type"] or o["side"] != q["side"]):
            amb.append(m.ticker)
    sup = sim.select_ambiguous_supplement(amb, sys_sample, supplement)
    return ([{"ticker": t, "reason": "systematic"} for t in sys_sample]
            + [{"ticker": t, "reason": "ambiguous_supplement"} for t in sup])


def model_report(trades: List[dict], p: sim.Params) -> dict:
    r = {"all": sim.stats(trades)}
    r["by_entry_bucket"] = sim.breakdown(trades, lambda t: sim.entry_bucket(t["entry"]))
    r["by_exit_type"] = sim.breakdown(trades, lambda t: t["exit_type"])
    r["by_mins_left"] = sim.breakdown(trades, lambda t: sim.mins_left_bucket(t["mins_left"]))
    r["by_week"] = sim.breakdown(trades, lambda t: sim.iso_week(t["entry_ts"]))
    r["by_side"] = sim.breakdown(trades, lambda t: t["side"])
    dl = {}
    for mode in ("utc_day", "since_start"):
        kept, events = sim.apply_daily_limits(trades, p.daily_loss_target, p.daily_profit_target, mode)
        dl[mode] = {"stats": sim.stats(kept), "stop_events": events, "trades_skipped": len(trades) - len(kept)}
    r["daily_limits"] = dl
    return r


SL_BUCKETS = [(380, 399, "0.380-0.399"), (350, 379, "0.350-0.379"), (300, 349, "0.300-0.349"),
              (200, 299, "0.200-0.299"), (100, 199, "0.100-0.199"), (0, 99, "0.000-0.099")]


def sl_distribution(trades: Sequence[dict], price_key: str, stop: int) -> dict:
    sls = [t[price_key] for t in trades if t["exit_type"] == sim.SL]
    dist = {name: 0 for _, _, name in SL_BUCKETS}
    for v in sls:
        for lo, hi, name in SL_BUCKETS:
            if lo <= v <= hi:
                dist[name] += 1
                break
    out = {"count": len(sls), "buckets": dist}
    if sls:
        out["mean_exit"] = sum(sls) / len(sls) / 1000.0
        out["median_exit"] = statistics.median(sls) / 1000.0
        out["mean_below_stop"] = sum(stop - v for v in sls) / len(sls) / 1000.0
        out["min_exit"] = min(sls) / 1000.0
        out["max_exit"] = max(sls) / 1000.0
    return out


def empty_bid_exits(live_trades: Sequence[dict]) -> dict:
    """Early exits that Tier A priced although the bar's side bid was empty at some point."""
    hit = [t for t in live_trades if t["live_bid_empty"] and t["exit_type"] in (sim.TP, sim.SL)]
    return {"count": len(hit), "total_pnl": sum(t["pnl"] for t in hit)}


def gap_report(live_trades: Sequence[dict]) -> dict:
    """Paper minus live on the same simulated path (the path the live bound selected)."""
    n = len(live_trades)
    if not n:
        return {"trades": 0}
    g = [t["pnl_paper"] - t["pnl_live"] for t in live_trades]
    fees = [t["fee_entry"] + t["fee_exit"] for t in live_trades]
    early = [t for t in live_trades if t["exit_type"] in (sim.TP, sim.SL)]
    spread = [(t["paper_exit"] - t["live_exit"]) / 1000.0 for t in early]
    return {
        "trades": n,
        "gap_per_trade": sum(g) / n,
        "fees_per_trade": sum(fees) / n,
        "entry_fee_per_trade": sum(t["fee_entry"] for t in live_trades) / n,
        "exit_fee_per_trade": sum(t["fee_exit"] for t in live_trades) / n,
        "exit_ask_minus_bid_per_trade": sum(spread) / n,
        "early_exits": len(early),
        "exit_ask_minus_bid_per_early_exit": (sum(spread) / len(early)) if early else 0.0,
        "gap_total": sum(g),
    }


# ---------------------------------------------------------------------------
# Tier B
# ---------------------------------------------------------------------------
def run_tier_b(data: str, markets: Sequence[sim.Market], sample: Sequence[dict], p: sim.Params,
               tier_a: Dict[str, Dict[str, List[dict]]]) -> dict:
    by_t = {m.ticker: m for m in markets}
    a_idx = {model: {b: {t["ticker"]: t for t in tier_a[model][b]} for b in BOUNDS} for model in MODELS}
    rows = []
    missing = []
    diag_tot: Dict[str, float] = {}
    for s in sample:
        t = s["ticker"]
        path = os.path.join(data, "trades", t + ".json")
        m = by_t.get(t)
        if m is None or not os.path.exists(path):
            missing.append(t)
            continue
        tj = read_json(path)
        T = m.close_ts
        sp = sim.reconstruct_path(tj["trades"], m, max(T - sim.TIERB_LOOKBACK_S, tj.get("min_ts", 0)), T)
        for k, val in sp.diag.items():
            diag_tot[k] = diag_tot.get(k, 0) + val
        row = {"ticker": t, "reason": s["reason"], "close_ts": T, "prints": len(tj["trades"]), "diag": sp.diag}
        for model in MODELS:
            tr = sim.simulate_seconds(sp, m, p, model)
            row[model] = tr
            for b in BOUNDS:
                row["%s_%s" % (model, b)] = a_idx[model][b].get(t)
        rows.append(row)
    return {"rows": rows, "missing": missing, "diag_totals": diag_tot}


def _pnl(tr: Optional[dict]) -> float:
    return tr["pnl"] if tr else 0.0


def _unprinted_extremes(diag: dict) -> int:
    """Minutes in which the candle saw a quote extreme that no print reached."""
    return sum(int(diag.get(k, 0)) for k in (
        "minutes_candle_ask_high_beyond_prints", "minutes_candle_ask_low_beyond_prints",
        "minutes_candle_bid_high_beyond_prints", "minutes_candle_bid_low_beyond_prints"))


def tier_b_summary(tb: dict, stop: int) -> dict:
    rows = tb["rows"]
    out = {"markets_simulated": len(rows), "missing_trade_files": tb["missing"]}
    d = tb["diag_totals"]
    if rows:
        out["print_checks"] = {
            "prints_ask_side": int(d.get("prints_ask", 0)),
            "prints_bid_side": int(d.get("prints_bid", 0)),
            "ask_prints_in_candle_yes_ask_range": d.get("ask_prints_in_candle_range", 0) / max(1, d.get("prints_ask", 0)),
            "bid_prints_in_candle_yes_bid_range": d.get("bid_prints_in_candle_range", 0) / max(1, d.get("prints_bid", 0)),
            "ask_prints_within_1_tick_of_range": d.get("ask_prints_within_1_tick", 0) / max(1, d.get("prints_ask", 0)),
            "bid_prints_within_1_tick_of_range": d.get("bid_prints_within_1_tick", 0) / max(1, d.get("prints_bid", 0)),
            "seconds_with_ask_print": d.get("seconds_with_ask_print", 0) / max(1, d.get("seconds", 0)),
            "seconds_with_bid_print": d.get("seconds_with_bid_print", 0) / max(1, d.get("seconds", 0)),
            "clamped_values_per_market": d.get("clamps", 0) / len(rows),
            "uncrossed_seconds_per_market": d.get("uncrossed_seconds", 0) / len(rows),
            "mean_ask_age_s": d.get("mean_ask_age_s", 0) / len(rows),
            "mean_bid_age_s": d.get("mean_bid_age_s", 0) / len(rows),
            "minutes_with_ask_prints": int(d.get("minutes_with_ask_prints", 0)),
            "minutes_with_bid_prints": int(d.get("minutes_with_bid_prints", 0)),
            "minutes_candle_ask_high_beyond_prints": int(d.get("minutes_candle_ask_high_beyond_prints", 0)),
            "minutes_candle_ask_low_beyond_prints": int(d.get("minutes_candle_ask_low_beyond_prints", 0)),
            "minutes_candle_bid_high_beyond_prints": int(d.get("minutes_candle_bid_high_beyond_prints", 0)),
            "minutes_candle_bid_low_beyond_prints": int(d.get("minutes_candle_bid_low_beyond_prints", 0)),
        }
    for subset_name, subset in (("all", rows),
                                ("systematic", [r for r in rows if r["reason"] == "systematic"]),
                                ("ambiguous_supplement", [r for r in rows if r["reason"] == "ambiguous_supplement"])):
        if not subset:
            continue
        s_out = {"markets": len(subset)}
        for model in MODELS:
            truth = [r[model] for r in subset if r[model]]
            s_out["%s_truth_stats" % model] = sim.stats(truth)
            s_out["%s_truth_by_exit" % model] = sim.breakdown(truth, lambda t: t["exit_type"])
            s_out["%s_truth_by_entry_bucket" % model] = sim.breakdown(truth, lambda t: sim.entry_bucket(t["entry"]))
            s_out["%s_truth_by_mins_left" % model] = sim.breakdown(truth, lambda t: sim.mins_left_bucket(t["mins_left"]))
            opt = sum(_pnl(r["%s_opt" % model]) for r in subset)
            pes = sum(_pnl(r["%s_pes" % model]) for r in subset)
            tru = sum(_pnl(r[model]) for r in subset)
            inside = below = above = inside_tol = 0
            positions = []
            raw_positions = []
            why = {"exit_price_only": 0, "path_differs_unprinted_quote_extremes": 0, "path_differs_other": 0}
            for r in subset:
                lo_v = _pnl(r["%s_pes" % model])
                hi_v = _pnl(r["%s_opt" % model])
                tv = _pnl(r[model])
                inside_tol += (lo_v - BRACKET_TOL <= tv <= hi_v + BRACKET_TOL)
                violated = None
                outside = True
                if tv < lo_v - 1e-9:
                    below += 1
                    violated = r["%s_pes" % model]
                elif tv > hi_v + 1e-9:
                    above += 1
                    violated = r["%s_opt" % model]
                else:
                    inside += 1
                    outside = False
                if outside:
                    if r[model] and violated and all(r[model][k] == violated[k] for k in ("side", "entry", "exit_type")):
                        why["exit_price_only"] += 1
                        et = "exit_price_only_" + r[model]["exit_type"]
                        why[et] = why.get(et, 0) + 1
                    elif _unprinted_extremes(r["diag"]) > 0:
                        why["path_differs_unprinted_quote_extremes"] += 1
                    else:
                        why["path_differs_other"] += 1
                if hi_v - lo_v > 1e-9:
                    raw_positions.append((tv - lo_v) / (hi_v - lo_v))
                    positions.append(min(POSITION_CLIP[1], max(POSITION_CLIP[0], raw_positions[-1])))
            both_trade = [r for r in subset if r[model] and r["%s_opt" % model]]
            same_side = sum(1 for r in both_trade if r[model]["side"] == r["%s_opt" % model]["side"])
            entry_diff = [(r[model]["entry"] - r["%s_opt" % model]["entry"]) / 1000.0 for r in both_trade
                          if r[model]["side"] == r["%s_opt" % model]["side"]]
            ex_match_opt = sum(1 for r in both_trade if r[model]["exit_type"] == r["%s_opt" % model]["exit_type"])
            ex_match_pes = sum(1 for r in both_trade if r["%s_pes" % model] and
                               r[model]["exit_type"] == r["%s_pes" % model]["exit_type"])
            s_out[model] = {
                "sum_pessimistic": pes,
                "sum_truth": tru,
                "sum_optimistic": opt,
                "per_market_bracketed": inside,
                "per_market_bracketed_within_0.0105": inside_tol,
                "outside_bounds_reasons": why,
                "per_market_truth_below_pessimistic": below,
                "per_market_truth_above_optimistic": above,
                "mean_position_in_bounds": (sum(positions) / len(positions)) if positions else None,
                "mean_position_unclipped": (sum(raw_positions) / len(raw_positions)) if raw_positions else None,
                "markets_position_clipped": sum(1 for v in raw_positions
                                                if v < POSITION_CLIP[0] or v > POSITION_CLIP[1]),
                "position_of_sums": ((tru - pes) / (opt - pes)) if opt - pes > 1e-9 else None,
                "markets_with_nonzero_bound_width": len(positions),
                "truth_trades": sum(1 for r in subset if r[model]),
                "tier_a_trades": sum(1 for r in subset if r["%s_opt" % model]),
                "entry_both": len(both_trade),
                "entry_same_side": same_side,
                "entry_price_truth_minus_tier_a_mean": (sum(entry_diff) / len(entry_diff)) if entry_diff else None,
                "entry_price_truth_minus_tier_a_abs_mean": (sum(abs(x) for x in entry_diff) / len(entry_diff)) if entry_diff else None,
                "exit_type_match_opt": ex_match_opt,
                "exit_type_match_pes": ex_match_pes,
            }
        s_out["truth_sl_live_exit"] = sl_distribution([r["live"] for r in subset if r["live"]], "live_exit", stop)
        s_out["truth_sl_paper_exit"] = sl_distribution([r["paper"] for r in subset if r["paper"]], "paper_exit", stop)
        s_out["truth_gap"] = gap_report([r["live"] for r in subset if r["live"]])
        out[subset_name] = s_out
    return out


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def write_csvs(out_dir: str, tier_a, tb_rows) -> None:
    keys = {}
    for model in MODELS:
        for b in BOUNDS:
            for t in tier_a[model][b]:
                keys.setdefault(t["ticker"], {})["%s_%s" % (model, b)] = t
    with open(os.path.join(out_dir, "tier_a_trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        hdr = ["ticker", "close_utc"]
        for model in MODELS:
            for b in BOUNDS:
                hdr += ["%s_%s_%s" % (model, b, c) for c in ("side", "entry", "exit_type", "exit_price", "pnl")]
        w.writerow(hdr)
        for tk in sorted(keys, key=lambda k: next(iter(keys[k].values()))["close_ts"]):
            d = keys[tk]
            any_t = next(iter(d.values()))
            row = [tk, dt.datetime.fromtimestamp(any_t["close_ts"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M")]
            for model in MODELS:
                for b in BOUNDS:
                    t = d.get("%s_%s" % (model, b))
                    if t:
                        px = t["paper_exit"] if model == "paper" else t["live_exit"]
                        row += [t["side"], "%.3f" % (t["entry"] / 1000), t["exit_type"], "%.3f" % (px / 1000), "%.4f" % t["pnl"]]
                    else:
                        row += ["", "", "", "", ""]
            w.writerow(row)
    with open(os.path.join(out_dir, "tier_b_trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        hdr = ["ticker", "reason", "prints"]
        for model in MODELS:
            hdr += ["%s_truth_%s" % (model, c) for c in ("side", "entry", "mins_left", "exit_type", "exit_price", "pnl")]
            hdr += ["%s_pes_pnl" % model, "%s_opt_pnl" % model]
        w.writerow(hdr)
        for r in tb_rows:
            row = [r["ticker"], r["reason"], r["prints"]]
            for model in MODELS:
                t = r[model]
                if t:
                    px = t["paper_exit"] if model == "paper" else t["live_exit"]
                    row += [t["side"], "%.3f" % (t["entry"] / 1000), "%.2f" % t["mins_left"], t["exit_type"], "%.3f" % (px / 1000), "%.4f" % t["pnl"]]
                else:
                    row += ["", "", "", "", "", ""]
                row += ["%.4f" % _pnl(r["%s_pes" % model]), "%.4f" % _pnl(r["%s_opt" % model])]
            w.writerow(row)


def fmt_money(v: Optional[float], nd: int = 4) -> str:
    if v is None:
        return "n/a"
    return ("%+." + str(nd) + "f") % v


def fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else "%.1f%%" % (100.0 * v)


def stats_row(label: str, s: dict) -> str:
    return "| %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
        label, s["trades"], fmt_pct(s["win_rate"]), fmt_money(s["avg_win"]), fmt_money(s["avg_loss"]),
        fmt_money(s["pnl_per_trade"]), fmt_money(s["total_pnl"], 2), "%.2f" % s["max_drawdown"],
        fmt_pct(s["breakeven_win_rate"]))


STATS_HDR = ("| Model / bound | Trades | Win rate | Avg win | Avg loss | PnL/trade | Total PnL | Max DD | Break-even win rate |\n"
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|")


def breakdown_table(title: str, reps: Dict[str, dict], key: str, order: Optional[List[str]] = None) -> str:
    combos = [("paper", "opt"), ("paper", "pes"), ("live", "opt"), ("live", "pes")]
    buckets = set()
    for model, b in combos:
        buckets |= set(reps[model][b][key].keys())
    buckets = [k for k in (order or sorted(buckets)) if k in buckets]
    lines = ["**%s** (n, win rate, PnL/trade)" % title, "",
             "| Bucket | Paper opt | Paper pes | Live opt | Live pes |", "|---|---|---|---|---|"]
    for k in buckets:
        cells = []
        for model, b in combos:
            s = reps[model][b][key].get(k)
            cells.append("%d, %s, %s" % (s["trades"], fmt_pct(s["win_rate"]), fmt_money(s["pnl_per_trade"])) if s else "0")
        lines.append("| %s | %s |" % (k, " | ".join(cells)))
    return "\n".join(lines)


def write_report(path: str, summary: dict) -> None:
    S = summary
    p = S["params"]
    A = S["tier_a"]
    reps = {m: {b: A[m][b] for b in BOUNDS} for m in MODELS}
    L = []
    L.append("# KXBTC15M backtest: entry [%.2f, %.2f], stop below %.2f, take-profit +%.2f, last %g minutes" % (
        p["entry_min"], p["entry_max"], p["exit_below"], p["take_profit_pts"], p["time_delay_min"]))
    L.append("")
    L.append("Generated %s from cached public Kalshi data. Every number below was computed by "
             "`run_backtest.py`; dollar amounts are per %d contract(s)." % (S["generated_at"], p["contracts"]))
    L.append("")
    L.append("Settings: enter when a side's ask is inside [%.2f, %.2f]; stop when the side ask falls below %.2f "
             "(%d one-second confirmation%s); take-profit %.2f above the fill; entry window of the last %g minutes%s; "
             "daily profit target %g and loss target %g." % (
                 p["entry_min"], p["entry_max"], p["exit_below"], p["pings"], "" if p["pings"] == 1 else "s",
                 p["take_profit_pts"], p["time_delay_min"],
                 (", no new entries in the last %g minutes" % p["min_left_min"]) if p["min_left_min"] else "",
                 p["daily_profit_target"], p["daily_loss_target"]))
    L.append("")
    L.append("## Bottom line")
    L.append("")
    B = S.get("tier_b", {})
    lo = A["live"]["pes"]["all"]
    hi = A["live"]["opt"]["all"]
    plo = A["paper"]["pes"]["all"]
    phi = A["paper"]["opt"]["all"]
    if B.get("systematic"):
        bs = B["systematic"]
        lt = bs["live_truth_stats"]
        pt = bs["paper_truth_stats"]
        L.append("- Per-second replay (Tier B) of %d markets spread evenly over the 30 days: the live model made %s per "
                 "trade after fees (standard error %s) over %d trades, total %s; win rate %s against a break-even win "
                 "rate of %s (average win %s, average loss %s). The paper model on the same markets: %s per trade, "
                 "win rate %s, break-even %s." % (
                     bs["markets"], fmt_money(lt["pnl_per_trade"]), "%.4f" % lt["pnl_per_trade_se"], lt["trades"],
                     fmt_money(lt["total_pnl"], 2), fmt_pct(lt["win_rate"]), fmt_pct(lt["breakeven_win_rate"]),
                     fmt_money(lt["avg_win"]), fmt_money(lt["avg_loss"]), fmt_money(pt["pnl_per_trade"]),
                     fmt_pct(pt["win_rate"]), fmt_pct(pt["breakeven_win_rate"])))
    L.append("- 1-minute candles (Tier A), all %d usable markets, %d trades: live between %s and %s per trade (total "
             "%s to %s); paper between %s and %s per trade. These are envelopes (every take-profit at the bar high "
             "versus every stop at the bar low), not estimates." % (
                 S["data"]["markets_usable"], hi["trades"], fmt_money(lo["pnl_per_trade"]), fmt_money(hi["pnl_per_trade"]),
                 fmt_money(lo["total_pnl"], 2), fmt_money(hi["total_pnl"], 2), fmt_money(plo["pnl_per_trade"]),
                 fmt_money(phi["pnl_per_trade"])))
    if B.get("all"):
        ball = B["all"]
        x = ball["live"]
        mp = x["mean_position_in_bounds"]
        ps = x["position_of_sums"]
        L.append("- Calibration on all %d replayed markets: live truth %s against Tier A %s (pessimistic) and %s "
                 "(optimistic); the truth was inside the bounds in %d markets (%d within +/-0.0105), below in %d, above "
                 "in %d. The summed truth sits %s of the way from the summed pessimistic to the summed optimistic "
                 "result; the mean of the per-market positions, clipped to [%g, %g], is %s over the %d markets whose "
                 "bounds differ (%d clipped, unclipped mean %s)." % (
                     ball["markets"], fmt_money(x["sum_truth"], 2), fmt_money(x["sum_pessimistic"], 2),
                     fmt_money(x["sum_optimistic"], 2), x["per_market_bracketed"], x["per_market_bracketed_within_0.0105"],
                     x["per_market_truth_below_pessimistic"], x["per_market_truth_above_optimistic"],
                     "n/a" if ps is None else "%.2f" % ps, POSITION_CLIP[0], POSITION_CLIP[1],
                     "n/a" if mp is None else "%.2f" % mp, x["markets_with_nonzero_bound_width"],
                     x["markets_position_clipped"],
                     "n/a" if x["mean_position_unclipped"] is None else "%.3f" % x["mean_position_unclipped"]))
        g = B.get("systematic", ball).get("truth_gap", {})
        if g.get("trades"):
            L.append("- Paper overstates live by %s per trade on the replayed systematic-sample paths: fees %s plus "
                     "selling at the bid instead of the ask %s." % (
                         fmt_money(g["gap_per_trade"]), fmt_money(g["fees_per_trade"]),
                         fmt_money(g["exit_ask_minus_bid_per_trade"])))
    L.append("")
    L.append("## Data")
    L.append("")
    D = S["data"]
    L.append("- Period: settled KXBTC15M markets with close_time in (%s, %s]; first close %s, last close %s." % (
        D["window_start_utc"], D["window_end_utc"], D["first_close_utc"], D["last_close_utc"]))
    L.append("- Markets listed by the API: %d (expected %d for 96 per day). Usable: %d." % (
        D["markets_listed"], D["markets_expected"], D["markets_usable"]))
    if D.get("absent_quarter_hours"):
        note = ("all are Thursday closes between 07:15 and 09:00 UTC, the weekly Thursday 03:00-05:00 ET trading "
                "pause, when no KXBTC15M market is listed" if D.get("absent_all_thursday_pause")
                else "not all fall in the Thursday maintenance pause")
        L.append("- The %d absent quarter-hours: %s; %s." % (
            D["absent_quarter_hours"], "; ".join("%s %s" % kv for kv in D["absent_by_day"].items()), note))
    L.append("- Results: %s." % ", ".join("%s %d" % (k, v) for k, v in sorted(D["results"].items())))
    L.append("- Candles per market (count: markets): %s. Markets without candles: %d. Candle fetch errors: %d." % (
        ", ".join("%s: %d" % kv for kv in D["candles_per_market"].items()), len(D["no_candles"]) + len(D["no_candle_file"]),
        len(D["candle_fetch_errors"])))
    L.append("- Missing minutes: %d in total, %d inside or just before the entry window (%d markets). Null OHLC fields: %d. "
             "Excluded: %d without a yes/no result, %d with an incomplete window." % (
                 D["missing_minutes_total"], D["missing_minutes_in_window"], len(D["markets_with_missing_window_minutes"]),
                 D["null_ohlc_fields"], len(D["excluded_no_result"]), len(D["excluded_bad_window"])))
    L.append("- Window bars with an empty book side somewhere in the minute (YES ask 1.00 or YES bid 0.00): %d of %d." % (
        D["window_bars_with_empty_book_side"], D["window_bars_total"]))
    L.append("")
    L.append("## Tier A results (all markets, 1-minute candles, no daily limit)")
    L.append("")
    L.append(STATS_HDR)
    for model in MODELS:
        for b in BOUNDS:
            L.append(stats_row("%s %s" % (model, BOUND_NAME[b]), A[model][b]["all"]))
    fc = A.get("live_fee_cent")
    if fc:
        for b in BOUNDS:
            L.append(stats_row("live %s, cent-rounded fees (FCM / pre-Jul-2026)" % BOUND_NAME[b], fc[b]))
    L.append("")
    L.append("Markets with no entry: %d. Markets whose Tier A outcome depends on intrabar order (live model, bounds give "
             "different PnL): %d; different exit type or side: %d." % (
                 A["no_entry_markets"], A["ambiguity"]["live_pnl_differs"], A["ambiguity"]["live_exit_or_side_differs"]))
    L.append("")
    L.append(breakdown_table("By entry price", reps, "by_entry_bucket", ["0.80-0.84", "0.85-0.89", "0.90-0.93", "other"]))
    L.append("")
    L.append(breakdown_table("By exit type", reps, "by_exit_type", list(sim.EXIT_TYPES)))
    L.append("")
    L.append(breakdown_table("By minutes left at entry", reps, "by_mins_left",
                             ["7-8", "6-7", "5-6", "4-5", "3-4", "2-3", "1-2", "0-1"]))
    L.append("")
    L.append(breakdown_table("By week (UTC, Monday start)", reps, "by_week"))
    L.append("")
    L.append(breakdown_table("By side", reps, "by_side", ["UP", "DOWN"]))
    L.append("")
    L.append("## Daily limits (loss %g, profit %g)" % (p["daily_loss_target"], p["daily_profit_target"]))
    L.append("")
    L.append("Two readings of the limit. `utc_day`: closed PnL accumulates per UTC day and trading stops for the rest of "
             "that day once it crosses a target. `since_start`: the starting balance is recorded once and the run ends "
             "the first time the closed PnL since the first trade crosses a target, modelled here as a single start at "
             "the beginning of the period.")
    L.append("")
    L.append("| Model / bound | Reading | Stop events | Trades skipped | Trades | PnL/trade | Total PnL | Max DD |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for model in MODELS:
        for b in BOUNDS:
            for mode in ("utc_day", "since_start"):
                d = A[model][b]["daily_limits"][mode]
                s = d["stats"]
                L.append("| %s %s | %s | %d | %d | %d | %s | %s | %.2f |" % (
                    model, BOUND_NAME[b], mode, len(d["stop_events"]), d["trades_skipped"], s["trades"],
                    fmt_money(s["pnl_per_trade"]), fmt_money(s["total_pnl"], 2), s["max_drawdown"]))
    for model in MODELS:
        for b in BOUNDS:
            ev = A[model][b]["daily_limits"]["since_start"]["stop_events"]
            if ev:
                e0 = ev[0]
                L.append("")
                L.append("- %s %s, since_start: trading would stop on %s after %s (accumulated %s)." % (
                    model, BOUND_NAME[b], e0["day"], e0["after_ticker"], fmt_money(e0["accumulated"], 2)))
    L.append("")
    L.append("## Stop-loss exit prices")
    L.append("")
    L.append("| Source | Stops | Mean exit | Median | Mean distance below the stop level | Min | " +
             " | ".join(name for _, _, name in SL_BUCKETS) + " |")
    L.append("|---|---:|---:|---:|---:|---:|" + "---:|" * len(SL_BUCKETS))

    def sl_row(label, d):
        if not d.get("count"):
            return "| %s | 0 | | | | |%s" % (label, " |" * len(SL_BUCKETS))
        return "| %s | %d | %.3f | %.3f | %.3f | %.3f | %s |" % (
            label, d["count"], d["mean_exit"], d["median_exit"], d["mean_below_stop"], d["min_exit"],
            " | ".join(str(d["buckets"][name]) for _, _, name in SL_BUCKETS))
    for model in MODELS:
        for b in BOUNDS:
            L.append(sl_row("Tier A %s %s (%s)" % (model, BOUND_NAME[b], "ask" if model == "paper" else "bid"),
                            A[model][b]["sl_exits"]))
    for sub in ("systematic", "all"):
        if B.get(sub):
            L.append(sl_row("Tier B truth %s, paper (ask at confirmation)" % sub, B[sub]["truth_sl_paper_exit"]))
            L.append(sl_row("Tier B truth %s, live (bid at confirmation)" % sub, B[sub]["truth_sl_live_exit"]))
    L.append("")
    L.append("## Paper minus live, same simulated path")
    L.append("")
    L.append("| Source | Trades | Gap/trade | Fees/trade | Entry fee | Exit fee | Exit ask-bid/trade | Ask-bid per early exit |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for b in BOUNDS:
        g = A["gap"][b]
        L.append("| Tier A %s | %d | %s | %s | %s | %s | %s | %s |" % (
            BOUND_NAME[b], g["trades"], fmt_money(g["gap_per_trade"]), fmt_money(g["fees_per_trade"]),
            fmt_money(g["entry_fee_per_trade"]), fmt_money(g["exit_fee_per_trade"]),
            fmt_money(g["exit_ask_minus_bid_per_trade"]), fmt_money(g["exit_ask_minus_bid_per_early_exit"])))
    for sub in ("systematic", "all"):
        if B.get(sub) and B[sub]["truth_gap"].get("trades"):
            g = B[sub]["truth_gap"]
            L.append("| Tier B truth %s | %d | %s | %s | %s | %s | %s | %s |" % (
                sub, g["trades"], fmt_money(g["gap_per_trade"]), fmt_money(g["fees_per_trade"]),
                fmt_money(g["entry_fee_per_trade"]), fmt_money(g["exit_fee_per_trade"]),
                fmt_money(g["exit_ask_minus_bid_per_trade"]), fmt_money(g["exit_ask_minus_bid_per_early_exit"])))
    L.append("")
    L.append("## Tier B calibration")
    L.append("")
    if B.get("all"):
        pc = B.get("print_checks", {})
        L.append("Per-second YES bid/ask rebuilt for the last 9 minutes from trade prints (a print with taker_book_side "
                 "'bid' reveals the YES ask, 'ask' reveals the YES bid; prints that share a timestamp are ordered by "
                 "price priority), then the entry and exit rules polled about once per second.")
        L.append("")
        L.append("- Sample: %d markets simulated (%s); missing trade files: %d." % (
            B["markets_simulated"], ", ".join("%s %d" % (k, B[k]["markets"]) for k in ("systematic", "ambiguous_supplement") if k in B),
            len(B["missing_trade_files"])))
        L.append("- Print sanity: %d YES-ask prints and %d YES-bid prints; %s of ask prints inside the candle yes_ask "
                 "[low, high] for their minute (%s within one tick), %s of bid prints inside yes_bid (%s within one "
                 "tick). Seconds with an ask print %s, with a bid print %s. Clamped values per market %.1f, uncrossed "
                 "seconds per market %.1f, mean quote age %.2f s (ask) / %.2f s (bid)." % (
                     pc["prints_ask_side"], pc["prints_bid_side"], fmt_pct(pc["ask_prints_in_candle_yes_ask_range"]),
                     fmt_pct(pc["ask_prints_within_1_tick_of_range"]), fmt_pct(pc["bid_prints_in_candle_yes_bid_range"]),
                     fmt_pct(pc["bid_prints_within_1_tick_of_range"]), fmt_pct(pc["seconds_with_ask_print"]),
                     fmt_pct(pc["seconds_with_bid_print"]), pc["clamped_values_per_market"],
                     pc["uncrossed_seconds_per_market"], pc["mean_ask_age_s"], pc["mean_bid_age_s"]))
        L.append("- Quote moves with no print behind them (candle extreme more than one tick beyond every print "
                 "of that minute): YES-ask high %d and low %d of %d minutes; YES-bid high %d and low %d of %d minutes. "
                 "The strategy polls the book and can react to these; a print-based path cannot, which is Tier B's main "
                 "blind spot." % (
                     pc["minutes_candle_ask_high_beyond_prints"], pc["minutes_candle_ask_low_beyond_prints"],
                     pc["minutes_with_ask_prints"], pc["minutes_candle_bid_high_beyond_prints"],
                     pc["minutes_candle_bid_low_beyond_prints"], pc["minutes_with_bid_prints"]))
        L.append("")
        L.append("| Subset | Model | Markets | Sum pes | Sum truth | Sum opt | Bracketed | Within +/-0.0105 | Truth < pes | Truth > opt | Position of sums (0 = pes, 1 = opt) | Mean clipped position (0 = pes, 1 = opt) |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for sub in ("all", "systematic", "ambiguous_supplement"):
            if sub not in B:
                continue
            for model in MODELS:
                x = B[sub][model]
                mp = x["mean_position_in_bounds"]
                ps = x["position_of_sums"]
                L.append("| %s | %s | %d | %s | %s | %s | %d | %d | %d | %d | %s | %s |" % (
                    sub, model, B[sub]["markets"], fmt_money(x["sum_pessimistic"], 2), fmt_money(x["sum_truth"], 2),
                    fmt_money(x["sum_optimistic"], 2), x["per_market_bracketed"], x["per_market_bracketed_within_0.0105"],
                    x["per_market_truth_below_pessimistic"], x["per_market_truth_above_optimistic"],
                    "n/a" if ps is None else "%.2f" % ps,
                    "n/a" if mp is None else "%.2f (over %d markets, %d clipped)" % (
                        mp, x["markets_with_nonzero_bound_width"], x["markets_position_clipped"])))
        L.append("")
        L.append("The position of the sums is (sum truth - sum pes) / (sum opt - sum pes). The mean clipped position "
                 "averages each market's own (truth - pes) / (opt - pes) after clipping it to [%g, %g], over the markets "
                 "whose two bounds differ; a market with a tiny bound width can otherwise dominate the mean." % (
                     POSITION_CLIP[0], POSITION_CLIP[1]))
        L.append("")
        for model in MODELS:
            w = B["all"][model]["outside_bounds_reasons"]
            by_type = ", ".join("%s %d" % (k[len("exit_price_only_"):], v) for k, v in sorted(w.items())
                                if k.startswith("exit_price_only_"))
            tp_note = ("; the live take-profit bid is the one non-strict Tier A price" if model == "live" else "")
            L.append("- Why the %s truth fell outside the bounds (all replayed markets): same side, entry and exit type "
                     "but a different exit price in %d (%s%s); a different path in a market whose candles show a quote "
                     "extreme no trade printed in %d (Tier A assumes the strategy's polls catch it, Tier B cannot see "
                     "it); a different path otherwise in %d." % (
                         model, w["exit_price_only"], by_type or "none", tp_note,
                         w["path_differs_unprinted_quote_extremes"], w["path_differs_other"]))
        x = B["all"]["live"]
        L.append("- Entry agreement (live, all Tier B markets): truth traded in %d markets, Tier A in %d; both in %d, "
                 "same side in %d; mean truth-minus-Tier-A entry price %s (mean absolute %s). Exit type matches "
                 "Tier A optimistic in %d and pessimistic in %d of the %d." % (
                     x["truth_trades"], x["tier_a_trades"], x["entry_both"], x["entry_same_side"],
                     fmt_money(x["entry_price_truth_minus_tier_a_mean"]), fmt_money(x["entry_price_truth_minus_tier_a_abs_mean"]),
                     x["exit_type_match_opt"], x["exit_type_match_pes"], x["entry_both"]))
        L.append("")
        L.append("| Tier B truth | " + STATS_HDR.split("\n")[0].split("|", 2)[2])
        L.append(STATS_HDR.split("\n")[1])
        for sub in ("all", "systematic", "ambiguous_supplement"):
            if sub in B:
                for model in MODELS:
                    L.append(stats_row("%s, %s" % (sub, model), B[sub]["%s_truth_stats" % model]))
        if "systematic" in B:
            bs = B["systematic"]
            for key, title, order in (("by_exit", "exit type", list(sim.EXIT_TYPES)),
                                      ("by_entry_bucket", "entry price", ["0.80-0.84", "0.85-0.89", "0.90-0.93", "other"]),
                                      ("by_mins_left", "minutes left at entry",
                                       ["7-8", "6-7", "5-6", "4-5", "3-4", "2-3", "1-2", "0-1"])):
                L.append("")
                L.append("**Tier B truth, systematic sample, by %s** (n, win rate, PnL/trade)" % title)
                L.append("")
                L.append("| Bucket | Paper | Live |")
                L.append("|---|---|---|")
                for k in order:
                    cells = []
                    for model in MODELS:
                        s = bs["%s_truth_%s" % (model, key)].get(k)
                        cells.append("%d, %s, %s" % (s["trades"], fmt_pct(s["win_rate"]), fmt_money(s["pnl_per_trade"]))
                                     if s else "0")
                    if any(c != "0" for c in cells):
                        L.append("| %s | %s |" % (k, " | ".join(cells)))
    else:
        L.append("Tier B was not run (no cached trade files).")
    L.append("")
    L.append("## Method")
    L.append("")
    for line in S["method"]:
        L.append("- " + line)
    L.append("")
    L.append("## Caveats")
    L.append("")
    for line in S["caveats"]:
        L.append("- " + line)
    eb = A.get("empty_bid_exits")
    if eb:
        L.append("- Tier A prices a live exit inside the bar's side-bid range, with a floor of 0.001, even when the bar's "
                 "bid side was empty at some point (a pessimistic stop is then sold at 0.001). Such exits could not "
                 "fill at those prices: %d in the live pessimistic bound (total %s) and %d in the live optimistic bound "
                 "(total %s). Tier B treats an empty bid as no fill and waits for a bid or rides to settlement." % (
                     eb["pes"]["count"], fmt_money(eb["pes"]["total_pnl"], 2), eb["opt"]["count"],
                     fmt_money(eb["opt"]["total_pnl"], 2)))
    L.append("")
    L.append("## Rerun")
    L.append("")
    L.append("```")
    L.append("python fetch.py all          # only needed once; resumable, cached under data/")
    L.append("python -m pytest tests -q    # or: python -m unittest discover -s tests")
    L.append("python run_backtest.py       # offline; writes results/")
    L.append("```")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


METHOD = [
    "Rules (the published strategy described in the README, written out here in this backtester's own terms): one "
    "market per quarter hour; entries only while the minutes left to the market's close are in (0, window]; UP if the "
    "UP ask is inside the entry range (and the DOWN ask is not, or UP ask >= DOWN ask), else DOWN if the DOWN ask is "
    "inside the range; one entry per market, no re-entry after an exit; stop when the side ask is below the stop level "
    "and still below it one second later (one confirmation); take-profit when the side ask reaches the fill plus the "
    "take-profit distance and that target is at most 1; otherwise held to settlement.",
    "Prices: UP ask = YES ask, DOWN ask = 1 - YES bid, UP bid = YES bid, DOWN bid = 1 - YES ask. The strategy never sees "
    "an ask of 1.00 (an empty book side leaves its up/down values stale), so a fill at exactly 0.90 (target 1.00) can "
    "never take profit.",
    "Paper model: fill = ask at trigger, exits at the side ask seen at the trigger or confirmation poll, no fees. Live "
    "model: fill = ask at trigger (a buy limit 0.02 above the ask crosses the book), take-profit and stop exits at the "
    "side bid (immediate-or-cancel sell at 1c), taker fee on the entry and on take-profit and stop exits, settlement free.",
    "Fees: Kalshi schedule effective July 7, 2026 with the docs fee_rounding algorithm: trade fee = 0.07 x C x P x (1-P) "
    "rounded up to $0.000001, balance change floored to $0.0001 (direct member). A cent-rounded variant (FCM account, or the "
    "pre-July-2026 rule) is reported as a sensitivity row.",
    "Tier A (1-minute candles, bar = [end_period_ts - 60, end_period_ts)): entry in the first window bar where a side's "
    "ask is in range at the bar open (price = the open) or crosses into the range inside the bar. For a crossing, the "
    "first in-range poll can have seen any price between the boundary crossed and the bar extreme (from below: the lower "
    "entry bound up to min(high, upper entry bound); from above: max(low, lower entry bound) up to the upper entry bound), so every grid price in that interval is simulated as "
    "a branch (the entry sets the take-profit target, so an interior price can beat both ends). If both sides only "
    "cross within the same bar, both sides are branches too.",
    "Tier A exits per bar: TP is touched if the side ask high (capped at 0.999) >= target; SL is touched if the side "
    "ask low is below the stop level. A stop is certain when the bar also closes below it (still below one second later); when the bar "
    "dips below the stop level but closes at or above it, the one-second confirmation is unknown, so both 'stopped' and 'not "
    "stopped' are kept as branches. In the entry bar, a dip that may have happened before an entry from below is also "
    "a branch. When TP and SL are both touched in a bar, both are kept.",
    "Tier A bounds: every branch is simulated to the end; OPTIMISTIC keeps the path with the best PnL for the model "
    "(so a same-bar TP/SL tie resolves TP first) and uses favorable prices, PESSIMISTIC keeps the worst path (SL first) "
    "with unfavorable prices.",
    "Tier A exit prices (the bar open when a TP bar already opens at or above the target): paper TP = the bar's highest "
    "side ask, capped at 0.999 (optimistic) or the target itself (pessimistic). Live TP = one tick under the optimistic "
    "paper price (optimistic) or the target minus the widest bid-ask spread seen at that bar's open or close "
    "(pessimistic), both kept inside the bar's side-bid range; the pessimistic live TP is the one price that is not a "
    "strict bar extreme (the bar's lowest bid usually predates the trigger), and Tier B checks it. Paper SL = the "
    "highest grid price under the stop level, 0.39 for a 0.40 stop (optimistic), or the bar's lowest side ask (pessimistic). Live SL = one tick under "
    "the optimistic paper price inside the bar's side-bid range (optimistic), or the bar's lowest side bid, floored at "
    "0.001 (pessimistic).",
    "Tier B: per-second YES bid/ask rebuilt from every trade print in the market's last 9 minutes. Each second holds "
    "the book at the start of that second: the candle open at a minute boundary (the same convention as Tier A), "
    "otherwise the last print of the previous second, clamped to the candle range and uncrossed; then about one poll "
    "per second. Prints that share a timestamp are ordered by price priority (YES-ask prints ascending, YES-bid prints "
    "descending), so the print that sets the book is the deepest level a sweep reached. Paper checks take-profit and "
    "stop from the next second; live from 3 s after the trigger (a 2 s pause plus order, position and fill lookups). A "
    "live stop sells at the side bid in the confirmation second.",
    "Daily limits are replayed on closed PnL in time order; results are reported without the limit, with a per-UTC-day "
    "reset, and with a since-start reading in which the first crossing ends the run.",
]

CAVEATS = [
    "Fills are assumed at the observed ask for 1 contract with no queue or latency effects; the live buy limit is ask + "
    "0.02, so a price that runs away by more than 2c during the request latency would rest unfilled and be cancelled, "
    "and the strategy would retry. Not modelled.",
    "The live model assumes fills report their fee at once, so take-profit is enabled immediately; if fill accounting "
    "lags, take-profit waits and results change.",
    "The original strategy also has an optional spot-price filter. It is assumed to be switched off, and outages of the "
    "price feed it reads (which would block entries) are not modelled.",
    "The market-list lag after each close (11-25 s observed) and Thursday 03:00-05:00 ET trading pauses are not "
    "modelled; neither affects an 8-minute window directly, but a paused market cannot be exited.",
    "Tier A bounds are deliberately wide: the optimistic bound sells every take-profit at the bar high and the "
    "pessimistic bound sells every stop at the bar low, and the stop confirmation is inferred from the bar close. "
    "Tier B shows where a per-second replay falls between them.",
    "Tier B reconstructs top of book from prints only. A side with no print carries its last value between minute "
    "boundaries, a print shows a level that traded rather than the resting book after the trade (after a sweep the ask "
    "can sit above the last printed level), prints that share a timestamp are put in price-priority order as an "
    "assumption about execution order, and quote moves that no trade printed (the strategy can see these by polling the "
    "book) are invisible to it. Tier B is the closest replay available from public data, not the strategy's own view; "
    "the calibration section counts why each out-of-bounds market fell outside.",
    "Tier B covers a sample (every 10th market plus ambiguous cases), so its per-trade mean carries sampling error; "
    "the report gives the standard error.",
    "Whether the original strategy halts when a daily limit is crossed is unverified; the daily-limit replay "
    "assumes it does.",
    "Fees assume a direct-member account (centi-cent rounding). An FCM-cleared account pays cent-rounded fees; see the "
    "sensitivity rows.",
    "Past 30 days only. BTC 15-minute markets moved to exchange shard 2 at 2026-08-25 04:00 UTC (in the cached list the "
    "last shard-0 market closes at that time and the first shard-2 market opens; all times in this report are UTC); "
    "market structure and liquidity can change.",
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--entry-min", type=float, default=0.80)
    ap.add_argument("--entry-max", type=float, default=0.93)
    ap.add_argument("--exit", type=float, default=0.40, dest="exit_below")
    ap.add_argument("--tp", type=float, default=0.10)
    ap.add_argument("--time-delay", type=float, default=8.0)
    ap.add_argument("--min-left", type=float, default=0.0,
                    help="no new entries with this many minutes or fewer left (open positions are still managed)")
    ap.add_argument("--pings", type=int, default=1)
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--fee-precision", choices=["centicent", "cent", "none"], default="centicent")
    ap.add_argument("--live-monitor-delay", type=int, default=3)
    ap.add_argument("--daily-loss", type=float, default=-20.0)
    ap.add_argument("--daily-profit", type=float, default=4000.0)
    ap.add_argument("--tierb-step", type=int, default=TIERB_STEP)
    ap.add_argument("--tierb-supplement", type=int, default=TIERB_SUPPLEMENT)
    ap.add_argument("--no-tierb", action="store_true")
    args = ap.parse_args(argv)

    p = sim.Params(entry_min=args.entry_min, entry_max=args.entry_max, exit_below=args.exit_below,
                   take_profit_pts=args.tp, time_delay_min=args.time_delay, min_left_min=args.min_left,
                   pings=args.pings,
                   contracts=args.contracts, fee_precision=args.fee_precision,
                   live_monitor_delay_s=args.live_monitor_delay, daily_loss_target=args.daily_loss,
                   daily_profit_target=args.daily_profit)
    os.makedirs(args.out_dir, exist_ok=True)
    idx = load_index(args.data_dir)
    metas = idx["markets"]
    markets, val = load_markets(args.data_dir, metas, p)
    val["window_start_utc"] = dt.datetime.fromtimestamp(idx["start_ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    val["window_end_utc"] = dt.datetime.fromtimestamp(idx["end_ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    val["markets_expected"] = (idx["end_ts"] - idx["start_ts"]) // 900
    val["first_close_utc"] = metas[0]["close_time"] if metas else None
    val["last_close_utc"] = metas[-1]["close_time"] if metas else None
    listed = {int(sim.iso_to_ts(m["close_time"])) for m in metas}
    absent = sorted(set(range(idx["start_ts"] + 900, idx["end_ts"] + 1, 900)) - listed)
    by_day: Dict[str, List[int]] = {}
    for ts in absent:
        by_day.setdefault(dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %a"), []).append(ts)
    val["absent_quarter_hours"] = len(absent)
    val["absent_all_thursday_pause"] = all(
        dt.datetime.fromtimestamp(ts, dt.timezone.utc).weekday() == 3
        and 7 * 3600 + 900 <= ts % 86400 <= 9 * 3600 for ts in absent)
    val["absent_by_day"] = {d: "%s-%s UTC (%d closes)" % (
        dt.datetime.fromtimestamp(v[0], dt.timezone.utc).strftime("%H:%M"),
        dt.datetime.fromtimestamp(v[-1], dt.timezone.utc).strftime("%H:%M"), len(v)) for d, v in by_day.items()}
    print("markets usable: %d of %d" % (len(markets), len(metas)))

    tier_a = run_tier_a(markets, p)
    A: dict = {}
    for model in MODELS:
        A[model] = {}
        for b in BOUNDS:
            rep = model_report(tier_a[model][b], p)
            price_key = "paper_exit" if model == "paper" else "live_exit"
            rep["sl_exits"] = sl_distribution(tier_a[model][b], price_key, p.x)
            A[model][b] = rep
    A["gap"] = {b: gap_report(tier_a["live"][b]) for b in BOUNDS}
    A["empty_bid_exits"] = {b: empty_bid_exits(tier_a["live"][b]) for b in BOUNDS}
    p_cent = dataclasses.replace(p, fee_precision="cent")
    A["live_fee_cent"] = {b: sim.stats([dict(t, pnl=sim.live_pnl(t["entry"], t["live_exit"], t["exit_type"], p_cent)[0])
                                        for t in tier_a["live"][b]]) for b in BOUNDS}
    traded = {t["ticker"] for t in tier_a["live"]["opt"]} | {t["ticker"] for t in tier_a["live"]["pes"]}
    A["no_entry_markets"] = len(markets) - len(traded)
    lo_i = {t["ticker"]: t for t in tier_a["live"]["pes"]}
    pnl_diff = exit_diff = 0
    for t in tier_a["live"]["opt"]:
        q = lo_i.get(t["ticker"])
        if q is None:
            continue
        pnl_diff += abs(t["pnl"] - q["pnl"]) > 1e-9
        exit_diff += (t["exit_type"] != q["exit_type"] or t["side"] != q["side"])
    A["ambiguity"] = {"live_pnl_differs": pnl_diff, "live_exit_or_side_differs": exit_diff,
                      "markets_with_branching": sum(1 for t in tier_a["live"]["opt"] if t["n_scenarios"] > 1)}

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "params": {k: (float(v) if k == "fee_rate" else v) for k, v in p.__dict__.items()},
        "data": val,
        "tier_a": A,
        "method": METHOD,
        "caveats": CAVEATS,
    }
    tb_rows = []
    if not args.no_tierb:
        # recomputed from the arguments every run (deterministic), so results never depend
        # on a stale data/tierb_sample.json; markets without cached prints are listed as missing
        sample = tierb_sample_from_markets(markets, p, args.tierb_step, args.tierb_supplement)
        tb = run_tier_b(args.data_dir, markets, sample, p, tier_a)
        tb_rows = tb["rows"]
        if tb["missing"]:
            print("warning: %d of %d Tier B sample markets have no cached trade prints and were skipped "
                  "(fetch.py caches prints for the default-settings sample only)" % (len(tb["missing"]), len(sample)))
        summary["tier_b"] = tier_b_summary(tb, p.x)
        summary["tier_b"]["sample_definition"] = ("every %dth market by close time (offset %d) plus up to %d markets whose "
                                                  "live Tier A bounds differ in side or exit type, evenly spaced" % (
                                                      args.tierb_step, TIERB_OFFSET, args.tierb_supplement))
    for model in MODELS:
        for b in BOUNDS:
            for t in tier_a[model][b]:
                t.pop("n_scenarios", None)
    write_csvs(args.out_dir, tier_a, tb_rows)
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str)
    write_report(os.path.join(args.out_dir, "report.md"), summary)
    for model in MODELS:
        for b in BOUNDS:
            s = A[model][b]["all"]
            print("%-5s %-3s trades %4d win %.3f pnl/trade %+.4f total %+.2f maxDD %.2f" % (
                model, b, s["trades"], s["win_rate"], s["pnl_per_trade"], s["total_pnl"], s["max_drawdown"]))
    if "tier_b" in summary and summary["tier_b"].get("all"):
        tb_all = summary["tier_b"]["all"]
        for model in MODELS:
            x = tb_all[model]
            print("TierB %-5s markets %d sum pes %+.2f truth %+.2f opt %+.2f bracketed %d below %d above %d" % (
                model, tb_all["markets"], x["sum_pessimistic"], x["sum_truth"], x["sum_optimistic"],
                x["per_market_bracketed"], x["per_market_truth_below_pessimistic"], x["per_market_truth_above_optimistic"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
