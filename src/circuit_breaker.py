"""Portfolio circuit breaker — the quant-quake protection.

Stat-arb's classic failure mode: in a crisis, correlations snap toward 1, many spreads diverge
AT ONCE, and the market-neutral book stops being neutral. The per-pair z-stop handles one pair
blowing out; this handles SEVERAL blowing out together.

If too many pairs hit a divergence-stop in a short window, the breaker trips: the orchestrator
pauses all NEW pair entries (so we don't pile into a breaking regime) and alerts via Telegram.
Existing positions still exit on their own z-stops. It is stateless: recomputed each cycle from
the trade log, so it auto-clears once the divergence rate subsides.
"""
from __future__ import annotations

DIVERGENCE_WINDOW_MIN = 120     # look-back window
DIVERGENCE_THRESHOLD = 3        # this many pair divergence-stops in the window → trip


def divergence_round_count(closed_rows, since_iso: str) -> int:
    """Count distinct pair ROUNDS that closed on a divergence-stop since `since_iso` (UTC ISO)."""
    rounds = set()
    for r in closed_rows:
        strat = r["strategy"] or ""
        if not strat.startswith("pairs:"):
            continue
        closed_at = r["closed_at"] or ""
        if closed_at < since_iso:
            continue
        reason = r["exit_reason"] or ""
        if "diverg" in reason or "stop" in reason:
            rounds.add((strat, closed_at))
    return len(rounds)


def is_tripped(divergence_count: int, threshold: int = DIVERGENCE_THRESHOLD) -> bool:
    return divergence_count >= threshold
