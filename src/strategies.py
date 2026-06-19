"""Native trend + mean-reversion strategies (Layers 3 & 4).

Replaces the external Freqtrade engine with logic that lives in the same process, uses the same
risk manager, database, and Telegram reports. All signal logic is pure (numpy in, dataclass out)
so it is unit-tested like the rest of the system.

Regime gating (via ADX, computed in regime.py):
  - ADX > 25  → TRENDING → trend layer active (EMA direction + RSI momentum)
  - ADX < 20  → RANGING  → mean-reversion layer active (RSI oversold/overbought)
  - 20–25     → AMBIGUOUS → both sit out (avoid the chop)

Exits are handled per-strategy in exit_signal(), on top of the universal stop-loss / take-profit
backstops the orchestrator applies to every position.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .regime import ADX_RANGING, ADX_TRENDING

# Entry/exit thresholds
RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
RSI_LONG_MIN, RSI_LONG_MAX = 50.0, 70.0     # trend long: momentum up but not overbought
RSI_SHORT_MIN, RSI_SHORT_MAX = 30.0, 50.0   # trend short: momentum down but not oversold
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0


@dataclass(frozen=True)
class StrategySignal:
    action: str          # "long" | "short" | "none"
    strategy: str        # "trend" | "grid"
    reason: str = ""


def ema(values: np.ndarray, period: int) -> float:
    """Latest EMA value."""
    values = np.asarray(values, dtype=float)
    if len(values) < period:
        raise ValueError(f"Need >= {period} values for EMA")
    k = 2.0 / (period + 1.0)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1.0 - k)
    return float(e)


def rsi(closes: np.ndarray, period: int = RSI_PERIOD) -> float:
    """Latest Wilder RSI. Returns 50 (neutral) if there is no movement."""
    closes = np.asarray(closes, dtype=float)
    if len(closes) < period + 1:
        raise ValueError(f"Need >= {period + 1} closes for RSI")
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def evaluate_trend(closes: np.ndarray, adx: float) -> StrategySignal:
    """Layer 3: trend following, only in a confirmed trend (ADX > 25)."""
    if adx <= ADX_TRENDING:
        return StrategySignal("none", "trend", "not trending")
    fast = ema(closes, EMA_FAST)
    slow = ema(closes, EMA_SLOW)
    r = rsi(closes)
    if fast > slow and RSI_LONG_MIN <= r <= RSI_LONG_MAX:
        return StrategySignal("long", "trend", f"uptrend (EMA{EMA_FAST}>EMA{EMA_SLOW}, RSI {r:.0f})")
    if fast < slow and RSI_SHORT_MIN <= r <= RSI_SHORT_MAX:
        return StrategySignal("short", "trend", f"downtrend (EMA{EMA_FAST}<EMA{EMA_SLOW}, RSI {r:.0f})")
    return StrategySignal("none", "trend", f"no clean entry (RSI {r:.0f})")


def evaluate_mean_reversion(closes: np.ndarray, adx: float) -> StrategySignal:
    """Layer 4: mean reversion, only in a ranging market (ADX < 20)."""
    if adx >= ADX_RANGING:
        return StrategySignal("none", "grid", "not ranging")
    r = rsi(closes)
    if r < RSI_OVERSOLD:
        return StrategySignal("long", "grid", f"oversold bounce (RSI {r:.0f})")
    if r > RSI_OVERBOUGHT:
        return StrategySignal("short", "grid", f"overbought fade (RSI {r:.0f})")
    return StrategySignal("none", "grid", f"mid-range (RSI {r:.0f})")


def exit_signal(strategy: str, side: str, closes: np.ndarray) -> bool:
    """Per-strategy exit (on top of the universal SL/TP backstops).

    Trend: exit when the EMA relationship flips or RSI hits the opposite extreme.
    Grid:  exit when price reverts back through the midline (RSI 50).
    """
    r = rsi(closes)
    if strategy == "trend":
        fast = ema(closes, EMA_FAST)
        slow = ema(closes, EMA_SLOW)
        if side == "long":
            return fast < slow or r > 75.0
        return fast > slow or r < 25.0
    if strategy == "grid":
        if side == "long":
            return r > 50.0
        return r < 50.0
    return False
