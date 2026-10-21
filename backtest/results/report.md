# KXBTC15M backtest: entry [0.80, 0.93], stop below 0.40, take-profit +0.10, last 8 minutes

Generated 2026-10-21T13:45:08Z from cached public Kalshi data. Every number below was computed by `run_backtest.py`; dollar amounts are per 1 contract(s).

Settings: enter when a side's ask is inside [0.80, 0.93]; stop when the side ask falls below 0.40 (1 one-second confirmation); take-profit 0.10 above the fill; entry window of the last 8 minutes; daily profit target 4000 and loss target -20.

## Bottom line

- Per-second replay (Tier B) of 285 markets spread evenly over the 30 days: the live model made -0.0247 per trade after fees (standard error 0.0139) over 273 trades, total -6.75; win rate 82.4% against a break-even win rate of 86.6% (average win +0.0805, average loss -0.5181). The paper model on the same markets: -0.0019 per trade, win rate 82.4%, break-even 82.7%.
- 1-minute candles (Tier A), all 2848 usable markets, 2748 trades: live between -0.1027 and +0.0403 per trade (total -282.16 to +110.84); paper between -0.0783 and +0.0595 per trade. These are envelopes (every take-profit at the bar high versus every stop at the bar low), not estimates.
- Calibration on all 345 replayed markets: live truth -9.59 against Tier A -56.73 (pessimistic) and +15.97 (optimistic); the truth was inside the bounds in 307 markets (332 within +/-0.0105), below in 26, above in 12. The summed truth sits 0.65 of the way from the summed pessimistic to the summed optimistic result; the mean of the per-market positions, clipped to [-0.5, 1.5], is 0.39 over the 309 markets whose bounds differ (13 clipped, unclipped mean -0.118).
- Paper overstates live by +0.0228 per trade on the replayed systematic-sample paths: fees +0.0162 plus selling at the bid instead of the ask +0.0066.

## Data

- Period: settled KXBTC15M markets with close_time in (2026-08-23T22:00:00Z, 2026-09-22T22:00:00Z]; first close 2026-08-23T22:15:00Z, last close 2026-09-22T22:00:00Z.
- Markets listed by the API: 2848 (expected 2880 for 96 per day). Usable: 2848.
- The 32 absent quarter-hours: 2026-08-27 Thu 07:15-09:00 UTC (8 closes); 2026-09-03 Thu 07:15-09:00 UTC (8 closes); 2026-09-10 Thu 07:15-09:00 UTC (8 closes); 2026-09-17 Thu 07:15-09:00 UTC (8 closes); all are Thursday closes between 07:15 and 09:00 UTC, the weekly Thursday 03:00-05:00 ET trading pause, when no KXBTC15M market is listed.
- Results: no 1431, yes 1417.
- Candles per market (count: markets): 15: 2848. Markets without candles: 0. Candle fetch errors: 0.
- Missing minutes: 0 in total, 0 inside or just before the entry window (0 markets). Null OHLC fields: 0. Excluded: 0 without a yes/no result, 0 with an incomplete window.
- Window bars with an empty book side somewhere in the minute (YES ask 1.00 or YES bid 0.00): 3810 of 22784.

## Tier A results (all markets, 1-minute candles, no daily limit)

| Model / bound | Trades | Win rate | Avg win | Avg loss | PnL/trade | Total PnL | Max DD | Break-even win rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| paper optimistic | 2748 | 86.7% | +0.1346 | -0.4287 | +0.0595 | +163.64 | 1.84 | 76.1% |
| paper pessimistic | 2748 | 76.6% | +0.0955 | -0.6474 | -0.0783 | -215.23 | 216.16 | 87.1% |
| live optimistic | 2748 | 86.6% | +0.1182 | -0.4632 | +0.0403 | +110.84 | 2.74 | 79.7% |
| live pessimistic | 2748 | 76.5% | +0.0739 | -0.6760 | -0.1027 | -282.16 | 282.91 | 90.1% |
| live optimistic, cent-rounded fees (FCM / pre-Jul-2026) | 2748 | 86.6% | +0.1077 | -0.4727 | +0.0297 | +81.71 | 3.49 | 81.4% |
| live pessimistic, cent-rounded fees (FCM / pre-Jul-2026) | 2748 | 76.3% | +0.0642 | -0.6811 | -0.1124 | -308.77 | 309.43 | 91.4% |

Markets with no entry: 100. Markets whose Tier A outcome depends on intrabar order (live model, bounds give different PnL): 2455; different exit type or side: 394.

**By entry price** (n, win rate, PnL/trade)

| Bucket | Paper opt | Paper pes | Live opt | Live pes |
|---|---|---|---|---|
| 0.80-0.84 | 2036, 85.4%, +0.0642 | 1632, 80.5%, -0.0350 | 1982, 84.9%, +0.0403 | 1739, 81.5%, -0.0570 |
| 0.85-0.89 | 417, 89.9%, +0.0555 | 493, 60.0%, -0.2132 | 467, 91.0%, +0.0493 | 498, 60.4%, -0.2302 |
| 0.90-0.93 | 295, 91.2%, +0.0334 | 623, 79.5%, -0.0852 | 299, 91.3%, +0.0264 | 511, 75.0%, -0.1337 |

**By exit type** (n, win rate, PnL/trade)

| Bucket | Paper opt | Paper pes | Live opt | Live pes |
|---|---|---|---|---|
| TP | 2113, 100.0%, +0.1406 | 1610, 100.0%, +0.1000 | 2109, 99.9%, +0.1228 | 1722, 99.8%, +0.0736 |
| SL | 366, 0.0%, -0.4287 | 643, 0.0%, -0.6474 | 366, 0.0%, -0.4655 | 643, 0.0%, -0.6799 |
| SETTLE_WIN | 269, 100.0%, +0.0869 | 495, 100.0%, +0.0809 | 273, 100.0%, +0.0815 | 383, 100.0%, +0.0740 |

**By minutes left at entry** (n, win rate, PnL/trade)

| Bucket | Paper opt | Paper pes | Live opt | Live pes |
|---|---|---|---|---|
| 7-8 | 1570, 87.3%, +0.0496 | 1570, 83.0%, -0.0285 | 1570, 87.3%, +0.0318 | 1570, 82.9%, -0.0502 |
| 6-7 | 370, 85.7%, +0.0629 | 370, 77.3%, -0.0659 | 370, 85.4%, +0.0410 | 370, 76.5%, -0.0954 |
| 5-6 | 261, 82.8%, +0.0537 | 261, 69.3%, -0.1268 | 261, 82.8%, +0.0316 | 261, 69.3%, -0.1562 |
| 4-5 | 208, 87.5%, +0.0821 | 208, 69.2%, -0.1371 | 208, 87.5%, +0.0611 | 208, 69.2%, -0.1655 |
| 3-4 | 144, 86.8%, +0.0809 | 144, 66.0%, -0.1664 | 144, 86.8%, +0.0604 | 144, 66.0%, -0.1930 |
| 2-3 | 101, 86.1%, +0.0860 | 101, 52.5%, -0.2710 | 101, 86.1%, +0.0656 | 101, 52.5%, -0.2970 |
| 1-2 | 81, 87.7%, +0.1050 | 81, 43.2%, -0.3642 | 81, 87.7%, +0.0864 | 81, 43.2%, -0.3854 |
| 0-1 | 13, 100.0%, +0.1990 | 13, 61.5%, -0.2834 | 13, 100.0%, +0.1866 | 13, 61.5%, -0.2929 |

**By week (UTC, Monday start)** (n, win rate, PnL/trade)

| Bucket | Paper opt | Paper pes | Live opt | Live pes |
|---|---|---|---|---|
| week of 2026-08-17 | 8, 100.0%, +0.1381 | 8, 100.0%, +0.0971 | 8, 100.0%, +0.1240 | 8, 100.0%, +0.0746 |
| week of 2026-08-24 | 640, 86.2%, +0.0573 | 640, 77.7%, -0.0719 | 640, 86.2%, +0.0383 | 640, 77.5%, -0.0953 |
| week of 2026-08-31 | 647, 85.3%, +0.0510 | 647, 75.4%, -0.0903 | 647, 85.2%, +0.0311 | 647, 75.3%, -0.1146 |
| week of 2026-09-07 | 639, 86.4%, +0.0591 | 639, 73.6%, -0.0958 | 639, 86.4%, +0.0393 | 639, 73.6%, -0.1219 |
| week of 2026-09-14 | 639, 87.6%, +0.0654 | 639, 78.2%, -0.0660 | 639, 87.5%, +0.0467 | 639, 78.1%, -0.0900 |
| week of 2026-09-21 | 175, 90.3%, +0.0762 | 175, 81.1%, -0.0471 | 175, 90.3%, +0.0585 | 175, 80.6%, -0.0695 |

**By side** (n, win rate, PnL/trade)

| Bucket | Paper opt | Paper pes | Live opt | Live pes |
|---|---|---|---|---|
| UP | 1381, 86.5%, +0.0578 | 1378, 75.9%, -0.0831 | 1381, 86.3%, +0.0387 | 1378, 75.7%, -0.1076 |
| DOWN | 1367, 86.9%, +0.0613 | 1370, 77.3%, -0.0735 | 1367, 86.9%, +0.0420 | 1370, 77.2%, -0.0977 |

## Daily limits (loss -20, profit 4000)

Two readings of the limit. `utc_day`: closed PnL accumulates per UTC day and trading stops for the rest of that day once it crosses a target. `since_start`: the starting balance is recorded once and the run ends the first time the closed PnL since the first trade crosses a target, modelled here as a single start at the beginning of the period.

| Model / bound | Reading | Stop events | Trades skipped | Trades | PnL/trade | Total PnL | Max DD |
|---|---|---:|---:|---:|---:|---:|---:|
| paper optimistic | utc_day | 0 | 0 | 2748 | +0.0595 | +163.64 | 1.84 |
| paper optimistic | since_start | 0 | 0 | 2748 | +0.0595 | +163.64 | 1.84 |
| paper pessimistic | utc_day | 0 | 0 | 2748 | -0.0783 | -215.23 | 216.16 |
| paper pessimistic | since_start | 1 | 2535 | 213 | -0.0940 | -20.02 | 20.96 |
| live optimistic | utc_day | 0 | 0 | 2748 | +0.0403 | +110.84 | 2.74 |
| live optimistic | since_start | 0 | 0 | 2748 | +0.0403 | +110.84 | 2.74 |
| live pessimistic | utc_day | 0 | 0 | 2748 | -0.1027 | -282.16 | 282.91 |
| live pessimistic | since_start | 1 | 2571 | 177 | -0.1152 | -20.39 | 21.14 |

- paper pessimistic, since_start: trading would stop on 2026-08-26 after KXBTC15M-26AUG260215-15 (accumulated -20.02).

- live pessimistic, since_start: trading would stop on 2026-08-25 after KXBTC15M-26AUG251715-15 (accumulated -20.39).

## Stop-loss exit prices

| Source | Stops | Mean exit | Median | Mean distance below the stop level | Min | 0.380-0.399 | 0.350-0.379 | 0.300-0.349 | 0.200-0.299 | 0.100-0.199 | 0.000-0.099 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Tier A paper optimistic (ask) | 366 | 0.390 | 0.390 | 0.010 | 0.320 | 365 | 0 | 1 | 0 | 0 | 0 |
| Tier A paper pessimistic (ask) | 643 | 0.205 | 0.230 | 0.195 | 0.001 | 38 | 58 | 89 | 181 | 119 | 158 |
| Tier A live optimistic (bid) | 366 | 0.380 | 0.380 | 0.020 | 0.310 | 365 | 0 | 1 | 0 | 0 | 0 |
| Tier A live pessimistic (bid) | 643 | 0.191 | 0.210 | 0.209 | 0.001 | 13 | 49 | 89 | 190 | 135 | 167 |
| Tier B truth systematic, paper (ask at confirmation) | 48 | 0.348 | 0.370 | 0.052 | 0.180 | 18 | 16 | 7 | 6 | 1 | 0 |
| Tier B truth systematic, live (bid at confirmation) | 48 | 0.338 | 0.360 | 0.062 | 0.170 | 13 | 16 | 9 | 9 | 1 | 0 |
| Tier B truth all, paper (ask at confirmation) | 61 | 0.352 | 0.370 | 0.048 | 0.180 | 26 | 18 | 10 | 6 | 1 | 0 |
| Tier B truth all, live (bid at confirmation) | 61 | 0.340 | 0.360 | 0.060 | 0.170 | 16 | 22 | 11 | 11 | 1 | 0 |

## Paper minus live, same simulated path

| Source | Trades | Gap/trade | Fees/trade | Entry fee | Exit fee | Exit ask-bid/trade | Ask-bid per early exit |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tier A optimistic | 2748 | +0.0190 | +0.0142 | +0.0098 | +0.0044 | +0.0048 | +0.0054 |
| Tier A pessimistic | 2748 | +0.0249 | +0.0154 | +0.0093 | +0.0060 | +0.0095 | +0.0111 |
| Tier B truth systematic | 273 | +0.0228 | +0.0162 | +0.0097 | +0.0066 | +0.0066 | +0.0074 |
| Tier B truth all | 333 | +0.0239 | +0.0165 | +0.0098 | +0.0067 | +0.0074 | +0.0082 |

## Tier B calibration

Per-second YES bid/ask rebuilt for the last 9 minutes from trade prints (a print with taker_book_side 'bid' reveals the YES ask, 'ask' reveals the YES bid; prints that share a timestamp are ordered by price priority), then the entry and exit rules polled about once per second.

- Sample: 345 markets simulated (systematic 285, ambiguous_supplement 60); missing trade files: 0.
- Print sanity: 3386003 YES-ask prints and 3325433 YES-bid prints; 100.0% of ask prints inside the candle yes_ask [low, high] for their minute (100.0% within one tick), 100.0% of bid prints inside yes_bid (100.0% within one tick). Seconds with an ask print 94.2%, with a bid print 93.9%. Clamped values per market 0.0, uncrossed seconds per market 24.2, mean quote age 1.74 s (ask) / 1.82 s (bid).
- Quote moves with no print behind them (candle extreme more than one tick beyond every print of that minute): YES-ask high 62 and low 25 of 3068 minutes; YES-bid high 23 and low 69 of 3063 minutes. The strategy polls the book and can react to these; a print-based path cannot, which is Tier B's main blind spot.

| Subset | Model | Markets | Sum pes | Sum truth | Sum opt | Bracketed | Within +/-0.0105 | Truth < pes | Truth > opt | Position of sums (0 = pes, 1 = opt) | Mean clipped position (0 = pes, 1 = opt) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all | paper | 345 | -48.26 | -1.61 | +22.37 | 336 | 338 | 1 | 8 | 0.66 | 0.37 (over 311 markets, 8 clipped) |
| all | live | 345 | -56.73 | -9.59 | +15.97 | 307 | 332 | 26 | 12 | 0.65 | 0.39 (over 309 markets, 13 clipped) |
| systematic | paper | 285 | -21.96 | -0.52 | +13.43 | 277 | 278 | 1 | 7 | 0.61 | 0.31 (over 251 markets, 8 clipped) |
| systematic | live | 285 | -28.76 | -6.75 | +8.21 | 250 | 272 | 24 | 11 | 0.60 | 0.34 (over 249 markets, 13 clipped) |
| ambiguous_supplement | paper | 60 | -26.30 | -1.10 | +8.94 | 59 | 60 | 0 | 1 | 0.72 | 0.63 (over 60 markets, 0 clipped) |
| ambiguous_supplement | live | 60 | -27.97 | -2.84 | +7.75 | 57 | 60 | 2 | 1 | 0.70 | 0.59 (over 60 markets, 0 clipped) |

The position of the sums is (sum truth - sum pes) / (sum opt - sum pes). The mean clipped position averages each market's own (truth - pes) / (opt - pes) after clipping it to [-0.5, 1.5], over the markets whose two bounds differ; a market with a tiny bound width can otherwise dominate the mean.

- Why the paper truth fell outside the bounds (all replayed markets): same side, entry and exit type but a different exit price in 1 (TP 1); a different path in a market whose candles show a quote extreme no trade printed in 5 (Tier A assumes the strategy's polls catch it, Tier B cannot see it); a different path otherwise in 3.
- Why the live truth fell outside the bounds (all replayed markets): same side, entry and exit type but a different exit price in 27 (TP 27; the live take-profit bid is the one non-strict Tier A price); a different path in a market whose candles show a quote extreme no trade printed in 6 (Tier A assumes the strategy's polls catch it, Tier B cannot see it); a different path otherwise in 5.
- Entry agreement (live, all Tier B markets): truth traded in 333 markets, Tier A in 333; both in 333, same side in 324; mean truth-minus-Tier-A entry price +0.0002 (mean absolute +0.0114). Exit type matches Tier A optimistic in 294 and pessimistic in 264 of the 333.

| Tier B truth |  Trades | Win rate | Avg win | Avg loss | PnL/trade | Total PnL | Max DD | Break-even win rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all, paper | 333 | 81.7% | +0.1013 | -0.4781 | -0.0048 | -1.61 | 4.94 | 82.5% |
| all, live | 333 | 81.7% | +0.0804 | -0.5156 | -0.0288 | -9.59 | 11.19 | 86.5% |
| systematic, paper | 273 | 82.4% | +0.1007 | -0.4826 | -0.0019 | -0.52 | 4.27 | 82.7% |
| systematic, live | 273 | 82.4% | +0.0805 | -0.5181 | -0.0247 | -6.75 | 8.77 | 86.6% |
| ambiguous_supplement, paper | 60 | 78.3% | +0.1043 | -0.4615 | -0.0183 | -1.10 | 2.29 | 81.6% |
| ambiguous_supplement, live | 60 | 78.3% | +0.0798 | -0.5065 | -0.0473 | -2.84 | 3.58 | 86.4% |

**Tier B truth, systematic sample, by exit type** (n, win rate, PnL/trade)

| Bucket | Paper | Live |
|---|---|---|
| TP | 196, 100.0%, +0.1033 | 196, 100.0%, +0.0810 |
| SL | 48, 0.0%, -0.4826 | 48, 0.0%, -0.5181 |
| SETTLE_WIN | 29, 100.0%, +0.0826 | 29, 100.0%, +0.0773 |

**Tier B truth, systematic sample, by entry price** (n, win rate, PnL/trade)

| Bucket | Paper | Live |
|---|---|---|
| 0.80-0.84 | 195, 82.1%, +0.0036 | 195, 82.1%, -0.0227 |
| 0.85-0.89 | 41, 87.8%, +0.0245 | 41, 87.8%, +0.0079 |
| 0.90-0.93 | 37, 78.4%, -0.0603 | 37, 78.4%, -0.0714 |

**Tier B truth, systematic sample, by minutes left at entry** (n, win rate, PnL/trade)

| Bucket | Paper | Live |
|---|---|---|
| 7-8 | 161, 83.9%, +0.0007 | 161, 83.9%, -0.0192 |
| 6-7 | 36, 83.3%, +0.0081 | 36, 83.3%, -0.0176 |
| 5-6 | 24, 91.7%, +0.0597 | 24, 91.7%, +0.0328 |
| 4-5 | 22, 77.3%, -0.0194 | 22, 77.3%, -0.0456 |
| 3-4 | 11, 63.6%, -0.1048 | 11, 63.6%, -0.1311 |
| 2-3 | 12, 66.7%, -0.0802 | 12, 66.7%, -0.1157 |
| 1-2 | 6, 83.3%, +0.0137 | 6, 83.3%, -0.0111 |
| 0-1 | 1, 100.0%, +0.1000 | 1, 100.0%, +0.0833 |

## Method

- Rules (the published strategy described in the README, written out here in this backtester's own terms): one market per quarter hour; entries only while the minutes left to the market's close are in (0, window]; UP if the UP ask is inside the entry range (and the DOWN ask is not, or UP ask >= DOWN ask), else DOWN if the DOWN ask is inside the range; one entry per market, no re-entry after an exit; stop when the side ask is below the stop level and still below it one second later (one confirmation); take-profit when the side ask reaches the fill plus the take-profit distance and that target is at most 1; otherwise held to settlement.
- Prices: UP ask = YES ask, DOWN ask = 1 - YES bid, UP bid = YES bid, DOWN bid = 1 - YES ask. The strategy never sees an ask of 1.00 (an empty book side leaves its up/down values stale), so a fill at exactly 0.90 (target 1.00) can never take profit.
- Paper model: fill = ask at trigger, exits at the side ask seen at the trigger or confirmation poll, no fees. Live model: fill = ask at trigger (a buy limit 0.02 above the ask crosses the book), take-profit and stop exits at the side bid (immediate-or-cancel sell at 1c), taker fee on the entry and on take-profit and stop exits, settlement free.
- Fees: Kalshi schedule effective July 7, 2026 with the docs fee_rounding algorithm: trade fee = 0.07 x C x P x (1-P) rounded up to $0.000001, balance change floored to $0.0001 (direct member). A cent-rounded variant (FCM account, or the pre-July-2026 rule) is reported as a sensitivity row.
- Tier A (1-minute candles, bar = [end_period_ts - 60, end_period_ts)): entry in the first window bar where a side's ask is in range at the bar open (price = the open) or crosses into the range inside the bar. For a crossing, the first in-range poll can have seen any price between the boundary crossed and the bar extreme (from below: the lower entry bound up to min(high, upper entry bound); from above: max(low, lower entry bound) up to the upper entry bound), so every grid price in that interval is simulated as a branch (the entry sets the take-profit target, so an interior price can beat both ends). If both sides only cross within the same bar, both sides are branches too.
- Tier A exits per bar: TP is touched if the side ask high (capped at 0.999) >= target; SL is touched if the side ask low is below the stop level. A stop is certain when the bar also closes below it (still below one second later); when the bar dips below the stop level but closes at or above it, the one-second confirmation is unknown, so both 'stopped' and 'not stopped' are kept as branches. In the entry bar, a dip that may have happened before an entry from below is also a branch. When TP and SL are both touched in a bar, both are kept.
- Tier A bounds: every branch is simulated to the end; OPTIMISTIC keeps the path with the best PnL for the model (so a same-bar TP/SL tie resolves TP first) and uses favorable prices, PESSIMISTIC keeps the worst path (SL first) with unfavorable prices.
- Tier A exit prices (the bar open when a TP bar already opens at or above the target): paper TP = the bar's highest side ask, capped at 0.999 (optimistic) or the target itself (pessimistic). Live TP = one tick under the optimistic paper price (optimistic) or the target minus the widest bid-ask spread seen at that bar's open or close (pessimistic), both kept inside the bar's side-bid range; the pessimistic live TP is the one price that is not a strict bar extreme (the bar's lowest bid usually predates the trigger), and Tier B checks it. Paper SL = the highest grid price under the stop level, 0.39 for a 0.40 stop (optimistic), or the bar's lowest side ask (pessimistic). Live SL = one tick under the optimistic paper price inside the bar's side-bid range (optimistic), or the bar's lowest side bid, floored at 0.001 (pessimistic).
- Tier B: per-second YES bid/ask rebuilt from every trade print in the market's last 9 minutes. Each second holds the book at the start of that second: the candle open at a minute boundary (the same convention as Tier A), otherwise the last print of the previous second, clamped to the candle range and uncrossed; then about one poll per second. Prints that share a timestamp are ordered by price priority (YES-ask prints ascending, YES-bid prints descending), so the print that sets the book is the deepest level a sweep reached. Paper checks take-profit and stop from the next second; live from 3 s after the trigger (a 2 s pause plus order, position and fill lookups). A live stop sells at the side bid in the confirmation second.
- Daily limits are replayed on closed PnL in time order; results are reported without the limit, with a per-UTC-day reset, and with a since-start reading in which the first crossing ends the run.

## Caveats

- Fills are assumed at the observed ask for 1 contract with no queue or latency effects; the live buy limit is ask + 0.02, so a price that runs away by more than 2c during the request latency would rest unfilled and be cancelled, and the strategy would retry. Not modelled.
- The live model assumes fills report their fee at once, so take-profit is enabled immediately; if fill accounting lags, take-profit waits and results change.
- The original strategy also has an optional spot-price filter. It is assumed to be switched off, and outages of the price feed it reads (which would block entries) are not modelled.
- The market-list lag after each close (11-25 s observed) and Thursday 03:00-05:00 ET trading pauses are not modelled; neither affects an 8-minute window directly, but a paused market cannot be exited.
- Tier A bounds are deliberately wide: the optimistic bound sells every take-profit at the bar high and the pessimistic bound sells every stop at the bar low, and the stop confirmation is inferred from the bar close. Tier B shows where a per-second replay falls between them.
- Tier B reconstructs top of book from prints only. A side with no print carries its last value between minute boundaries, a print shows a level that traded rather than the resting book after the trade (after a sweep the ask can sit above the last printed level), prints that share a timestamp are put in price-priority order as an assumption about execution order, and quote moves that no trade printed (the strategy can see these by polling the book) are invisible to it. Tier B is the closest replay available from public data, not the strategy's own view; the calibration section counts why each out-of-bounds market fell outside.
- Tier B covers a sample (every 10th market plus ambiguous cases), so its per-trade mean carries sampling error; the report gives the standard error.
- Whether the original strategy halts when a daily limit is crossed is unverified; the daily-limit replay assumes it does.
- Fees assume a direct-member account (centi-cent rounding). An FCM-cleared account pays cent-rounded fees; see the sensitivity rows.
- Past 30 days only. BTC 15-minute markets moved to exchange shard 2 at 2026-08-25 04:00 UTC (in the cached list the last shard-0 market closes at that time and the first shard-2 market opens; all times in this report are UTC); market structure and liquidity can change.
- Tier A prices a live exit inside the bar's side-bid range, with a floor of 0.001, even when the bar's bid side was empty at some point (a pessimistic stop is then sold at 0.001). Such exits could not fill at those prices: 69 in the live pessimistic bound (total -60.75) and 16 in the live optimistic bound (total -4.87). Tier B treats an empty bid as no fill and waits for a bid or rides to settlement.

## Rerun

```
python fetch.py all          # only needed once; resumable, cached under data/
python -m pytest tests -q    # or: python -m unittest discover -s tests
python run_backtest.py       # offline; writes results/
```
