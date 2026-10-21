#!/usr/bin/env python3
"""Turn results/sweep_grid.json (from sweep_run.py) into results/sweep.json, results/sweep.md
and results/sweep_grid.csv. Pure post-processing: no simulation here.

  python sweep/sweep_report.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
RESULTS = os.path.join(BACKTEST, "results")
GRID = os.path.join(RESULTS, "sweep_grid.json")
MIN_TRAIN_TRADES = 150
TOP_N = 10
BASELINE = {"entry_min": 0.80, "entry_max": 0.93, "exit": 0.40, "tp": 0.10, "delay": 8}
PARAM_NAMES = ["entry_min", "entry_max", "exit", "tp", "delay"]
PARAM_LABEL = {"entry_min": "entry min", "entry_max": "entry max", "exit": "stop", "tp": "take-profit",
               "delay": "window (min)"}

STAT_KEYS = ("trades", "win_rate", "avg_win", "avg_loss", "pnl_per_trade", "total_pnl", "max_drawdown")


def fmt_val(name: str, v) -> str:
    if v is None:
        return "hold" if name == "tp" else "none"
    if name == "delay":
        return "%d" % v
    return "%.2f" % v


def short_params(c: dict) -> str:
    return "[%s, %s] exit %s tp %s delay %s" % (fmt_val("entry_min", c["entry_min"]), fmt_val("entry_max", c["entry_max"]),
                                                fmt_val("exit", c["exit"]), fmt_val("tp", c["tp"]), fmt_val("delay", c["delay"]))


def pick(s: dict) -> dict:
    return {k: s[k] for k in STAT_KEYS}


def rank_avg(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n < 3:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    return pearson(rank_avg(x), rank_avg(y))


def money(v) -> str:
    return "n/a" if v is None else ("%+.2f" % v)


def pt(v) -> str:
    return "n/a" if v is None else ("%+.4f" % v)


def pct(v) -> str:
    return "n/a" if v is None else ("%.1f%%" % (100.0 * v))


def num1(v) -> str:
    return "n/a" if v is None else ("%.1f" % v)


def fmt_grid_values(vals) -> str:
    return "{" + ", ".join(v if isinstance(v, str) else ("%d" % v if isinstance(v, int) else "%.2f" % v)
                           for v in vals) + "}"


def stat_row(s: dict) -> str:
    return "%d | %s | %s | %s | %s | %s | %.2f" % (s["trades"], pct(s["win_rate"]), pt(s["avg_win"]), pt(s["avg_loss"]),
                                                pt(s["pnl_per_trade"]), money(s["total_pnl"]), s["max_drawdown"])


STAT_HDR = "trades | win rate | avg win | avg loss | PnL/trade | total PnL | max DD"


def main() -> int:
    with open(GRID, "r", encoding="utf-8") as f:
        d = json.load(f)
    meta = d["meta"]
    cfgs: List[dict] = d["configs"]
    by_key: Dict[str, dict] = {c["key"]: c for c in cfgs}

    def key_of(pd: dict) -> str:
        return "%.2f|%.2f|%s|%s|%d" % (pd["entry_min"], pd["entry_max"],
                                        "none" if pd["exit"] is None else "%.2f" % pd["exit"],
                                        "hold" if pd["tp"] is None else "%.2f" % pd["tp"], pd["delay"])

    base = by_key.get(key_of(BASELINE))
    if base is None:
        sys.exit("the baseline configuration is missing from %s; rerun sweep_run.py "
                 "(--limit always keeps the baseline in the run)" % GRID)

    # ---- cross-check against the full-period summary written by run_backtest.py ----
    check = {}
    summ_path = os.path.join(RESULTS, "summary.json")
    if os.path.exists(summ_path):
        with open(summ_path, "r", encoding="utf-8") as f:
            summ = json.load(f)
        A = summ.get("tier_a") or {}
        try:
            ref_pes = A["live"]["pes"]["all"]
        except (KeyError, TypeError):
            ref_pes = None
        check["run_backtest_summary_available"] = ref_pes is not None
        if ref_pes is not None:
            check["run_backtest_live_pes_all"] = {k: ref_pes.get(k) for k in ("trades", "total_pnl", "pnl_per_trade")}
        try:
            check["daily_loss_target"] = summ["params"]["daily_loss_target"]
            check["baseline_daily_stop_events"] = {
                b: {"utc_day": len(A["live"][b]["daily_limits"]["utc_day"]["stop_events"]),
                    "since_start": len(A["live"][b]["daily_limits"]["since_start"]["stop_events"]),
                    "since_start_skipped": A["live"][b]["daily_limits"]["since_start"]["trades_skipped"]}
                for b in ("pes", "opt")}
        except (KeyError, TypeError):
            pass
        try:
            check["tier_b_systematic_live_position_of_sums"] = summ["tier_b"]["systematic"]["live"]["position_of_sums"]
        except (KeyError, TypeError):
            pass
    check["sweep_baseline_live_pes_all"] = pick(base["pes"]["all"])
    check["sweep_baseline_live_opt_all"] = pick(base["opt"]["all"])

    # ---- qualifying configs and ranking on train (pessimistic, total PnL) ----
    qual = [c for c in cfgs if c["pes"]["train"]["trades"] >= MIN_TRAIN_TRADES]
    qual.sort(key=lambda c: (-c["pes"]["train"]["total_pnl"], c["key"]))
    for i, c in enumerate(qual, 1):
        c["train_rank"] = i
    test_sorted = sorted(qual, key=lambda c: -c["pes"]["test"]["total_pnl"])
    for i, c in enumerate(test_sorted, 1):
        c["test_rank"] = i
    base_qual = base["pes"]["train"]["trades"] >= MIN_TRAIN_TRADES
    top = qual[:TOP_N]
    winner = qual[0] if qual else None

    def cfg_out(c: dict) -> dict:
        return {
            "params": short_params(c),
            "key": c["key"],
            "train_rank": c.get("train_rank"),
            "test_rank_among_qualifying": c.get("test_rank"),
            "train": pick(c["pes"]["train"]),
            "test": pick(c["pes"]["test"]),
            "train_pnl_per_trade_se": c["pes"]["train"]["pnl_per_trade_se"],
            "test_pnl_per_trade_se": c["pes"]["test"]["pnl_per_trade_se"],
            "train_exits": c["pes"]["train"]["exits"],
            "test_exits": c["pes"]["test"]["exits"],
            "optimistic_train": pick(c["opt"]["train"]),
            "optimistic_test": pick(c["opt"]["test"]),
        }

    # ---- one-at-a-time sensitivity from the baseline (judged on train, shown on test) ----
    oat_rows = []
    for name in PARAM_NAMES:
        for val in meta["grid"][name]:
            v = None if val in ("none", "hold") else val
            if v == BASELINE[name]:
                continue
            pd = dict(BASELINE)
            pd[name] = v
            if not (pd["entry_min"] < pd["entry_max"]):
                continue
            c = by_key.get(key_of(pd))
            if c is None:
                continue
            oat_rows.append({
                "param": PARAM_LABEL[name], "value": fmt_val(name, v), "params": short_params(c), "key": c["key"],
                "qualifies": c["pes"]["train"]["trades"] >= MIN_TRAIN_TRADES,
                "train": pick(c["pes"]["train"]), "test": pick(c["pes"]["test"]),
                "train_delta_vs_baseline": c["pes"]["train"]["total_pnl"] - base["pes"]["train"]["total_pnl"],
                "test_delta_vs_baseline": c["pes"]["test"]["total_pnl"] - base["pes"]["test"]["total_pnl"],
                "optimistic_train_total": c["opt"]["train"]["total_pnl"],
                "optimistic_test_total": c["opt"]["test"]["total_pnl"],
            })
    oat_q = [r for r in oat_rows if r["qualifies"]]
    oat_best = max(oat_q, key=lambda r: r["train"]["total_pnl"]) if oat_q else None
    oat_by_param = {}
    for name in PARAM_NAMES:
        rows = [r for r in oat_q if r["param"] == PARAM_LABEL[name]]
        if rows:
            b = max(rows, key=lambda r: r["train"]["total_pnl"])
            oat_by_param[PARAM_LABEL[name]] = {"best_value": b["value"], "train_total": b["train"]["total_pnl"],
                                               "test_total": b["test"]["total_pnl"],
                                               "train_delta": b["train_delta_vs_baseline"], "test_delta": b["test_delta_vs_baseline"]}

    # ---- multiple-comparison diagnostics ----
    K = len(qual)
    tr_tot = [c["pes"]["train"]["total_pnl"] for c in qual]
    te_tot = [c["pes"]["test"]["total_pnl"] for c in qual]
    tr_pt = [c["pes"]["train"]["pnl_per_trade"] for c in qual]
    te_pt = [c["pes"]["test"]["pnl_per_trade"] for c in qual]
    n_tr_pos = sum(1 for v in tr_tot if v > 0)
    n_te_pos = sum(1 for v in te_tot if v > 0)
    n_both_pos = sum(1 for a, b in zip(tr_tot, te_tot) if a > 0 and b > 0)
    base_te = base["pes"]["test"]["total_pnl"]
    base_tr = base["pes"]["train"]["total_pnl"]
    n_beat_base_train = sum(1 for v in tr_tot if v > base_tr)
    n_beat_base_test = sum(1 for v in te_tot if v > base_te)
    top_te = [c["pes"]["test"]["total_pnl"] for c in top]
    top_beat_base_test = sum(1 for v in top_te if v > base_te)
    top_pos_test = sum(1 for v in top_te if v > 0)
    dec = max(1, K // 10)
    top_decile_te_mean = (sum(te_tot[:dec]) / dec) if K else None
    bottom_decile_te_mean = (sum(te_tot[-dec:]) / dec) if K else None
    all_te_mean = (sum(te_tot) / K) if K else None
    mc = {
        "configs_in_grid": meta["grid_size"],
        "configs_qualifying_min_train_trades": K,
        "min_train_trades": MIN_TRAIN_TRADES,
        "spearman_train_vs_test_total_pnl": spearman(tr_tot, te_tot),
        "spearman_train_vs_test_pnl_per_trade": spearman(tr_pt, te_pt),
        "pearson_train_vs_test_total_pnl": pearson(tr_tot, te_tot),
        "configs_positive_train": n_tr_pos,
        "configs_positive_test": n_te_pos,
        "configs_positive_both": n_both_pos,
        "configs_beating_baseline_train": n_beat_base_train,
        "configs_beating_baseline_test": n_beat_base_test,
        "top10_beating_baseline_test": top_beat_base_test,
        "top10_positive_test": top_pos_test,
        "top10_mean_test_total": (sum(top_te) / len(top_te)) if top_te else None,
        "top_decile_by_train_mean_test_total": top_decile_te_mean,
        "bottom_decile_by_train_mean_test_total": bottom_decile_te_mean,
        "all_qualifying_mean_test_total": all_te_mean,
        "baseline_test_total": base_te,
        "baseline_train_total": base_tr,
        "baseline_train_rank": base.get("train_rank"),
        "baseline_test_rank": base.get("test_rank"),
    }
    if winner is not None:
        w = winner["pes"]["train"]
        se_total = w["pnl_per_trade_se"] * w["trades"]
        mc["winner"] = {
            "params": short_params(winner),
            "train_total": w["total_pnl"],
            "train_total_se": se_total,
            "train_total_in_se_above_zero": (w["total_pnl"] / se_total) if se_total > 0 else None,
            "train_total_in_se_above_baseline": ((w["total_pnl"] - base_tr) / se_total) if se_total > 0 else None,
            "expected_max_of_K_noise_in_se_sqrt_2lnK": math.sqrt(2 * math.log(K)) if K > 1 else None,
            "test_total": winner["pes"]["test"]["total_pnl"],
            "test_total_se": winner["pes"]["test"]["pnl_per_trade_se"] * winner["pes"]["test"]["trades"],
            "test_rank_among_qualifying": winner.get("test_rank"),
        }

    # ---- where do the top configs sit structurally (exit / tp / delay counts) ----
    def counts(rows: List[dict], name: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in rows:
            k = fmt_val(name, c[name])
            out[k] = out.get(k, 0) + 1
        return out

    top_structure = {PARAM_LABEL[n]: counts(top, n) for n in PARAM_NAMES}
    top_decile_structure = {PARAM_LABEL[n]: counts(qual[:dec], n) for n in PARAM_NAMES}

    # per-parameter marginal means on train and test (qualifying configs)
    marginals = {}
    for name in PARAM_NAMES:
        groups: Dict[str, List[dict]] = {}
        for c in qual:
            groups.setdefault(fmt_val(name, c[name]), []).append(c)
        marginals[PARAM_LABEL[name]] = {
            k: {"n": len(v),
                "mean_train_total": sum(x["pes"]["train"]["total_pnl"] for x in v) / len(v),
                "mean_test_total": sum(x["pes"]["test"]["total_pnl"] for x in v) / len(v),
                "mean_train_pnl_per_trade": sum(x["pes"]["train"]["pnl_per_trade"] for x in v) / len(v),
                "mean_test_pnl_per_trade": sum(x["pes"]["test"]["pnl_per_trade"] for x in v) / len(v)}
            for k, v in sorted(groups.items())
        }

    train_period = "%s to %s (markets closing in the first 20 days, %d markets)" % (
        meta["data_first_close"], meta["train_cut_iso"], meta["train_markets"])
    test_period = "%s to %s (markets closing in the last 10 days, %d markets)" % (
        meta["train_cut_iso"], meta["data_last_close"], meta["test_markets"])

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_grid": GRID,
        "sim_md5": meta.get("sim_md5"),
        "model": "live (Kalshi taker fees, centi-cent rounding, direct member); settlement fee-free",
        "primary_bound": "pessimistic (Tier A, 1-minute candles, all markets)",
        "tier": meta["tier"],
        "train_period": train_period,
        "test_period": test_period,
        "train_markets": meta["train_markets"],
        "test_markets": meta["test_markets"],
        "grid": meta["grid"],
        "grid_size": meta["grid_size"],
        "ranking_rule": "train total PnL (live, pessimistic), configs with >= %d train trades" % MIN_TRAIN_TRADES,
        "daily_limits_applied": meta.get("daily_limits_applied", False),
        "runtime_s": meta.get("elapsed_s"),
        "cross_check": check,
        "baseline": {
            "params": short_params(base), "key": base["key"], "qualifies": base_qual,
            "train_rank": base.get("train_rank"), "test_rank_among_qualifying": base.get("test_rank"),
            "train": pick(base["pes"]["train"]), "test": pick(base["pes"]["test"]),
            "train_pnl_per_trade_se": base["pes"]["train"]["pnl_per_trade_se"],
            "test_pnl_per_trade_se": base["pes"]["test"]["pnl_per_trade_se"],
            "train_exits": base["pes"]["train"]["exits"], "test_exits": base["pes"]["test"]["exits"],
            "optimistic_train": pick(base["opt"]["train"]), "optimistic_test": pick(base["opt"]["test"]),
        },
        "top_configs": [cfg_out(c) for c in top],
        "winner_beats_baseline_out_of_sample": {
            "winner_params": short_params(winner) if winner else None,
            "winner_test_total": winner["pes"]["test"]["total_pnl"] if winner else None,
            "baseline_test_total": base_te,
            "beats_baseline_on_test_total": (winner["pes"]["test"]["total_pnl"] > base_te) if winner else None,
            "winner_test_pnl_per_trade": winner["pes"]["test"]["pnl_per_trade"] if winner else None,
            "baseline_test_pnl_per_trade": base["pes"]["test"]["pnl_per_trade"],
            "winner_test_positive": (winner["pes"]["test"]["total_pnl"] > 0) if winner else None,
            "winner_optimistic_test_total": winner["opt"]["test"]["total_pnl"] if winner else None,
            "baseline_optimistic_test_total": base["opt"]["test"]["total_pnl"],
        },
        "one_at_a_time": {
            "rows": oat_rows,
            "best_single_change_on_train": oat_best,
            "best_per_parameter_on_train": oat_by_param,
        },
        "multiple_comparisons": mc,
        "top10_structure": top_structure,
        "top_decile_structure": top_decile_structure,
        "marginal_means_by_parameter_value": marginals,
        "all_qualifying_ranked": [
            {"train_rank": c["train_rank"], "test_rank": c["test_rank"], "params": short_params(c), "key": c["key"],
             "train_trades": c["pes"]["train"]["trades"], "train_total": c["pes"]["train"]["total_pnl"],
             "train_pnl_per_trade": c["pes"]["train"]["pnl_per_trade"],
             "test_trades": c["pes"]["test"]["trades"], "test_total": c["pes"]["test"]["total_pnl"],
             "test_pnl_per_trade": c["pes"]["test"]["pnl_per_trade"],
             "opt_train_total": c["opt"]["train"]["total_pnl"], "opt_test_total": c["opt"]["test"]["total_pnl"]}
            for c in qual
        ],
        "not_qualifying": [{"params": short_params(c), "train_trades": c["pes"]["train"]["trades"]}
                           for c in cfgs if c["pes"]["train"]["trades"] < MIN_TRAIN_TRADES],
    }
    with open(os.path.join(RESULTS, "sweep.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    # ---- CSV of every config ----
    with open(os.path.join(RESULTS, "sweep_grid.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        hdr = ["key", "entry_min", "entry_max", "exit", "tp", "delay", "train_rank", "test_rank"]
        for b in ("pes", "opt"):
            for split in ("train", "test"):
                for k in STAT_KEYS + ("pnl_per_trade_se",):
                    hdr.append("%s_%s_%s" % (b, split, k))
        w.writerow(hdr)
        for c in sorted(cfgs, key=lambda c: (c.get("train_rank") or 10 ** 6, c["key"])):
            row = [c["key"], c["entry_min"], c["entry_max"], "none" if c["exit"] is None else c["exit"],
                   "hold" if c["tp"] is None else c["tp"], c["delay"], c.get("train_rank", ""), c.get("test_rank", "")]
            for b in ("pes", "opt"):
                for split in ("train", "test"):
                    for k in STAT_KEYS + ("pnl_per_trade_se",):
                        row.append(c[b][split][k])
            w.writerow(row)

    write_md(out, base, top, winner, oat_rows, oat_best, oat_by_param, mc, marginals, top_structure, qual, meta, check)
    return 0


def daily_stop_sentence(check: dict) -> str:
    """The sweep applies no daily limits; quote what they do at the baseline settings, from run_backtest's summary."""
    ev = check.get("baseline_daily_stop_events")
    if not ev:
        return "No daily limits are applied."
    return ("No daily limits are applied. For comparison, `results/summary.json` (baseline settings) shows the daily loss stop (%g) "
            "firing %d time(s) with a per-UTC-day reset and %d time(s) counted since the start for the live pessimistic bound "
            "(%d trades skipped after that stop), and %d and %d time(s) for the live optimistic bound." % (
                check["daily_loss_target"], ev["pes"]["utc_day"], ev["pes"]["since_start"], ev["pes"]["since_start_skipped"],
                ev["opt"]["utc_day"], ev["opt"]["since_start"]))


def calibration_sentence(check: dict) -> str:
    ps = check.get("tier_b_systematic_live_position_of_sums")
    if ps is None:
        return "No Tier B calibration is applied to any config here."
    return ("For the baseline, the Tier B per-second replay of the evenly spaced sample put the summed live result %.2f of the way from the "
            "summed pessimistic to the summed optimistic result; that calibration was measured only on the baseline and is not applied "
            "to any other config here." % ps)


def write_md(out, base, top, winner, oat_rows, oat_best, oat_by_param, mc, marginals, top_structure, qual, meta, check):
    L: List[str] = []
    a = L.append
    a("# Parameter sweep with a chronological train/test holdout (Tier A, live model with fees)")
    a("")
    a("Generated %s. Simulator: `backtest/sim.py` (md5 %s), imported unchanged and driven through `sim.tier_a_trade` "
      "exactly as `run_backtest.run_tier_a` does. Runner: `backtest/sweep/sweep_run.py`; this report: `backtest/sweep/sweep_report.py`. "
      "Raw per-config output: `results/sweep_grid.json`, flat table: `results/sweep_grid.csv`, machine summary: `results/sweep.json`."
      % (out["generated_at"], out["sim_md5"]))
    a("")
    a("## Setup")
    a("")
    a("- Data: %d settled KXBTC15M markets with full 1-minute candles, closes %s to %s." % (
        meta["markets_usable"], meta["data_first_close"], meta["data_last_close"]))
    a("- Chronological train/test holdout by market close time (one split, not rolling windows). Train: %s. Test: %s." % (
        out["train_period"], out["test_period"]))
    a("- Model: LIVE with Kalshi taker fees (July 7, 2026 schedule, centi-cent rounding, direct member), settlement fee-free. "
      "Primary bound: PESSIMISTIC Tier A (worst feasible intrabar path). The optimistic bound is shown for the finalists only.")
    g = meta["grid"]
    partial = meta.get("full_grid_size") not in (None, out["grid_size"])
    a("- Grid: entry min %s x entry max %s x stop %s x take-profit %s x window %s minutes, keeping only entry min below entry max: "
      "%d configs%s. 'none' is exit_below 0.0 (the stop `ask < 0` never fires); 'hold' is take_profit_pts 1.0 (the target is above 1, "
      "so take-profit is off). One stop confirmation, 1 contract. %s" % (
          fmt_grid_values(g["entry_min"]), fmt_grid_values(g["entry_max"]), fmt_grid_values(g["exit"]), fmt_grid_values(g["tp"]),
          fmt_grid_values(g["delay"]), out["grid_size"],
          " (a partial run, the full grid has %d)" % meta["full_grid_size"] if partial else "", daily_stop_sentence(check)))
    a("- Ranking: train total PnL (live, pessimistic), only configs with at least %d train trades. Ties broken by key." % MIN_TRAIN_TRADES)
    a("- Runtime: %s s%s." % (meta.get("elapsed_s"), " on %d worker processes" % meta["workers"] if meta.get("workers") else ""))
    a("- Not modelled (same caveats as `results/report.md`): entry slippage of the buy limit 0.02 above the ask, polling gaps under rate "
      "limits, resting or cancelled entries, price-feed outages, the 11-25 s market-list lag after close, trading pauses. All of these are adverse or neutral.")
    a("")
    a("### Cross-check")
    a("")
    bp = check["sweep_baseline_live_pes_all"]
    bo = check["sweep_baseline_live_opt_all"]
    a("Baseline over the whole 30 days from this sweep: live pessimistic %d trades, total %s, per trade %s; live optimistic %d trades, total %s, per trade %s. "
      % (bp["trades"], money(bp["total_pnl"]), pt(bp["pnl_per_trade"]), bo["trades"], money(bo["total_pnl"]), pt(bo["pnl_per_trade"])))
    if check.get("run_backtest_live_pes_all"):
        r = check["run_backtest_live_pes_all"]
        a("`results/summary.json` from `run_backtest.py`, live pessimistic: %s trades, total %s, per trade %s. %s" % (
            r["trades"], money(r["total_pnl"]), pt(r["pnl_per_trade"]),
            "Match." if (r["trades"] == bp["trades"] and abs(r["total_pnl"] - bp["total_pnl"]) < 0.005) else "MISMATCH, investigate."))
    a("")
    a("## Baseline (default settings: %s)" % short_params(base))
    a("")
    a("Qualifies: %s. Train rank %s of %d qualifying configs; test rank %s." % (
        "yes" if out["baseline"]["qualifies"] else "no", base.get("train_rank"), len(qual), base.get("test_rank")))
    a("")
    a("| bound | split | %s |" % STAT_HDR)
    a("|---|---|" + "---|" * 7)
    a("| pessimistic | train | %s |" % stat_row(base["pes"]["train"]))
    a("| pessimistic | test | %s |" % stat_row(base["pes"]["test"]))
    a("| optimistic | train | %s |" % stat_row(base["opt"]["train"]))
    a("| optimistic | test | %s |" % stat_row(base["opt"]["test"]))
    a("")
    a("Exit mix (pessimistic): train %s; test %s. Standard error of PnL per trade: train %.4f, test %.4f." % (
        json.dumps(base["pes"]["train"]["exits"]), json.dumps(base["pes"]["test"]["exits"]),
        base["pes"]["train"]["pnl_per_trade_se"], base["pes"]["test"]["pnl_per_trade_se"]))
    a("")
    a("## Top %d configs on train (pessimistic total PnL), with their test results" % TOP_N)
    a("")
    a("%d of %d configs qualify (>= %d train trades). Pessimistic bound in both halves; optimistic totals in the last two columns." % (
        len(qual), out["grid_size"], MIN_TRAIN_TRADES))
    a("")
    a("| rank | params | train trades | train win | train PnL/tr | train total | train DD | test trades | test win | test PnL/tr | test total | test DD | test rank | opt train | opt test |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in top:
        tr = c["pes"]["train"]
        te = c["pes"]["test"]
        a("| %d | %s | %d | %s | %s | %s | %.2f | %d | %s | %s | %s | %.2f | %s | %s | %s |" % (
            c["train_rank"], short_params(c), tr["trades"], pct(tr["win_rate"]), pt(tr["pnl_per_trade"]), money(tr["total_pnl"]), tr["max_drawdown"],
            te["trades"], pct(te["win_rate"]), pt(te["pnl_per_trade"]), money(te["total_pnl"]), te["max_drawdown"], c.get("test_rank"),
            money(c["opt"]["train"]["total_pnl"]), money(c["opt"]["test"]["total_pnl"])))
    a("| base | %s | %d | %s | %s | %s | %.2f | %d | %s | %s | %s | %.2f | %s | %s | %s |" % (
        short_params(base), base["pes"]["train"]["trades"], pct(base["pes"]["train"]["win_rate"]), pt(base["pes"]["train"]["pnl_per_trade"]),
        money(base["pes"]["train"]["total_pnl"]), base["pes"]["train"]["max_drawdown"], base["pes"]["test"]["trades"], pct(base["pes"]["test"]["win_rate"]),
        pt(base["pes"]["test"]["pnl_per_trade"]), money(base["pes"]["test"]["total_pnl"]), base["pes"]["test"]["max_drawdown"], base.get("test_rank"),
        money(base["opt"]["train"]["total_pnl"]), money(base["opt"]["test"]["total_pnl"])))
    a("")
    a("Full stats for each finalist (pessimistic):")
    a("")
    a("| rank | params | split | %s | exits |" % STAT_HDR)
    a("|---|---|---|" + "---|" * 8)
    for c in top:
        for split in ("train", "test"):
            a("| %d | %s | %s | %s | %s |" % (c["train_rank"], short_params(c), split, stat_row(c["pes"][split]), json.dumps(c["pes"][split]["exits"])))
    a("")
    a("Top-10 parameter mix: %s." % "; ".join("%s %s" % (k, json.dumps(v)) for k, v in top_structure.items()))
    a("")
    a("## Does the train winner beat the baseline out of sample?")
    a("")
    wb = out["winner_beats_baseline_out_of_sample"]
    if winner is not None:
        a("Train winner: %s. Test (pessimistic): total %s, per trade %s over %d trades. Baseline test: total %s, per trade %s over %d trades. "
          "Winner beats baseline on test total: **%s**. Winner positive on test: **%s**. Optimistic test totals: winner %s, baseline %s." % (
              wb["winner_params"], money(wb["winner_test_total"]), pt(wb["winner_test_pnl_per_trade"]), winner["pes"]["test"]["trades"],
              money(wb["baseline_test_total"]), pt(wb["baseline_test_pnl_per_trade"]), base["pes"]["test"]["trades"],
              "yes" if wb["beats_baseline_on_test_total"] else "no", "yes" if wb["winner_test_positive"] else "no",
              money(wb["winner_optimistic_test_total"]), money(wb["baseline_optimistic_test_total"])))
        w = mc["winner"]
        a("")
        a("Winner's train total %s has a standard error of %.2f (per-trade SE x trades): %.1f SE above zero and %.1f SE above the baseline's train total. "
          "Its test total %s has SE %.2f." % (money(w["train_total"]), w["train_total_se"],
                                             w["train_total_in_se_above_zero"] or 0.0, w["train_total_in_se_above_baseline"] or 0.0,
                                             money(w["test_total"]), w["test_total_se"]))
    a("")
    a("## One-at-a-time sensitivity from the baseline (judged on train, shown on test)")
    a("")
    a("Each row changes exactly one parameter of the baseline. Deltas are pessimistic total PnL minus the baseline's (train %s, test %s)." % (
        money(base["pes"]["train"]["total_pnl"]), money(base["pes"]["test"]["total_pnl"])))
    a("")
    a("| parameter | value | qualifies | train trades | train total | train delta | train PnL/tr | test trades | test total | test delta | test PnL/tr | opt train | opt test |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in oat_rows:
        a("| %s | %s | %s | %d | %s | %s | %s | %d | %s | %s | %s | %s | %s |" % (
            r["param"], r["value"], "yes" if r["qualifies"] else "no", r["train"]["trades"], money(r["train"]["total_pnl"]),
            money(r["train_delta_vs_baseline"]), pt(r["train"]["pnl_per_trade"]), r["test"]["trades"], money(r["test"]["total_pnl"]),
            money(r["test_delta_vs_baseline"]), pt(r["test"]["pnl_per_trade"]), money(r["optimistic_train_total"]), money(r["optimistic_test_total"])))
    a("")
    if oat_best is not None:
        a("Best single change on train: **%s = %s** (%s). Train total %s (delta %s vs baseline). On test: total %s (delta %s), per trade %s, %d trades." % (
            oat_best["param"], oat_best["value"], oat_best["params"], money(oat_best["train"]["total_pnl"]), money(oat_best["train_delta_vs_baseline"]),
            money(oat_best["test"]["total_pnl"]), money(oat_best["test_delta_vs_baseline"]), pt(oat_best["test"]["pnl_per_trade"]), oat_best["test"]["trades"]))
        a("")
        a("Best value per parameter on train, with its test delta: " + "; ".join(
            "%s -> %s (train %s, test %s)" % (k, v["best_value"], money(v["train_delta"]), money(v["test_delta"])) for k, v in oat_by_param.items()) + ".")
    a("")
    a("## Marginal means by parameter value (qualifying configs, pessimistic)")
    a("")
    a("Mean total PnL over every qualifying config that uses the value. These average over the other four parameters, so they show direction, not a recommended setting.")
    a("")
    a("| parameter | value | configs | mean train total | mean test total | mean train PnL/tr | mean test PnL/tr |")
    a("|---|---|---|---|---|---|---|")
    for pname, vals in marginals.items():
        for v, s in vals.items():
            a("| %s | %s | %d | %s | %s | %s | %s |" % (pname, v, s["n"], money(s["mean_train_total"]), money(s["mean_test_total"]),
                                                       pt(s["mean_train_pnl_per_trade"]), pt(s["mean_test_pnl_per_trade"])))
    a("")
    a("## Multiple-comparison risk")
    a("")
    a("- %d configs were evaluated and %d qualify. Picking the best of %d noisy train results biases the winner's train PnL upward (winner's curse). "
      "If the configs were independent zero-mean noise, the best of K would sit about sqrt(2 ln K) = %s standard errors above zero by luck alone. "
      "The configs are heavily correlated (they share entries and markets), so the effective K is smaller, but the direction of the bias is the same." % (
          mc["configs_in_grid"], mc["configs_qualifying_min_train_trades"], mc["configs_qualifying_min_train_trades"],
          num1(mc["winner"]["expected_max_of_K_noise_in_se_sqrt_2lnK"] if mc.get("winner") else None)))
    a("- Spearman rank correlation between train and test total PnL across qualifying configs: %s (per-trade PnL: %s). Pearson on totals: %s. "
      "A value near 1 means the train ranking carries over; near 0 means it is mostly noise." % (
          "n/a" if mc["spearman_train_vs_test_total_pnl"] is None else "%.3f" % mc["spearman_train_vs_test_total_pnl"],
          "n/a" if mc["spearman_train_vs_test_pnl_per_trade"] is None else "%.3f" % mc["spearman_train_vs_test_pnl_per_trade"],
          "n/a" if mc["pearson_train_vs_test_total_pnl"] is None else "%.3f" % mc["pearson_train_vs_test_total_pnl"]))
    a("- Configs with positive pessimistic total: train %d, test %d, both %d of %d." % (
        mc["configs_positive_train"], mc["configs_positive_test"], mc["configs_positive_both"], mc["configs_qualifying_min_train_trades"]))
    a("- Configs beating the baseline: on train %d, on test %d of %d. Of the top 10 on train, %d beat the baseline on test and %d are positive on test." % (
        mc["configs_beating_baseline_train"], mc["configs_beating_baseline_test"], mc["configs_qualifying_min_train_trades"],
        mc["top10_beating_baseline_test"], mc["top10_positive_test"]))
    a("- Mean test total: top 10 by train %s; top decile by train %s; bottom decile by train %s; all qualifying %s; baseline %s." % (
        money(mc["top10_mean_test_total"]), money(mc["top_decile_by_train_mean_test_total"]), money(mc["bottom_decile_by_train_mean_test_total"]),
        money(mc["all_qualifying_mean_test_total"]), money(mc["baseline_test_total"])))
    if mc.get("winner"):
        a("- Train winner's test rank among qualifying configs: %s of %d." % (mc["winner"]["test_rank_among_qualifying"], mc["configs_qualifying_min_train_trades"]))
    a("- The test half is 10 days (%d markets). A 10-day out-of-sample result is one draw; it cannot certify a config, it can only fail to contradict it." % meta["test_markets"])
    a("- The pessimistic bound is a bound, not an estimate. It punishes every intrabar ambiguity, so configs with fewer intrabar decisions (hold to settlement, no stop) "
      "are treated less harshly than take-profit/stop configs. That is a structural tilt of the ranking, separate from sampling noise. "
      + calibration_sentence(check))
    a("")
    a("## Conclusions")
    a("")
    for s in conclusions(out, base, top, winner, oat_best, mc, marginals, qual):
        a("- " + s)
    a("")
    a("## Files")
    a("")
    a("- `results/sweep.json`: everything above in machine form, plus all %d qualifying configs ranked and the %d non-qualifying ones." % (
        len(qual), len(out["not_qualifying"])))
    a("- `results/sweep_grid.csv`: one row per config, pessimistic and optimistic, train and test.")
    a("- `results/sweep_grid.json`: raw per-config output of `sweep/sweep_run.py`.")
    with open(os.path.join(RESULTS, "sweep.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def conclusions(out, base, top, winner, oat_best, mc, marginals, qual) -> List[str]:
    c: List[str] = []
    bt = base["pes"]
    c.append("Baseline (%s), live pessimistic: train %s over %d trades (%s per trade), test %s over %d trades (%s per trade). Optimistic: train %s, test %s." % (
        short_params(base), money(bt["train"]["total_pnl"]), bt["train"]["trades"], pt(bt["train"]["pnl_per_trade"]),
        money(bt["test"]["total_pnl"]), bt["test"]["trades"], pt(bt["test"]["pnl_per_trade"]),
        money(base["opt"]["train"]["total_pnl"]), money(base["opt"]["test"]["total_pnl"])))
    if winner is not None:
        wt = winner["pes"]
        c.append("Train winner %s: train %s (%d trades), test %s (%d trades, %s per trade). It %s the baseline out of sample and is %s on test. "
                 "Optimistic test total %s." % (
                     short_params(winner), money(wt["train"]["total_pnl"]), wt["train"]["trades"], money(wt["test"]["total_pnl"]), wt["test"]["trades"],
                     pt(wt["test"]["pnl_per_trade"]), "beats" if wt["test"]["total_pnl"] > bt["test"]["total_pnl"] else "does not beat",
                     "positive" if wt["test"]["total_pnl"] > 0 else "negative", money(winner["opt"]["test"]["total_pnl"])))
    c.append("Of %d qualifying configs, %d are positive on train and %d on test under the pessimistic bound; %d are positive in both halves." % (
        mc["configs_qualifying_min_train_trades"], mc["configs_positive_train"], mc["configs_positive_test"], mc["configs_positive_both"]))
    c.append("Train-to-test rank correlation (Spearman) across qualifying configs is %s on total PnL; %d of the top 10 by train beat the baseline on test." % (
        "n/a" if mc["spearman_train_vs_test_total_pnl"] is None else "%.3f" % mc["spearman_train_vs_test_total_pnl"], mc["top10_beating_baseline_test"]))
    if oat_best is not None:
        c.append("Best single-parameter change from the baseline on train: %s = %s (train delta %s); on test its delta is %s." % (
            oat_best["param"], oat_best["value"], money(oat_best["train_delta_vs_baseline"]), money(oat_best["test_delta_vs_baseline"])))
    # direction from marginals: which values were best on train and did they hold on test
    for pname, vals in marginals.items():
        best_tr = max(vals.items(), key=lambda kv: kv[1]["mean_train_total"])
        best_te = max(vals.items(), key=lambda kv: kv[1]["mean_test_total"])
        c.append("%s: best marginal value on train %s (mean %s), on test %s (mean %s)%s." % (
            pname, best_tr[0], money(best_tr[1]["mean_train_total"]), best_te[0], money(best_te[1]["mean_test_total"]),
            " (agree)" if best_tr[0] == best_te[0] else " (disagree)"))
    c.append("With %d configs the top train result is biased upward by selection; the winner's train total is %s SE above zero, against a sqrt(2 ln K) luck line of %s SE, "
             "and the configs are correlated so the true effective K is unknown. Treat the top list as candidates for a per-second (Tier B) replay and a longer out-of-sample window, not as validated settings." % (
                 mc["configs_qualifying_min_train_trades"], num1(mc["winner"]["train_total_in_se_above_zero"] if mc.get("winner") else None),
                 num1(mc["winner"]["expected_max_of_K_noise_in_se_sqrt_2lnK"] if mc.get("winner") else None)))
    return c


if __name__ == "__main__":
    sys.exit(main())
