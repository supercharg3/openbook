"""Funding-carry edge research.

Delta-neutral (long spot + short perp), you collect the perp funding rate each 8h when funding is
positive. Pure structural, market-neutral income. This backtests the REALIZED carry from actual
Binance funding history: how much you'd have earned holding the carry on high-funding perps.
"""
from __future__ import annotations
import argparse
import numpy as np

ENTER = 0.0001   # only carry perps paying >0.01%/8h (skip near-zero, fees would eat it)
FEE_ANNUAL_DRAG = 0.02   # rough annual cost of rolling/rebalancing the hedge


def fetch_funding(ex, symbol, limit_total=1000):
    out = []
    try:
        rows = ex.fetch_funding_rate_history(symbol, limit=1000)
        out = [(r["timestamp"], float(r["fundingRate"])) for r in rows if r.get("fundingRate") is not None]
    except Exception:
        pass
    return out


def carry_return(funding_series):
    """Annualised realised carry: collect funding each period it clears the threshold."""
    rates = np.array([f for _, f in funding_series])
    if len(rates) < 50:
        return None
    collected = np.where(rates >= ENTER, rates, 0.0)   # only deploy when worth it
    deployed_frac = float((rates >= ENTER).mean())
    periods_per_year = 3 * 365                          # funding every 8h
    ann = collected.mean() * periods_per_year - FEE_ANNUAL_DRAG * deployed_frac
    # split-half consistency
    mid = len(collected) // 2
    h1 = collected[:mid].mean() * periods_per_year
    h2 = collected[mid:].mean() * periods_per_year
    return dict(ann=ann, deployed=deployed_frac, h1=h1, h2=h2, periods=len(rates))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=14); ap.parse_args()
    from src.ccxt_feed import build_binance
    ex = build_binance(None, None)
    universe = ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","XRP/USDT:USDT","DOGE/USDT:USDT",
                "AVAX/USDT:USDT","LINK/USDT:USDT","ARB/USDT:USDT","OP/USDT:USDT","SUI/USDT:USDT",
                "INJ/USDT:USDT","SEI/USDT:USDT","TIA/USDT:USDT","APT/USDT:USDT"]
    print(f"\n{'='*68}\nFUNDING CARRY — realised from Binance funding history\n{'='*68}")
    rows = []
    for s in universe:
        r = carry_return(fetch_funding(ex, s))
        if r:
            rows.append((r["ann"], s, r))
    rows.sort(reverse=True)
    for ann, s, r in rows:
        print(f"  {s.split('/')[0]:6s}  net carry {ann*100:+5.1f}%/yr  "
              f"(deployed {r['deployed']*100:.0f}% of time, H1 {r['h1']*100:+.0f}% / H2 {r['h2']*100:+.0f}%)")
    if rows:
        avg = np.mean([a for a, _, _ in rows])
        print(f"\n  Equal-weight basket net carry: {avg*100:+.1f}%/yr (market-neutral)")
    print("=" * 68)


if __name__ == "__main__":
    main()
