"""Print the grid table from whatever configs have finished (grid_run.py may still be running)."""
import json
from pathlib import Path

GRID = Path(__file__).resolve().parents[1] / "backtest" / "results_grid"


def pick(d, *keys):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def main() -> None:
    rows = []
    for summary in sorted(GRID.glob("*/summary.json")):
        s = json.loads(summary.read_text(encoding="utf-8"))
        truth = pick(s, "tier_b", "systematic", "live_truth_stats") or {}
        paper = pick(s, "tier_b", "systematic", "paper_truth_stats") or {}
        opt = pick(s, "tier_a", "live", "opt", "all") or {}
        pes = pick(s, "tier_a", "live", "pes", "all") or {}
        se = truth.get("pnl_per_trade_se") or truth.get("se") or truth.get("std_err")
        rows.append((truth.get("pnl_per_trade", float("nan")), summary.parent.name, truth, paper, opt, pes, se))
    rows.sort(key=lambda r: -r[0])
    print(f"{'config':<28} {'replay live/trade':>17} {'SE':>7} {'trades':>6} {'win':>6} {'total':>7} | {'TierA live pes..opt':>20}")
    for ppt, name, truth, paper, opt, pes, se in rows:
        se_s = f"{se:.4f}" if isinstance(se, (int, float)) else "  n/a"
        print(f"{name:<28} {ppt:>+17.4f} {se_s:>7} {truth.get('trades', 0):>6} {truth.get('win_rate', 0):>6.1%} {truth.get('total_pnl', 0):>+7.2f} | "
              f"{pes.get('pnl_per_trade', float('nan')):+.4f}..{opt.get('pnl_per_trade', float('nan')):+.4f}")


if __name__ == "__main__":
    main()
