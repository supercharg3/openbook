"""Profit cashout reserve — locks in gains so they can't be re-risked.

A ratchet on the equity high-water mark: each time the account makes a new high, a fraction of
the new gain is moved into a RESERVE. The strategy sizes its trades against
`tradeable = equity - reserve`, so reserved profit is taken off the table and not re-risked.
(Money physically stays in the Binance account — the API key has no withdrawal permission by
design — but the strategy treats the reserve as untouchable. Actual withdrawal to her bank is the
separate manual monthly step.)

Trade-off: a higher cashout fraction is safer but compounds slower (reserved money stops growing).
While the account is small, keep this modest so growth is not strangled; raise it once there is
real profit worth protecting.
"""
from __future__ import annotations

CASHOUT_FRAC = 0.30     # fraction of each new-high gain to lock away (tune for growth vs safety)


def update_reserve(equity: float, prev_hwm: float, prev_reserve: float,
                   frac: float = CASHOUT_FRAC) -> tuple[float, float]:
    """Return (new_high_water_mark, new_reserve) after applying the ratchet to current equity."""
    if equity > prev_hwm:
        prev_reserve += frac * (equity - prev_hwm)
        prev_hwm = equity
    return prev_hwm, prev_reserve


def tradeable_capital(equity: float, reserve: float) -> float:
    return max(0.0, equity - reserve)
