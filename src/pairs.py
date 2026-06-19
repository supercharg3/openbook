"""Market-neutral pairs trading (statistical arbitrage).

Idea: two correlated assets (e.g. ETH & BTC) tend to move together. Their spread
(A - beta*B, where beta is a rolling hedge ratio) is often mean-reverting. When the spread
stretches far from its recent mean (high |z-score|), we bet it reverts: short the rich leg, long
the cheap leg. P&L comes from convergence, NOT from predicting market direction — so it survives
in markets where directional TA does not.

Everything here uses only PAST data at each step (rolling estimation), so the backtest is
walk-forward by construction. Parameters are fixed (not fit to the data) to avoid overfitting.

Usage:
    python -m src.pairs ETH/USDT BTC/USDT SOL/USDT ETH/USDT --days 360
    (pairs are read two at a time: (ETH,BTC), (SOL,ETH), ...)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np

WINDOW = 168            # rolling estimation window (1 week of 1h bars)
ENTRY_Z = 2.0           # enter when |z| exceeds this
EXIT_Z = 0.5            # exit when |z| falls back inside this
STOP_Z = 4.0            # bail if the spread keeps diverging (relationship may have broken)
ROUND_TRIP_FEE = 0.0014  # both legs, in and out (~0.07% per leg-side)
POSITION_FRACTION = 0.10


@dataclass
class PairTrade:
    side: str            # "long_spread" | "short_spread"
    entry_z: float
    exit_z: float
    pnl_pct: float       # net of fees, on gross notional
    reason: str


@dataclass
class PairResult:
    name: str
    bars: int
    period_days: float = 0.0
    trades: list[PairTrade] = field(default_factory=list)

    def stats(self) -> dict:
        rets = np.array([t.pnl_pct for t in self.trades])
        if len(rets) == 0:
            return {"trades": 0}
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        gross_loss = -losses.sum()
        equity = np.concatenate([[1.0], np.cumprod(1 + POSITION_FRACTION * rets)])
        peak = np.maximum.accumulate(equity)
        max_dd = float((1 - equity / peak).max())
        per_trade_sharpe = float(rets.mean() / rets.std()) if rets.std() > 0 else 0.0
        years = max(self.period_days / 365.0, 1e-9)
        ann_sharpe = per_trade_sharpe * np.sqrt(len(rets) / years)
        return {
            "trades": len(rets),
            "win_rate": float(len(wins) / len(rets)),
            "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
            "expectancy": float(rets.mean()),
            "sharpe": float(ann_sharpe),
            "max_drawdown": max_dd,
            "total_return": float(equity[-1] - 1.0),
        }


def hedge_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """OLS slope of A on B (no intercept) — the units of B per unit of A to hold."""
    denom = float(np.dot(b, b))
    return float(np.dot(a, b) / denom) if denom > 0 else 1.0


def compute_z(a_window, b_window, a_now: float, b_now: float):
    """Rolling hedge ratio + z-score of the current spread. Returns (z, beta, sd).

    Shared by the backtest and the live trader so live behaviour matches exactly what we
    validated. Returns (0.0, beta, 0.0) when the spread has no variance (no signal).
    """
    wa = np.asarray(a_window, dtype=float)
    wb = np.asarray(b_window, dtype=float)
    beta = hedge_ratio(wa, wb)
    spread_win = wa - beta * wb
    mu, sd = float(spread_win.mean()), float(spread_win.std())
    if sd == 0:
        return 0.0, beta, 0.0
    z = (a_now - beta * b_now - mu) / sd
    return z, beta, sd


# ── Live trader (used by the orchestrator) ───────────────────────────────────
@dataclass
class PairDecision:
    action: str          # "enter" | "exit" | "hold" | "none"
    name: str
    side: str = ""       # "long_spread" | "short_spread" (on enter)
    beta: float = 1.0
    z: float = 0.0
    reason: str = ""


class PairsTrader:
    """Live state machine for a basket of validated pairs. Pure decision logic — the orchestrator
    feeds it price windows and executes the two legs. Mirrors backtest_pair exactly."""

    def __init__(self, pairs: list[tuple[str, str]], window: int = WINDOW) -> None:
        self.pairs = pairs           # [(a_symbol, b_symbol), ...]
        self.window = window
        self.held: dict[str, str] = {}   # name -> side

    @staticmethod
    def name_of(a_sym: str, b_sym: str) -> str:
        return f"{a_sym}~{b_sym}"

    def evaluate(self, a_sym, b_sym, a_window, b_window, a_now, b_now) -> PairDecision:
        name = self.name_of(a_sym, b_sym)
        z, beta, sd = compute_z(a_window, b_window, a_now, b_now)
        if sd == 0:
            return PairDecision("none", name, reason="degenerate spread")
        held_side = self.held.get(name)
        if held_side is not None:
            if abs(z) < EXIT_Z:
                return PairDecision("exit", name, side=held_side, z=z, reason="converged")
            if abs(z) > STOP_Z:
                return PairDecision("exit", name, side=held_side, z=z, reason="stop (diverged)")
            return PairDecision("hold", name, side=held_side, z=z)
        if abs(z) > ENTRY_Z:
            side = "long_spread" if z < 0 else "short_spread"
            return PairDecision("enter", name, side=side, beta=beta, z=z,
                                reason=f"z={z:+.2f}")
        return PairDecision("none", name, z=z)

    def record_entry(self, name: str, side: str) -> None:
        self.held[name] = side

    def record_exit(self, name: str) -> None:
        self.held.pop(name, None)


def backtest_pair(name: str, a_prices: list[float], b_prices: list[float],
                  timestamps: list[float], window: int = WINDOW) -> PairResult:
    a = np.asarray(a_prices, dtype=float)
    b = np.asarray(b_prices, dtype=float)
    period_days = (timestamps[-1] - timestamps[0]) / 86_400_000 if len(timestamps) > 1 else 0.0
    result = PairResult(name=name, bars=len(a), period_days=period_days)

    pos = None  # {"side", "entry_spread", "beta", "entry_z", "a_entry", "b_entry"}
    for i in range(window, len(a)):
        wa, wb = a[i - window:i], b[i - window:i]
        z, beta, sd = compute_z(wa, wb, a[i], b[i])
        if sd == 0:
            continue

        if pos is not None:
            exit_now, reason = False, ""
            if abs(z) < EXIT_Z:
                exit_now, reason = True, "converged"
            elif abs(z) > STOP_Z:
                exit_now, reason = True, "stop (diverged)"
            if exit_now:
                # P&L of the spread leg, normalised by gross notional at entry.
                notional = pos["a_entry"] + pos["beta"] * pos["b_entry"]
                d_spread = (a[i] - pos["a_entry"]) - pos["beta"] * (b[i] - pos["b_entry"])
                sign = 1.0 if pos["side"] == "long_spread" else -1.0
                pnl = sign * d_spread / notional - ROUND_TRIP_FEE
                result.trades.append(PairTrade(pos["side"], pos["entry_z"], z, pnl, reason))
                pos = None

        if pos is None and abs(z) > ENTRY_Z:
            side = "long_spread" if z < 0 else "short_spread"  # z<0 → spread cheap → long it
            pos = {"side": side, "beta": beta, "entry_z": z,
                   "a_entry": a[i], "b_entry": b[i]}

    return result


def align(a_ohlcv: list[list[float]], b_ohlcv: list[list[float]]):
    """Align two OHLCV series on shared timestamps; return (a_closes, b_closes, timestamps)."""
    b_by_ts = {row[0]: row[4] for row in b_ohlcv}
    ts, ac, bc = [], [], []
    for row in a_ohlcv:
        if row[0] in b_by_ts:
            ts.append(row[0])
            ac.append(row[4])
            bc.append(b_by_ts[row[0]])
    return ac, bc, ts


def format_report(results: list[PairResult]) -> str:
    lines = ["", "=" * 64, "PAIRS / STAT-ARB BACKTEST", "=" * 64]
    any_pass = False
    for r in results:
        s = r.stats()
        lines.append(f"\n{r.name}  ({r.bars} aligned bars, {r.period_days:.0f}d)")
        if s["trades"] == 0:
            lines.append("  no trades")
            continue
        lines += [
            f"  trades         {s['trades']}",
            f"  win rate       {s['win_rate']*100:.1f}%",
            f"  profit factor  {s['profit_factor']:.2f}   (gate > 1.3)",
            f"  expectancy     {s['expectancy']*100:+.3f}% per trade",
            f"  Sharpe (ann.)  {s['sharpe']:.2f}   (gate > 1.5)",
            f"  max drawdown   {s['max_drawdown']*100:.1f}%   (gate < 15%)",
            f"  total return   {s['total_return']*100:+.1f}%",
        ]
        ok = s["profit_factor"] >= 1.3 and s["sharpe"] >= 1.5 and s["max_drawdown"] <= 0.15
        lines.append("  ✅ clears the gate" if ok else "  ⚠️  below gate")
        any_pass = any_pass or ok
    lines += ["", "=" * 64,
              "VERDICT: " + ("✅ at least one pair shows durable edge" if any_pass
                             else "⚠️ no pair clears the gate on this window"),
              "=" * 64]
    return "\n".join(lines)


def split_validate(name: str, ac: list[float], bc: list[float], ts: list[float],
                    window: int = WINDOW):
    """Out-of-sample robustness: a pair must be profitable in BOTH time halves.

    This is the filter that kills the flukes (like LINK~ETH, which only worked in one window).
    Returns (full_result, robust: bool, first_half_stats, second_half_stats).
    """
    n = len(ac)
    mid = n // 2
    first = backtest_pair(name, ac[:mid], bc[:mid], ts[:mid], window).stats()
    second = backtest_pair(name, ac[mid:], bc[mid:], ts[mid:], window).stats()
    full = backtest_pair(name, ac, bc, ts, window)
    sfull = full.stats()
    robust = (
        first.get("trades", 0) >= 5 and second.get("trades", 0) >= 5
        and first.get("expectancy", -1) > 0 and second.get("expectancy", -1) > 0
        and first.get("profit_factor", 0) >= 1.2 and second.get("profit_factor", 0) >= 1.2
        and sfull.get("profit_factor", 0) >= 1.3 and sfull.get("sharpe", 0) >= 1.5
        and sfull.get("max_drawdown", 1) <= 0.15
    )
    return full, robust, first, second


def screen(symbols: list[str], hist) -> str:
    """Test every pair in a universe; report only those robust across both halves, ranked."""
    from itertools import combinations

    robust_rows = []
    tested = 0
    for a_sym, b_sym in combinations(symbols, 2):
        ac, bc, ts = align(hist(a_sym), hist(b_sym))
        if len(ac) < 2 * WINDOW:
            continue
        tested += 1
        full, robust, first, second = split_validate(f"{a_sym} ~ {b_sym}", ac, bc, ts)
        if robust:
            s = full.stats()
            robust_rows.append((s["sharpe"], f"{a_sym} ~ {b_sym}", s, first, second))
    robust_rows.sort(reverse=True)

    lines = ["", "=" * 70, f"PAIR SCREEN — {tested} pairs tested, {len(robust_rows)} robust", "=" * 70]
    if not robust_rows:
        lines.append("\nNo pair survived out-of-sample validation on this universe/window.")
    for sharpe, name, s, first, second in robust_rows:
        lines += [
            f"\n✅ {name}",
            f"   full:   Sharpe {s['sharpe']:.2f}  PF {s['profit_factor']:.2f}  "
            f"win {s['win_rate']*100:.0f}%  ret {s['total_return']*100:+.1f}%  dd {s['max_drawdown']*100:.1f}%",
            f"   H1:     PF {first['profit_factor']:.2f}  exp {first['expectancy']*100:+.3f}%  "
            f"({first['trades']} trades)",
            f"   H2:     PF {second['profit_factor']:.2f}  exp {second['expectancy']*100:+.3f}%  "
            f"({second['trades']} trades)",
        ]
    lines += ["", "=" * 70]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+", help="pairs two-at-a-time, or a universe with --screen")
    ap.add_argument("--days", type=int, default=360)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--screen", action="store_true", help="treat symbols as a universe; test all pairs")
    args = ap.parse_args()

    from .backtest import fetch_history
    from .ccxt_feed import build_binance

    exchange = build_binance(None, None)
    per = 24 if args.timeframe.endswith("h") else 1
    total = args.days * per
    cache: dict[str, list] = {}

    def hist(sym):
        if sym not in cache:
            cache[sym] = fetch_history(exchange, sym, args.timeframe, total)
        return cache[sym]

    if args.screen:
        print(screen(args.symbols, hist))
        return

    results = []
    for i in range(0, len(args.symbols) - 1, 2):
        a_sym, b_sym = args.symbols[i], args.symbols[i + 1]
        ac, bc, ts = align(hist(a_sym), hist(b_sym))
        results.append(backtest_pair(f"{a_sym} ~ {b_sym}", ac, bc, ts))
    print(format_report(results))


if __name__ == "__main__":
    main()
