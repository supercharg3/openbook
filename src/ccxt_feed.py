"""Live market data via ccxt (Binance USDT-perp futures).

Implements the PriceFeed protocol. Public market data (prices, funding, OHLCV) needs no API
key, so this is also what paper trading uses to get *real* market data while the
DryRunExecutionClient simulates the fills.
"""
from __future__ import annotations

from .funding_arb import ENTER_RATE, FundingSnapshot


class CcxtPriceFeed:
    """Wraps a ccxt.binance() instance configured for futures.

    Pair convention: callers use "BTC/USDT"; Binance perps are "BTC/USDT:USDT". We append the
    settle suffix transparently so the rest of the system stays in the simple notation.
    """

    def __init__(self, exchange, settle_suffix: str = ":USDT") -> None:
        self.exchange = exchange
        self.settle_suffix = settle_suffix

    def _sym(self, pair: str) -> str:
        return pair if ":" in pair else f"{pair}{self.settle_suffix}"

    def _unsym(self, market_symbol: str) -> str:
        return market_symbol.split(":")[0]

    def get_price(self, pair: str) -> float:
        ticker = self.exchange.fetch_ticker(self._sym(pair))
        return float(ticker["last"])

    def get_funding_rates(self) -> list[FundingSnapshot]:
        """All perps whose funding clears the entry threshold (the orchestrator ranks them)."""
        rates = self.exchange.fetch_funding_rates()
        out: list[FundingSnapshot] = []
        for market_symbol, info in rates.items():
            rate = info.get("fundingRate")
            if rate is None:
                continue
            rate = float(rate)
            if rate >= ENTER_RATE:
                out.append(FundingSnapshot(symbol=self._unsym(market_symbol), funding_rate=rate))
        return out

    def get_ohlcv(self, pair: str, timeframe: str = "1d", limit: int = 60) -> list[list[float]]:
        return self.exchange.fetch_ohlcv(self._sym(pair), timeframe=timeframe, limit=limit)


def build_binance(api_key: str | None, api_secret: str | None):
    """Construct a ccxt Binance futures client. Read-only if keys are None (public data only)."""
    import ccxt

    return ccxt.binance({
        "apiKey": api_key or "",
        "secret": api_secret or "",
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
