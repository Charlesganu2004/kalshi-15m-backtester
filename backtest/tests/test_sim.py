"""Unit tests for sim.py on synthetic paths. Run: python -m pytest tests -q
(or python -m unittest discover -s tests)."""
import datetime as dt
import itertools
import os
import sys
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sim  # noqa: E402

OPEN = 1790117100          # 2026-09-22T21:45:00Z, a quarter-hour boundary
CLOSE = OPEN + 900
P = sim.Params()


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _d(v):
    return "%.4f" % v


def candle(end, ya, yb):
    return {"end_period_ts": end,
            "yes_ask": dict(zip(("open_dollars", "high_dollars", "low_dollars", "close_dollars"), map(_d, ya))),
            "yes_bid": dict(zip(("open_dollars", "high_dollars", "low_dollars", "close_dollars"), map(_d, yb)))}


def flat(ask, bid):
    return ((ask,) * 4, (bid,) * 4)


NEUTRAL = flat(0.51, 0.50)


def make_market(window, result="yes", pre=None, drop_minutes=()):
    """window: 8 (ya OHLC, yb OHLC) tuples for the last 8 minutes; pre: 7 bars before."""
    bars = list(pre or [NEUTRAL] * 7) + list(window)
    assert len(bars) == 15
    cs = [candle(OPEN + 60 * (i + 1), ya, yb) for i, (ya, yb) in enumerate(bars) if i not in drop_minutes]
    meta = {"ticker": "KXBTC15M-TEST", "open_time": iso(OPEN), "close_time": iso(CLOSE), "result": result}
    return sim.parse_market(meta, {"candlesticks": cs})


def trade(m, model, bound, p=P):
    return sim.tier_a_trade(m, p, model, bound)


def fee(price, contracts=1, action="buy", precision="centicent"):
    return float(sim.taker_fee(price, contracts, action, precision))


def seconds_path(segments, t0=CLOSE - 600):
    """segments: list of (n_seconds, yes_ask, yes_bid) in dollars, covering 600 seconds."""
    ask, bid = [], []
    for n, a, b in segments:
        ask += [sim.milli(a)] * n
        bid += [sim.milli(b)] * n
    assert len(ask) == 600, len(ask)
    return sim.SecondPath(t0, ask, bid)


class TestGridAndFees(unittest.TestCase):
    def test_grid_steps(self):
        self.assertEqual(sim.next_below(400), 390)
        self.assertEqual(sim.next_below(100), 99)
        self.assertEqual(sim.next_below(950), 949)
        self.assertEqual(sim.next_below(901), 900)
        self.assertEqual(sim.next_below(900), 890)
        self.assertEqual(sim.next_above(890), 900)
        self.assertEqual(sim.next_above(900), 901)
        self.assertEqual(sim.next_above(99), 100)
        self.assertEqual(sim.next_above(100), 110)

    def test_fee_examples_direct_member(self):
        # research worked examples (July 7, 2026 schedule, centi-cent balance precision)
        self.assertAlmostEqual(fee(850), 0.0090, places=10)
        self.assertAlmostEqual(fee(920), 0.0052, places=10)
        self.assertAlmostEqual(fee(950, action="sell"), 0.0034, places=10)
        self.assertAlmostEqual(fee(350, action="sell"), 0.0160, places=10)
        self.assertAlmostEqual(fee(50, action="sell"), 0.0034, places=10)
        self.assertAlmostEqual(fee(850, 10), 0.0893, places=10)
        self.assertAlmostEqual(fee(800), 0.0112, places=10)
        self.assertAlmostEqual(fee(870), 0.0080, places=10)
        self.assertAlmostEqual(fee(930), 0.0046, places=10)
        self.assertAlmostEqual(fee(925), 0.0049, places=10)
        self.assertAlmostEqual(fee(400, action="sell"), 0.0168, places=10)
        self.assertAlmostEqual(fee(995, action="sell"), 0.0004, places=10)

    def test_fee_examples_cent_rounding(self):
        self.assertAlmostEqual(fee(850, precision="cent"), 0.01, places=10)
        self.assertAlmostEqual(fee(920, precision="cent"), 0.01, places=10)
        self.assertAlmostEqual(fee(950, action="sell", precision="cent"), 0.01, places=10)
        self.assertAlmostEqual(fee(350, action="sell", precision="cent"), 0.02, places=10)
        self.assertAlmostEqual(fee(50, action="sell", precision="cent"), 0.01, places=10)
        self.assertAlmostEqual(fee(850, 10, precision="cent"), 0.09, places=10)

    def test_fee_round_trips_from_research(self):
        self.assertAlmostEqual(sim.live_pnl(850, 950, sim.TP, P)[0], 0.0876, places=10)
        self.assertAlmostEqual(sim.live_pnl(850, 1000, sim.SETTLE_WIN, P)[0], 0.1410, places=10)
        self.assertAlmostEqual(sim.live_pnl(850, 400, sim.SL, P)[0], -0.4758, places=10)
        self.assertAlmostEqual(sim.live_pnl(850, 350, sim.SL, P)[0], -0.5250, places=10)
        self.assertAlmostEqual(sim.live_pnl(930, 1000, sim.SETTLE_WIN, P)[0], 0.0654, places=10)
        self.assertAlmostEqual(sim.live_pnl(800, 900, sim.TP, P)[0], 0.0825, places=10)
        # settlement is fee-free on the exit leg
        self.assertAlmostEqual(sim.live_pnl(850, 0, sim.SETTLE_LOSS, P)[2], 0.0, places=10)
        self.assertEqual(sim.taker_fee(850, precision="none"), Fraction(0))

    def test_paper_has_no_fees(self):
        self.assertAlmostEqual(sim.paper_pnl(850, 950, P), 0.10, places=10)


class TestParsing(unittest.TestCase):
    def test_missing_minute_forward_filled(self):
        window = [flat(0.60, 0.59)] * 8
        m = make_market(window, drop_minutes=(9,))
        self.assertEqual(m.missing_minutes, [9])
        b = m.bars[9]
        self.assertTrue(b.synthetic)
        self.assertEqual(b.ya, (600, 600, 600, 600))

    def test_bar_alignment(self):
        m = make_market([flat(0.60, 0.59)] * 8)
        self.assertEqual(len(m.bars), 15)
        self.assertEqual(m.bars[0].start, OPEN)
        wb, first = sim.window_bars(m, P)
        self.assertEqual(len(wb), 8)
        self.assertEqual(first, 7)
        self.assertEqual(wb[0].start, CLOSE - 480)


class TestTierA(unittest.TestCase):
    def test_no_entry(self):
        m = make_market([((0.65, 0.70, 0.60, 0.66), (0.64, 0.69, 0.59, 0.65))] * 8)
        self.assertEqual(sim.tier_a_scenarios(m, P, "opt"), [])
        for model in ("paper", "live"):
            for b in ("opt", "pes"):
                self.assertIsNone(trade(m, model, b))

    def test_up_entry_then_tp(self):
        w = [((0.85, 0.86, 0.84, 0.86), (0.84, 0.85, 0.83, 0.85)),
             ((0.86, 0.95, 0.86, 0.95), (0.85, 0.949, 0.85, 0.949))] + [flat(0.97, 0.969)] * 6
        m = make_market(w, result="yes")
        for b in ("opt", "pes"):
            t = trade(m, "paper", b)
            self.assertEqual((t["side"], t["entry"], t["exit_type"], t["paper_exit"]), ("UP", 850, sim.TP, 950))
            self.assertAlmostEqual(t["pnl"], 0.10, places=10)
        lo = trade(m, "live", "opt")
        self.assertEqual(lo["live_exit"], 949)          # one tick under the 0.95 trigger ask
        self.assertAlmostEqual(lo["pnl"], 0.949 - 0.85 - 0.0090 - 0.0034, places=10)
        lp = trade(m, "live", "pes")
        self.assertEqual(lp["live_exit"], 940)          # target minus the widest bar spread (0.86 - 0.85)
        self.assertAlmostEqual(lp["pnl"], 0.94 - 0.85 - 0.0090 - fee(940, action="sell"), places=10)
        self.assertEqual(lp["mins_left"], 8.0)

    def test_down_price_conversions(self):
        bar = sim.Bar(0, (160, 170, 150, 160), (150, 160, 140, 150))
        self.assertEqual(sim.side_ask(bar, sim.DOWN), (850, 860, 840, 850))   # 1 - YES bid
        self.assertEqual(sim.side_bid(bar, sim.DOWN), (840, 850, 830, 840))   # 1 - YES ask
        self.assertEqual(sim.side_ask(bar, sim.UP), (160, 170, 150, 160))
        self.assertEqual(sim.side_bid(bar, sim.UP), (150, 160, 140, 150))
        w = [((0.16, 0.17, 0.15, 0.16), (0.15, 0.16, 0.14, 0.15))] + [flat(0.10, 0.09)] * 7
        m = make_market(w, result="no")
        t = trade(m, "paper", "pes")
        self.assertEqual((t["side"], t["entry"], t["exit_type"]), ("DOWN", 850, sim.SETTLE_WIN))
        self.assertAlmostEqual(t["pnl"], 0.15, places=10)
        tl = trade(m, "live", "pes")
        self.assertAlmostEqual(tl["pnl"], 0.15 - 0.0090, places=10)

    def test_entry_crossing_boundaries(self):
        # from below: UP ask opens 0.75 and trades up to 0.82 -> first in-range poll saw
        # something in [0.80, 0.82]: optimistic 0.80 (boundary), pessimistic 0.82 (bar high)
        w = [((0.75, 0.82, 0.74, 0.82), (0.74, 0.81, 0.73, 0.81))] + [flat(0.83, 0.82)] * 7
        m = make_market(w)
        t = trade(m, "paper", "opt")
        self.assertEqual((t["side"], t["entry"], t["entry_mode"]), ("UP", 800, "below"))
        self.assertAlmostEqual(t["mins_left"], 7.5)
        self.assertEqual(trade(m, "paper", "pes")["entry"], 820)
        # from above: UP ask opens 0.96 and falls to 0.90 -> [0.90, 0.93]
        w = [((0.96, 0.96, 0.90, 0.91), (0.95, 0.95, 0.89, 0.90))] + [flat(0.92, 0.91)] * 7
        m = make_market(w)
        t = trade(m, "paper", "opt")
        self.assertEqual((t["side"], t["entry"], t["entry_mode"]), ("UP", 900, "above"))
        self.assertEqual(trade(m, "paper", "pes")["entry"], 930)

    def test_tp_prices_within_bar(self):
        # the ask jumps past the 0.95 target to a 0.97 high inside the bar
        w = [flat(0.85, 0.84), ((0.86, 0.97, 0.86, 0.96), (0.85, 0.965, 0.85, 0.955))] + [flat(0.96, 0.955)] * 6
        m = make_market(w, result="yes")
        self.assertEqual(trade(m, "paper", "opt")["paper_exit"], 970)   # bar high
        self.assertEqual(trade(m, "paper", "pes")["paper_exit"], 950)   # the target itself
        self.assertEqual(trade(m, "live", "opt")["live_exit"], 965)     # capped by the bar's best bid
        self.assertEqual(trade(m, "live", "pes")["live_exit"], 940)     # target minus widest spread (0.01)

    def test_interior_entry_price_is_a_branch(self):
        # UP ask crosses 0.80 inside bar 0 (high 0.84), so the entry is anywhere in 0.80-0.84.
        # 0.80: target 0.90, hit in bar 1 at 0.90 -> +0.10. 0.84: target 0.94, never hit,
        # settles at 0 -> -0.84. 0.81: target 0.91, hit in bar 2 whose high is 0.925 -> +0.115.
        # The best path uses an interior price, so every grid price is a branch.
        w = [((0.75, 0.84, 0.74, 0.79), (0.74, 0.83, 0.73, 0.78)),
             ((0.79, 0.90, 0.79, 0.85), (0.78, 0.89, 0.78, 0.84)),
             ((0.85, 0.925, 0.85, 0.92), (0.84, 0.92, 0.84, 0.915))] + [flat(0.90, 0.899)] * 5
        m = make_market(w, result="no")
        self.assertEqual(sim.grid_between(800, 840), [800, 810, 820, 830, 840])
        t = trade(m, "paper", "opt")
        self.assertEqual((t["entry"], t["exit_type"], t["paper_exit"]), (810, sim.TP, 925))
        self.assertAlmostEqual(t["pnl"], 0.115, places=10)
        t = trade(m, "paper", "pes")
        self.assertEqual((t["entry"], t["exit_type"]), (840, sim.SETTLE_LOSS))

    def test_both_sides_cross_in_same_bar_are_branches(self):
        # UP ask 0.70 -> 0.85 and YES bid 0.30 -> 0.10 (DOWN ask 0.70 -> 0.90) inside one minute
        w = [((0.70, 0.85, 0.11, 0.50), (0.30, 0.84, 0.10, 0.49))] + [flat(0.51, 0.50)] * 7
        scs = sim.tier_a_scenarios(make_market(w), P, "opt")
        self.assertEqual({s.side for s in scs}, {"UP", "DOWN"})

    def test_both_sides_in_range_at_open_follow_code_rule(self):
        # a wide book: YES ask 0.88, YES bid 0.15 -> UP ask 0.88, DOWN ask 0.85 -> UP (up >= down)
        w = [flat(0.88, 0.15)] + [flat(0.88, 0.15)] * 7
        t = trade(make_market(w), "paper", "opt")
        self.assertEqual((t["side"], t["entry"]), ("UP", 880))

    def test_sl_confirmed_when_bar_closes_below_exit(self):
        w = [flat(0.85, 0.84), ((0.84, 0.84, 0.30, 0.35), (0.83, 0.83, 0.29, 0.34))] + [flat(0.35, 0.34)] * 6
        m = make_market(w, result="yes")
        for b in ("opt", "pes"):
            t = trade(m, "paper", b)
            self.assertEqual(t["exit_type"], sim.SL)
        self.assertEqual(trade(m, "paper", "opt")["paper_exit"], 390)
        self.assertEqual(trade(m, "paper", "pes")["paper_exit"], 300)
        self.assertEqual(trade(m, "live", "opt")["live_exit"], 380)
        self.assertEqual(trade(m, "live", "pes")["live_exit"], 290)

    def test_sl_rebound_is_ambiguous_in_tier_a(self):
        # dips to 0.35 but closes back at 0.60 -> the 1 s confirmation is unknown
        # later bars stay under the 0.95 target so the only open question is the stop
        w = [flat(0.85, 0.84), ((0.84, 0.84, 0.35, 0.60), (0.83, 0.83, 0.34, 0.59))] + [flat(0.93, 0.929)] * 6
        m = make_market(w, result="yes")  # the trade would win if not stopped
        self.assertEqual(trade(m, "paper", "opt")["exit_type"], sim.SETTLE_WIN)
        self.assertEqual(trade(m, "paper", "pes")["exit_type"], sim.SL)
        self.assertEqual(trade(m, "paper", "pes")["paper_exit"], 350)
        m2 = make_market(w[:2] + [flat(0.45, 0.44)] * 6, result="no")  # would lose if not stopped
        self.assertEqual(trade(m2, "paper", "opt")["exit_type"], sim.SL)
        self.assertEqual(trade(m2, "paper", "pes")["exit_type"], sim.SETTLE_LOSS)

    def test_tp_and_sl_in_same_bar(self):
        for close in (0.36, 0.60):
            w = [flat(0.85, 0.84), ((0.85, 0.95, 0.35, close), (0.84, 0.949, 0.34, close - 0.01))] + [flat(close, close - 0.01)] * 6
            m = make_market(w, result="no")
            for model in ("paper", "live"):
                self.assertEqual(trade(m, model, "opt")["exit_type"], sim.TP, (close, model))
                self.assertEqual(trade(m, model, "pes")["exit_type"], sim.SL, (close, model))

    def test_settle_win_and_loss(self):
        w = [flat(0.85, 0.84)] + [flat(0.88, 0.87)] * 7
        win = trade(make_market(w, result="yes"), "paper", "opt")
        self.assertEqual(win["exit_type"], sim.SETTLE_WIN)
        self.assertAlmostEqual(win["pnl"], 0.15, places=10)
        loss = trade(make_market(w, result="no"), "live", "pes")
        self.assertEqual(loss["exit_type"], sim.SETTLE_LOSS)
        self.assertAlmostEqual(loss["pnl"], -0.85 - 0.0090, places=10)

    def test_fill_above_090_disables_tp(self):
        w = [flat(0.91, 0.909)] + [((0.95, 0.999, 0.95, 0.999), (0.949, 0.998, 0.949, 0.998))] * 7
        t = trade(make_market(w, result="yes"), "paper", "opt")
        self.assertEqual((t["entry"], t["exit_type"]), (910, sim.SETTLE_WIN))

    def test_fill_at_090_never_sees_target_1(self):
        # target 1.00 is allowed by the rules (<= 1) but the strategy can never observe an ask of 1.00
        w = [flat(0.90, 0.899)] + [((0.99, 1.0, 0.99, 1.0), (0.989, 0.999, 0.989, 0.999))] * 7
        t = trade(make_market(w, result="yes"), "paper", "opt")
        self.assertEqual((t["entry"], t["exit_type"]), (900, sim.SETTLE_WIN))

    def test_one_trade_per_market(self):
        w = [flat(0.85, 0.84), ((0.85, 0.95, 0.85, 0.95), (0.84, 0.949, 0.84, 0.949)),
             flat(0.85, 0.84), flat(0.82, 0.81)] + [flat(0.85, 0.84)] * 4
        m = make_market(w, result="no")
        scs = sim.tier_a_scenarios(m, P, "pes")
        self.assertEqual(len(scs), 1)
        self.assertEqual(scs[0].exit_type, sim.TP)
        self.assertEqual(scs[0].exit_bar, 1)


class TestTierB(unittest.TestCase):
    def _mkt(self, result="yes"):
        return make_market([flat(0.85, 0.84)] * 8, result=result)

    def test_reconstruct_from_prints(self):
        m = make_market([flat(0.85, 0.84)] * 8)
        t0 = CLOSE - 120

        def pr(ts, side, px):
            return {"created_time": iso(ts)[:-1] + ".250000Z", "yes_price_dollars": "%.4f" % px,
                    "taker_book_side": side}
        prints = [pr(t0 + 5, "bid", 0.85), pr(t0 + 5, "ask", 0.84), pr(t0 + 10, "ask", 0.86)]
        prints.reverse()  # API returns newest first
        sp = sim.reconstruct_path(prints, m, t0, t0 + 20)
        self.assertEqual(sp.at(t0), (850, 840))       # minute boundary: candle open
        self.assertEqual(sp.at(t0 + 6), (850, 840))   # after the second-5 prints
        # a YES bid print at 0.86 above the stale 0.85 ask is clamped into the candle range
        # (bid <= 0.84 in this minute), so the book stays uncrossed
        a, b = sp.at(t0 + 11)
        self.assertLess(b, a)
        self.assertEqual(sp.diag["prints_ask"], 1)
        self.assertEqual(sp.diag["prints_bid"], 2)
        self.assertEqual(sp.diag["bid_prints_in_candle_range"], 1)

    def test_uncross_moves_stale_side(self):
        m = make_market([((0.80, 0.90, 0.70, 0.80), (0.79, 0.89, 0.69, 0.79))] * 8)
        t0 = CLOSE - 120

        def pr(ts, side, px):
            return {"created_time": iso(ts)[:-1] + ".100000Z", "yes_price_dollars": "%.4f" % px,
                    "taker_book_side": side}
        prints = [pr(t0 + 2, "bid", 0.80), pr(t0 + 2, "ask", 0.79), pr(t0 + 6, "bid", 0.75)]
        sp = sim.reconstruct_path(list(reversed(prints)), m, t0, t0 + 10)
        self.assertEqual(sp.at(t0 + 6), (800, 790))  # book at the start of second 6, before its print
        self.assertEqual(sp.at(t0 + 7), (750, 740))  # stale bid 0.79 pushed one tick under the fresh 0.75 ask

    def test_same_timestamp_sweep_uses_price_priority(self):
        # a taker that sweeps several levels prints them all with one timestamp, and the API's
        # order inside that timestamp is not execution order, so the deepest level must win
        # whatever order the prints arrive in
        m = make_market([((0.80, 0.90, 0.70, 0.80), (0.79, 0.89, 0.69, 0.79))] * 8)
        t0 = CLOSE - 120

        def pr(side, px):
            return {"created_time": iso(t0 + 2)[:-1] + ".500000Z", "yes_price_dollars": "%.4f" % px,
                    "taker_book_side": side}
        # YES-ask prints (taker buys YES, lifting asks upward); YES-bid prints (taker sells YES, hitting bids downward)
        prints = [pr("bid", 0.80), pr("bid", 0.82), pr("bid", 0.81),
                  pr("ask", 0.78), pr("ask", 0.76), pr("ask", 0.77)]
        for order in itertools.permutations(prints):
            sp = sim.reconstruct_path(list(order), m, t0, t0 + 6)
            self.assertEqual(sp.at(t0 + 3), (820, 760), order)   # deepest ask 0.82, deepest bid 0.76

    def test_price_priority_does_not_reorder_different_timestamps(self):
        m = make_market([((0.80, 0.90, 0.70, 0.80), (0.79, 0.89, 0.69, 0.79))] * 8)
        t0 = CLOSE - 120

        def pr(micro, side, px):
            return {"created_time": iso(t0 + 2)[:-1] + ".%06dZ" % micro, "yes_price_dollars": "%.4f" % px,
                    "taker_book_side": side}
        # the later print is cheaper on the ask side and dearer on the bid side, and it still wins
        prints = [pr(100, "bid", 0.82), pr(200, "bid", 0.80), pr(100, "ask", 0.76), pr(200, "ask", 0.78)]
        for order in itertools.permutations(prints):
            sp = sim.reconstruct_path(list(order), m, t0, t0 + 6)
            self.assertEqual(sp.at(t0 + 3), (800, 780), order)

    def test_up_entry_then_tp_per_second(self):
        segs = [(120, 0.60, 0.59), (10, 0.85, 0.84), (20, 0.90, 0.89), (450, 0.95, 0.949)]
        sp = seconds_path(segs)
        t = sim.simulate_seconds(sp, self._mkt(), P, "paper")
        self.assertEqual((t["side"], t["entry"], t["entry_ts"], t["exit_type"], t["paper_exit"]),
                         ("UP", 850, CLOSE - 480, sim.TP, 950))
        self.assertEqual(t["exit_ts"], CLOSE - 450)
        tl = sim.simulate_seconds(sp, self._mkt(), P, "live")
        self.assertEqual(tl["live_exit"], 949)
        self.assertAlmostEqual(tl["pnl"], 0.949 - 0.85 - 0.0090 - 0.0034, places=10)

    def test_down_entry_per_second(self):
        # YES bid 0.13 -> DOWN ask 0.87; YES ask 0.14 -> DOWN bid 0.86
        segs = [(120, 0.40, 0.39), (480, 0.14, 0.13)]
        t = sim.simulate_seconds(seconds_path(segs), self._mkt("no"), P, "live")
        self.assertEqual((t["side"], t["entry"], t["exit_type"]), ("DOWN", 870, sim.SETTLE_WIN))
        self.assertAlmostEqual(t["pnl"], 0.13 - fee(870), places=10)

    def test_sl_rebound_vs_confirmed(self):
        # entry 0.85 at T-480; one second at 0.39 then back to 0.41: no stop
        rebound = [(120, 0.60, 0.59), (60, 0.85, 0.84), (1, 0.39, 0.38), (1, 0.41, 0.40), (418, 0.93, 0.929)]
        t = sim.simulate_seconds(seconds_path(rebound), self._mkt("yes"), P, "paper")
        self.assertEqual(t["exit_type"], sim.SETTLE_WIN)
        # 0.39 then 0.38: confirmed one second later, exit at that second's ask / bid
        confirmed = [(120, 0.60, 0.59), (60, 0.85, 0.84), (1, 0.39, 0.38), (1, 0.38, 0.37), (418, 0.30, 0.29)]
        sp = seconds_path(confirmed)
        tp_ = sim.simulate_seconds(sp, self._mkt("yes"), P, "paper")
        self.assertEqual((tp_["exit_type"], tp_["paper_exit"], tp_["exit_ts"]), (sim.SL, 380, CLOSE - 419))
        tl = sim.simulate_seconds(sp, self._mkt("yes"), P, "live")
        self.assertEqual((tl["exit_type"], tl["live_exit"]), (sim.SL, 370))
        self.assertAlmostEqual(tl["pnl"], 0.37 - 0.85 - 0.0090 - fee(370, action="sell"), places=10)

    def test_live_monitor_delay(self):
        # the ask spikes to the target for 2 s right after entry: paper takes it, live (3 s delay) does not
        segs = [(120, 0.60, 0.59), (1, 0.85, 0.84), (2, 0.95, 0.949), (477, 0.88, 0.87)]
        sp = seconds_path(segs)
        self.assertEqual(sim.simulate_seconds(sp, self._mkt("yes"), P, "paper")["exit_type"], sim.TP)
        self.assertEqual(sim.simulate_seconds(sp, self._mkt("yes"), P, "live")["exit_type"], sim.SETTLE_WIN)

    def test_no_entry_and_one_trade(self):
        sp = seconds_path([(600, 0.60, 0.59)])
        self.assertIsNone(sim.simulate_seconds(sp, self._mkt(), P, "paper"))
        segs = [(120, 0.60, 0.59), (10, 0.85, 0.84), (10, 0.95, 0.949), (460, 0.85, 0.84)]
        t = sim.simulate_seconds(seconds_path(segs), self._mkt("no"), P, "paper")
        self.assertEqual((t["entry_ts"], t["exit_type"]), (CLOSE - 480, sim.TP))

    def test_fill_above_090_no_tp_per_second(self):
        segs = [(120, 0.60, 0.59), (5, 0.92, 0.919), (475, 0.999, 0.998)]
        t = sim.simulate_seconds(seconds_path(segs), self._mkt("yes"), P, "paper")
        self.assertEqual((t["entry"], t["exit_type"]), (920, sim.SETTLE_WIN))

    def test_stop_not_confirmed_after_close(self):
        # the ask drops under 0.40 in the last second: confirmation would land after close -> settle
        segs = [(120, 0.60, 0.59), (479, 0.85, 0.84), (1, 0.30, 0.29)]
        t = sim.simulate_seconds(seconds_path(segs), self._mkt("no"), P, "paper")
        self.assertEqual(t["exit_type"], sim.SETTLE_LOSS)


class TestPortfolio(unittest.TestCase):
    def _t(self, day, hour, pnl, i):
        ts = int(dt.datetime(2026, 9, day, hour, tzinfo=dt.timezone.utc).timestamp()) + i * 900
        return {"ticker": "T%d_%d" % (day, i), "close_ts": ts + 900, "entry_ts": ts + 420, "exit_ts": ts + 910,
                "pnl": pnl, "entry": 850, "exit_type": sim.SL if pnl < 0 else sim.TP, "mins_left": 8.0, "side": "UP"}

    def test_stats_and_drawdown(self):
        tr = [self._t(1, 0, v, i) for i, v in enumerate([0.1, -0.5, 0.1, 0.1])]
        s = sim.stats(tr)
        self.assertEqual(s["trades"], 4)
        self.assertAlmostEqual(s["win_rate"], 0.75)
        self.assertAlmostEqual(s["avg_win"], 0.1)
        self.assertAlmostEqual(s["avg_loss"], -0.5)
        self.assertAlmostEqual(s["max_drawdown"], 0.5)
        self.assertAlmostEqual(s["breakeven_win_rate"], 0.5 / 0.6)
        self.assertAlmostEqual(sim.max_drawdown([1, -2, 3, -1, -1]), 2.0)

    def test_daily_limits(self):
        day1 = [self._t(1, 0, -5.0, i) for i in range(6)]   # crosses -20 on the 5th trade
        day2 = [self._t(2, 0, 1.0, i) for i in range(3)]
        kept, ev = sim.apply_daily_limits(day1 + day2, -20, 4000, "utc_day")
        self.assertEqual(len(kept), 5 + 3)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["day"], "2026-09-01")
        kept2, ev2 = sim.apply_daily_limits(day1 + day2, -20, 4000, "since_start")
        self.assertEqual(len(kept2), 5)
        self.assertEqual(len(ev2), 1)

    def test_buckets(self):
        self.assertEqual(sim.entry_bucket(800), "0.80-0.84")
        self.assertEqual(sim.entry_bucket(849), "0.80-0.84")
        self.assertEqual(sim.entry_bucket(850), "0.85-0.89")
        self.assertEqual(sim.entry_bucket(925), "0.90-0.93")
        self.assertEqual(sim.mins_left_bucket(8.0), "7-8")
        self.assertEqual(sim.mins_left_bucket(7.5), "7-8")
        self.assertEqual(sim.mins_left_bucket(0.2), "0-1")

    def test_sample_selection(self):
        tick = ["m%04d" % i for i in range(2880)]
        s = sim.select_tierb_sample(tick, 30, 15)
        self.assertEqual(len(s), 96)
        self.assertEqual(s[0], "m0015")
        sup = sim.select_ambiguous_supplement(tick[:10], ["m0000"], 3)
        self.assertEqual(len(sup), 3)
        self.assertNotIn("m0000", sup)


if __name__ == "__main__":
    unittest.main()
