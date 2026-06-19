"""Half-Kelly position sizing.

The Kelly criterion gives the bet fraction that maximises long-run geometric growth:

    f* = win_rate - (1 - win_rate) / (avg_win / avg_loss)

Full Kelly is too aggressive in practice (it assumes the win-rate and payoff ratio are known
exactly), so we use HALF-Kelly and clamp to a [floor, cap] band. The stats are recomputed
every 20 trades from the SQLite trade log, so sizing grows as the system proves its edge.
"""
from __future__ import annotations

from dataclasses import dataclass

# Recompute cadence and clamp band (as % of current capital)
RECOMPUTE_EVERY_N_TRADES = 20
SIZE_FLOOR = 0.03   # 3% — minimum meaningful trade
SIZE_CAP = 0.15     # 15% — concentration limit
MIN_SAMPLE = 10     # below this many trades, use the conservative floor


@dataclass(frozen=True)
class KellyStats:
    win_rate: float
    avg_win_pct: float   # average winning trade return, as a positive fraction (e.g. 0.02)
    avg_loss_pct: float  # average losing trade loss, as a positive fraction (e.g. 0.015)
    sample_size: int


def kelly_fraction(stats: KellyStats) -> float:
    """Raw full-Kelly fraction. Can be negative (no edge) or > 1 (huge edge)."""
    if stats.avg_loss_pct <= 0:
        # No losses observed yet — undefined payoff ratio; treat as no actionable edge.
        return 0.0
    payoff_ratio = stats.avg_win_pct / stats.avg_loss_pct
    loss_rate = 1.0 - stats.win_rate
    return stats.win_rate - (loss_rate / payoff_ratio)


def half_kelly_fraction(stats: KellyStats) -> float:
    """Half-Kelly, clamped to the [floor, cap] band.

    - Too few trades → fall back to the floor (small, safe).
    - Negative or zero edge → fall back to the floor (we still trade small; the strategy-level
      auto-pause in risk_manager handles genuinely losing strategies separately).
    """
    if stats.sample_size < MIN_SAMPLE:
        return SIZE_FLOOR
    raw = kelly_fraction(stats) / 2.0
    if raw <= 0:
        return SIZE_FLOOR
    return max(SIZE_FLOOR, min(SIZE_CAP, raw))


def position_size_usd(stats: KellyStats, capital_usd: float) -> float:
    """Dollar position size for the next trade given current capital."""
    return half_kelly_fraction(stats) * capital_usd


def compute_stats_from_returns(returns_pct: list[float]) -> KellyStats:
    """Build KellyStats from a list of realised trade returns (as fractions, e.g. 0.02 = +2%).

    Wins are returns > 0; losses are returns < 0 (taken as absolute value). Break-even
    trades (exactly 0) are ignored for the payoff ratio but counted in the sample.
    """
    wins = [r for r in returns_pct if r > 0]
    losses = [abs(r) for r in returns_pct if r < 0]
    sample = len(returns_pct)
    win_rate = len(wins) / sample if sample else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return KellyStats(
        win_rate=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        sample_size=sample,
    )
