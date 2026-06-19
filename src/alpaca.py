"""Alpaca stock execution for the thesis sleeve — plain REST, no gateway/Docker/Xvfb.

This is the `stock_exec` client the orchestrator routes stock theses to. Implements the same
ExecutionClient shape (open_position / close_position / get_balance) as the crypto clients.

Why Alpaca over IBKR for an autonomous system: it's a REST API behind an API key, so there's no
long-running gateway to crash, no nightly-maintenance disconnects, no memory hog. Alpaca also
supports NOTIONAL orders (buy $25 of a stock) so fractional sizing is built in — no share math.

Symbols arrive canonical as '{BASE}/USDT'; we translate to the bare US ticker ('MU'). Stocks are
long-only here (cash account). The `client` is injected so unit tests use a fake; build_alpaca()
makes the real one.
"""
from __future__ import annotations

import time

from .execution import ClosedTrade, Position, compute_pnl, _utcnow_iso

FILL_POLL_SECONDS = 5.0      # how long to wait for a market order to fill before giving up
FILL_POLL_INTERVAL = 0.5


def build_alpaca(cfg):
    """Construct an Alpaca TradingClient from config. Lazy import so the dep is optional."""
    from alpaca.trading.client import TradingClient
    return TradingClient(cfg.alpaca_api_key_id, cfg.alpaca_api_secret, paper=cfg.alpaca_paper)


def _sym(pair: str) -> str:
    return pair.split("/")[0].upper()


class AlpacaExecutionClient:
    def __init__(self, client, *, paper: bool = True, price_fn=None) -> None:
        self.client = client                 # alpaca TradingClient (or a fake in tests)
        self.paper = paper
        self._price_fn = price_fn            # optional symbol -> price, for marks when not yet filled

    # ── balance ──────────────────────────────────────────────────────────────
    def get_balance(self) -> float:
        acct = self.client.get_account()
        return float(getattr(acct, "cash", None) or getattr(acct, "buying_power", 0.0))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _latest_price(self, symbol: str) -> float:
        if self._price_fn:
            try:
                p = self._price_fn(symbol)
                if p:
                    return float(p)
            except Exception:
                pass
        from .stocks import stock_quote            # fallback: Yahoo Finance
        return float(stock_quote(symbol) or 0.0)

    def _await_fill(self, order_id):
        """Poll the order until it fills (or we time out). Market orders fill fast in hours."""
        deadline = time.monotonic() + FILL_POLL_SECONDS
        order = None
        while time.monotonic() < deadline:
            order = self.client.get_order_by_id(order_id)
            if str(getattr(order, "status", "")).lower().endswith("filled") and \
                    getattr(order, "filled_avg_price", None):
                return order
            time.sleep(FILL_POLL_INTERVAL)
        return order

    # ── orders ───────────────────────────────────────────────────────────────
    def open_position(self, pair: str, side: str, size_usd: float, leverage: float,
                      strategy: str) -> Position:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        symbol = _sym(pair)
        req = MarketOrderRequest(
            symbol=symbol, notional=round(size_usd, 2),      # notional = fractional sizing, no share math
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        submitted = self.client.submit_order(req)
        filled = self._await_fill(submitted.id)
        avg = getattr(filled, "filled_avg_price", None)
        price = float(avg) if avg else self._latest_price(symbol)
        qty = getattr(filled, "filled_qty", None)
        size = float(qty) * price if qty and float(qty) > 0 else size_usd
        return Position(pair=pair, side=side, size_usd=size, leverage=1.0,
                        entry_price=price or 0.0, strategy=strategy, opened_at=_utcnow_iso())

    def close_position(self, position: Position, reason: str) -> ClosedTrade:
        symbol = _sym(position.pair)
        try:
            self.client.close_position(symbol)               # liquidate the whole position
        except Exception:
            pass
        price = self._latest_price(symbol) or position.entry_price
        pnl_usd, pnl_pct = compute_pnl(position.side, position.entry_price, price,
                                       position.size_usd, 1.0)
        return ClosedTrade(position=position, exit_price=price, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                           fee_usd=0.0, reason=reason, closed_at=_utcnow_iso())
