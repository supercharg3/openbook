import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.correlation import (  # noqa: E402
    check_new_position,
    correlation_from_closes,
    daily_returns,
    pearson,
)


def test_pearson_perfect_positive():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert abs(pearson(a, a) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert abs(pearson(a, -a) + 1.0) < 1e-9


def test_pearson_flat_series_returns_zero():
    a = np.array([1.0, 2.0, 3.0])
    flat = np.array([5.0, 5.0, 5.0])
    assert pearson(a, flat) == 0.0


def test_daily_returns():
    closes = np.array([100.0, 110.0, 99.0])
    r = daily_returns(closes)
    assert abs(r[0] - 0.1) < 1e-9
    assert abs(r[1] - (-0.1)) < 1e-9


def test_correlation_from_closes_correlated():
    base = np.cumsum(np.random.RandomState(0).randn(40)) + 100
    closes_a = base
    closes_b = base * 2 + 50  # perfectly linearly related → returns correlate ~1
    assert correlation_from_closes(closes_a, closes_b) > 0.99


def test_guard_blocks_correlated_pair():
    lookup = lambda a, b: 0.85 if {a, b} == {"BTC/USDT", "ETH/USDT"} else 0.1
    d = check_new_position("ETH/USDT", ["BTC/USDT"], lookup)
    assert not d.allowed
    assert d.blocking_pair == "BTC/USDT"


def test_guard_allows_uncorrelated_pair():
    lookup = lambda a, b: 0.1
    d = check_new_position("SOL/USDT", ["BTC/USDT"], lookup)
    assert d.allowed


def test_guard_blocks_duplicate_pair():
    lookup = lambda a, b: None
    d = check_new_position("BTC/USDT", ["BTC/USDT"], lookup)
    assert not d.allowed


def test_guard_fails_open_on_unknown_correlation():
    lookup = lambda a, b: None
    d = check_new_position("DOGE/USDT", ["BTC/USDT"], lookup)
    assert d.allowed  # unknown → not blocked
