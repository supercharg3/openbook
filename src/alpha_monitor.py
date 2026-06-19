"""Alpha channel monitor — scrapes public Telegram channels for trade signals, no auth needed.

Flow:
  1. Poll https://t.me/s/{channel} every 5 minutes (public HTML, no login required).
  2. Extract new messages (tracks last-seen message ID in the database).
  3. Each new message is parsed by Claude Haiku to extract: ticker, direction, confidence.
  4. High/medium-confidence signals run through the full bear/bull research panel.
  5. Auto-route:
       - Crypto → degen sleeve (queued for next 15-min degen cycle)
       - Stock  → swing sleeve (queued as a thesis order)
       - AVOID  → notify only, no trade
  6. Telegram notification with the full verdict + action taken.

Requires only:
  ALPHA_CHANNELS — comma-separated public channel usernames, e.g. "paste_trade"
  No Telegram account, no session file, no API keys beyond what's already configured.
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.request
from datetime import datetime, timezone
from html import unescape

from .config import get_config
from .venues import classify_venue


# ── Web scraper ───────────────────────────────────────────────────────────────

def fetch_messages(channel: str) -> list[dict]:
    """Fetch the latest messages from a public Telegram channel via t.me/s/.

    Returns a list of dicts with keys: id (int), text (str).
    Newest messages last, so we can process in order.
    """
    url = f"https://t.me/s/{channel}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[alpha] fetch failed for @{channel}: {e}")
        return []

    messages = []
    # Each message block: data-post="channel/ID"
    blocks = re.findall(
        r'data-post="[^/]+/(\d+)".*?'
        r'js-message_text[^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    for msg_id_str, raw_text in blocks:
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", raw_text)
        text = unescape(text).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            messages.append({"id": int(msg_id_str), "text": text})

    return sorted(messages, key=lambda m: m["id"])


# ── Signal parsing (Claude Haiku — cheap, fast) ───────────────────────────────

PARSE_PROMPT = """You are extracting trading signals from a Telegram alpha channel message.

Message:
{message}

Extract the trading signal. Respond in this exact format (one line each, no extra text):
TICKER: <asset ticker uppercase, e.g. DOGE, SOL, NVDA, BTC — or NONE if no clear ticker>
DIRECTION: <LONG | SHORT | NEUTRAL | NONE>
CONFIDENCE: <HIGH | MEDIUM | LOW> — how clearly is this a trade call vs noise/commentary?
CONTEXT: <one sentence: why they like this trade>

If this is not a trade signal (news, memes, commentary), output TICKER: NONE."""


def parse_signal(message: str, cfg) -> dict | None:
    """Use Claude Haiku to extract a structured signal. Returns None if not a trade call."""
    if not cfg.anthropic_api_key:
        return None
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=cfg.anthropic_api_key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": PARSE_PROMPT.format(message=message[:800])}],
        )
        text = r.content[0].text if r.content else ""
        result = {}
        for line in text.strip().splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip().upper()] = v.strip()
        ticker = result.get("TICKER", "NONE").upper()
        direction = result.get("DIRECTION", "NONE").upper()
        confidence = result.get("CONFIDENCE", "LOW").upper()
        if ticker == "NONE" or direction == "NONE" or confidence == "LOW":
            return None
        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": confidence,
            "context": result.get("CONTEXT", ""),
        }
    except Exception as e:
        print(f"[alpha] parse_signal error: {e}")
        return None


# ── Routing ───────────────────────────────────────────────────────────────────

def route_signal(ticker: str, verdict: str, direction: str, db) -> str:
    """Queue the trade in the right sleeve. Returns a description of the action taken."""
    if "AVOID" in verdict.upper():
        return "no-trade (research says AVOID)"

    venue = classify_venue(ticker)
    now = datetime.now(timezone.utc).isoformat()

    if venue == "crypto":
        db.set_state(f"degen_alpha_{ticker}", f"{direction}|{now}", now)
        return "queued for degen sleeve (crypto, next 15-min cycle)"

    if venue == "stock":
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO thesis_orders (created_at, action, pair, size_pct, status) VALUES (?,?,?,?,?)",
                (now, "buy" if direction == "LONG" else "sell", ticker, 5.0, "pending"),
            )
        return "queued for swing sleeve (stock thesis)"

    return "no-trade (unknown asset type)"


# ── Main poll loop ────────────────────────────────────────────────────────────

def poll_once(channels: list[str], cfg, db) -> None:
    """One poll pass across all configured channels. Called every POLL_INTERVAL seconds."""
    from .research import research

    for channel in channels:
        messages = fetch_messages(channel)
        if not messages:
            continue

        last_id = int(db.get_state(f"alpha_last_id_{channel}") or 0)
        new_messages = [m for m in messages if m["id"] > last_id]

        for msg in new_messages:
            print(f"[alpha] @{channel}/{msg['id']}: {msg['text'][:80]}")
            signal = parse_signal(msg["text"], cfg)
            if signal:
                ticker = signal["ticker"]
                verdict = research(ticker, cfg)
                action = route_signal(ticker, verdict, signal["direction"], db)
                header = (
                    f"📡 Alpha signal — @{channel}\n"
                    f"{ticker} {signal['direction']} ({signal['confidence']} confidence)\n"
                    f"Their take: {signal['context']}\n"
                    f"Action: {action}\n\n"
                )
                _notify(cfg, header + verdict)
            # Mark seen regardless — don't re-process on next poll
            db.set_state(f"alpha_last_id_{channel}", str(msg["id"]),
                         datetime.now(timezone.utc).isoformat())


def run_loop(channels: list[str], cfg, db, interval: int = 300) -> None:
    """Blocking poll loop. Runs forever, polling every `interval` seconds (default 5 min)."""
    print(f"[alpha] polling {channels} every {interval}s")
    while True:
        try:
            poll_once(channels, cfg, db)
        except Exception as e:
            print(f"[alpha] poll error: {e}")
        time.sleep(interval)


def _notify(cfg, text: str) -> None:
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(text); return
    try:
        from telegram import Bot

        async def _s():
            kw = {"message_thread_id": cfg.telegram_topic_id} if cfg.telegram_topic_id else {}
            await Bot(cfg.telegram_bot_token).send_message(
                chat_id=cfg.telegram_chat_id, text=text[:4000], **kw)
        asyncio.run(_s())
    except Exception as e:
        print(f"[alpha] notify failed: {e}")
