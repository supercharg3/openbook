"""Degen sleeve — hyper-active, rule-based technical momentum on volatile crypto.

No Claude per trade (too slow at 15-min cadence). Pure signal:
  Entry: price breaks 20-bar high + volume surge (2x avg) + minimum ATR (we want volatility)
  Exit:  +25% take-profit | -15% stop-loss | price below 10-bar low (trend reversal)
  Sizing: flat 10% of current pot per bet, up to 5 concurrent

The floor is the only backstop: halt if the pot falls to DEGEN_FLOOR_PCT of budget.
This is the highest-variance sleeve by design — it trades meme coins and high-beta alts
aggressively and accepts it will have ugly losing streaks. Watch it; don't size it large.
"""
from __future__ import annotations

WATCHLIST = [
    # Meme coins — maximum volatility
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT",
    # Liquid alts — volatile but with liquidity
    "SOL/USDT", "AVAX/USDT", "BNB/USDT", "NEAR/USDT", "APT/USDT", "SUI/USDT",
    # AI / narrative tokens — move fast on news
    "RENDER/USDT", "FET/USDT", "TAO/USDT",
    # Other high-beta alts
    "TRX/USDT", "ADA/USDT", "LINK/USDT", "INJ/USDT", "ARB/USDT",
]

MAX_BETS = 5
BET_FRACTION = 0.10       # 10% of current pot per bet
TAKE_PROFIT = 0.25        # close at +25%
STOP_LOSS = 0.15          # cut at -15%
REVERSAL_BARS = 10        # also close if price falls below 10-bar low (trend gone)
MIN_ATR_PCT = 0.003       # skip coins with <0.3% ATR — not volatile enough to bother
VOLUME_MULT = 2.0         # entry volume must be >2x the 20-bar average
LOOKBACK = 20             # bars for breakout high + average volume
TIMEFRAME = "15m"


def fetch_signals(symbols: list[str], ex) -> dict[str, dict]:
    """Fetch 15-min OHLCV for each symbol and compute entry signals.

    Returns a dict of symbol → signal dict with keys:
      px, atr_pct, volume_surge, breakout, reversal_low, entry_ok
    """
    out = {}
    for sym in symbols:
        try:
            bars = ex.fetch_ohlcv(sym, TIMEFRAME, limit=LOOKBACK + 5)
            if len(bars) < LOOKBACK + 1:
                continue
            closes = [b[4] for b in bars]
            highs = [b[2] for b in bars]
            lows = [b[3] for b in bars]
            vols = [b[5] for b in bars]

            px = closes[-1]
            prev_closes = closes[:-1]
            prev_highs = highs[:-1]
            prev_lows = lows[:-1]
            prev_vols = vols[:-1]

            # ATR (simple: mean of high-low range over lookback)
            ranges = [prev_highs[i] - prev_lows[i] for i in range(len(prev_highs))]
            atr = sum(ranges[-LOOKBACK:]) / LOOKBACK if ranges else 0.0
            atr_pct = atr / px if px else 0.0

            # Volume: is current bar volume > VOLUME_MULT * average?
            avg_vol = sum(prev_vols[-LOOKBACK:]) / LOOKBACK if prev_vols else 0.0
            curr_vol = vols[-1]
            volume_surge = avg_vol > 0 and curr_vol >= VOLUME_MULT * avg_vol

            # Breakout: price above 20-bar high (of previous bars)
            high_20 = max(prev_highs[-LOOKBACK:]) if len(prev_highs) >= LOOKBACK else max(prev_highs)
            breakout = px > high_20

            # Reversal: 10-bar low of previous bars (for exit check)
            low_10 = min(prev_lows[-REVERSAL_BARS:]) if len(prev_lows) >= REVERSAL_BARS else min(prev_lows)

            out[sym] = {
                "px": px,
                "atr_pct": atr_pct,
                "volume_surge": volume_surge,
                "breakout": breakout,
                "reversal_low": low_10,
                "entry_ok": (atr_pct >= MIN_ATR_PCT and volume_surge and breakout),
            }
        except Exception:
            continue
    return out


def bet_size(pot: float) -> float:
    """Flat fraction of current pot, minimum $10."""
    return max(10.0, round(pot * BET_FRACTION, 2))


def should_halt(pot: float, floor: float) -> bool:
    return pot <= floor
