"""Red-flag safety check for the stock factor picks (the disciplined 'council reviews the stocks').

After the mechanical screen selects a name, this asks: is there a CONCRETE, SERIOUS, disqualifying
problem that means we shouldn't hold it right now? It can VETO a pick, never ADD one — so it's a
safety filter, not a stock-picker (the council was clear LLM picking has no edge).

Deliberately conservative: it only vetoes for genuine disqualifiers (fraud, imminent bankruptcy,
delisting, active SEC enforcement, a pending buyout that caps the price). It does NOT veto for high
valuation, downgrades, a bad quarter, competition, or general bearishness — those are normal and the
mechanical strategy already prices them. Fails OPEN (no veto) on any error, so a research hiccup
never blocks a pick. Every decision is logged so we can later MEASURE if the veto adds value
(do vetoed names actually underperform the ones we held?).
"""
from __future__ import annotations

SYSTEM = """You are a conservative risk screener for a long-only stock holding. You are NOT picking
stocks and NOT judging whether it's a good buy — a mechanical strategy already chose it. Your ONLY
job: is there a CONCRETE, SERIOUS, DISQUALIFYING red flag that means we should not hold it right now?

VETO only for: accounting fraud or restatement, imminent bankruptcy/insolvency, delisting, an active
SEC/DOJ enforcement action, a pending acquisition/merger that caps the share price, or a catastrophic
company-specific event (e.g. the CEO just resigned amid scandal, a core product banned).

Do NOT veto for: high valuation, analyst downgrades, a weak quarter, rich multiples, competition,
sector rotation, or general bearish opinion. Those are normal and NOT your concern.

Default strongly to OK. Only VETO if there is a specific, severe, named red flag in the evidence.
Reply on ONE line exactly: 'VETO: <reason>' or 'OK: <one-line note>'."""


def red_flag_check(ticker: str, cfg) -> dict:
    """Return {'vetoed': bool, 'reason': str}. Fails OPEN (not vetoed) on any problem."""
    if not cfg.anthropic_api_key:
        return {"vetoed": False, "reason": "no screener configured"}
    try:
        evidence = _recent_news(ticker, cfg)
        from anthropic import Anthropic
        client = Anthropic(api_key=cfg.anthropic_api_key)
        user = f"Stock: {ticker}\n\nRecent news/evidence:\n{evidence}\n\nIs there a disqualifying red flag?"
        resp = client.messages.create(model=cfg.claude_model, max_tokens=120, system=SYSTEM,
                                       messages=[{"role": "user", "content": user}])
        text = (resp.content[0].text if resp.content else "").strip()
        vetoed = text.upper().startswith("VETO")
        reason = text.split(":", 1)[1].strip() if ":" in text else text
        return {"vetoed": vetoed, "reason": reason[:200]}
    except Exception as e:
        return {"vetoed": False, "reason": f"screen error ({type(e).__name__}) — failed open"}


def _recent_news(ticker: str, cfg) -> str:
    if not cfg.exa_api_key:
        return "(no news source configured)"
    try:
        from exa_py import Exa
        res = Exa(cfg.exa_api_key).search_and_contents(
            f"{ticker} stock fraud bankruptcy delisting SEC investigation acquisition halted",
            num_results=5, text=True)
        items = getattr(res, "results", [])
        return "\n".join(f"- {getattr(r, 'title', '')}: {(getattr(r, 'text', '') or '')[:300]}"
                         for r in items) or "(no recent news found)"
    except Exception:
        return "(news fetch failed)"
