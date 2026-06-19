import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.position_sizing import (  # noqa: E402
    SIZE_CAP,
    SIZE_FLOOR,
    KellyStats,
    compute_stats_from_returns,
    half_kelly_fraction,
    kelly_fraction,
    position_size_usd,
)


def test_kelly_fraction_known_value():
    # 55% win, avg win 2%, avg loss 1.5% → payoff 1.333; f = .55 - .45/1.333 = 0.2125
    stats = KellyStats(win_rate=0.55, avg_win_pct=0.02, avg_loss_pct=0.015, sample_size=50)
    assert abs(kelly_fraction(stats) - 0.2125) < 1e-3


def test_half_kelly_is_half_and_clamped():
    stats = KellyStats(win_rate=0.55, avg_win_pct=0.02, avg_loss_pct=0.015, sample_size=50)
    # half of 0.2125 = 0.106, within band
    assert abs(half_kelly_fraction(stats) - 0.10625) < 1e-3


def test_half_kelly_caps_at_15pct():
    # Huge edge → raw half-Kelly well above cap
    stats = KellyStats(win_rate=0.9, avg_win_pct=0.05, avg_loss_pct=0.01, sample_size=50)
    assert half_kelly_fraction(stats) == SIZE_CAP


def test_floor_applied_below_min_sample():
    stats = KellyStats(win_rate=0.8, avg_win_pct=0.05, avg_loss_pct=0.01, sample_size=5)
    assert half_kelly_fraction(stats) == SIZE_FLOOR


def test_negative_edge_falls_back_to_floor():
    # 40% win, symmetric payoff → negative Kelly
    stats = KellyStats(win_rate=0.4, avg_win_pct=0.01, avg_loss_pct=0.01, sample_size=50)
    assert kelly_fraction(stats) < 0
    assert half_kelly_fraction(stats) == SIZE_FLOOR


def test_no_losses_gives_no_actionable_edge():
    stats = KellyStats(win_rate=1.0, avg_win_pct=0.02, avg_loss_pct=0.0, sample_size=50)
    assert kelly_fraction(stats) == 0.0
    assert half_kelly_fraction(stats) == SIZE_FLOOR


def test_position_size_usd():
    stats = KellyStats(win_rate=0.55, avg_win_pct=0.02, avg_loss_pct=0.015, sample_size=50)
    size = position_size_usd(stats, 1000.0)
    assert abs(size - 106.25) < 0.5


def test_compute_stats_from_returns():
    returns = [0.02, -0.01, 0.03, -0.015, 0.01]  # 3 wins, 2 losses
    stats = compute_stats_from_returns(returns)
    assert stats.sample_size == 5
    assert abs(stats.win_rate - 0.6) < 1e-9
    assert abs(stats.avg_win_pct - 0.02) < 1e-9       # (0.02+0.03+0.01)/3
    assert abs(stats.avg_loss_pct - 0.0125) < 1e-9    # (0.01+0.015)/2
