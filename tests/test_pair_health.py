import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pair_health import evaluate_health, pair_round_pnls  # noqa: E402


def _rows(pairs):
    """pairs: list of (closed_at, pnl_usd) leg rows."""
    return [{"closed_at": c, "pnl_usd": p} for c, p in pairs]


def test_round_aggregation_sums_legs_by_close_time():
    # Two rounds, each with two legs sharing a close timestamp.
    rows = _rows([
        ("t1", -0.5), ("t1", 1.5),   # round 1 net +1.0
        ("t2", -0.3), ("t2", 0.1),   # round 2 net -0.2
    ])
    got = pair_round_pnls(rows)
    assert len(got) == 2
    assert abs(got[0] - 1.0) < 1e-9 and abs(got[1] - (-0.2)) < 1e-9


def test_insufficient_history_is_healthy():
    v = evaluate_health([1.0, -0.5, 0.8])   # < MIN_ROUNDS
    assert v.healthy
    assert "insufficient" in v.reason


def test_healthy_when_profitable():
    rounds = [0.5, -0.2, 0.6, -0.1, 0.4, 0.3, -0.2, 0.5, 0.2, 0.4, 0.3, -0.1]
    v = evaluate_health(rounds)
    assert v.healthy
    assert v.profit_factor > 1.0
    assert v.expectancy > 0


def test_decayed_when_losing():
    rounds = [-0.5, -0.3, 0.1, -0.4, -0.2, -0.6, 0.05, -0.3, -0.5, -0.2, -0.4, -0.1]
    v = evaluate_health(rounds)
    assert not v.healthy
    assert "decayed" in v.reason
    assert v.expectancy < 0


def test_decayed_when_profit_factor_below_one():
    # Net roughly break-even but gross losses exceed gross gains → PF < 1 → paused.
    rounds = [0.2, -0.3, 0.2, -0.3, 0.2, -0.3, 0.2, -0.3, 0.2, -0.3, 0.1, -0.2]
    v = evaluate_health(rounds)
    assert not v.healthy


def test_only_recent_window_counts():
    # Old losses, but recent rounds are strongly positive → healthy.
    old_losses = [-1.0] * 10
    recent_wins = [0.5] * 15
    v = evaluate_health(old_losses + recent_wins)
    assert v.healthy


def test_recovery_unpauses_automatically():
    # Recent window flips from losing to winning → verdict flips healthy (stateless).
    losing = [-0.4] * 15
    assert not evaluate_health(losing).healthy
    recovered = losing + [0.5] * 15
    assert evaluate_health(recovered).healthy
