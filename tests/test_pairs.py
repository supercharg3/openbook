import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pairs import (  # noqa: E402
    PairsTrader,
    backtest_pair,
    compute_z,
    hedge_ratio,
)


def test_hedge_ratio_recovers_known_slope():
    b = np.arange(1, 100, dtype=float)
    a = 2.5 * b                      # A = 2.5 * B exactly
    assert abs(hedge_ratio(a, b) - 2.5) < 1e-6


def test_compute_z_zero_at_mean():
    # Spread oscillates around 0 → current point at mean gives z≈0
    b = np.full(168, 100.0)
    a = 100.0 + np.sin(np.arange(168) / 5.0)   # mean ~100
    z, beta, sd = compute_z(a, b, 100.0, 100.0)
    assert sd > 0
    assert abs(z) < 1.0


def test_compute_z_high_when_stretched():
    b = np.full(168, 100.0)
    a = 100.0 + np.sin(np.arange(168) / 5.0)
    # push A far above its spread mean → large positive z
    z, beta, sd = compute_z(a, b, 105.0, 100.0)
    assert z > 3


def test_degenerate_spread_returns_zero():
    a = np.full(168, 50.0)
    b = np.full(168, 100.0)
    z, beta, sd = compute_z(a, b, 50.0, 100.0)
    assert sd == 0.0 and z == 0.0


# ── Live PairsTrader state machine ───────────────────────────────────────────
def _windows():
    b = np.full(168, 100.0)
    a = 100.0 + np.sin(np.arange(168) / 5.0)
    return a, b


def test_trader_enters_when_stretched():
    pt = PairsTrader([("XRP/USDT", "DOGE/USDT")])
    a, b = _windows()
    d = pt.evaluate("XRP/USDT", "DOGE/USDT", a, b, a_now=105.0, b_now=100.0)
    assert d.action == "enter"
    assert d.side == "short_spread"          # A rich → short the spread
    pt.record_entry(d.name, d.side)
    assert pt.held[d.name] == "short_spread"


def test_trader_holds_then_exits_on_convergence():
    pt = PairsTrader([("XRP/USDT", "DOGE/USDT")])
    a, b = _windows()
    pt.record_entry("XRP/USDT~DOGE/USDT", "short_spread")
    # mildly stretched (z between exit 0.5 and stop 4) → hold
    d_hold = pt.evaluate("XRP/USDT", "DOGE/USDT", a, b, 101.5, 100.0)
    assert d_hold.action == "hold"
    # back near mean → exit
    d_exit = pt.evaluate("XRP/USDT", "DOGE/USDT", a, b, 100.0, 100.0)
    assert d_exit.action == "exit"
    assert d_exit.reason == "converged"


def test_trader_stop_on_divergence():
    pt = PairsTrader([("XRP/USDT", "DOGE/USDT")])
    a, b = _windows()
    pt.record_entry("XRP/USDT~DOGE/USDT", "short_spread")
    d = pt.evaluate("XRP/USDT", "DOGE/USDT", a, b, 110.0, 100.0)   # z way beyond stop
    assert d.action == "exit"
    assert "stop" in d.reason


def test_backtest_still_runs_after_refactor():
    # Synthetic cointegrated pair: B random walk, A = 2*B + mean-reverting noise.
    rng = np.random.RandomState(1)
    b = 100 + np.cumsum(rng.randn(2000))
    noise = np.zeros(2000)
    for i in range(1, 2000):
        noise[i] = 0.9 * noise[i - 1] + rng.randn()   # mean-reverting spread
    a = 2 * b + noise
    ts = list(range(0, 2000 * 3_600_000, 3_600_000))
    res = backtest_pair("A~B", list(a), list(b), ts)
    assert res.bars == 2000
    assert len(res.trades) >= 0   # runs without error on the mean-reverting spread
