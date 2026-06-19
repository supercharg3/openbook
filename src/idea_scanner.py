"""Idea scanner — finds candidate names from where people bet and talk (Polymarket + financial
news), then runs each through the skeptical research stress-test and surfaces only what survives.

This is IDEA SOURCING, not an alpha source: it tells you what's being discussed/bet on, and the
bull/bear/risk panel vets it. You still decide. Nothing here auto-trades.

(paste.trade would slot in here as another source once it ships a public API; for now Exa news
covers the same media-idea ground, and Polymarket adds the real-money-bets angle.)
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from .names import NAMES

_UA = "mqbt-idea-scanner/1.0"


# Names that are also common English words → matching them in prose causes false positives
# ("visa application", "cash flow", "the sandbox"). For these we rely on the ticker match only.
_AMBIGUOUS = {"visa", "flow", "curve", "maker", "render", "stacks", "oasis", "sei", "gala",
              "theta", "sand", "the sandbox", "sui", "near", "rose", "kava", "ens", "apple"}


def _reverse_index():
    """Two matchers: company/coin NAMES (case-insensitive) and TICKERS (case-SENSITIVE uppercase,
    so the ticker 'COST' matches but the word 'cost' does not). Skips short/ambiguous keys."""
    names, tickers = {}, {}
    for tk, nm in NAMES.items():
        if len(nm) >= 4 and nm.lower() not in _AMBIGUOUS:
            names[re.compile(r"\b" + re.escape(nm.lower()) + r"\b")] = tk
        if len(tk) >= 3:
            tickers[re.compile(r"\b" + re.escape(tk) + r"\b")] = tk      # uppercase, case-sensitive
    return names, tickers


def _match(text: str, idx=None) -> dict:
    names, tickers = _reverse_index()
    low = (text or "").lower()
    hits = {}
    for rx, tk in names.items():
        if rx.search(low):
            hits[tk] = True
    for rx, tk in tickers.items():
        if rx.search(text or ""):           # match against ORIGINAL case (real tickers are uppercase)
            hits[tk] = True
    return hits


def polymarket_candidates(limit: int = 60) -> dict:
    """Tickers/coins that show up in the most-active Polymarket markets. {ticker: example question}."""
    found = {}
    try:
        url = ("https://gamma-api.polymarket.com/markets?closed=false&order=volumeNum&"
               f"ascending=false&limit={limit}")
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
        markets = data if isinstance(data, list) else data.get("data", [])
        for m in markets:
            q = m.get("question") or ""
            for tk in _match(q):
                found.setdefault(tk, q[:120])
    except Exception:
        pass
    return found


def news_candidates(cfg, query: str = "undervalued AI and semiconductor stocks to buy this month") -> dict:
    """Tickers/coins mentioned in recent financial media on the theme. {ticker: example headline}."""
    found = {}
    if not getattr(cfg, "exa_api_key", None):
        return found
    try:
        from exa_py import Exa
        res = Exa(cfg.exa_api_key).search_and_contents(query, num_results=6, text=True)
        for r in getattr(res, "results", []):
            blob = f"{getattr(r, 'title', '')} {getattr(r, 'text', '') or ''}"
            for tk in _match(blob):
                found.setdefault(tk, (getattr(r, "title", "") or "")[:120])
    except Exception:
        pass
    return found


def scan_ideas(cfg, max_candidates: int = 3) -> str:
    """Gather candidates from Polymarket + news, stress-test the top few, surface the verdicts."""
    from .research import research
    from .assistant import _plain
    cands = {}
    for tk, why in polymarket_candidates().items():
        cands.setdefault(tk, ("Polymarket", why))
    for tk, why in news_candidates(cfg).items():
        cands.setdefault(tk, ("in the news", why))

    if not cands:
        return ("I scanned Polymarket and the financial news and nothing notable surfaced that maps "
                "to names we can trade right now. Try again later, or send `look into <name>` directly.")

    picks = list(cands.items())[:max_candidates]
    out = [f"🔎 Idea scan — {len(picks)} candidate(s) found and stress-tested "
           f"(these are IDEAS, not signals, the panel's honest read is below):", ""]
    for tk, (src, why) in picks:
        verdict = research(tk, cfg)
        out.append(f"━━━ {tk} (spotted: {src}) ━━━")
        out.append(verdict)
        out.append("")
    return _plain("\n".join(out))


def is_scan_request(text: str) -> bool:
    return bool(re.search(r"\b(scan|find ideas|any ideas|idea scan|what should i buy|hunt)\b",
                          text.strip(), re.I))
