"""Floor-and-swing risk control for the aggressive 'Agentic Conviction' sleeve.

The deal: CONTROLLED downside (a hard floor + an account-level circuit breaker that halts everything
if the pot ever touches it) + CONVEX upside (the agent swings the cushion above the floor on
capped-downside bets) + a PROFIT-LOCK ratchet (the floor rises with every new high, so a winning run
can't fully give it back).

HONEST: there is NO validated edge here — this is high-variance BY DESIGN, a demonstration with risk
money. The floor is what guarantees we don't lose the whole pot; it is not a promise of profit. Paper
until it proves its mechanics. To keep the floor robust, the executor must use only capped-downside
instruments (long options, sized spot/positions) — never naked-short options, all-in memecoins, or
high leverage that gaps straight through the floor.
"""
from __future__ import annotations

FLOOR_LOCK_FROM_PEAK = 0.5     # once in profit, never give back more than 50% from any new high
DEFAULT_BET_FRACTION = 0.25    # each swing risks at most 25% of the cushion (capped-downside)


def floor_value(start: float, initial_floor: float, high_water: float) -> float:
    """The halt level. Starts at `initial_floor`; ratchets UP to lock gains once the pot makes new
    highs above `start`, so a good run is protected and can't fully unwind."""
    ratchet = high_water * (1 - FLOOR_LOCK_FROM_PEAK) if high_water > start else 0.0
    return max(initial_floor, ratchet)


def should_halt(current_value: float, floor: float) -> bool:
    """Circuit breaker: flatten everything and stop the agent at or below the floor."""
    return current_value <= floor


def risk_budget(current_value: float, floor: float) -> float:
    """The cushion — the most $ that can be at risk right now WITHOUT breaching the floor."""
    return max(0.0, current_value - floor)


def size_bet(current_value: float, floor: float, fraction: float = DEFAULT_BET_FRACTION) -> float:
    """A single capped-downside bet: a fraction of the cushion, so even a total loss on the bet
    cannot push the pot below the floor."""
    return risk_budget(current_value, floor) * max(0.0, min(1.0, fraction))
