"""Rigorous equities/ETF pair screener — sector-constrained + cointegration-tested.

Equities are where pairs trading belongs, but they need MORE rigor than crypto, not less,
because a naive all-vs-all screen over hundreds of names produces spurious pairs (PEP~IWM and
the like). Three guards, on top of the both-halves out-of-sample filter we always use:

  1. SAME-SECTOR ONLY. Only test pairs WITHIN a sector (two banks, two oil majors). Pairs cross
     sectors are almost always coincidence, and restricting cuts the multiple-comparison count
     from ~1000s to dozens.
  2. COINTEGRATION TEST. Engle-Granger (statsmodels.coint) on the spread, p < 0.05, BEFORE
     backtesting — confirms a real statistical relationship, not just co-movement.
  3. ENOUGH TRADES. Require a minimum number of trades per half so the stats mean something
     (no validating on 5 trades). Uses long free history (default 8y daily).

Usage:  python -m src.equities --years 8
"""
from __future__ import annotations

import argparse

from .pairs import backtest_pair  # noqa: F401

EQUITY_WINDOW = 60          # ~3 months of daily bars for the rolling hedge/spread estimate
COINT_PVALUE_MAX = 0.05     # Engle-Granger cointegration significance
MIN_TRADES_PER_HALF = 12    # below this, the half is too thin to trust

# Same-sector groups — pairs are only tested WITHIN a group.
SECTOR_GROUPS = {
    "mega_tech": ["MSFT", "AAPL", "GOOGL", "META", "AMZN"],
    "semis": ["NVDA", "AMD", "INTC", "MU", "AVGO", "QCOM", "TXN", "AMAT", "LRCX"],
    "banks": ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC"],
    "payments": ["V", "MA", "AXP", "PYPL", "FIS", "GPN"],
    "energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY"],
    "retail": ["WMT", "TGT", "COST", "HD", "LOW", "DG", "DLTR"],
    "staples": ["KO", "PEP", "PG", "CL", "KMB", "MDLZ", "GIS"],
    "telecom": ["T", "VZ", "TMUS"],
    "pharma": ["PFE", "MRK", "JNJ", "ABBV", "BMY", "LLY", "AMGN", "GILD"],
    "autos": ["F", "GM"],
    "airlines": ["DAL", "UAL", "AAL", "LUV"],
    "broad_etfs": ["SPY", "QQQ", "DIA", "IWM"],
    "sector_etfs": ["XLF", "XLK", "XLE", "XLV", "XLP", "XLY", "XLI", "SMH"],
}


def fetch_equities(tickers: list[str], years: int) -> dict[str, list[list[float]]]:
    """Daily OHLCV per ticker as ccxt-style rows [ts_ms, o, h, l, c, v]."""
    import pandas as pd
    import yfinance as yf

    raw = yf.download(tickers, period=f"{years}y", interval="1d", progress=False,
                      auto_adjust=True, group_by="ticker", threads=True)
    out: dict[str, list[list[float]]] = {}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna()
        if df.empty or len(df) < 2 * EQUITY_WINDOW:
            continue
        rows = [[int(pd.Timestamp(ts).timestamp() * 1000), float(r["Open"]), float(r["High"]),
                 float(r["Low"]), float(r["Close"]), float(r.get("Volume", 0.0))]
                for ts, r in df.iterrows()]
        out[t] = rows
    return out


def cointegration_pvalue(a_closes: list[float], b_closes: list[float]) -> float:
    """Engle-Granger p-value. Low p = cointegrated (mean-reverting spread exists)."""
    import numpy as np
    from statsmodels.tsa.stattools import coint

    try:
        _, pvalue, _ = coint(np.asarray(a_closes), np.asarray(b_closes))
        return float(pvalue)
    except Exception:
        return 1.0


def screen_equities(data: dict[str, list[list[float]]], groups: dict[str, list[str]]) -> str:
    from .pairs import align, split_validate

    robust, tested, coint_pass = [], 0, 0
    for sector, tickers in groups.items():
        present = [t for t in tickers if t in data]
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                a, b = present[i], present[j]
                ac, bc, ts = align(data[a], data[b])
                if len(ac) < 2 * EQUITY_WINDOW + 10:
                    continue
                tested += 1
                if cointegration_pvalue(ac, bc) > COINT_PVALUE_MAX:
                    continue                      # not cointegrated → skip before backtest
                coint_pass += 1
                full, is_robust, first, second = split_validate(
                    f"{a}~{b}", ac, bc, ts, window=EQUITY_WINDOW)
                if is_robust and first["trades"] >= MIN_TRADES_PER_HALF \
                        and second["trades"] >= MIN_TRADES_PER_HALF:
                    s = full.stats()
                    robust.append((s["sharpe"], sector, f"{a} ~ {b}", s, first, second))
    robust.sort(reverse=True)

    lines = ["", "=" * 70,
             f"EQUITIES SCREEN — {tested} same-sector pairs, {coint_pass} cointegrated, "
             f"{len(robust)} robust", "=" * 70]
    if not robust:
        lines.append("\nNo equity pair cleared cointegration + out-of-sample + trade-count gates.")
    for sharpe, sector, name, s, first, second in robust:
        lines += [
            f"\n✅ {name}  [{sector}]",
            f"   full:  Sharpe {s['sharpe']:.2f}  PF {s['profit_factor']:.2f}  "
            f"win {s['win_rate']*100:.0f}%  ret {s['total_return']*100:+.1f}%  dd {s['max_drawdown']*100:.1f}%",
            f"   H1 PF {first['profit_factor']:.2f} ({first['trades']}t)   "
            f"H2 PF {second['profit_factor']:.2f} ({second['trades']}t)",
        ]
    lines += ["", "=" * 70]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=8)
    args = ap.parse_args()
    universe = sorted({t for group in SECTOR_GROUPS.values() for t in group})
    print(f"Fetching {len(universe)} tickers, {args.years}y daily...")
    data = fetch_equities(universe, args.years)
    print(f"Got data for {len(data)} tickers. Screening within sectors...")
    print(screen_equities(data, SECTOR_GROUPS))


if __name__ == "__main__":
    main()
