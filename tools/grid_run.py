"""Run a named set of settings through run_backtest.py and tabulate the results.

Each row: the Tier B per-second replay of the evenly spaced sample (the closer estimate)
next to the Tier A envelope (live model, optimistic and pessimistic bounds over all
markets). Each configuration writes to backtest/results_grid/<name>/ and the summary table
goes to backtest/results_grid/grid.md.

usage: python grid_run.py [name ...]    (about 1.5 minutes per config on its own; no names runs them all)
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BT = ROOT / "backtest"
OUT = BT / "results_grid"

# name: (entry_min, entry_max, exit, tp, time_delay)
GRID = {
    "baseline_80-93_s40_tp10_w8": (0.80, 0.93, 0.40, 0.10, 8),
    "85-89_s40_tp10_w8": (0.85, 0.89, 0.40, 0.10, 8),
    "85-89_s30_tp10_w8": (0.85, 0.89, 0.30, 0.10, 8),
    "85-89_s40_hold_w8": (0.85, 0.89, 0.40, 1.00, 8),
    "85-89_nostop_hold_w8": (0.85, 0.89, 0.01, 1.00, 8),
    "85-89_s40_tp05_w8": (0.85, 0.89, 0.40, 0.05, 8),
    "85-89_s40_tp10_w5": (0.85, 0.89, 0.40, 0.10, 5),
    "85-89_s40_tp10_w3": (0.85, 0.89, 0.40, 0.10, 3),
    "80-89_s40_tp10_w8": (0.80, 0.89, 0.40, 0.10, 8),
    "80-89_nostop_hold_w8": (0.80, 0.89, 0.01, 1.00, 8),
    "80-89_s40_hold_w5": (0.80, 0.89, 0.40, 1.00, 5),
    "70-80_s40_tp10_w8": (0.70, 0.80, 0.40, 0.10, 8),
    "70-80_s40_hold_w8": (0.70, 0.80, 0.40, 1.00, 8),
    "70-80_s30_tp15_w8": (0.70, 0.80, 0.30, 0.15, 8),
    "60-75_s30_hold_w8": (0.60, 0.75, 0.30, 1.00, 8),
    "80-93_s30_tp10_w8": (0.80, 0.93, 0.30, 0.10, 8),
    "80-93_s50_tp10_w8": (0.80, 0.93, 0.50, 0.10, 8),
    "80-93_nostop_tp10_w8": (0.80, 0.93, 0.01, 0.10, 8),
    # Narrower windows with a floor: the sixth value is the number of minutes before the close
    # in which no new entries are taken.
    "85-89_s40_tp10_w7_ml4": (0.85, 0.89, 0.40, 0.10, 7, 4),
    "85-89_s40_hold_w7_ml4": (0.85, 0.89, 0.40, 1.00, 7, 4),
    "85-89_s50_tp10_w7_ml4": (0.85, 0.89, 0.50, 0.10, 7, 4),
    "85-89_s30_tp10_w7_ml4": (0.85, 0.89, 0.30, 0.10, 7, 4),
    "85-89_s40_tp05_w7_ml4": (0.85, 0.89, 0.40, 0.05, 7, 4),
    "80-93_s40_tp10_w7_ml4": (0.80, 0.93, 0.40, 0.10, 7, 4),
    "80-89_s40_tp10_w7_ml4": (0.80, 0.89, 0.40, 0.10, 7, 4),
    "85-89_s40_tp10_w6_ml4": (0.85, 0.89, 0.40, 0.10, 6, 4),
    "85-89_s40_tp10_w8_ml3": (0.85, 0.89, 0.40, 0.10, 8, 3),
    "85-89_s40_tp10_w5_ml2": (0.85, 0.89, 0.40, 0.10, 5, 2),
    "10-25_s01_hold_w8": (0.10, 0.25, 0.01, 1.00, 8, 0),
    "30-50_s05_hold_w2": (0.30, 0.50, 0.05, 1.00, 2, 0),
    "30-45_s15_tp20_w8": (0.30, 0.45, 0.15, 0.20, 8, 0),
    "94-98_s60_hold_w8_ml0.25": (0.94, 0.98, 0.60, 1.00, 8, 0.25),
    # second round around the 0.85-0.89 range with a 0.50 stop
    "85-89_s50_tp10_w6_ml4": (0.85, 0.89, 0.50, 0.10, 6, 4),
    "85-89_s50_tp10_w8_ml5": (0.85, 0.89, 0.50, 0.10, 8, 5),
    "86-89_s50_tp10_w7_ml4": (0.86, 0.89, 0.50, 0.10, 7, 4),
    "84-90_s50_tp10_w7_ml4": (0.84, 0.90, 0.50, 0.10, 7, 4),
    "85-89_s50_tp08_w7_ml4": (0.85, 0.89, 0.50, 0.08, 7, 4),
    "85-89_s50_tp12_w7_ml4": (0.85, 0.89, 0.50, 0.12, 7, 4),
}


def walk(obj, path=""):
    """Yield (path, dict) for every dict that carries pnl_per_trade."""
    if isinstance(obj, dict):
        if "pnl_per_trade" in obj:
            yield path, obj
        for k, v in obj.items():
            yield from walk(v, f"{path}/{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def run_one(name: str):
    lo, hi, ex, tp, w = GRID[name][:5]
    ml = GRID[name][5] if len(GRID[name]) > 5 else 0
    out = OUT / name
    cmd = [sys.executable, str(BT / "run_backtest.py"), "--entry-min", str(lo), "--entry-max", str(hi),
           "--exit", str(ex), "--tp", str(tp), "--time-delay", str(w), "--min-left", str(ml), "--out-dir", str(out)]
    r = subprocess.run(cmd, cwd=BT, capture_output=True, text=True, timeout=1800)
    summary = out / "summary.json"
    if r.returncode != 0 or not summary.exists():
        return name, "FAILED: " + (r.stderr or r.stdout)[-300:]
    data = json.loads(summary.read_text(encoding="utf-8"))
    cells = {path: (d.get("trades"), d.get("win_rate"), d.get("pnl_per_trade"), d.get("total_pnl")) for path, d in walk(data)}
    print(f"done {name}", flush=True)
    return name, cells


def main() -> None:
    from concurrent.futures import ThreadPoolExecutor
    OUT.mkdir(exist_ok=True)
    names = [a for a in sys.argv[1:] if a in GRID] or list(GRID)
    with ThreadPoolExecutor(max_workers=6) as pool:   # each config is its own python process
        rows = list(pool.map(run_one, names))

    # Headline = Tier B systematic live replay (per-second, evenly spaced markets);
    # Tier A live bounds are shown as the wide envelope across all markets.
    lines = ["| config | replay live/trade | replay trades | replay win | Tier A live low..high |", "|---|---|---|---|---|"]
    for name, cells in rows:
        if isinstance(cells, str):
            lines.append(f"| {name} | {cells} | | | |")
            continue
        truth = cells.get("tier_b/systematic/live_truth_stats")
        bounds = [cells[p] for p in ("tier_a/live/opt/all", "tier_a/live/pes/all") if p in cells]
        env = ""
        if bounds:
            ppts = sorted(b[2] for b in bounds if isinstance(b[2], (int, float)))
            env = f"{ppts[0]:+.4f} .. {ppts[-1]:+.4f}"
        if truth:
            t, wr, ppt, tot = truth
            lines.append(f"| {name} | {ppt:+.4f} | {t} | {wr:.1%} | {env} |")
        else:
            lines.append(f"| {name} | n/a | | | {env} |")
    (OUT / "grid.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
