"""Stock market data via Yahoo Finance (yfinance) — free, no API key.

Used for live quotes on equity tickers (MU, NVDA, CRDO, ...) that Binance can't price, so the
chat price lookup and the research stress-test work for stocks as well as crypto.

NOTE: this is DATA only. The engine still executes on Binance (crypto). Trading stocks for real
would need a stock broker API (Alpaca / IBKR) and a separate funded account — a deliberate later
step, not wired here.
"""
from __future__ import annotations


def stock_quote(ticker: str) -> float | None:
    """Latest price for a stock ticker, or None if it can't be fetched / isn't a real ticker."""
    try:
        import yfinance as yf
    except Exception:
        return None
    t = yf.Ticker(ticker)
    # fast_info is the near-real-time path; fall back to the last daily close.
    try:
        px = t.fast_info.get("last_price") or t.fast_info.get("lastPrice")
        if px:
            return float(px)
    except Exception:
        pass
    try:
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None
