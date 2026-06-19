"""Thesis-trade sleeve — execute discretionary conviction trades you decide on (after research).

You send 'BUY MU 10%' or 'SHORT SOL' or 'CLOSE MU' in chat; it's queued and the trading loop
executes it (paper until you go live), in a capped sleeve walled off from the market-neutral core,
with an automatic stop-loss, and tracked separately so you learn whether your calls have edge.
"""
from __future__ import annotations
import re

THESIS_SLEEVE_PCT = 0.15      # max total thesis notional, as a share of capital
DEFAULT_SIZE_PCT = 5.0        # % of capital if you don't specify a size
THESIS_STOP_PCT = 0.08        # short-term thesis: tight 8% stop
THESIS_LT_STOP_PCT = 0.35     # long-term hold: WIDE stop (ride volatility); risk = small size, not a tight stop


def parse_thesis_order(text: str):
    """Return (action, pair, size_pct) or None.

    action in {buy, sell, buy_lt, sell_lt, close}. The '_lt' suffix marks a LONG-TERM hold
    (add 'hold' / 'long term' / 'lt' to the message), which gets a wide stop and no take-profit
    so a multi-year conviction position isn't shaken out by normal volatility.
    """
    m = re.match(
        r"^\s*(buy|long|sell|short|close)\s+([a-z0-9]{2,12})\s*(\d+(?:\.\d+)?)?\s*%?\s*"
        r"(hold|long[\s-]?term|longterm|lt)?\s*$",
        text.strip(), re.I)
    if not m:
        return None
    verb, tick, size, horizon = m.group(1).lower(), m.group(2).upper(), m.group(3), m.group(4)
    if tick in ("ALL",):          # 'CLOSE ALL' is a separate fixed command, not a thesis order
        return None
    base = "buy" if verb in ("buy", "long") else "sell" if verb in ("sell", "short") else "close"
    if base != "close" and horizon:
        base += "_lt"
    return base, f"{tick}/USDT", (float(size) if size else DEFAULT_SIZE_PCT)
