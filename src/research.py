"""Multi-agent thesis stress-test. You name something ("look into Micron"); separate analysts
(Bull, Bear/short-seller, Risk) argue it from real recent data, then a skeptical Judge weighs the
cases and issues an honest verdict with a TIME HORIZON. Decision-support only; it reasons over
PUBLIC info so it has no edge, its value is forcing the bear case and an honest, calibrated read.
"""
from __future__ import annotations

ROLES = {
    "Bull": ("You are a bull analyst. Using the data, make the STRONGEST honest case to buy this. "
             "3-4 crisp specific points. Note whether the edge (if any) is short-term or a "
             "multi-year structural story. No hype, no fabrication."),
    "Bear": ("You are a short-seller. Make the STRONGEST honest case AGAINST buying this: why it "
             "may already be priced in, overvalued, or set to fall. 3-4 specific points. Be harsh."),
    "Risk": ("You are a risk manager. List the key risks, the single thing that would prove the "
             "thesis WRONG (invalidation), and how it should be sized. Be concrete."),
}

JUDGE = """You are a skeptical portfolio judge protecting the user's capital. You are given a bull
case, a bear case, a risk view, and real recent data. Default stance: most ideas are already
priced in, so the bar to buy is high. Weigh the arguments honestly and decide.

Output (plain text, no markdown, concise for a phone):
VERDICT: one of [BUY NOW] / [WAIT - near $X] / [AVOID]
HORIZON: commit to ONE, do not hedge. [short-term trade] = a specific catalyst or mispricing you
  expect to resolve in weeks (tight stop). [long-term hold] = a structural multi-year thesis (wide
  stop, ride volatility, small size). Say which and why in one line.
SIZE: small / medium (discretionary bet, keep it modest); suggest a % of capital (e.g. 5%)
WHY: 2-3 lines weighing bull vs bear
INVALIDATION: what proves it wrong
CONFIDENCE: low / medium / high, and why
HONESTY: one line, this is opinion over public information, not an edge. If Reddit sentiment is
  euphoric, say so and lean contrarian; weight Polymarket (real money) above social chatter.
COMMAND: the exact chat command to act, or 'no trade'. Use 'BUY <TICKER> <size>%' for a short-term
  trade, 'BUY <TICKER> <size>% hold' for a long-term hold, or 'no trade' for WAIT/AVOID. Use the
  subject's ticker. Example: BUY SOL 5% hold"""


def gather_context(subject: str, cfg) -> dict:
    ctx = {"subject": subject}
    if cfg.exa_api_key:
        try:
            from exa_py import Exa
            res = Exa(cfg.exa_api_key).search_and_contents(
                f"{subject} analysis valuation outlook risks catalysts", num_results=6, text=True)
            ctx["news"] = [{"title": getattr(r, "title", ""), "text": (getattr(r, "text", "") or "")[:700]}
                           for r in getattr(res, "results", [])]
        except Exception as e:
            ctx["news_err"] = str(e)
    base = subject.upper().split()[0]
    try:
        from .ccxt_feed import build_binance
        ex = build_binance(None, None)
        for sym in (f"{base}/USDT", f"{base}/USDT:USDT"):
            try:
                ctx["price"] = float(ex.fetch_ticker(sym)["last"]); ctx["symbol"] = sym; break
            except Exception:
                continue
    except Exception:
        pass
    if not ctx.get("price"):                       # not a Binance ticker → try it as a stock
        try:
            from .stocks import stock_quote
            px = stock_quote(base)
            if px:
                ctx["price"] = px; ctx["symbol"] = f"{base} (stock)"
        except Exception:
            pass
    if not ctx.get("price"):                       # still nothing → resolve crypto by name (CoinGecko)
        try:
            from .coingecko import coin_price
            hit = coin_price(subject)
            if hit:
                sym, px = hit
                ctx["price"] = px; ctx["symbol"] = f"{sym} (CoinGecko)"
        except Exception:
            pass
    try:                                            # crowd + money signals (in the /last30days spirit)
        from .social import reddit_signal, polymarket_signal
        ctx["reddit"] = reddit_signal(subject)
        ctx["polymarket"] = polymarket_signal(subject)
    except Exception:
        pass
    return ctx


def _price_line(ctx) -> str:
    """Human-readable price string for the verdict header, or an explicit 'unavailable'."""
    if ctx.get("price"):
        s = f"{ctx['price']:,.6f}".rstrip("0").rstrip(".")
        return f"Live price: ${s} ({ctx.get('symbol', '?')})"
    return "Live price: unavailable"


def _context_str(subject, ctx):
    news = "\n".join(f"- {n['title']}: {n['text'][:400]}" for n in ctx.get("news", [])) \
        or "(no recent news retrieved)"
    if ctx.get("price"):
        price = f"Current price: {ctx['price']} ({ctx['symbol']}). Use THIS price; do not change it."
    else:
        price = ("Live price NOT available. Do NOT invent or assume a specific price or price "
                 "level. Reason qualitatively and say the entry price is unknown.")
    if ctx.get("reddit"):
        reddit_block = ("Reddit, last 30 days, ranked by upvotes (community sentiment — CONTEXT only; "
                        "at extremes often a CONTRARIAN signal, peak hype tends to mark a top, NOT a "
                        "buy signal):\n" +
                        "\n".join(f"- [{r['score']} upvotes, {r['comments']} comments, r/{r['sub']}] "
                                  f"{r['title']}" for r in ctx["reddit"][:8]))
    else:
        # Reddit's public API is currently blocked → empty. Make clear this is MISSING data, so the
        # panel does NOT misread an absence as "quiet sentiment" / a contrarian signal.
        reddit_block = ("Reddit: data UNAVAILABLE this run (public API blocked, not fetched). Draw NO "
                        "sentiment conclusion from this absence — it is missing data, not silence.")
    poly = "\n".join(f"- {p['title']}: {p['odds']}" for p in ctx.get("polymarket", []) if p.get("odds")) \
        or "(no relevant prediction markets found)"
    return (f"Subject: {subject}\n{price}\n\nRecent news:\n{news}\n\n{reddit_block}\n\n"
            f"Polymarket real-money prediction odds (the strongest crowd signal here, people betting "
            f"cash — but only relevant if a market actually matches the subject):\n{poly}")


def research(subject: str, cfg) -> str:
    if not cfg.anthropic_api_key:
        return "Research needs ANTHROPIC_API_KEY configured."
    try:
        from anthropic import Anthropic
        from .assistant import _plain
        client = Anthropic(api_key=cfg.anthropic_api_key)
        ctx_data = gather_context(subject, cfg)
        ctx = _context_str(subject, ctx_data)
        price_hdr = _price_line(ctx_data)

        def ask(system, user, mt=420):
            r = client.messages.create(model=cfg.claude_model, max_tokens=mt, system=system,
                                       messages=[{"role": "user", "content": user}])
            return r.content[0].text if r.content else ""

        # Bull / Bear / Risk are independent → run them concurrently (cuts ~3x off the wait).
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {role: pool.submit(ask, prompt, ctx) for role, prompt in ROLES.items()}
            views = {role: f.result() for role, f in futures.items()}
        judge_input = (f"{ctx}\n\nBULL CASE:\n{views['Bull']}\n\nBEAR CASE:\n{views['Bear']}\n\n"
                       f"RISK VIEW:\n{views['Risk']}\n\nNow issue your verdict.")
        verdict = ask(JUDGE, judge_input, mt=700)
        return _plain(f"🔬 Stress-test: {subject}\n{price_hdr}\n"
                      f"(debated by Bull / Bear / Risk, judged skeptically)\n\n{verdict}")
    except Exception as e:
        return f"Couldn't complete the research ({type(e).__name__})."


def is_research_request(text: str):
    import re
    m = re.match(r"^\s*(?:look into|research|analy[sz]e|thesis on|should i (?:buy|short))\s+(.+)",
                 text.strip(), re.I)
    return m.group(1).strip(" ?.") if m else None
