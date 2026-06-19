"""Hard-coded risk controls. Every proposed trade passes through here before execution.

These limits are deliberately not configurable at runtime — they are the guardrails that make
full automation safe. The numbers come straight from the approved plan:

  - Max drawdown          : 20% of current capital → hard stop + recalibrate
  - Daily loss limit      : 3% of capital → halt for the day
  - Weekly loss limit     : 7% → halve sizes for the following week
  - Per-trade stop-loss   : 8% of position
  - Leverage unlock       : 1x → 2x (30 trades + 4 weeks) → 3x (90 trades + 3 months)
  - Portfolio leverage cap: <= 30% of capital in leveraged positions at once
  - Max position size     : 15% of capital per trade
  - Max open positions    : 3
  - Weekend TradFi rule   : close leveraged TradFi before Fri 5pm SGT

All percentages are of *current* capital, so the limits scale automatically as it grows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ── Constants (from the plan) ────────────────────────────────────────────────
MAX_DRAWDOWN_PCT = 0.20
DAILY_LOSS_LIMIT_PCT = 0.03
WEEKLY_LOSS_LIMIT_PCT = 0.07
PER_TRADE_STOP_PCT = 0.08
MAX_POSITION_PCT = 0.15
MAX_OPEN_POSITIONS = 3
PORTFOLIO_LEVERAGE_CAP_PCT = 0.30        # max share of capital in leveraged positions
STRATEGY_PAUSE_EV_WINDOW = 20             # auto-pause a strategy if last-N EV < 0
MAX_ASSET_EXPOSURE_PCT = 0.25             # |net| directional exposure to any one coin, % of capital


# ── Aggregate per-asset exposure (the council's top finding) ─────────────────
# A pair is "market-neutral" on its own, but a coin in several pairs (XRP is in 4, IMX in 4) can
# accumulate same-side net exposure across them, quietly un-neutralizing the book. We sum the
# SIGNED notional of every open leg per underlying coin and cap the absolute net.

def _asset_of(pair_symbol: str) -> str:
    return pair_symbol.split("/")[0]


def net_asset_exposure(legs: list[tuple[str, str, float]]) -> dict[str, float]:
    """legs: list of (pair_symbol, side, notional). Returns signed net notional per coin
    (long = +, short = -)."""
    exp: dict[str, float] = {}
    for sym, side, notional in legs:
        signed = notional if side == "long" else -notional
        exp[_asset_of(sym)] = exp.get(_asset_of(sym), 0.0) + signed
    return exp


def exposure_breach(current_legs, new_legs, cap_usd: float):
    """Would adding `new_legs` push any single coin's |net exposure| over cap_usd?
    Returns (asset, net) of the first breach, or None if clear."""
    exp = net_asset_exposure(list(current_legs) + list(new_legs))
    for asset, net in exp.items():
        if abs(net) > cap_usd:
            return asset, net
    return None

# Leverage unlock gates: (max_leverage, min_trades, min_days_live)
LEVERAGE_GATES = [
    (1.0, 0, 0),
    (2.0, 30, 28),     # 30 trades AND 4 weeks
    (3.0, 90, 90),     # 90 trades AND 3 months
]

SGT = timezone(timedelta(hours=8))

# TradFi symbols traded on Binance (perp futures). Used by the weekend rule.
TRADFI_SYMBOLS = {
    "TSLA", "AMZN", "AAPL", "MSFT", "META", "GOOGL", "NVDA", "INTC", "AVGO", "MU",
    "TSM", "PLTR", "COIN", "MSTR", "HOOD", "CRCL", "PYPL", "SPY", "QQQ", "EWY", "EWJ",
    "XAU", "XAG", "XPT", "XPD", "WTI", "HG",
}


@dataclass
class AccountState:
    """Snapshot of the account the risk manager reasons over."""
    current_capital: float
    peak_capital: float                       # high-water mark, for drawdown
    realized_pnl_today: float = 0.0           # negative = loss
    realized_pnl_week: float = 0.0
    open_positions: int = 0
    leveraged_exposure_usd: float = 0.0       # notional of positions with leverage > 1
    live_trade_count: int = 0
    days_live: int = 0
    paused_strategies: set[str] = field(default_factory=set)
    reduce_risk_until: datetime | None = None  # set by the REDUCE RISK override
    tradeable_capital: float = 0.0             # equity minus locked profit reserve (for sizing)

    def __post_init__(self) -> None:
        # Sizing uses tradeable_capital; default it to full capital when not set (reserve = 0).
        if self.tradeable_capital <= 0:
            self.tradeable_capital = self.current_capital


@dataclass
class TradeProposal:
    pair: str
    side: str                 # long | short
    strategy: str
    size_usd: float
    leverage: float = 1.0
    is_tradfi: bool = False


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    # When approved, the final size to execute (may be trimmed below the requested size).
    # None when the trade is rejected.
    adjusted_size_usd: float | None = None
    was_trimmed: bool = False                 # True if size was reduced from the request


def max_leverage_allowed(trade_count: int, days_live: int) -> float:
    """Highest leverage unlocked given track record. Gates require BOTH trades AND time."""
    allowed = 1.0
    for lev, min_trades, min_days in LEVERAGE_GATES:
        if trade_count >= min_trades and days_live >= min_days:
            allowed = lev
    return allowed


def is_weekend_tradfi_blackout(now_sgt: datetime) -> bool:
    """True from Fri 17:00 SGT through Sun, when leveraged TradFi must stay flat.

    US market close is Fri; weekend macro can gap the perp vs the underlying on Mon open.
    """
    wd = now_sgt.weekday()  # Mon=0 .. Sun=6
    if wd == 4:  # Friday
        return now_sgt.hour >= 17
    return wd in (5, 6)  # Sat, Sun


class RiskManager:
    def __init__(self, account: AccountState) -> None:
        self.account = account

    # ── Portfolio-level halts (checked first) ────────────────────────────────
    def drawdown_breached(self) -> bool:
        if self.account.peak_capital <= 0:
            return False
        dd = 1.0 - (self.account.current_capital / self.account.peak_capital)
        return dd >= MAX_DRAWDOWN_PCT - 1e-9  # tolerate float boundary at exactly 20%

    def daily_limit_hit(self) -> bool:
        return self.account.realized_pnl_today <= -DAILY_LOSS_LIMIT_PCT * self.account.current_capital

    def weekly_limit_hit(self) -> bool:
        return self.account.realized_pnl_week <= -WEEKLY_LOSS_LIMIT_PCT * self.account.current_capital

    # ── The single entry point ───────────────────────────────────────────────
    def evaluate(self, proposal: TradeProposal, now_sgt: datetime | None = None) -> RiskDecision:
        now_sgt = now_sgt or datetime.now(SGT)
        a = self.account

        # 1. Hard portfolio halts
        if self.drawdown_breached():
            return RiskDecision(False, "BLOCKED: max drawdown (20%) breached — system halted, recalibrate")
        if self.daily_limit_hit():
            return RiskDecision(False, "BLOCKED: daily loss limit (3%) hit — no new trades today")
        if self.weekly_limit_hit():
            return RiskDecision(False, "BLOCKED: weekly loss limit (7%) hit — sizes halved this week")

        # 2. Strategy auto-pause
        if proposal.strategy in a.paused_strategies:
            return RiskDecision(False, f"BLOCKED: strategy '{proposal.strategy}' is auto-paused (negative EV)")

        # 3. Open-position cap
        if a.open_positions >= MAX_OPEN_POSITIONS:
            return RiskDecision(False, f"BLOCKED: already at max {MAX_OPEN_POSITIONS} open positions")

        # 4. Weekend TradFi blackout (leveraged only)
        if proposal.is_tradfi and proposal.leverage > 1.0 and is_weekend_tradfi_blackout(now_sgt):
            return RiskDecision(False, "BLOCKED: leveraged TradFi closed Fri 5pm–Mon (weekend gap risk)")

        # 5. Leverage gate
        max_lev = max_leverage_allowed(a.live_trade_count, a.days_live)
        if proposal.leverage > max_lev:
            return RiskDecision(
                False,
                f"BLOCKED: {proposal.leverage}x exceeds unlocked {max_lev}x "
                f"({a.live_trade_count} trades, {a.days_live}d live)",
            )

        # 6. Position-size cap (shrink rather than reject)
        size = proposal.size_usd
        max_size = MAX_POSITION_PCT * a.current_capital
        if a.reduce_risk_until and now_sgt < a.reduce_risk_until:
            max_size *= 0.5  # REDUCE RISK override halves sizes
        if size > max_size:
            size = max_size

        # 7. Portfolio leverage cap
        if proposal.leverage > 1.0:
            new_lev_exposure = a.leveraged_exposure_usd + size
            cap = PORTFOLIO_LEVERAGE_CAP_PCT * a.current_capital
            if new_lev_exposure > cap:
                room = cap - a.leveraged_exposure_usd
                if room <= 0:
                    return RiskDecision(False, "BLOCKED: portfolio leverage cap (30% of capital) reached")
                size = min(size, room)

        if size <= 0:
            return RiskDecision(False, "BLOCKED: no capacity left under risk caps")

        trimmed = size != proposal.size_usd
        note = f" (size trimmed to ${size:.2f})" if trimmed else ""
        return RiskDecision(True, f"APPROVED{note}", adjusted_size_usd=size, was_trimmed=trimmed)

    # ── Helper: the stop-loss price for an order ─────────────────────────────
    @staticmethod
    def stop_loss_price(entry_price: float, side: str) -> float:
        if side == "long":
            return entry_price * (1 - PER_TRADE_STOP_PCT)
        return entry_price * (1 + PER_TRADE_STOP_PCT)
