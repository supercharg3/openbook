"""Pair-health / decay monitor.

Cointegration relationships break (we watched LINK~ETH work, then stop). A pair that was
validated can quietly stop working live. This module watches each live pair's *realized*
performance and auto-pauses it for NEW entries when its recent edge has decayed. Existing
positions are unaffected — they still exit on their own z-score logic.

It is stateless: the verdict is recomputed each cycle from the SQLite trade log, so a pair
un-pauses automatically if it recovers, and pauses again if it relapses. No flags to get stuck.

A pair "round" is the two legs that open and close together (same close timestamp); its net P&L
is the sum of both legs. We judge on rounds, not individual legs (a leg alone is meaningless).
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_ROUNDS = 10            # below this, not enough live history to judge — allow trading
EVAL_ROUNDS = 15          # judge on the most recent N rounds
MIN_PROFIT_FACTOR = 1.0   # below break-even gross → decayed


@dataclass(frozen=True)
class HealthVerdict:
    healthy: bool
    rounds: int
    profit_factor: float
    expectancy: float
    reason: str


def pair_round_pnls(closed_rows) -> list[float]:
    """Aggregate per-pair-round net P&L from leg-level closed trades.

    Both legs of a round share a closed_at (they close together in _close_pair), so we group by
    it and sum. Returns net pnl per round, oldest first.
    """
    by_close: dict[str, float] = {}
    for r in closed_rows:
        key = r["closed_at"] or ""
        by_close[key] = by_close.get(key, 0.0) + (r["pnl_usd"] or 0.0)
    return [by_close[k] for k in sorted(by_close)]


def evaluate_health(round_pnls: list[float], *, min_rounds: int = MIN_ROUNDS,
                    eval_rounds: int = EVAL_ROUNDS,
                    min_profit_factor: float = MIN_PROFIT_FACTOR) -> HealthVerdict:
    n = len(round_pnls)
    if n < min_rounds:
        return HealthVerdict(True, n, 0.0, 0.0, "insufficient live history — trading allowed")
    recent = round_pnls[-eval_rounds:]
    gains = sum(p for p in recent if p > 0)
    losses = -sum(p for p in recent if p < 0)
    pf = gains / losses if losses > 0 else float("inf")
    expectancy = sum(recent) / len(recent)
    if expectancy <= 0 or pf < min_profit_factor:
        return HealthVerdict(False, n, pf, expectancy,
                             f"decayed: PF {pf:.2f}, expectancy {expectancy:+.4f}/round over "
                             f"last {len(recent)} — paused")
    return HealthVerdict(True, n, pf, expectancy, "healthy")
