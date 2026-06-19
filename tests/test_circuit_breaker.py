import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.circuit_breaker import divergence_round_count, is_tripped  # noqa: E402


def _row(strat, closed_at, reason):
    return {"strategy": strat, "closed_at": closed_at, "exit_reason": reason}


def test_counts_divergence_rounds_in_window():
    rows = [
        _row("pairs:A~B", "2026-06-17T10:00:00+00:00", "pairs:stop (diverged)"),
        _row("pairs:A~B", "2026-06-17T10:00:00+00:00", "pairs:stop (diverged)"),  # same round (2 legs)
        _row("pairs:C~D", "2026-06-17T10:05:00+00:00", "pairs:stop (diverged)"),
        _row("pairs:E~F", "2026-06-17T10:10:00+00:00", "pairs:converged"),         # not a divergence
        _row("pairs:G~H", "2026-06-17T08:00:00+00:00", "pairs:stop (diverged)"),   # before window
    ]
    n = divergence_round_count(rows, since_iso="2026-06-17T09:00:00+00:00")
    assert n == 2     # A~B and C~D rounds; converged + out-of-window excluded


def test_trips_at_threshold():
    assert is_tripped(3) is True
    assert is_tripped(2) is False


def test_ignores_non_pair_rows():
    rows = [_row("funding", "2026-06-17T10:00:00+00:00", "funding decayed")]
    assert divergence_round_count(rows, "2026-06-17T09:00:00+00:00") == 0
