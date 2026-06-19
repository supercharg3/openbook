import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.regime import (  # noqa: E402
    Regime,
    classify,
    compute_adx,
    regime_from_ohlcv,
    size_multiplier,
)


def _trending_series(n=80, slope=2.0):
    """Strong steady uptrend → high ADX."""
    close = np.arange(n, dtype=float) * slope + 100
    high = close + 0.5
    low = close - 0.5
    return high, low, close


def _ranging_series(n=80):
    """Oscillating sideways → low ADX."""
    x = np.arange(n)
    close = 100 + np.sin(x / 2.0) * 1.5
    high = close + 0.3
    low = close - 0.3
    return high, low, close


def test_classify_thresholds():
    assert classify(30) == Regime.TRENDING
    assert classify(15) == Regime.RANGING
    assert classify(22) == Regime.AMBIGUOUS


def test_trending_series_high_adx():
    high, low, close = _trending_series()
    regime, adx = regime_from_ohlcv(high, low, close)
    assert adx > 25
    assert regime == Regime.TRENDING


def test_ranging_series_low_adx():
    high, low, close = _ranging_series()
    regime, adx = regime_from_ohlcv(high, low, close)
    assert adx < 20
    assert regime == Regime.RANGING


def test_insufficient_bars_raises():
    try:
        compute_adx(np.ones(10), np.ones(10), np.ones(10), period=14)
        assert False, "should have raised"
    except ValueError:
        pass


def test_size_multiplier_gating():
    assert size_multiplier(Regime.TRENDING, "trend") == 1.0
    assert size_multiplier(Regime.RANGING, "trend") == 0.0
    assert size_multiplier(Regime.AMBIGUOUS, "trend") == 0.5
    assert size_multiplier(Regime.RANGING, "grid") == 1.0
    assert size_multiplier(Regime.TRENDING, "grid") == 0.0
