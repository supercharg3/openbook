"""Cross-sectional factor research — the next candidate edge.

Each rebalance, rank a universe of coins by trailing return, go LONG the top k and SHORT the
bottom k, equal-weight and dollar-neutral. Momentum = long winners; reversal = long losers.
Market-neutral (no market-direction bet). Validated the same way as everything else: both time
halves must hold, or it is rejected.

Usage:  python -m src.factor --days 540
"""
from __future__ import annotations
import argparse
import numpy as np

FEE_PER_REBAL = 0.0012   # round-trip turnover cost on the rebalanced book


def build_matrix(data):
    sets = [set(r[0] for r in rows) for rows in data.values()]
    common = sorted(set.intersection(*sets))
    syms = list(data.keys())
    idx = {ts: i for i, ts in enumerate(common)}
    M = np.full((len(common), len(syms)), np.nan)
    for j, sym in enumerate(syms):
        for r in data[sym]:
            if r[0] in idx:
                M[idx[r[0]], j] = r[4]
    return syms, M


def backtest(M, lookback, rebal, k, mode):
    T, N = M.shape
    rets = []
    t = lookback
    while t + rebal < T:
        look = M[t] / M[t - lookback] - 1
        fwd = M[t + rebal] / M[t] - 1
        ok = ~np.isnan(look) & ~np.isnan(fwd)
        if ok.sum() >= 2 * k:
            cand = np.where(ok)[0]
            order = cand[np.argsort(look[cand])]
            longs = order[-k:] if mode == "momentum" else order[:k]
            shorts = order[:k] if mode == "momentum" else order[-k:]
            rets.append(float(fwd[longs].mean() - fwd[shorts].mean() - FEE_PER_REBAL))
        t += rebal
    return np.array(rets)


def stats(rets, rebal_days, frac=0.5):
    if len(rets) < 4:
        return None
    ppy = 365.0 / rebal_days
    sharpe = float(rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0
    eq = np.cumprod(1 + frac * rets)
    maxdd = float((1 - eq / np.maximum.accumulate(eq)).max())
    return dict(n=len(rets), sharpe=sharpe, total=float(eq[-1] - 1),
                win=float((rets > 0).mean()), maxdd=maxdd, mean=float(rets.mean()))


def evaluate(name, M, lookback, rebal, k, mode):
    rets = backtest(M, lookback, rebal, k, mode)
    full = stats(rets, rebal)
    if full is None:
        return f"{name}: too few rebalances"
    mid = len(rets) // 2
    h1, h2 = stats(rets[:mid], rebal), stats(rets[mid:], rebal)
    robust = (full["sharpe"] >= 1.0 and h1 and h2 and h1["mean"] > 0 and h2["mean"] > 0
              and h1["sharpe"] > 0.3 and h2["sharpe"] > 0.3)
    flag = "ROBUST" if robust else "weak"
    return (f"{name}: Sharpe {full['sharpe']:.2f} | ret {full['total']*100:+.0f}% | "
            f"win {full['win']*100:.0f}% | dd {full['maxdd']*100:.0f}% | "
            f"H1 Sh {h1['sharpe']:.2f} / H2 Sh {h2['sharpe']:.2f} -> {flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=540)
    args = ap.parse_args()
    from src.backtest import fetch_history
    from src.ccxt_feed import build_binance
    universe = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","DOGE/USDT","ADA/USDT",
                "AVAX/USDT","LINK/USDT","DOT/USDT","LTC/USDT","ATOM/USDT","NEAR/USDT","INJ/USDT",
                "UNI/USDT","AAVE/USDT","ARB/USDT","OP/USDT","SUI/USDT","APT/USDT","FIL/USDT",
                "TIA/USDT","SEI/USDT","GALA/USDT","IMX/USDT","GRT/USDT"]
    ex = build_binance(None, None)
    data = {}
    for s in universe:
        try:
            data[s] = fetch_history(ex, s, "1d", args.days)
        except Exception:
            pass
    syms, M = build_matrix(data)
    print(f"\n{'='*70}\nCROSS-SECTIONAL FACTOR — {len(syms)} coins, {M.shape[0]} days\n{'='*70}")
    print("(gate: full Sharpe >= 1.0 AND both halves positive)\n")
    print(evaluate("Momentum  L30 R7  k5", M, 30, 7, 5, "momentum"))
    print(evaluate("Momentum  L14 R7  k5", M, 14, 7, 5, "momentum"))
    print(evaluate("Momentum  L60 R14 k5", M, 60, 14, 5, "momentum"))
    print(evaluate("Reversal  L7  R7  k5", M, 7, 7, 5, "reversal"))
    print(evaluate("Reversal  L3  R3  k5", M, 3, 3, 5, "reversal"))
    print("=" * 70)


if __name__ == "__main__":
    main()
