"""Venue routing for the thesis sleeve.

A thesis order names a ticker (e.g. MU, NVDA, SOL). We must send crypto to the Binance client and
stocks to the IBKR client. Capital does NOT move between venues automatically (separate
institutions; trade-only keys can't withdraw) — the cross-venue split is a manual funding choice.
This module only decides WHICH venue a given ticker belongs to.

The thesis sleeve keeps every symbol in canonical '{BASE}/USDT' form for consistent position
matching; the stock execution client is responsible for translating that to its own contract
(e.g. 'MU/USDT' -> IBKR stock 'MU').
"""
from __future__ import annotations

# Liquid crypto we'd ever take a thesis on. Anything NOT here is treated as a stock ticker.
# (Production wraps this with a CoinGecko check so an unlisted coin isn't mis-routed to stocks.)
MAJOR_CRYPTO = {
    "BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "ARB", "GALA", "IMX", "SEI", "GRT",
    "BNB", "ADA", "MATIC", "LINK", "DOT", "LTC", "BCH", "ATOM", "NEAR", "APT", "OP",
    "SUI", "TIA", "INJ", "HYPE", "PEPE", "WIF", "TON", "TRX",
}


def classify_venue(base: str, crypto_universe: set[str] | None = None) -> str:
    """Return 'crypto' or 'stock' for a ticker base. Pure (no network) so it's unit-testable."""
    b = base.upper().strip()
    universe = MAJOR_CRYPTO | (crypto_universe or set())
    return "crypto" if b in universe else "stock"
