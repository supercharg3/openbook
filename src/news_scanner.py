"""Layer 2: medium-speed information trading.

Exa surfaces slower-moving signals (governance votes, research, smaller partnership news);
Claude Haiku scores each for trade direction + confidence. We act only when:
  - confidence >= MIN_CONFIDENCE, and
  - price hasn't already moved more than MAX_PRICE_DRIFT since the article timestamp
(retail cannot beat HFT on millisecond news; the edge is the 5–30 min window).

Sizing tiers by confidence: 0.75–0.80 → 5%, 0.80–0.90 → 10%, >0.90 → 15% of capital.

The Exa + Anthropic clients are injected so this is testable with fakes. Network calls are
isolated in the thin `_search` / `_score` adapters.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MIN_CONFIDENCE = 0.75
MAX_PRICE_DRIFT = 0.005   # 0.5% — if it already moved this much, we're too late

CRYPTO_QUERY_TERMS = [
    "crypto protocol upgrade", "token governance vote", "exchange listing",
    "crypto regulatory decision", "blockchain partnership announcement",
    "defi exploit", "stablecoin depeg",
]

SCORING_PROMPT = """You are a crypto trading signal classifier. Given a news item, output STRICT JSON:
{{"direction": "long"|"short"|"neutral", "confidence": 0.0-1.0, "asset": "<TICKER or NONE>", "rationale": "<one sentence>"}}

Rules:
- direction "neutral" with confidence 0 if the item is not market-moving.
- confidence reflects how actionable and clear the signal is, not how big the move.
- asset is the single most-affected ticker (e.g. BTC, ETH, SOL, ARB) or NONE.

News item:
Title: {title}
Text: {text}
Published: {published}
"""


@dataclass
class NewsItem:
    title: str
    text: str
    url: str
    published: str          # ISO8601


@dataclass
class SignalScore:
    direction: str          # long | short | neutral
    confidence: float
    asset: str              # ticker or "NONE"
    rationale: str


@dataclass
class TradeIntent:
    asset: str
    direction: str
    confidence: float
    size_fraction: float    # of capital
    rationale: str
    source_url: str


def size_fraction_for_confidence(confidence: float) -> float:
    if confidence > 0.90:
        return 0.15
    if confidence >= 0.80:
        return 0.10
    if confidence >= MIN_CONFIDENCE:
        return 0.05
    return 0.0


def should_act(score: SignalScore, price_drift_since_publish: float) -> bool:
    """Decide whether a scored signal is still tradeable."""
    if score.direction == "neutral" or score.asset == "NONE":
        return False
    if score.confidence < MIN_CONFIDENCE:
        return False
    if abs(price_drift_since_publish) > MAX_PRICE_DRIFT:
        return False  # market already moved — no edge left
    return True


def build_intent(score: SignalScore, item: NewsItem) -> TradeIntent | None:
    frac = size_fraction_for_confidence(score.confidence)
    if frac == 0.0:
        return None
    return TradeIntent(
        asset=score.asset,
        direction=score.direction,
        confidence=score.confidence,
        size_fraction=frac,
        rationale=score.rationale,
        source_url=item.url,
    )


def parse_score(raw_json: str) -> SignalScore:
    """Parse Claude's JSON output defensively (it may wrap in code fences)."""
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned)
    return SignalScore(
        direction=str(data.get("direction", "neutral")).lower(),
        confidence=float(data.get("confidence", 0.0)),
        asset=str(data.get("asset", "NONE")).upper(),
        rationale=str(data.get("rationale", "")),
    )


class NewsScanner:
    """Orchestrates Exa search → Haiku scoring → trade intents.

    exa_client     : object with .search_and_contents(query, ...) -> results  (exa_py.Exa)
    claude_client  : object with .messages.create(...)                         (anthropic.Anthropic)
    model          : Claude model id (Haiku)
    price_drift_fn : callable(asset, published_iso) -> float, the % move since publish
    """

    def __init__(self, exa_client, claude_client, model: str, price_drift_fn) -> None:
        self.exa = exa_client
        self.claude = claude_client
        self.model = model
        self.price_drift_fn = price_drift_fn

    def _search(self, query: str, limit: int = 5) -> list[NewsItem]:
        # Keep options minimal — the Exa SDK has tightened which kwargs it accepts.
        res = self.exa.search_and_contents(query, num_results=limit, text=True)
        items: list[NewsItem] = []
        for r in getattr(res, "results", []):
            items.append(NewsItem(
                title=getattr(r, "title", "") or "",
                text=(getattr(r, "text", "") or "")[:2000],
                url=getattr(r, "url", "") or "",
                published=getattr(r, "published_date", "") or "",
            ))
        return items

    def _score(self, item: NewsItem) -> SignalScore:
        prompt = SCORING_PROMPT.format(title=item.title, text=item.text, published=item.published)
        resp = self.claude.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else "{}"
        return parse_score(text)

    def scan(self, queries: list[str] | None = None) -> list[TradeIntent]:
        queries = queries or CRYPTO_QUERY_TERMS
        intents: list[TradeIntent] = []
        seen_urls: set[str] = set()
        for q in queries:
            for item in self._search(q):
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                score = self._score(item)
                drift = self.price_drift_fn(score.asset, item.published) if score.asset != "NONE" else 0.0
                if should_act(score, drift):
                    intent = build_intent(score, item)
                    if intent:
                        intents.append(intent)
        return intents
