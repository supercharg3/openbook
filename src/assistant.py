"""Conversational assistant for the trading Telegram chat.

Any message that is not a fixed command (STOP, STATUS, etc.) is treated as a question and
answered by Claude, grounded in the system's REAL state (open positions, recent trades, capital,
regime, breaker). So you can ask "why did you open XRP~DOGE?" or "are we doing ok?" and get a
plain, honest answer based on actual data, not a canned reply. Read-only: the assistant never
trades, it only explains.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are the assistant for your autonomous crypto trading system. Answer
questions plainly, honestly, and concisely (this is a phone chat). No hype, no jargon dumps.

How the system works:
- It runs a MARKET-NEUTRAL statistical-arbitrage strategy: 11 validated pairs of correlated coins
  (e.g. SOL & AVAX). When a pair's price spread stretches too far (z-score > 2 std devs), it shorts
  the expensive coin and longs the cheap one, betting the spread reverts. Exit on convergence
  (|z| < 0.5) or a divergence stop (|z| > 4). Plus a small funding-rate arbitrage layer.
- It is patient by design: it only trades when a spread is genuinely stretched, so long quiet
  stretches with zero trades are NORMAL and correct, not a bug.
- Capital preservation first. Realistic target ~15-25%/year, NOT a fast-riches scheme. It is
  currently PAPER trading (no real money) until a readiness gate clears.
- Protections: 20% drawdown halt, per-pair stops, per-asset exposure cap, a pair-health monitor
  that pauses decayed pairs, and a circuit breaker that pauses everything if many pairs diverge at
  once.
- Directional strategies (trend, news) were REMOVED because they failed validation. Do not suggest
  re-adding guesses.

There are also TWO STOCK robots on Alpaca (practice money), separate from the crypto system. You
KNOW exactly how they pick, so explain it plainly when asked — never say you "can't see the
reasoning" or tell her to "check the logs":
- They pick MECHANICALLY, not by opinion. Each ranks its universe by MOMENTUM (how close a stock is
  to its 52-week high) PLUS QUALITY (gross-profits-to-assets, i.e. how profitable the company is for
  its size), keeps only names trading ABOVE their 200-day average (an uptrend filter), and holds the
  top ~8 equal-weight. Rebalanced monthly; a stock is sold when it drops out of that ranking. A
  red-flag check can skip a name with serious bad news (fraud, bankruptcy, etc.).
- "Diversified" sleeve = broad large-caps, judged against the S&P 500. "AI & Semis" sleeve = AI/chip
  names only, judged against the AI-sector ETF SMH.
- So if asked WHY a stock was picked: it ranked highest on the momentum + quality blend among the
  eligible (above-trend) names in that sleeve. It is the ranking, not a hunch. Say that clearly.
  Today's AI sleeve bought only 5 of ~30 names because most AI/chip stocks are currently below their
  200-day trend — it only buys what's actually rising.

Be truthful. If something is uncertain or the data does not show why, say so. Never invent trades
or numbers that are not in the state below. (You DO have a live Binance price feed for crypto;
live prices for any coins mentioned are included in the state below when available.)

Reply in PLAIN TEXT only. Do NOT use Markdown: no **asterisks**, no #headers, no `backticks`.
They show up as literal characters in this chat, so just write plain sentences."""


def is_price_request(text: str):
    """Return the ticker base (e.g. 'SOL') if this is a live-price question, else None."""
    import re
    t = text.strip()
    pats = [
        r"price of ([a-z0-9]{2,12})",
        r"([a-z0-9]{2,12})\s+price",
        r"how much is ([a-z0-9]{2,12})",
        r"what(?:'s|s| is)?\s+([a-z0-9]{2,12})\s+(?:at|now|trading|worth)\b",
        r"([a-z0-9]{2,12})\s+(?:at|trading at)\b",
    ]
    stop = {"THE", "PRICE", "WHAT", "HOW", "NOW", "OF", "IS", "ARE", "WE"}
    for p in pats:
        m = re.search(p, t, re.I)
        if m:
            base = m.group(1).upper()
            if base not in stop:
                return base
    return None


def lookup_price(base: str, price_feed) -> str:
    """Live price: try the Binance crypto feed first, then fall back to stocks (Yahoo Finance)."""
    for sym in (f"{base}/USDT", f"{base}/USDT:USDT"):
        try:
            px = price_feed.get_price(sym)
            if px:
                s = f"{px:,.4f}".rstrip("0").rstrip(".")
                return f"{base} is ${s} on Binance right now."
        except Exception:
            continue
    try:
        from .stocks import stock_quote
        px = stock_quote(base)
        if px:
            return f"{base} is ${px:,.2f} (stock, via Yahoo Finance)."
    except Exception:
        pass
    try:
        from .coingecko import coin_price          # resolves crypto by name too (hyperliquid -> HYPE)
        hit = coin_price(base)
        if hit:
            sym, px = hit
            s = f"{px:,.6f}".rstrip("0").rstrip(".")
            return f"{sym} is ${s} right now (via CoinGecko)."
    except Exception:
        pass
    return f"I couldn't fetch a live price for {base} (not found on Binance, Yahoo Finance, or CoinGecko)."


def _plain(text: str) -> str:
    """Strip Markdown artifacts so they don't show as literal characters in Telegram."""
    import re
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"^#{1,6}\s*", "", text, flags=re.M)


def build_context(db, cfg) -> str:
    lines = []
    capital = db.get_state("capital", str(cfg.starting_capital_usd))
    mtd = db.get_state("mtd_pnl", "0")
    regime = db.get_state("regime", "UNKNOWN")
    breaker = db.get_state("breaker_tripped", "0") == "1"
    mode = cfg.trading_mode
    lines.append(f"Mode: {mode} | Capital: ${capital} | MTD realized P&L: ${mtd} | "
                 f"BTC regime: {regime} | circuit breaker: {'TRIPPED' if breaker else 'normal'}")

    open_rows = db.open_positions()
    crypto = [r for r in open_rows if not str(r["strategy"]).startswith("factor")]
    diversified = [r for r in open_rows if str(r["strategy"]) == "factor"]
    ai = [r for r in open_rows if str(r["strategy"]) == "factor-ai"]

    lines.append(f"\nCRYPTO positions ({len(crypto)}):")
    for r in crypto:
        lines.append(f"  {r['side']} {r['pair']} ${r['size_usd']:.0f} entry {r['entry_price']}")
    if not crypto:
        lines.append("  none (waiting for a spread to stretch past the entry threshold)")

    lines.append(f"\nSTOCK robot — Diversified sleeve ({len(diversified)}), held because they ranked "
                 f"top on momentum+quality among broad large-caps:")
    for r in diversified:
        lines.append(f"  {r['pair']} ${r['size_usd']:.0f}")
    lines.append(f"\nSTOCK robot — AI & Semis sleeve ({len(ai)}), held because they ranked top on "
                 f"momentum+quality among AI/chip names that are above their 200-day trend:")
    for r in ai:
        lines.append(f"  {r['pair']} ${r['size_usd']:.0f}")

    closed = db.closed_trades(limit=8)
    lines.append(f"\nRecent closed trades ({len(closed)}):")
    for r in closed:
        lines.append(f"  {r['strategy']} {r['pair']} {r['exit_reason']} "
                     f"P&L ${(r['pnl_usd'] or 0):+.2f} closed {r['closed_at']}")
    if not closed:
        lines.append("  none yet")
    return "\n".join(lines)


def answer_question(question: str, db, cfg) -> str:
    if not cfg.anthropic_api_key:
        return "I cannot answer free-form questions yet (no Claude key configured)."
    try:
        from anthropic import Anthropic
        ctx = build_context(db, cfg)
        client = Anthropic(api_key=cfg.anthropic_api_key)
        resp = client.messages.create(
            model=cfg.claude_model,
            max_tokens=600,
            system=f"{SYSTEM_PROMPT}\n\n=== CURRENT SYSTEM STATE ===\n{ctx}",
            messages=[{"role": "user", "content": question}],
        )
        return _plain(resp.content[0].text) if resp.content else "(no answer)"
    except Exception as e:
        return f"Sorry, I couldn't answer that right now ({type(e).__name__})."
