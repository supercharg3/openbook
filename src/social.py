"""Crowd + money signals for the thesis stress-test, in the spirit of /last30days.

Pulls what real people engage with (Reddit, by upvotes) and what real money bets (Polymarket odds)
so the bull/bear/risk panel reasons over sentiment + prediction markets, not just published news.
Both are zero-key public endpoints. Everything is best-effort: any failure returns [] silently so
it can never break the research flow.

HONEST FRAMING (carried into the prompt): social sentiment is NOT alpha. Retail enthusiasm at
extremes is often a CONTRARIAN signal (peak hype = local top). Polymarket (real money) is the
stronger of the two. These improve the breadth/honesty of the read; they are not buy signals.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (last30days-research; +https://github.com/mvanhorn/last30days-skill)"


def _get_json(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def reddit_signal(query: str) -> list[dict]:
    """Top Reddit posts about the subject in the last month, scored by upvotes."""
    try:
        url = ("https://www.reddit.com/search.json?q=" + urllib.parse.quote(query) +
               "&sort=top&t=month&limit=8")
        data = _get_json(url)
        out = []
        for c in (data.get("data", {}).get("children") or []):
            d = c.get("data", {})
            out.append({
                "title": (d.get("title") or "")[:140],
                "score": int(d.get("score", 0) or 0),
                "comments": int(d.get("num_comments", 0) or 0),
                "sub": d.get("subreddit", ""),
            })
        return sorted(out, key=lambda r: r["score"], reverse=True)[:8]
    except Exception:
        return []


def polymarket_signal(query: str) -> list[dict]:
    """Active Polymarket prediction markets matching the subject, with their real-money odds."""
    try:
        url = ("https://gamma-api.polymarket.com/public-search?q=" + urllib.parse.quote(query) +
               "&limit_per_type=5&events_status=active")
        data = _get_json(url)
        out = []
        for ev in (data.get("events") or [])[:5]:
            title = ev.get("title") or ev.get("question") or ""
            odds = None
            markets = ev.get("markets") or []
            if markets:
                m = markets[0]
                try:
                    prices = json.loads(m.get("outcomePrices") or "[]")
                    outcomes = json.loads(m.get("outcomes") or "[]")
                    if prices and outcomes:
                        odds = ", ".join(f"{o} {float(p) * 100:.0f}%"
                                         for o, p in zip(outcomes, prices))
                except Exception:
                    pass
            if title:
                out.append({"title": title[:140], "odds": odds})
        return out
    except Exception:
        return []
