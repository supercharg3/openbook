"""Autonomous long-only stock factor engine (council-approved design, 18 Jun 2026).

The brain is MECHANICAL, not LLM: rank a liquid large-cap universe by momentum (proximity to the
52-week high, with a 200-day trend filter), hold the top handful equal-weight, rebalance monthly,
sell on rank-drop (no arbitrary price targets), with an ATR catastrophe stop as a seatbelt.

v1 = momentum sleeve (price-only, fully reliable). Quality sleeve (gross-profits-to-assets, needs
fundamentals) is the next layer. Everything here is pure/testable; the runner wires it to Alpaca.

NON-NEGOTIABLES baked in (from the council):
- Liquid large-caps ONLY (small-caps bleed 60-200bps/round-trip and free data misrepresents them).
- Unlevered (1x) — leverage would contaminate the "does the picking beat SPY?" measurement.
- Paper until it clears the gate (IR>0.5, +net alpha, t>2, 100+ trades / 12+ months incl. a downturn).
"""
from __future__ import annotations

# Liquid large-cap universe (mega-caps + liquid sector leaders). Small-caps deliberately excluded.
LIQUID_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "AMD", "MU",
    "QCOM", "INTC", "TXN", "ADBE", "CRM", "ORCL", "CSCO", "ACN", "IBM", "NOW",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "SCHW",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR",
    "XOM", "CVX", "COP", "WMT", "COST", "HD", "PG", "KO", "PEP", "MCD",
    "NKE", "DIS", "NFLX", "BA", "CAT", "GE", "HON", "UNP", "LIN",
]

# AI + semiconductor universe (concentrated thematic sleeve). Liquid US-listed names only.
AI_SEMI_UNIVERSE = [
    "NVDA", "AMD", "AVGO", "MU", "QCOM", "INTC", "TXN", "ASML", "AMAT", "LRCX",
    "KLAC", "ADI", "MRVL", "MCHP", "ON", "NXPI", "TSM", "ARM", "SMCI", "ANET",
    "CRDO", "MPWR", "TER", "SWKS", "QRVO", "WDC", "SNPS", "CDNS", "PLTR", "DELL",
]

# Two sleeves run side by side so we can MEASURE whether concentrating in AI beats diversifying.
# Each benchmarks against a fair opponent: the diversified book vs the whole market (SPY); the AI
# book vs the AI/chip sector itself (SMH) — so "winning" means beating the sector, not just riding it.
SLEEVES = [
    {"name": "Diversified", "tag": "factor", "benchmark": "SPY"},      # universe filled at runtime
    {"name": "AI & Semis", "tag": "factor-ai", "benchmark": "SMH"},
]

TARGET_POSITIONS = 8           # how many names to hold (council: 5-8)
MA_WINDOW = 200                # trend filter: only hold names above their 200-day average
HIGH_WINDOW = 252              # ~52 weeks of trading days for the 52-week high
ATR_WINDOW = 14
CATASTROPHE_ATR_MULT = 3.0     # between-rebalance seatbelt: stop at -3*ATR ...
CATASTROPHE_MAX_LOSS = 0.20    # ... or -20%, whichever triggers first
REBALANCE_TRADING_DAYS = 21    # ~monthly


def momentum_score(closes: list[float]) -> dict | None:
    """Momentum metrics for one name from its daily closes. None if not enough history.

    proximity = price / 52-week-high  (1.0 = at the high; the ranking signal)
    above_ma  = price > 200-day moving average  (the trend filter)
    """
    if not closes or len(closes) < MA_WINDOW:
        return None
    price = closes[-1]
    high = max(closes[-HIGH_WINDOW:]) if len(closes) >= HIGH_WINDOW else max(closes)
    ma = sum(closes[-MA_WINDOW:]) / MA_WINDOW
    if not high or not price:
        return None
    return {"price": price, "proximity": price / high, "above_ma": price > ma}


def rank_and_select(scores: dict[str, dict], n: int = TARGET_POSITIONS) -> list[str]:
    """Pick the target holdings: names above their 200-day MA, ranked by 52-week-high proximity.

    Returns FEWER than n (even zero) when not enough names pass the trend filter — we hold cash
    rather than force-fill slots (council: forcing trades drifts you into closet-indexing).
    """
    eligible = [(t, s["proximity"]) for t, s in scores.items() if s and s["above_ma"]]
    eligible.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in eligible[:n]]


def combined_select(scores: dict[str, dict], gpa: dict[str, float],
                    n: int = TARGET_POSITIONS, quality_weight: float = 0.5) -> list[str]:
    """Blend momentum + quality. Names must pass the trend filter (above 200-day MA), then rank by
    the average of their momentum rank and quality rank (gross-profits-to-assets).

    Graceful: if no quality data is available (EDGAR down, or all financials with no GrossProfit),
    falls back to pure momentum so the system never stalls.
    """
    eligible = [t for t, s in scores.items() if s and s["above_ma"]]
    if not eligible:
        return []
    if not gpa:
        return rank_and_select(scores, n)
    mom_sorted = sorted(eligible, key=lambda t: scores[t]["proximity"], reverse=True)
    mom_rank = {t: i for i, t in enumerate(mom_sorted)}
    q_names = [t for t in eligible if t in gpa]
    q_sorted = sorted(q_names, key=lambda t: gpa[t], reverse=True)
    q_rank = {t: i for i, t in enumerate(q_sorted)}
    worst_q = len(q_sorted)                       # names without quality data → worst quality rank
    def blend(t):
        return (1 - quality_weight) * mom_rank[t] + quality_weight * q_rank.get(t, worst_q)
    return sorted(eligible, key=blend)[:n]


def rebalance_diff(current: set[str], target: set[str]) -> dict:
    """What to trade to move from current holdings to the target set."""
    return {"sell": sorted(current - target),
            "buy": sorted(target - current),
            "hold": sorted(current & target)}


def equal_weights(holdings: list[str]) -> dict[str, float]:
    """Equal-weight target as fractions of sleeve equity (empty list → all cash)."""
    if not holdings:
        return {}
    w = 1.0 / len(holdings)
    return {t: w for t in holdings}


def atr(highs: list[float], lows: list[float], closes: list[float], window: int = ATR_WINDOW) -> float:
    """Average True Range — used to size the catastrophe stop to each name's own volatility."""
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return 0.0
    trs = []
    for i in range(n - window, n):
        if i <= 0:
            continue
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def catastrophe_stop_hit(entry: float, current: float, atr_value: float) -> bool:
    """True if a held name breached the seatbelt: -3*ATR OR -20% from entry, whichever first."""
    if entry <= 0:
        return False
    loss_frac = (entry - current) / entry
    atr_stop = (atr_value * CATASTROPHE_ATR_MULT) / entry if entry else 1.0
    return loss_frac >= min(CATASTROPHE_MAX_LOSS, atr_stop) if atr_value else loss_frac >= CATASTROPHE_MAX_LOSS


def compute_scorecard(nav_rows: list[dict]) -> dict:
    """Honest benchmark scorecard from the NAV history (the shadow-SPY judge).

    nav_rows: chronological [{date, sleeve_value, spy_value}, ...]. Both series start equal (same
    cash flows). Returns total returns, excess, Information Ratio, and a t-stat for 'is this luck?'.
    """
    import math
    if len(nav_rows) < 2:
        return {"days": len(nav_rows), "verdict": "not enough data yet"}
    s0, p0 = nav_rows[0]["sleeve_value"], nav_rows[0]["spy_value"]
    s1, p1 = nav_rows[-1]["sleeve_value"], nav_rows[-1]["spy_value"]
    sleeve_ret = s1 / s0 - 1 if s0 else 0.0
    spy_ret = p1 / p0 - 1 if p0 else 0.0
    # daily excess returns for IR / t-stat
    excess = []
    for a, b in zip(nav_rows[:-1], nav_rows[1:]):
        sr = (b["sleeve_value"] / a["sleeve_value"] - 1) if a["sleeve_value"] else 0.0
        pr = (b["spy_value"] / a["spy_value"] - 1) if a["spy_value"] else 0.0
        excess.append(sr - pr)
    n = len(excess)
    mean = sum(excess) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in excess) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    ir = (mean / sd) * math.sqrt(252) if sd else 0.0          # annualized information ratio
    years = n / 252
    t_stat = ir * math.sqrt(years) if years > 0 else 0.0
    return {
        "days": len(nav_rows), "sleeve_return": sleeve_ret, "spy_return": spy_ret,
        "excess_return": sleeve_ret - spy_ret, "information_ratio": ir, "t_stat": t_stat,
        # graduation gate (council): IR>0.5 AND +excess AND t>2 AND 100+ trades / 12+ months
        "gate_cleared": ir > 0.5 and (sleeve_ret - spy_ret) > 0 and t_stat > 2 and n >= 252,
    }
