"""ADX-based market regime detection.

ADX (Average Directional Index) measures trend *strength* (not direction). We gate strategies
on it:
  - ADX > 25  → TRENDING → run trend following (Layer 3); suspend grid bot
  - ADX < 20  → RANGING  → run grid bot (Layer 4); halve trend trades
  - 20–25     → AMBIGUOUS → half-size on both

This is a pure-numpy implementation of Wilder's ADX so the regime gate has no external
TA-lib dependency and is unit-testable on synthetic data.
"""
from __future__ import annotations

from enum import Enum

import numpy as np

ADX_TRENDING = 25.0
ADX_RANGING = 20.0


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    AMBIGUOUS = "ambiguous"


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    """Return the latest ADX value (Wilder's smoothing). NaN-safe; needs > 2*period bars."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)

    n = len(close)
    if n < 2 * period + 1:
        raise ValueError(f"Need at least {2 * period + 1} bars, got {n}")

    # True Range and directional movement
    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder smoothing
    def _wilder(arr: np.ndarray) -> np.ndarray:
        out = np.zeros_like(arr)
        out[period - 1] = arr[:period].sum()
        for i in range(period, len(arr)):
            out[i] = out[i - 1] - (out[i - 1] / period) + arr[i]
        return out[period - 1:]

    atr = _wilder(tr)
    plus_di = 100.0 * _wilder(plus_dm) / np.where(atr == 0, np.nan, atr)
    minus_di = 100.0 * _wilder(minus_dm) / np.where(atr == 0, np.nan, atr)

    dx = 100.0 * np.abs(plus_di - minus_di) / np.where(
        (plus_di + minus_di) == 0, np.nan, plus_di + minus_di
    )
    dx = np.nan_to_num(dx)

    # ADX = smoothed DX
    if len(dx) < period:
        return float(np.mean(dx))
    adx = np.mean(dx[:period])
    for i in range(period, len(dx)):
        adx = (adx * (period - 1) + dx[i]) / period
    return float(adx)


def classify(adx: float) -> Regime:
    if adx > ADX_TRENDING:
        return Regime.TRENDING
    if adx < ADX_RANGING:
        return Regime.RANGING
    return Regime.AMBIGUOUS


def regime_from_ohlcv(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                      period: int = 14) -> tuple[Regime, float]:
    """Convenience: compute ADX and classify in one call. Returns (regime, adx_value)."""
    adx = compute_adx(high, low, close, period)
    return classify(adx), adx


def size_multiplier(regime: Regime, layer: str) -> float:
    """How much of a strategy layer's normal size to deploy in the given regime.

    layer is one of: 'trend' (Layer 3) or 'grid' (Layer 4).
    """
    if layer == "trend":
        return {Regime.TRENDING: 1.0, Regime.AMBIGUOUS: 0.5, Regime.RANGING: 0.0}[regime]
    if layer == "grid":
        return {Regime.RANGING: 1.0, Regime.AMBIGUOUS: 0.5, Regime.TRENDING: 0.0}[regime]
    raise ValueError(f"Unknown layer: {layer}")
