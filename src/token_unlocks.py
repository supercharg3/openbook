"""Token-unlock alpha.

Large vesting unlocks (founders/VCs able to sell) create predictable selling pressure. The
market tends to price it in 3–7 days ahead. We short the token ~5–7 days before a large unlock
and cover 1–2 days after. "Large" is defined relative to circulating supply.

Data comes from free sources (CoinGecko + TokenUnlocks.app). This module holds the decision
logic over unlock events; the HTTP fetch lives in the orchestrator and is injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# An unlock is "material" if it releases at least this fraction of circulating supply.
MATERIAL_UNLOCK_PCT = 0.02      # 2% of circulating supply
ENTER_DAYS_BEFORE = 7          # open short up to 7 days before
LATEST_ENTRY_DAYS_BEFORE = 3   # don't open if fewer than 3 days remain (edge already priced)
EXIT_DAYS_AFTER = 2            # cover within 2 days after the unlock


@dataclass
class UnlockEvent:
    symbol: str
    unlock_date: date
    unlock_pct_of_supply: float    # fraction of circulating supply unlocking
    usd_value: float               # notional being unlocked


@dataclass
class UnlockSignal:
    symbol: str
    action: str                    # "short" | "cover" | "ignore"
    unlock_date: date
    days_until: int
    reason: str


def is_material(event: UnlockEvent) -> bool:
    return event.unlock_pct_of_supply >= MATERIAL_UNLOCK_PCT


def evaluate_unlock(event: UnlockEvent, today: date, holding: bool) -> UnlockSignal:
    days_until = (event.unlock_date - today).days

    if holding:
        # We're already short — decide when to cover.
        if days_until <= -EXIT_DAYS_AFTER:
            return UnlockSignal(event.symbol, "cover", event.unlock_date, days_until,
                                "unlock passed — selling pressure spent, cover the short")
        return UnlockSignal(event.symbol, "ignore", event.unlock_date, days_until,
                            "short still working through the unlock window")

    if not is_material(event):
        return UnlockSignal(event.symbol, "ignore", event.unlock_date, days_until,
                            f"unlock {event.unlock_pct_of_supply*100:.1f}% < material threshold")

    if LATEST_ENTRY_DAYS_BEFORE <= days_until <= ENTER_DAYS_BEFORE:
        return UnlockSignal(
            event.symbol, "short", event.unlock_date, days_until,
            f"{event.unlock_pct_of_supply*100:.1f}% supply unlock in {days_until}d "
            f"(${event.usd_value:,.0f}) — short the anticipated drop",
        )

    if days_until > ENTER_DAYS_BEFORE:
        return UnlockSignal(event.symbol, "ignore", event.unlock_date, days_until,
                            "too early — wait for the 7-day window")
    return UnlockSignal(event.symbol, "ignore", event.unlock_date, days_until,
                        "too late — edge already priced in")


def next_actionable(events: list[UnlockEvent], today: date,
                    held_symbols: set[str]) -> list[UnlockSignal]:
    """Return all actionable signals (short or cover) from a list of upcoming unlocks."""
    signals = []
    for ev in events:
        sig = evaluate_unlock(ev, today, holding=ev.symbol in held_symbols)
        if sig.action in ("short", "cover"):
            signals.append(sig)
    return signals


def parse_coingecko_date(raw: str) -> date:
    """CoinGecko/TokenUnlocks return ISO timestamps; normalise to a date."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
