"""CoinGecko price lookup — resolves a crypto by NAME or ticker (e.g. 'hyperliquid' -> HYPE) and
returns a live USD price. Free, no API key. Used as a fallback when the Binance feed can't price
something by its ticker (many coins aren't on Binance, or the user types the project name).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


def coin_price(query: str):
    """Return (symbol, usd_price) for the best-matching coin, or None. Never raises."""
    q = query.strip().lower()
    if not q:
        return None
    try:
        url = "https://api.coingecko.com/api/v3/search?query=" + urllib.parse.quote(q)
        with urllib.request.urlopen(url, timeout=10) as r:
            coins = (json.load(r).get("coins") or [])
        if not coins:
            return None
        # Guard against fuzzy mismatches (e.g. "micron" -> "MUON" coin): the top hit's symbol/id/
        # name must actually relate to the query, else we'd hand back a wrong price for a stock.
        import re
        def _norm(s):
            return re.sub(r"[^a-z0-9]", "", (s or "").lower())
        q = _norm(query)
        top = coins[0]
        sym_n, id_n, name_n = _norm(top.get("symbol")), _norm(top.get("id")), _norm(top.get("name"))
        if not (q and (sym_n == q or q in id_n or id_n in q or q in name_n or name_n in q)):
            return None
        cid = top["id"]
        sym = (top.get("symbol") or "").upper()
        purl = f"https://api.coingecko.com/api/v3/simple/price?ids={urllib.parse.quote(cid)}&vs_currencies=usd"
        with urllib.request.urlopen(purl, timeout=10) as r:
            px = json.load(r).get(cid, {}).get("usd")
        return (sym or query.upper(), float(px)) if px else None
    except Exception:
        return None
