"""Layer 1: funding-rate arbitrage monitor (market-neutral).

When a perpetual's funding rate is meaningfully positive, shorts get paid by longs every 8h.
A delta-neutral position (long spot + short perp on the same asset) collects that funding with
no directional exposure. We enter when funding > ENTER threshold and exit when it decays below
EXIT for two consecutive checks (so a single noisy print doesn't churn the position).

This module is pure decision logic over funding-rate inputs. The actual ccxt calls live in the
orchestrator; `FundingMonitor` is fed rate snapshots so it's fully unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ENTER_RATE = 0.0005     # 0.05% / 8h — enter arb above this
EXIT_RATE = 0.00005     # 0.005% / 8h — exit if below this for 2 consecutive windows
EXIT_CONSECUTIVE = 2

# Liquidity floor: funding arb is only "market-neutral" on pairs deep enough to hedge cheaply.
# Thin microcap perps (high funding precisely because they're illiquid) have wide spreads and a
# hard-to-fill spot leg, so we restrict to a curated allowlist of liquid USDT perps. A live cycle
# without this filter surfaced SKHYNIX/STXX/SIREN as "top opportunities" — exactly what to avoid.
LIQUID_PERPS = {
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT",
    "AVAX/USDT", "LINK/USDT", "DOT/USDT", "MATIC/USDT", "ARB/USDT", "OP/USDT", "LTC/USDT",
    "TRX/USDT", "ATOM/USDT", "UNI/USDT", "AAVE/USDT", "INJ/USDT", "SUI/USDT", "SEI/USDT",
    "TIA/USDT", "NEAR/USDT", "APT/USDT", "FIL/USDT", "ETC/USDT", "BCH/USDT",
}


def is_liquid(symbol: str) -> bool:
    return symbol in LIQUID_PERPS


def filter_liquid(snapshots: list["FundingSnapshot"]) -> list["FundingSnapshot"]:
    """Drop funding opportunities on pairs outside the liquid allowlist."""
    return [s for s in snapshots if is_liquid(s.symbol)]


@dataclass
class FundingSnapshot:
    symbol: str
    funding_rate: float     # per 8h, as a fraction (0.0005 = 0.05%)


@dataclass
class ArbPosition:
    symbol: str
    notional_usd: float
    entered_rate: float
    low_streak: int = 0     # consecutive windows below EXIT_RATE


@dataclass
class FundingDecision:
    action: str             # "enter" | "exit" | "hold" | "none"
    symbol: str
    reason: str
    estimated_8h_income_usd: float = 0.0


class FundingMonitor:
    def __init__(self) -> None:
        self.positions: dict[str, ArbPosition] = {}

    def rank_opportunities(self, snapshots: list[FundingSnapshot]) -> list[FundingSnapshot]:
        """Highest funding first, only those above the entry threshold."""
        eligible = [s for s in snapshots if s.funding_rate >= ENTER_RATE]
        return sorted(eligible, key=lambda s: s.funding_rate, reverse=True)

    def evaluate_entry(self, snapshot: FundingSnapshot, notional_usd: float) -> FundingDecision:
        if snapshot.symbol in self.positions:
            return FundingDecision("hold", snapshot.symbol, "already in position")
        if snapshot.funding_rate < ENTER_RATE:
            return FundingDecision("none", snapshot.symbol, "funding below entry threshold")
        income = snapshot.funding_rate * notional_usd
        return FundingDecision(
            "enter", snapshot.symbol,
            f"funding {snapshot.funding_rate*100:.4f}%/8h ≥ entry",
            estimated_8h_income_usd=income,
        )

    def evaluate_exit(self, snapshot: FundingSnapshot) -> FundingDecision:
        pos = self.positions.get(snapshot.symbol)
        if pos is None:
            return FundingDecision("none", snapshot.symbol, "no open arb position")
        if snapshot.funding_rate < EXIT_RATE:
            pos.low_streak += 1
            if pos.low_streak >= EXIT_CONSECUTIVE:
                return FundingDecision(
                    "exit", snapshot.symbol,
                    f"funding {snapshot.funding_rate*100:.4f}%/8h below exit for "
                    f"{pos.low_streak} windows",
                )
            return FundingDecision(
                "hold", snapshot.symbol,
                f"low funding ({pos.low_streak}/{EXIT_CONSECUTIVE} windows)",
            )
        pos.low_streak = 0  # recovered — reset the streak
        return FundingDecision("hold", snapshot.symbol, "funding healthy")

    # ── State mutation (called by the orchestrator after acting) ─────────────
    def record_entry(self, symbol: str, notional_usd: float, rate: float) -> None:
        self.positions[symbol] = ArbPosition(symbol, notional_usd, rate)

    def record_exit(self, symbol: str) -> None:
        self.positions.pop(symbol, None)
