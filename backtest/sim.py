"""Pure simulation of a rule-based strategy on KXBTC15M 15-minute BTC up/down markets.

The strategy buys the side whose ask is inside an entry range during the last minutes of
a market, then exits on a stop, a take-profit or settlement (see the README).

No file I/O and no network: every function takes plain data and returns plain data,
so the unit tests in tests/test_sim.py can drive it with synthetic paths.

Price unit: integer milli-dollars (0.001 = 1, 1.000 = 1000). KXBTC15M uses the
tapered_deci_cent grid: 0.001 steps below 0.10 and above 0.90, 0.01 steps between.

Book conventions:
  UP ask   = YES ask            = 1 - best NO bid
  DOWN ask = NO ask             = 1 - best YES bid
  UP bid   = best YES bid
  DOWN bid = best NO bid        = 1 - YES ask
A YES ask of 1000 means "no YES asks" (empty NO-bid side) and a YES bid of 0 means
"no YES bids". The simulated strategy never observes those values: when a side of the
orderbook is empty it keeps the previous up/down value, so the largest ask it can ever
see is 0.999.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

UP = "UP"
DOWN = "DOWN"
FULL = 1000
TIERB_LOOKBACK_S = 540  # Tier B rebuilds the last 9 minutes (window + 1 minute warm-up)

TP = "TP"
SL = "SL"
SETTLE_WIN = "SETTLE_WIN"
SETTLE_LOSS = "SETTLE_LOSS"
EXIT_TYPES = (TP, SL, SETTLE_WIN, SETTLE_LOSS)

# touch flags for one bar in Tier A
NONE, CERTAIN, POSSIBLE = 0, 1, 2


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Params:
    """Strategy settings plus model knobs. Defaults are the settings the report is run at."""

    entry_min: float = 0.80          # lower end of the entry range (side ask)
    entry_max: float = 0.93          # upper end of the entry range (side ask)
    exit_below: float = 0.40         # stop when the side ask falls below this
    take_profit_pts: float = 0.10    # target = fill + this, only if target <= 1
    time_delay_min: float = 8.0      # entry window = minutes left in (0, time_delay_min]
    min_left_min: float = 0.0        # no NEW entries with <= this many minutes left
                                     # (positions already open are still managed to the close)
    pings: int = 1                   # one-second stop-loss confirmations
    contracts: int = 1               # contracts per trade
    fee_precision: str = "centicent"  # live fee model: 'centicent' | 'cent' | 'none'
    fee_rate: Fraction = Fraction(7, 100)  # taker fee coefficient (KXBTC15M quadratic, multiplier 1)
    live_monitor_delay_s: int = 3    # Tier B: seconds from entry trigger to first live TP/SL check
    daily_loss_target: float = -20.0
    daily_profit_target: float = 4000.0

    @property
    def lo(self) -> int:
        return milli(self.entry_min)

    @property
    def hi(self) -> int:
        return milli(self.entry_max)

    @property
    def x(self) -> int:
        return milli(self.exit_below)

    @property
    def tp(self) -> int:
        return milli(self.take_profit_pts)

    @property
    def window_s(self) -> int:
        return int(round(self.time_delay_min * 60))

    @property
    def floor_s(self) -> int:
        return int(round(self.min_left_min * 60))


def milli(x) -> int:
    """Dollar value (float or numeric string) to integer milli-dollars."""
    return int(round(float(x) * 1000))


def to_milli_or_none(x) -> Optional[int]:
    if x is None or x == "":
        return None
    return milli(x)


# ---------------------------------------------------------------------------
# price grid (tapered_deci_cent)
# ---------------------------------------------------------------------------
def next_below(p: int) -> int:
    """Largest grid price strictly below p."""
    if p <= 100:
        return p - 1
    if p <= 900:
        return p - 10 if p % 10 == 0 else p - (p % 10)
    return p - 1


def next_above(p: int) -> int:
    """Smallest grid price strictly above p."""
    if p < 100:
        return p + 1
    if p < 900:
        return (p // 10 + 1) * 10
    return p + 1


def grid_between(lo: int, hi: int) -> List[int]:
    """Every grid price in [lo, hi] (lo and hi themselves included even if off-grid)."""
    out = [lo]
    p = lo
    while True:
        p = next_above(p)
        if p > hi:
            break
        out.append(p)
    if out[-1] != hi and hi > lo:
        out.append(hi)
    return out


# ---------------------------------------------------------------------------
# fees (Kalshi fee schedule effective July 7, 2026 + docs fee_rounding algorithm)
# ---------------------------------------------------------------------------
def _ceil_to(x: Fraction, q: Fraction) -> Fraction:
    return Fraction(math.ceil(x / q)) * q


def _floor_to(x: Fraction, q: Fraction) -> Fraction:
    return Fraction(math.floor(x / q)) * q


def taker_fee(price: int, contracts=1, action: str = "buy", precision: str = "centicent",
              rate: Fraction = Fraction(7, 100), multiplier: int = 1) -> Fraction:
    """Net fee in dollars (trade fee + rounding fee) for ONE immediately-matched fill.

    trade fee = rate * M * C * P * (1 - P), rounded up to $0.000001; the balance change
    (revenue - fee, revenue negative for buyers) is then floored to the account precision:
    $0.0001 for direct members ('centicent'), $0.01 for FCM/non-direct members ('cent',
    which also reproduces the pre-July-2026 round-up-to-the-cent rule for 1 lot).
    Settlement is free and is never passed through this function.
    """
    if precision == "none":
        return Fraction(0)
    P = Fraction(price, 1000)
    C = Fraction(contracts).limit_denominator(100)
    raw = rate * multiplier * C * P * (1 - P)
    fee6 = _ceil_to(raw, Fraction(1, 10 ** 6))
    if precision == "centicent":
        q = Fraction(1, 10 ** 4)
    elif precision == "cent":
        q = Fraction(1, 100)
    else:
        raise ValueError("unknown fee precision %r" % precision)
    notional = C * P
    if action == "buy":
        charged = _ceil_to(notional + fee6, q)
        return charged - notional
    if action == "sell":
        received = _floor_to(notional - fee6, q)
        return notional - received
    raise ValueError("action must be buy or sell")


def live_pnl(entry: int, exit_price: int, exit_type: str, p: Params) -> Tuple[float, float, float]:
    """(net pnl, entry fee, exit fee) in dollars for the live model. Settlement is fee-free."""
    ef = taker_fee(entry, p.contracts, "buy", p.fee_precision, p.fee_rate)
    xf = Fraction(0)
    if exit_type in (TP, SL):
        xf = taker_fee(exit_price, p.contracts, "sell", p.fee_precision, p.fee_rate)
    gross = Fraction(exit_price - entry, 1000) * p.contracts
    return float(gross - ef - xf), float(ef), float(xf)


def paper_pnl(entry: int, exit_price: int, p: Params) -> float:
    """Paper model: (exit - entry) * contracts, no fees."""
    return (exit_price - entry) / 1000.0 * p.contracts


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------
@dataclass
class Bar:
    """One 1-minute candle. ya/yb = YES ask / YES bid (open, high, low, close) in milli."""

    start: int
    ya: Tuple[int, int, int, int]
    yb: Tuple[int, int, int, int]
    synthetic: bool = False


@dataclass
class Market:
    ticker: str
    open_ts: int
    close_ts: int
    result: str
    bars: List[Optional[Bar]]           # index i covers [open_ts + 60 i, open_ts + 60 (i + 1))
    missing_minutes: List[int] = field(default_factory=list)
    null_fields: int = 0


def iso_to_ts(s: str) -> float:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _ohlc(d: Optional[dict]) -> Optional[List[Optional[int]]]:
    if not isinstance(d, dict):
        return None
    return [to_milli_or_none(d.get(k)) for k in ("open_dollars", "high_dollars", "low_dollars", "close_dollars")]


def parse_market(meta: dict, candles_json: Optional[dict]) -> Market:
    """Build a Market from a /markets row and a /candlesticks response (period 1).

    Candle end_period_ts E covers [E - 60, E). Missing minutes are forward-filled from
    the previous close (O = H = L = C) and listed in missing_minutes; null OHLC fields
    are filled from the previous close and counted in null_fields.
    """
    open_ts = int(iso_to_ts(meta["open_time"]))
    close_ts = int(iso_to_ts(meta["close_time"]))
    n = max(0, (close_ts - open_ts) // 60)
    by_start: Dict[int, dict] = {}
    for c in (candles_json or {}).get("candlesticks", []) or []:
        by_start[int(c["end_period_ts"]) - 60] = c
    bars: List[Optional[Bar]] = []
    missing: List[int] = []
    nulls = 0
    prev_ya: Optional[int] = None
    prev_yb: Optional[int] = None
    for i in range(n):
        s = open_ts + 60 * i
        c = by_start.get(s)
        if c is None:
            missing.append(i)
            if prev_ya is None or prev_yb is None:
                bars.append(None)
            else:
                bars.append(Bar(s, (prev_ya,) * 4, (prev_yb,) * 4, synthetic=True))
            continue
        ya = _ohlc(c.get("yes_ask"))
        yb = _ohlc(c.get("yes_bid"))
        if ya is None or yb is None:
            nulls += 8
            if prev_ya is None or prev_yb is None:
                bars.append(None)
                continue
            ya = ya or [None] * 4
            yb = yb or [None] * 4
        for arr, prev in ((ya, prev_ya), (yb, prev_yb)):
            for j in range(4):
                if arr[j] is None:
                    nulls += 1
                    arr[j] = prev
        if any(v is None for v in ya + yb):
            bars.append(None)
            continue
        o, h, l, cl = ya
        ya_t = (o, max(o, h, l, cl), min(o, h, l, cl), cl)
        o, h, l, cl = yb
        yb_t = (o, max(o, h, l, cl), min(o, h, l, cl), cl)
        bars.append(Bar(s, ya_t, yb_t))
        prev_ya, prev_yb = ya_t[3], yb_t[3]
    return Market(meta["ticker"], open_ts, close_ts, (meta.get("result") or "").lower(), bars, missing, nulls)


def side_ask(bar: Bar, side: str) -> Tuple[int, int, int, int]:
    """Side ask OHLC. DOWN ask = 1 - YES bid, so its high comes from the YES-bid low."""
    if side == UP:
        return bar.ya
    o, h, l, c = bar.yb
    return (FULL - o, FULL - l, FULL - h, FULL - c)


def side_bid(bar: Bar, side: str) -> Tuple[int, int, int, int]:
    """Side bid OHLC. DOWN bid = 1 - YES ask."""
    if side == UP:
        return bar.yb
    o, h, l, c = bar.ya
    return (FULL - o, FULL - l, FULL - h, FULL - c)


def side_wins(result: str, side: str) -> bool:
    return (result == "yes") if side == UP else (result == "no")


def _valid_ask_high(a: Tuple[int, int, int, int]) -> int:
    """Highest ask the strategy could have observed. An ask of 1000 is an empty book; when the
    high is 1000 but open and close are real, the real high is unknown and taken as
    max(open, close); when the close itself is empty the path ended at the extreme (999)."""
    o, h, l, c = a
    if h < FULL:
        return h
    real = [v for v in (o, c) if v < FULL]
    if c >= FULL:
        return FULL - 1
    return max(real) if real else FULL - 1


# ---------------------------------------------------------------------------
# Tier A: 1-minute candles with optimistic / pessimistic bounds
# ---------------------------------------------------------------------------
@dataclass
class Scenario:
    """One feasible path of the strategy through a market under the Tier A approximations."""

    side: str
    entry: int
    entry_bar: int           # index in the window bars
    entry_mode: str          # 'open' | 'below' | 'above'
    exit_type: str
    exit_bar: Optional[int]  # None for settlement
    paper_exit: int          # ask-based exit (paper) or settlement value
    live_exit: int           # bid-based exit (live) or settlement value
    branched: bool = False   # True when an intrabar ambiguity was resolved on this path
    live_bid_empty: bool = False


def window_bars(m: Market, p: Params) -> Tuple[List[Bar], int]:
    """Bars inside the entry window: minutes left in (0, time_delay_min]. A bar that straddles
    the window start is included (only relevant for a non-integer window)."""
    ws = m.close_ts - p.window_s
    out: List[Bar] = []
    first = -1
    for i, b in enumerate(m.bars):
        start = m.open_ts + 60 * i
        if start + 60 <= ws or start >= m.close_ts:
            continue
        if b is None:
            continue
        if first < 0:
            first = i
        out.append(b)
    return out, first


def find_entry_tier_a(wbars: Sequence[Bar], p: Params) -> Tuple[Optional[int], List[Tuple[str, str, List[int]]]]:
    """First window bar where a side's ask is (or becomes) inside the entry range.

    Returns (bar index, [(side, mode, candidate entry prices)]). The entry price is the
    ask at the first poll that sees the side in range:
      - ask open in range -> the open (the first poll of the bar sees it);
      - open below the range, high reaches it -> anywhere in [lower bound, min(high,
        upper bound)]: every grid price in that interval is a candidate;
      - open above the range, low reaches it -> anywhere in [max(low, lower bound),
        upper bound]: every grid price in that interval is a candidate.
    Interior prices matter: the entry sets the take-profit target, so PnL is not
    monotonic in the entry price. Both sides at the open: UP if up >= down (ties go to UP).
    Exactly one side at the open: that side. Otherwise every crossing side is a branch.
    """
    lo, hi = p.lo, p.hi
    # minimum minutes left: the last floor_s // 60 whole bars of the window take no new entry
    # (a bar only partly under the floor stays eligible; candles have no gaps, so bar k of the
    # window is the k-th minute). Exits still use every bar.
    last_entry_k = len(wbars) - (p.floor_s // 60)
    for k, bar in enumerate(wbars):
        if k >= last_entry_k:
            break
        info: Dict[str, Tuple[str, List[int]]] = {}
        for side in (UP, DOWN):
            a = side_ask(bar, side)
            o, l = a[0], a[2]
            h = _valid_ask_high(a)
            if lo <= o <= hi:
                info[side] = ("open", [o])
            elif o < lo <= h:
                info[side] = ("below", grid_between(lo, min(h, hi)))
            elif o > hi >= l:
                info[side] = ("above", grid_between(max(l, lo), hi))
        if not info:
            continue
        opens = [s for s in (UP, DOWN) if s in info and info[s][0] == "open"]
        if len(opens) == 2:
            s = UP if side_ask(bar, UP)[0] >= side_ask(bar, DOWN)[0] else DOWN
            return k, [(s, "open", info[s][1])]
        if len(opens) == 1:
            s = opens[0]
            return k, [(s, "open", info[s][1])]
        return k, [(s, info[s][0], info[s][1]) for s in (UP, DOWN) if s in info]
    return None, []


def _bar_options(tp: int, sl: int) -> List[str]:
    if tp == NONE and sl == NONE:
        return ["CONT"]
    if sl == NONE:
        return [TP] if tp == CERTAIN else [TP, "CONT"]
    if tp == NONE:
        return [SL] if sl == CERTAIN else [SL, "CONT"]
    if tp == POSSIBLE and sl == POSSIBLE:
        return [TP, SL, "CONT"]
    return [TP, SL]


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _valid_spreads(a, b) -> List[int]:
    """Bid-ask spreads observed at the bar open and close (empty book sides skipped)."""
    out = []
    for ai, bi in ((a[0], b[0]), (a[3], b[3])):
        if ai < FULL and bi > 0 and ai > bi:
            out.append(ai - bi)
    return out


def _tp_prices(a, b, target: int, first: bool, bound: str, high_seen: int) -> Tuple[int, int, bool]:
    """Paper exit = side ask at the trigger poll, live exit = side bid then.

    Gap (bar opens at or above the target, not the entry bar): the first poll of the bar
    triggers, so paper = the open. Otherwise the trigger ask lies in [target, bar high]:
    optimistic paper takes the bar high (capped at 0.999), pessimistic the target.
    Live optimistic: one tick under the optimistic ask. Live pessimistic: the pessimistic
    ask minus the widest spread seen at the bar's open or close. That is the one price
    that is not a strict bar extreme: the strict floor (the bar's lowest bid) usually
    predates the trigger and would imply spreads of tens of cents. Tier B measures how
    often the real bid at the trigger was lower. Live prices stay inside the bar's bid range.
    """
    ao = a[0]
    bo, bh, bl, bc = b
    if not first and ao < FULL and ao >= target:
        paper = ao
    elif bound == "opt":
        paper = max(target, high_seen)
    else:
        paper = target
    paper = min(paper, FULL - 1)
    if bh <= 0:
        return paper, 1, True
    one_tick = paper - next_below(paper)
    spread = one_tick if bound == "opt" else max([one_tick] + _valid_spreads(a, b))
    live = _clamp(paper - spread, max(bl, 1), bh)
    return paper, live, bl <= 0


def _sl_prices(a, b, x: int, bound: str) -> Tuple[int, int, bool]:
    ao, ah, al, ac = a
    bo, bh, bl, bc = b
    nb = next_below(x)
    if bound == "opt":
        paper = min(nb, ao) if ao < x else nb
        paper = max(paper, al)
        if bh <= 0:
            return paper, 1, True
        live = _clamp(next_below(paper), max(bl, 1), bh)
        return paper, live, bl <= 0
    paper = al
    live = max(bl, 1)
    return paper, live, bl <= 0


def tier_a_scenarios(m: Market, p: Params, bound: str) -> List[Scenario]:
    """Every feasible path of the strategy through market m under the Tier A rules.

    bound 'opt' uses favorable intrabar prices, 'pes' unfavorable ones; the caller
    then keeps the best ('opt') or worst ('pes') path by the chosen model's PnL, which
    makes 'opt' resolve a same-bar TP/SL tie as TP-first and 'pes' as SL-first.
    Returns [] when the strategy never enters.
    """
    if bound not in ("opt", "pes"):
        raise ValueError("bound must be opt or pes")
    wbars, _ = window_bars(m, p)
    k0, options = find_entry_tier_a(wbars, p)
    if k0 is None:
        return []
    out: List[Scenario] = []
    n_entries = sum(len(prices) for _, _, prices in options)
    for side, mode, prices in options:
        for price in prices:
            out.extend(_tier_a_position(m, wbars, k0, side, mode, price, p, bound, n_entries > 1))
    return out


def _tier_a_position(m: Market, wbars: Sequence[Bar], k0: int, side: str, mode: str, e: int,
                     p: Params, bound: str, branched_entry: bool) -> List[Scenario]:
    x = p.x
    target = e + p.tp
    tp_on = target <= FULL
    win = side_wins(m.result, side)
    res: List[Scenario] = []
    stack: List[Tuple[int, bool, bool]] = [(k0, True, branched_entry)]
    while stack:
        k, first, br = stack.pop()
        if k >= len(wbars):
            v = FULL if win else 0
            res.append(Scenario(side, e, k0, mode, SETTLE_WIN if win else SETTLE_LOSS, None, v, v, br))
            continue
        a = side_ask(wbars[k], side)
        b = side_bid(wbars[k], side)
        high_seen = min(_valid_ask_high(a), FULL - 1)
        tp = NONE
        if tp_on and high_seen >= target:
            tp = POSSIBLE if (first and mode == "above") else CERTAIN
        sl = NONE
        if a[2] < x:
            sl = CERTAIN if a[3] < x else POSSIBLE
        opts = _bar_options(tp, sl)
        nbr = br or len(opts) > 1
        for o in opts:
            if o == TP:
                pp, lp, empty = _tp_prices(a, b, target, first, bound, high_seen)
                res.append(Scenario(side, e, k0, mode, TP, k, pp, lp, nbr, empty))
            elif o == SL:
                pp, lp, empty = _sl_prices(a, b, x, bound)
                res.append(Scenario(side, e, k0, mode, SL, k, pp, lp, nbr, empty))
            else:
                stack.append((k + 1, False, nbr))
    return res


def scenario_pnl(sc: Scenario, model: str, p: Params) -> float:
    if model == "paper":
        return paper_pnl(sc.entry, sc.paper_exit, p)
    return live_pnl(sc.entry, sc.live_exit, sc.exit_type, p)[0]


def select_scenario(scs: Sequence[Scenario], model: str, bound: str, p: Params) -> Optional[Scenario]:
    """Best path for 'opt', worst for 'pes', by the given model's PnL (ties: first)."""
    if not scs:
        return None
    best = None
    best_v = None
    for sc in scs:
        v = scenario_pnl(sc, model, p)
        if best is None or (v > best_v if bound == "opt" else v < best_v):
            best, best_v = sc, v
    return best


def tier_a_trade(m: Market, p: Params, model: str, bound: str) -> Optional[dict]:
    """Tier A trade record for one market, model ('paper'|'live') and bound ('opt'|'pes')."""
    scs = tier_a_scenarios(m, p, bound)
    sc = select_scenario(scs, model, bound, p)
    if sc is None:
        return None
    _, first_i = window_bars(m, p)
    entry_bar_start = m.open_ts + 60 * (first_i + sc.entry_bar)
    entry_ts = entry_bar_start if sc.entry_mode == "open" else entry_bar_start + 30
    if sc.exit_bar is None:
        exit_ts = m.close_ts + 10
    else:
        exit_ts = m.open_ts + 60 * (first_i + sc.exit_bar) + 30
    rec = scenario_record(m, sc, p, model)
    rec.update({
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "mins_left": (m.close_ts - entry_ts) / 60.0,
        "n_scenarios": len(scs),
    })
    return rec


def scenario_record(m: Market, sc: Scenario, p: Params, model: str) -> dict:
    pp = paper_pnl(sc.entry, sc.paper_exit, p)
    lp, ef, xf = live_pnl(sc.entry, sc.live_exit, sc.exit_type, p)
    return {
        "ticker": m.ticker,
        "close_ts": m.close_ts,
        "side": sc.side,
        "entry": sc.entry,
        "entry_mode": sc.entry_mode,
        "exit_type": sc.exit_type,
        "paper_exit": sc.paper_exit,
        "live_exit": sc.live_exit,
        "pnl_paper": pp,
        "pnl_live": lp,
        "fee_entry": ef,
        "fee_exit": xf,
        "pnl": pp if model == "paper" else lp,
        "branched": sc.branched,
        "live_bid_empty": sc.live_bid_empty,
    }


# ---------------------------------------------------------------------------
# Tier B: per-second top of book rebuilt from trade prints
# ---------------------------------------------------------------------------
@dataclass
class SecondPath:
    t0: int
    ask: List[int]          # YES ask per second (1000 = no YES asks)
    bid: List[int]          # YES bid per second (0 = no YES bids)
    diag: dict = field(default_factory=dict)

    def at(self, s: int) -> Tuple[int, int]:
        i = s - self.t0
        return self.ask[i], self.bid[i]


def _bar_at(m: Market, s: int) -> Optional[Bar]:
    i = (s - m.open_ts) // 60
    if 0 <= i < len(m.bars):
        return m.bars[i]
    return None


def reconstruct_path(prints: Sequence[dict], m: Market, t0: int, t1: int) -> SecondPath:
    """Per-second YES bid/ask for seconds [t0, t1), each the book at the start of the second.

    A print with taker_book_side 'bid' (taker bought YES) executed against a resting YES
    ask, so its yes_price reveals the YES ask; 'ask' (taker sold YES / bought NO) executed
    against a resting YES bid and reveals the YES bid. At each minute boundary both sides
    take the candle open (the quote at that instant); otherwise the last print of each
    kind in the previous second sets that side and a side with no print carries forward.
    Every value is clamped into the candle's [low, high] for its minute, and a crossed
    book is uncrossed by moving the staler side one tick away from the fresher one.
    Quote changes that no trade printed (flickers, pulled liquidity) stay invisible here;
    the diag counts minutes where the candle saw such an extreme.

    Tie rule: a taker order that sweeps several price levels prints every level with one
    timestamp, and the order the API lists those prints in (by trade id, a UUID) is not
    execution order. Prints that share a timestamp are therefore ordered by price priority:
    YES-ask prints ascending (a buyer lifts the cheapest ask first) and YES-bid prints
    descending (a seller hits the highest bid first). The print that sets the book for the
    next second is then the deepest level the sweep reached.
    """
    evs = []
    for idx, tr in enumerate(prints):
        try:
            ts = iso_to_ts(tr["created_time"])
            px = milli(tr["yes_price_dollars"])
            kind = tr.get("taker_book_side")
            if kind not in ("bid", "ask"):
                kind = "bid" if tr.get("taker_outcome_side", tr.get("taker_side")) == "yes" else "ask"
        except (KeyError, TypeError, ValueError):
            continue
        # price priority within a timestamp: ascending for YES-ask prints, descending for
        # YES-bid prints; the input position only breaks ties between equal prices
        evs.append((ts, kind, px if kind == "bid" else -px, idx, px))
    evs.sort()
    last_ask: Dict[int, int] = {}
    last_bid: Dict[int, int] = {}
    minute_ext: Dict[Tuple[int, str], List[int]] = {}  # (bar index, side) -> [min print, max print]
    n_ask = n_bid = in_rng_ask = in_rng_bid = near_ask = near_bid = 0
    for ts, kind, _, _, px in evs:
        s = int(math.floor(ts))
        if s < t0 or s >= t1:
            continue
        bar = _bar_at(m, s)
        ext = minute_ext.setdefault(((s - m.open_ts) // 60, kind), [px, px])
        ext[0] = min(ext[0], px)
        ext[1] = max(ext[1], px)
        if kind == "bid":
            last_ask[s] = px
            n_ask += 1
            if bar is not None:
                lo_, hi_ = bar.ya[2], bar.ya[1]
                in_rng_ask += lo_ <= px <= hi_
                near_ask += (next_below(lo_) <= px <= next_above(hi_))
        else:
            last_bid[s] = px
            n_bid += 1
            if bar is not None:
                lo_, hi_ = bar.yb[2], bar.yb[1]
                in_rng_bid += lo_ <= px <= hi_
                near_bid += (next_below(lo_) <= px <= next_above(hi_))
    # quote extremes the candle saw but no print reached (by more than one tick): moves
    # the strategy could observe by polling the book but a print-based path cannot show
    unprinted = {"ask_high": 0, "ask_low": 0, "bid_high": 0, "bid_low": 0, "ask_minutes": 0, "bid_minutes": 0}
    for (i, kind), (mn, mx) in minute_ext.items():
        if not (0 <= i < len(m.bars)) or m.bars[i] is None:
            continue
        o, h, l, c = m.bars[i].ya if kind == "bid" else m.bars[i].yb
        name = "ask" if kind == "bid" else "bid"
        unprinted[name + "_minutes"] += 1
        if 0 < h < FULL and h > next_above(mx):
            unprinted[name + "_high"] += 1
        if 0 < l < FULL and l < next_below(mn):
            unprinted[name + "_low"] += 1

    ask, bid = FULL, 0
    ua = ub = t0 - 1
    asks: List[int] = []
    bids: List[int] = []
    clamps = uncross = boundary_sets = 0
    age_a = age_b = 0
    for s in range(t0, t1):
        # value for second s = the book at the START of s: the candle open at a minute
        # boundary (the same convention Tier A uses), else the last print of second s-1
        bar = _bar_at(m, s)
        at_boundary = (s - m.open_ts) % 60 == 0 or s == t0
        if bar is not None and at_boundary:
            ask, bid, ua, ub = bar.ya[0], bar.yb[0], s, s
            boundary_sets += 1
        else:
            if (s - 1) in last_ask:
                ask, ua = last_ask[s - 1], s - 1
            if (s - 1) in last_bid:
                bid, ub = last_bid[s - 1], s - 1
        if bar is not None:
            ca = _clamp(ask, bar.ya[2], bar.ya[1])
            cb = _clamp(bid, bar.yb[2], bar.yb[1])
            clamps += (ca != ask) + (cb != bid)
            ask, bid = ca, cb
        if 0 < bid < FULL and 0 < ask < FULL and bid >= ask:
            uncross += 1
            if ua >= ub:
                bid = next_below(ask)
            else:
                ask = next_above(bid)
        age_a += s - ua
        age_b += s - ub
        asks.append(ask)
        bids.append(bid)
    n = max(1, t1 - t0)
    diag = {
        "prints_ask": n_ask,
        "prints_bid": n_bid,
        "ask_prints_in_candle_range": in_rng_ask,
        "bid_prints_in_candle_range": in_rng_bid,
        "ask_prints_within_1_tick": near_ask,
        "bid_prints_within_1_tick": near_bid,
        "seconds": t1 - t0,
        "seconds_with_ask_print": len(last_ask),
        "seconds_with_bid_print": len(last_bid),
        "clamps": clamps,
        "uncrossed_seconds": uncross,
        "boundary_sets": boundary_sets,
        "mean_ask_age_s": age_a / n,
        "mean_bid_age_s": age_b / n,
        "minutes_with_ask_prints": unprinted["ask_minutes"],
        "minutes_with_bid_prints": unprinted["bid_minutes"],
        "minutes_candle_ask_high_beyond_prints": unprinted["ask_high"],
        "minutes_candle_ask_low_beyond_prints": unprinted["ask_low"],
        "minutes_candle_bid_high_beyond_prints": unprinted["bid_high"],
        "minutes_candle_bid_low_beyond_prints": unprinted["bid_low"],
    }
    return SecondPath(t0, asks, bids, diag)


def _bot_views(path: SecondPath) -> Tuple[List[int], List[int]]:
    """What the strategy's UP and DOWN ask values hold each second (stale when a book side is empty)."""
    up_v: List[int] = []
    dn_v: List[int] = []
    up = dn = None
    for a, b in zip(path.ask, path.bid):
        if a < FULL:
            up = a
        if b > 0:
            dn = FULL - b
        up_v.append(up if up is not None else FULL)
        dn_v.append(dn if dn is not None else FULL)
    return up_v, dn_v


def simulate_seconds(path: SecondPath, m: Market, p: Params, model: str) -> Optional[dict]:
    """Poll the strategy about once per second (one simulated poll per second).

    Entry: polls at minutes left in (0, time_delay_min]; UP if UP ask in range (and DOWN
    not in range or up >= down), else DOWN if DOWN ask in range; fill = that ask.
    Position: first check 1 s after entry (paper) or live_monitor_delay_s (live: a 2 s
    pause plus order, position and fill lookups). Each poll: if side ask < exit_below,
    wait 1 s per ping and require it still < exit_below (else skip to the next poll); a
    confirmed stop exits at that second's ask (paper) or side bid (live immediate-or-cancel
    sell at 1c). Otherwise, if the target is enabled (fill + take_profit_pts <= 1) and side
    ask >= target, exit at that ask (paper) or side bid (live). Otherwise hold to
    settlement (1 or 0, no fee).
    """
    T = m.close_ts
    ws = T - p.window_s
    up_v, dn_v = _bot_views(path)
    lo, hi, x = p.lo, p.hi, p.x

    def idx(s):
        return s - path.t0

    s = max(ws, path.t0)
    side = None
    e = None
    s_entry = None
    while s < T - p.floor_s:   # minimum minutes left: entries stop floor_s before the close
        up, dn = up_v[idx(s)], dn_v[idx(s)]
        up_in = lo <= up <= hi
        dn_in = lo <= dn <= hi
        if up_in and (not dn_in or up >= dn):
            side, e = UP, up
        elif dn_in:
            side, e = DOWN, dn
        if side is not None:
            s_entry = s
            break
        s += 1
    if side is None:
        return None

    target = e + p.tp
    tp_on = target <= FULL
    delay = 1 if model == "paper" else max(1, int(p.live_monitor_delay_s))

    def side_ask_at(t):
        return up_v[idx(t)] if side == UP else dn_v[idx(t)]

    def side_bid_at(t):
        a, b = path.at(t)
        return b if side == UP else (FULL - a if a < FULL else 0)

    exit_type = None
    exit_s = None
    bid_wait = 0
    s = s_entry + delay
    while s < T:
        cur = side_ask_at(s)
        if cur < x:
            ok = True
            t = s
            for _ in range(max(0, int(p.pings))):
                t += 1
                if t >= T or side_ask_at(t) >= x:
                    ok = False
                    break
            if ok:
                exit_type, exit_s = SL, t
                break
            s = t + 1
            continue
        if tp_on and cur >= target:
            exit_type, exit_s = TP, s
            break
        s += 1

    win = side_wins(m.result, side)
    settle_type = SETTLE_WIN if win else SETTLE_LOSS
    settle_px = FULL if win else 0
    paper_type = live_type = settle_type
    paper_px = live_px = settle_px
    paper_exit_s = live_exit_s = T + 10
    if exit_type is not None:
        paper_type, paper_px, paper_exit_s = exit_type, side_ask_at(exit_s), exit_s
        u = exit_s
        while u < T and side_bid_at(u) <= 0:
            u += 1
        if u < T:  # live IoC sell needs a bid; with none before close it rides to settlement
            live_type, live_px, live_exit_s = exit_type, side_bid_at(u), u
            bid_wait = u - exit_s
    pp = paper_pnl(e, paper_px, p)
    lp, ef, xf = live_pnl(e, live_px, live_type, p)
    is_paper = model == "paper"
    return {
        "ticker": m.ticker,
        "close_ts": m.close_ts,
        "side": side,
        "entry": e,
        "entry_ts": s_entry,
        "mins_left": (T - s_entry) / 60.0,
        "exit_type": paper_type if is_paper else live_type,
        "exit_ts": paper_exit_s if is_paper else live_exit_s,
        "paper_exit": paper_px,
        "live_exit": live_px,
        "pnl_paper": pp,
        "pnl_live": lp,
        "fee_entry": ef,
        "fee_exit": xf,
        "pnl": pp if is_paper else lp,
        "live_bid_wait_s": bid_wait,
    }


# ---------------------------------------------------------------------------
# portfolio metrics
# ---------------------------------------------------------------------------
def entry_bucket(entry: int) -> str:
    if 800 <= entry <= 849:
        return "0.80-0.84"
    if 850 <= entry <= 899:
        return "0.85-0.89"
    if 900 <= entry <= 930:
        return "0.90-0.93"
    return "other"


def mins_left_bucket(mins_left: float) -> str:
    k = int(math.ceil(round(mins_left, 6)))
    return "%d-%d" % (k - 1, k)


def iso_week(ts: float) -> str:
    d = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date()
    monday = d - _dt.timedelta(days=d.weekday())
    return "week of %s" % monday.isoformat()


def utc_day(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date().isoformat()


def max_drawdown(pnls_in_time_order: Sequence[float]) -> float:
    peak = 0.0
    cum = 0.0
    mdd = 0.0
    for v in pnls_in_time_order:
        cum += v
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def stats(trades: Sequence[dict], key: str = "pnl") -> dict:
    """Trades, win rate (pnl > 0 is a win), avg win, avg loss, PnL per
    trade, total, max drawdown (on cumulative closed PnL in exit order), break-even win
    rate = |avg loss| / (avg win + |avg loss|)."""
    ts = sorted(trades, key=lambda t: (t["exit_ts"], t["close_ts"]))
    pnls = [t[key] for t in ts]
    n = len(pnls)
    wins = [v for v in pnls if v > 0]
    losses = [v for v in pnls if v <= 0]
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    be = (abs(avg_l) / (avg_w + abs(avg_l))) if (wins and losses and (avg_w + abs(avg_l)) > 0) else None
    mean = (sum(pnls) / n) if n else 0.0
    sd = math.sqrt(sum((v - mean) ** 2 for v in pnls) / (n - 1)) if n > 1 else 0.0
    return {
        "trades": n,
        "wins": len(wins),
        "win_rate": (len(wins) / n) if n else 0.0,
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "pnl_per_trade": mean,
        "total_pnl": sum(pnls),
        "max_drawdown": max_drawdown(pnls),
        "breakeven_win_rate": be,
        "pnl_stdev": sd,
        "pnl_per_trade_se": (sd / math.sqrt(n)) if n > 1 else 0.0,
    }


def breakdown(trades: Sequence[dict], keyfunc, key: str = "pnl") -> Dict[str, dict]:
    groups: Dict[str, List[dict]] = {}
    for t in trades:
        groups.setdefault(keyfunc(t), []).append(t)
    return {k: stats(v, key) for k, v in sorted(groups.items())}


def apply_daily_limits(trades: Sequence[dict], loss_target: float, profit_target: float,
                       mode: str = "utc_day", key: str = "pnl") -> Tuple[List[dict], List[dict]]:
    """Replay the daily stop. After each closed trade (the strategy is flat then), if the
    accumulated closed PnL is < loss_target (when loss_target < 0) or > profit_target
    (when > 0) the strategy stops. mode 'utc_day': accumulation resets and trading resumes
    at the next UTC day (a per-UTC-day reading of the limit). mode 'since_start':
    accumulation runs from the first trade and the stop is permanent (the reading in which
    the starting balance is recorded once and the run ends when a target is crossed).
    Returns (kept trades, stop events).
    """
    ts = sorted(trades, key=lambda t: (t["entry_ts"], t["close_ts"]))
    kept: List[dict] = []
    events: List[dict] = []
    acc = 0.0
    cur_day = None
    stopped_until_day = None
    stopped_forever = False
    for t in ts:
        d = utc_day(t["entry_ts"])
        if mode == "utc_day" and d != cur_day:
            cur_day = d
            acc = 0.0
        if stopped_forever:
            continue
        if stopped_until_day is not None and d == stopped_until_day:
            continue
        stopped_until_day = None
        kept.append(t)
        acc += t[key]
        hit = None
        if loss_target < 0 and acc < loss_target:
            hit = "loss"
        elif profit_target > 0 and acc > profit_target:
            hit = "profit"
        if hit:
            events.append({"day": d, "type": hit, "after_ticker": t["ticker"], "accumulated": acc,
                           "stop_ts": t["exit_ts"]})
            if mode == "utc_day":
                stopped_until_day = d
            else:
                stopped_forever = True
    return kept, events


def select_tierb_sample(tickers_by_close: Sequence[str], step: int = 30, offset: int = 15) -> List[str]:
    """Systematic sample spread evenly over the period (every step-th market)."""
    return list(tickers_by_close[offset::step])


def select_ambiguous_supplement(ambiguous_by_close: Sequence[str], exclude: Sequence[str], n: int) -> List[str]:
    """Evenly spaced pick of n markets whose Tier A bounds differ, excluding a set."""
    ex = set(exclude)
    pool = [t for t in ambiguous_by_close if t not in ex]
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        return pool
    step = len(pool) / float(n)
    return [pool[int(i * step)] for i in range(n)]
