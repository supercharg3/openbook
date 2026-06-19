"""Portfolio-level sizing for the pairs book.

Per-pair sizing alone leaves return on the table: a market-neutral pair with ~1% drawdown is
barely using its risk budget. This module sets each pair's live notional from the *combined*
risk of the book, so the portfolio targets a chosen volatility with controlled leverage —
the standard way market-neutral funds turn small per-strategy edges into real returns.

Method (deliberately robust, not falsely precise — we only have a handful of pairs):
  1. Annualised volatility per pair, from its backtest trade returns.
  2. Inverse-volatility base weights (risk parity: each pair contributes similar risk).
  3. Correlation from a structural prior, not noisy estimation: pairs that SHARE a leg
     (e.g. XRP~DOGE and XRP~ARB both hold XRP) are assumed correlated; otherwise near-independent.
  4. Portfolio volatility from weights + covariance.
  5. Leverage = target_vol / portfolio_vol, capped — then per-pair fraction = weight × leverage,
     also capped per pair.

Run `python -m src.portfolio` to recompute the recommended fractions for the live basket.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np

DEFAULT_TARGET_VOL = 0.15            # 15% annualised (Balanced) — configurable via portfolio.py
DEFAULT_MAX_TOTAL_LEVERAGE = 3.0   # cap on summed notional / capital
DEFAULT_MAX_PAIR_FRACTION = 0.50   # cap any single pair's notional fraction
SHARED_LEG_CORR = 0.50             # prior correlation for pairs sharing a symbol
INDEP_CORR = 0.15                  # prior correlation for otherwise-independent pairs


@dataclass
class Allocation:
    name: str
    ann_vol: float
    weight: float        # inverse-vol base weight (sums to 1)
    fraction: float      # final live notional fraction (weight × leverage, capped)


def _symbols(name: str) -> set[str]:
    return set(name.split("~"))


def annualized_vol(returns: list[float], period_days: float) -> float:
    r = np.asarray(returns, dtype=float)
    if len(r) < 2 or period_days <= 0:
        return 0.0
    trades_per_year = len(r) / (period_days / 365.0)
    return float(r.std() * np.sqrt(trades_per_year))


def correlation_matrix(names: list[str]) -> np.ndarray:
    n = len(names)
    c = np.eye(n)
    for i, j in combinations(range(n), 2):
        shared = bool(_symbols(names[i]) & _symbols(names[j]))
        c[i, j] = c[j, i] = SHARED_LEG_CORR if shared else INDEP_CORR
    return c


def allocate(
    pair_stats: list[tuple[str, list[float], float]],   # (name, returns, period_days)
    target_vol: float = DEFAULT_TARGET_VOL,
    max_total_leverage: float = DEFAULT_MAX_TOTAL_LEVERAGE,
    max_pair_fraction: float = DEFAULT_MAX_PAIR_FRACTION,
):
    """Return (allocations, portfolio_vol_at_base_weights, leverage)."""
    names = [p[0] for p in pair_stats]
    vols = np.array([annualized_vol(p[1], p[2]) for p in pair_stats])
    usable = vols > 0
    if not usable.any():
        return [], 0.0, 0.0

    inv = np.where(usable, 1.0 / np.where(vols == 0, np.nan, vols), 0.0)
    inv = np.nan_to_num(inv)
    weights = inv / inv.sum()

    corr = correlation_matrix(names)
    cov = np.outer(vols, vols) * corr
    port_vol = float(np.sqrt(weights @ cov @ weights))
    leverage = min(max_total_leverage, target_vol / port_vol) if port_vol > 0 else 0.0

    fractions = np.minimum(weights * leverage, max_pair_fraction)
    allocs = [
        Allocation(name=names[i], ann_vol=float(vols[i]), weight=float(weights[i]),
                   fraction=float(fractions[i]))
        for i in range(len(names))
    ]
    return allocs, port_vol, leverage


def format_report(allocs: list[Allocation], port_vol: float, leverage: float,
                  target_vol: float) -> str:
    lines = ["", "=" * 64, "PORTFOLIO SIZING (pairs book)", "=" * 64,
             f"target vol {target_vol*100:.0f}%   base portfolio vol {port_vol*100:.1f}%   "
             f"leverage {leverage:.2f}x", ""]
    for a in sorted(allocs, key=lambda x: -x.fraction):
        lines.append(f"  {a.name:24s}  vol {a.ann_vol*100:5.1f}%  weight {a.weight*100:4.0f}%  "
                     f"→ notional {a.fraction*100:4.1f}% of capital")
    total = sum(a.fraction for a in allocs)
    lines += ["", f"  total notional deployed: {total*100:.0f}% of capital (gross, market-neutral)",
              "=" * 64,
              "Paste these fractions into PAIR_ALLOCATIONS in src/orchestrator.py", "=" * 64]
    return "\n".join(lines)


def main() -> None:
    from .backtest import fetch_history
    from .ccxt_feed import build_binance
    from .orchestrator import VALIDATED_PAIRS
    from .pairs import PairsTrader, align, backtest_pair

    exchange = build_binance(None, None)
    cache: dict[str, list] = {}

    def hist(sym):
        if sym not in cache:
            cache[sym] = fetch_history(exchange, sym, "1h", 360 * 24)
        return cache[sym]

    pair_stats = []
    for a_sym, b_sym in VALIDATED_PAIRS:
        ac, bc, ts = align(hist(a_sym), hist(b_sym))
        res = backtest_pair(PairsTrader.name_of(a_sym, b_sym), ac, bc, ts)
        pair_stats.append((res.name, [t.pnl_pct for t in res.trades], res.period_days))

    allocs, port_vol, leverage = allocate(pair_stats)
    print(format_report(allocs, port_vol, leverage, DEFAULT_TARGET_VOL))


if __name__ == "__main__":
    main()
