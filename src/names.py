"""Human-readable names for tickers (stocks) and coins (crypto), for friendly STATUS output."""
from __future__ import annotations

NAMES = {
    # ── US stocks (factor universe) ──────────────────────────────────────────
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon",
    "GOOGL": "Alphabet", "META": "Meta", "AVGO": "Broadcom", "TSLA": "Tesla",
    "AMD": "AMD", "MU": "Micron", "QCOM": "Qualcomm", "INTC": "Intel",
    "TXN": "Texas Instruments", "ADBE": "Adobe", "CRM": "Salesforce", "ORCL": "Oracle",
    "CSCO": "Cisco", "ACN": "Accenture", "IBM": "IBM", "NOW": "ServiceNow",
    "JPM": "JPMorgan", "BAC": "Bank of America", "WFC": "Wells Fargo", "GS": "Goldman Sachs",
    "MS": "Morgan Stanley", "V": "Visa", "MA": "Mastercard", "AXP": "American Express",
    "BLK": "BlackRock", "SCHW": "Charles Schwab", "UNH": "UnitedHealth", "JNJ": "Johnson & Johnson",
    "LLY": "Eli Lilly", "ABBV": "AbbVie", "MRK": "Merck", "PFE": "Pfizer",
    "TMO": "Thermo Fisher", "ABT": "Abbott", "DHR": "Danaher", "XOM": "Exxon Mobil",
    "CVX": "Chevron", "COP": "ConocoPhillips", "WMT": "Walmart", "COST": "Costco",
    "HD": "Home Depot", "PG": "Procter & Gamble", "KO": "Coca-Cola", "PEP": "PepsiCo",
    "MCD": "McDonald's", "NKE": "Nike", "DIS": "Disney", "NFLX": "Netflix",
    "BA": "Boeing", "CAT": "Caterpillar", "GE": "GE Aerospace", "HON": "Honeywell",
    "UNP": "Union Pacific", "LIN": "Linde",
    # ── crypto coins ─────────────────────────────────────────────────────────
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB", "XRP": "XRP",
    "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche", "LINK": "Chainlink",
    "DOT": "Polkadot", "LTC": "Litecoin", "ATOM": "Cosmos", "NEAR": "NEAR", "INJ": "Injective",
    "UNI": "Uniswap", "AAVE": "Aave", "ARB": "Arbitrum", "OP": "Optimism", "SUI": "Sui",
    "APT": "Aptos", "FIL": "Filecoin", "TIA": "Celestia", "SEI": "Sei", "GALA": "Gala",
    "IMX": "Immutable", "GRT": "The Graph", "RUNE": "THORChain", "LDO": "Lido",
    "ICP": "Internet Computer", "ETC": "Ethereum Classic", "FTM": "Fantom", "HBAR": "Hedera",
    "ALGO": "Algorand", "VET": "VeChain", "AXS": "Axie Infinity", "SAND": "The Sandbox",
    "MANA": "Decentraland", "FLOW": "Flow", "EGLD": "MultiversX", "XLM": "Stellar",
    "XTZ": "Tezos", "THETA": "Theta", "CHZ": "Chiliz", "CRV": "Curve", "SNX": "Synthetix",
    "MKR": "Maker", "DYDX": "dYdX", "ENS": "ENS", "STX": "Stacks", "FET": "Fetch.ai",
    "AR": "Arweave", "KAVA": "Kava", "ROSE": "Oasis", "EOS": "EOS", "IOTA": "IOTA",
    "ZIL": "Zilliqa", "CFX": "Conflux", "GMT": "STEPN", "APE": "ApeCoin", "RENDER": "Render",
    "PENDLE": "Pendle", "JTO": "Jito",
}


def display(symbol: str) -> str:
    """'BAC' -> 'Bank of America (BAC)', 'GRT/USDT' -> 'The Graph (GRT)'. Falls back to the ticker."""
    base = symbol.split("/")[0].upper()
    name = NAMES.get(base)
    return f"{name} ({base})" if name else base
