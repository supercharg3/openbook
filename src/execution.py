"""Execution layer — the seam between decisions and the exchange.

Two protocols isolate all network I/O so the orchestrator is fully testable with fakes:
  - PriceFeed       : reads marks, funding rates, OHLCV (ccxt in production)
  - ExecutionClient : opens/closes positions, reports balance

Three concrete pieces:
  - DryRunExecutionClient : simulates fills + balance for paper trading and tests
  - CcxtExecutionClient   : live Binance via ccxt (structure complete; must be validated with a
                            tiny live balance before real capital — see the guard in open_position)

Fees match Binance futures: 0.02% maker (our limit entries) / 0.05% taker (market exits).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .funding_arb import FundingSnapshot

FEE_RATE_MAKER = 0.0002   # limit orders (entries)
FEE_RATE_TAKER = 0.0005   # market orders (stop-loss exits)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Position:
    pair: str
    side: str               # long | short
    size_usd: float         # notional at entry (before leverage multiplier on PnL)
    leverage: float
    entry_price: float
    strategy: str
    opened_at: str
    db_id: int | None = None


@dataclass
class ClosedTrade:
    position: Position
    exit_price: float
    pnl_usd: float
    pnl_pct: float          # return on notional (already includes leverage)
    fee_usd: float
    reason: str
    closed_at: str


def compute_pnl(side: str, entry: float, exit_: float, size_usd: float, leverage: float):
    """Return (pnl_usd, pnl_pct_on_notional). pnl_pct includes leverage."""
    direction = 1.0 if side == "long" else -1.0
    raw_pct = direction * (exit_ - entry) / entry
    pnl_pct = raw_pct * leverage
    pnl_usd = pnl_pct * size_usd
    return pnl_usd, pnl_pct


@runtime_checkable
class PriceFeed(Protocol):
    def get_price(self, pair: str) -> float: ...
    def get_funding_rates(self) -> list[FundingSnapshot]: ...
    def get_ohlcv(self, pair: str, timeframe: str = "1d", limit: int = 60) -> list[list[float]]: ...


@runtime_checkable
class ExecutionClient(Protocol):
    def get_balance(self) -> float: ...
    def open_position(self, pair: str, side: str, size_usd: float, leverage: float,
                      strategy: str) -> Position: ...
    def close_position(self, position: Position, reason: str) -> ClosedTrade: ...


# ── Dry-run / paper client ───────────────────────────────────────────────────
class DryRunExecutionClient:
    """Simulates fills against the price feed and tracks a virtual balance.

    Entries fill at the current mark (we model our limit-at-ask as effectively the mark) and pay
    the maker fee; exits pay the taker fee on the realised side. This is the paper-trading client
    and also what the unit tests drive.
    """

    def __init__(self, starting_balance: float, price_feed: PriceFeed) -> None:
        self._balance = starting_balance
        self.price_feed = price_feed

    def get_balance(self) -> float:
        return self._balance

    def open_position(self, pair: str, side: str, size_usd: float, leverage: float,
                      strategy: str) -> Position:
        mark = self.price_feed.get_price(pair)
        self._balance -= size_usd * FEE_RATE_MAKER  # entry fee
        return Position(
            pair=pair, side=side, size_usd=size_usd, leverage=leverage,
            entry_price=mark, strategy=strategy, opened_at=_utcnow_iso(),
        )

    def close_position(self, position: Position, reason: str) -> ClosedTrade:
        mark = self.price_feed.get_price(position.pair)
        pnl_usd, pnl_pct = compute_pnl(
            position.side, position.entry_price, mark, position.size_usd, position.leverage
        )
        fee = position.size_usd * FEE_RATE_TAKER
        self._balance += pnl_usd - fee
        return ClosedTrade(
            position=position, exit_price=mark, pnl_usd=pnl_usd - fee, pnl_pct=pnl_pct,
            fee_usd=fee, reason=reason, closed_at=_utcnow_iso(),
        )


# ── Live client (ccxt) ───────────────────────────────────────────────────────
class CcxtExecutionClient:
    """Live Binance futures execution via ccxt.

    Structurally complete but intentionally guarded: the first time it runs live it refuses to
    place an order unless ALLOW_LIVE_ORDERS is explicitly set, so the deploy can be smoke-tested
    against a real (tiny) balance before real capital flows. Validate fills, fees, and leverage
    behaviour here before clearing the paper-trading gate.
    """

    def __init__(self, exchange, price_feed: PriceFeed, allow_live_orders: bool = False) -> None:
        self.exchange = exchange            # a configured ccxt.binance() instance
        self.price_feed = price_feed
        self.allow_live_orders = allow_live_orders

    def get_balance(self) -> float:
        bal = self.exchange.fetch_balance()
        return float(bal["USDT"]["free"])

    def _guard(self) -> None:
        if not self.allow_live_orders:
            raise RuntimeError(
                "Live order blocked: set allow_live_orders=True only after smoke-testing on a "
                "tiny balance. This guard prevents an unverified deploy from trading real money."
            )

    def open_position(self, pair: str, side: str, size_usd: float, leverage: float,
                      strategy: str) -> Position:
        self._guard()
        mark = self.price_feed.get_price(pair)
        self.exchange.set_leverage(int(leverage), pair)
        amount = (size_usd * leverage) / mark
        order_side = "buy" if side == "long" else "sell"
        # Limit at the mark (entry_pricing in config nudges to the book); reduceOnly stays False.
        self.exchange.create_order(pair, "limit", order_side, amount, mark)
        return Position(
            pair=pair, side=side, size_usd=size_usd, leverage=leverage,
            entry_price=mark, strategy=strategy, opened_at=_utcnow_iso(),
        )

    def close_position(self, position: Position, reason: str) -> ClosedTrade:
        self._guard()
        mark = self.price_feed.get_price(position.pair)
        amount = (position.size_usd * position.leverage) / position.entry_price
        order_side = "sell" if position.side == "long" else "buy"
        self.exchange.create_order(position.pair, "market", order_side, amount, None,
                                   {"reduceOnly": True})
        pnl_usd, pnl_pct = compute_pnl(
            position.side, position.entry_price, mark, position.size_usd, position.leverage
        )
        fee = position.size_usd * FEE_RATE_TAKER
        return ClosedTrade(
            position=position, exit_price=mark, pnl_usd=pnl_usd - fee, pnl_pct=pnl_pct,
            fee_usd=fee, reason=reason, closed_at=_utcnow_iso(),
        )
