# Kalshi 15-Minute BTC Market Backtester

**Timeline:** September 2026 to October 2026

A Python backtester for Kalshi's KXBTC15M series, the 15-minute "will Bitcoin go up" markets. It bounds the result of a fixed set of entry, stop-loss and take-profit rules on all 2,848 settled markets in a 30-day window of public market data using one-minute candles, and replays 345 of those markets about once per second from trade prints. It charges Kalshi's taker fees and reports whether the rules made money. The simulator is a pure module with no file or network access, a separate fetcher caches the public data, and a sweep runner tries other settings. The standard library is enough for everything in this repository.

The rules being simulated (entry price range, stop-loss level, take-profit distance, entry window) come from the Kalshi Trading Bot v5 shared by the YouTube creator processoverprofit for the AutoHotkey-based Nightshark runtime. That bot's scripts are not part of this repository. Only the Python simulator, its tests, the sweep tooling and the grid tools are, and none of them needs the bot or Nightshark.

## Results at the tested settings

The tested settings are entry when the side's ask is between 0.80 and 0.93, stop-loss when the ask falls below 0.40, take-profit at 0.10 above the fill, and an entry window of the last 8 minutes of each market. The full write-up is in [backtest/results/report.md](backtest/results/report.md).

- Per-second replay (Tier B) of 285 markets spread evenly over the 30 days: the live model made -0.0247 per trade after fees (standard error 0.0139) over 273 trades, for a total of -6.75 per contract. The win rate was 82.4% against a break-even win rate of 86.6%, with an average win of +0.0805 and an average loss of -0.5181.
- The paper model on the same markets, which has no fees and exits at the ask, made -0.0019 per trade. Paper overstates live by 0.0228 per trade: fees account for 0.0162 and selling at the bid instead of the ask for 0.0066.
- One-minute candles (Tier A) over all 2,848 markets (2,748 trades) cannot pin the answer down. The live model lands between -0.1027 and +0.0403 per trade (total -282.16 to +110.84), because the optimistic bound sells every take-profit at the bar high and the pessimistic bound sells every stop at the bar low. These are envelopes, not estimates. In the pessimistic bound, 69 stops (total -60.75) are priced at 0.001 although the bar's bid side was empty at some point, so they could not have filled at that price.
- On all 345 replayed markets the per-second live total of -9.59 sits 0.65 of the way from the summed pessimistic result (-56.73) to the summed optimistic result (+15.97). The mean of the per-market positions, clipped to the range -0.5 to 1.5, is 0.39 over the 309 markets whose two bounds differ; 13 of them are clipped and the unclipped mean is -0.118. The per-second result fell inside the bounds in 307 of the 345 markets.
- Prints that share a timestamp come from one sweeping order and the API's order among them is not execution order, so the replay orders them by price priority. Ordering them by trade id, as the API returns them, gave -0.0218 per trade for live and +0.0006 for paper on the same markets.

At these settings the rules do not show a positive edge after fees. The average win is far smaller than the average loss, so the strategy needs a win rate above 86.6% to break even and the replay measured 82.4%.

The numbers apply to one 30-day window, one contract per trade, and fills at the observed ask with no queue or latency effects. The report lists the other caveats, including that book top is rebuilt from trade prints only.

## How it works

- `backtest/fetch.py` pulls public, unauthenticated Kalshi data: the settled market list, one-minute candlesticks (YES bid and ask, open/high/low/close) and the trade prints from the last 9 minutes of a sample of markets. It is resumable, writes one file per market atomically, spaces requests to a configurable rate and backs off on HTTP 429 and 5xx responses.
- `backtest/sim.py` holds the simulation. Prices are integer milli-dollars on Kalshi's tapered grid (0.001 steps below 0.10 and above 0.90, 0.01 steps in between). A side's ask and bid are derived from the YES book the way the strategy derives them, including the stale-value behavior when one side of the book is empty.
- Fees follow the taker formula `0.07 * contracts * price * (1 - price)`, rounded up to $0.000001, with the balance change floored to $0.0001 for a direct member. `--fee-precision` switches to cent rounding or to no fees.
- Tier A uses the one-minute candles for every market. For each market it enumerates the feasible paths the strategy could have taken and reports an optimistic and a pessimistic result.
- Tier B rebuilds a per-second YES bid and ask from the trade prints (prints with one timestamp are ordered by price priority, so the deepest level of a sweep sets the book), polls about once per second as an approximation of the strategy's own polling, and starts the paper take-profit and stop checks at the next second and the live ones 3 seconds after the trigger. It runs on every 10th market plus up to 60 markets where the Tier A bounds disagree, and it is compared with the Tier A bounds as a calibration.
- Daily profit and loss limits are replayed on closed trades in time order, once with a reset at each UTC day and once as a since-start limit that ends the run at the first crossing.
- `backtest/run_backtest.py` loads and validates the cached data, runs both tiers, and writes `report.md`, `summary.json` and trade-level CSVs. Every setting is a command-line flag. It prints a warning when Tier B sample markets have no cached prints.
- `backtest/sweep/sweep_run.py` evaluates 1,200 settings combinations on the Tier A live model with a chronological train/test holdout (train on the first 20 days, test on the last 10), and `sweep_report.py` turns the raw grid into tables. `--limit N` runs a smoke test of N configurations that always includes the baseline. Settings chosen from a sweep on the same window are in-sample and are not evidence of an edge.
- `tools/grid_run.py` runs a named list of 38 settings as separate processes and tabulates the per-second replay next to the Tier A live envelope. `tools/grid_table.py` prints the table for whichever configurations have finished.

## Build and run

Tested with Python 3.11.5. Standard library only.

```
cd backtest
python fetch.py all                 # once; needs network, resumable, caches JSON under data/
python run_backtest.py              # offline; writes results/
python run_backtest.py --entry-min 0.85 --entry-max 0.89 --tp 0.05 --out-dir results_alt
python sweep/sweep_run.py           # parameter sweep; then python sweep/sweep_report.py
cd ../tools
python grid_run.py                  # 38 named settings, six at a time
python grid_table.py
```

Run status, with Python 3.11.5 against a cached copy of the data (2,848 candle files and the trade prints of the Tier B sample):

- `python run_backtest.py` produced every figure above and the report.
- `python sweep/sweep_run.py` evaluated all 1,200 configurations in 1,504 s on 7 worker processes and `python sweep/sweep_report.py` wrote the tables from the result. The sweep's baseline matches the report's Tier A live pessimistic result (2,748 trades, total -282.16). `--limit 20` and `--limit 1` smoke runs also completed, and `sweep_report.py` ran on both.
- `python grid_run.py` ran all 38 named settings, six processes at a time, and `python grid_table.py` printed the table with the Tier A live envelope filled in; the baseline row reads -0.1027 to +0.0403 per trade.
- `python run_backtest.py --entry-min 0.85 --entry-max 0.89 --tp 0.05 --out-dir results_alt` ran to completion. Its Tier B replay covers 301 of the 345 sample markets: the cache holds trade prints for the default-settings sample only, so 44 of the 60 ambiguous-supplement markets are skipped. The run prints a warning and the report states the count.
- `fetch.py` was not run again, so the fetch commands are unverified. The default fetch window ends at 2026-09-22T22:00:00Z and covers 30 days; whether Kalshi still serves that window is unverified, and `--end` and `--days` change it.

## Tests

```
cd backtest
python -m pytest tests -q           # or: python -m unittest discover -s tests
```

37 tests pass under both pytest and unittest. They drive `sim.py` with synthetic market paths and cover the price grid, fee examples, market parsing, the Tier A path enumeration, the Tier B replay (including prints that share a timestamp) and the portfolio statistics.

## Background

Independent research project in September 2026 that tests the published rules of a Kalshi trading bot against public market data.

## License

MIT, see [LICENSE](LICENSE).
