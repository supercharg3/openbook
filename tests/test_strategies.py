import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.strategies import (  # noqa: E402
    ema,
    evaluate_mean_reversion,
    evaluate_trend,
    exit_signal,
    rsi,
)


def test_ema_responds_to_recent_values():
    flat = np.full(50, 100.0)
    assert abs(ema(flat, 9) - 100.0) < 1e-6
    # rising series → EMA below the latest price but above the mean
    rising = np.arange(50, dtype=float)
    assert ema(rising, 9) > ema(rising, 21)   # fast EMA tracks closer to recent highs


def test_rsi_extremes():
    rising = np.arange(30, dtype=float)         # all gains → RSI 100
    assert rsi(rising) > 99
    falling = np.arange(30, 0, -1, dtype=float)  # all losses → RSI ~0
    assert rsi(falling) < 1
    flat = np.full(30, 100.0)
    assert abs(rsi(flat) - 50.0) < 1e-6


def _uptrend_with_pullback():
    # Uptrend then a small pullback so RSI lands in the 50–70 entry band.
    up = list(np.linspace(100, 130, 60))
    pull = list(np.linspace(130, 126, 8))
    return np.array(up + pull)


def test_evaluate_trend_long_in_uptrend():
    closes = _uptrend_with_pullback()
    sig = evaluate_trend(closes, adx=30)   # trending
    assert sig.strategy == "trend"
    assert sig.action == "long"


def test_evaluate_trend_sits_out_when_not_trending():
    closes = _uptrend_with_pullback()
    sig = evaluate_trend(closes, adx=15)   # not trending
    assert sig.action == "none"


def test_mean_reversion_long_when_oversold():
    falling = np.linspace(120, 90, 40)     # steep drop → low RSI
    sig = evaluate_mean_reversion(falling, adx=15)  # ranging
    assert sig.action == "long"
    assert sig.strategy == "grid"


def test_mean_reversion_inactive_when_trending():
    falling = np.linspace(120, 90, 40)
    assert evaluate_mean_reversion(falling, adx=30).action == "none"


def test_trend_exit_long_flips_on_downturn():
    down = np.linspace(130, 100, 60)       # EMA fast < slow → exit a long
    assert exit_signal("trend", "long", down) is True
    # Healthy uptrend with a pullback: EMA fast still > slow and RSI not overbought → hold.
    healthy = _uptrend_with_pullback()
    assert exit_signal("trend", "long", healthy) is False


def test_grid_exit_on_reversion_to_mid():
    rising = np.arange(40, dtype=float)    # RSI high (>50) → exit a grid long
    assert exit_signal("grid", "long", rising) is True
