"""Alpha channel monitor — reads Telegram channels for trade signals, researches them, routes to sleeves.

Flow:
  1. Telethon client listens to configured ALPHA_CHANNELS (public channel usernames).
  2. Each new message is parsed by Claude (fast, cheap) to extract: ticker, direction, context.
  3. The ticker is run through the full bear/bull research panel (existing src/research.py).
  4. Based on asset type + verdict, the signal is queued for the right sleeve:
       - Crypto → degen sleeve (opens a paper position on next degen cycle)
       - Stock  → swing sleeve (queued as a thesis order for next swing cycle)
       - AVOID  → no trade, but still notified so you can manually act
  5. A Telegram notification is sent with the full verdict + what action was taken.

Requires:
  TELEGRAM_API_ID    — from https://my.telegram.org (not the bot token; this is your user account)
  TELEGRAM_API_HASH  — same source
  ALPHA_CHANNELS     — comma-separated, e.g. "paste_trade,another_channel"
  ALPHA_SESSION_FILE — path to the telethon .session file (default: ./data/alpha.session)

The telethon session authenticates your personal Telegram account (read-only). It only reads; it
never sends messages on your behalf. The session file is the auth token — keep it private and
add it to .gitignore (it already is via data/ exclusion).
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from .config import get_config
from .venues import classify_venue


# ── Signal parsing (Claude, fast model) ──────────────────────────────────────

PARSE_PROMPT = """You are extracting trading signals from a message posted in a Telegram alpha channel.

Message:
{message}

Extract the trading signal. Respond in this exact format (one line each, no extra text):
TICKER: <the asset ticker, uppercase, e.g. DOGE, SOL, NVDA, BTC — or NONE if no clear ticker>
DIRECTION: <LONG | SHORT | NEUTRAL | NONE>
CONFIDENCE: <HIGH | MEDIUM | LOW> — how clearly is this a trade signal vs noise/commentary?
CONTEXT: <one sentence summary of why they like this trade>

If the message is not a trade signal (news, commentary, memes), output TICKER: NONE."""


def parse_signal(message: str, cfg) -> dict | None:
    """Extract structured signal from a raw channel message. Returns None if not a trade signal."""
    if not cfg.anthropic_api_key:
        return None
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=cfg.anthropic_api_key)
        # Use haiku — this runs on every message, must be cheap
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": PARSE_PROMPT.format(message=message[:1000])}],
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
        print(f"[alpha] parse_signal failed: {e}")
        return None


# ── Routing ───────────────────────────────────────────────────────────────────

def route_signal(ticker: str, verdict: str, direction: str, cfg, db) -> str:
    """Given a research verdict, queue the trade in the appropriate sleeve. Returns action taken."""
    verdict_up = verdict.upper()
    if "AVOID" in verdict_up:
        return "no-trade (AVOID)"

    venue = classify_venue(ticker)
    now = datetime.now(timezone.utc).isoformat()

    if venue == "crypto":
        # Queue as a degen alpha signal — the next degen cycle will pick it up
        db.set_state(f"degen_alpha_{ticker}", f"{direction}|{now}", now)
        return f"queued → degen sleeve (crypto)"

    if venue == "stock":
        # Queue as a swing thesis order
        action = "buy" if direction == "LONG" else "sell"
        from .database import TradeRecord
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO thesis_orders (created_at, action, pair, size_pct, status) VALUES (?,?,?,?,?)",
                (now, action, ticker, 5.0, "pending"),
            )
        return f"queued → swing sleeve (stock thesis)"

    return "no-trade (unknown venue)"


# ── Main listener loop ────────────────────────────────────────────────────────

async def monitor(cfg, db, channels: list[str]) -> None:
    """Long-running coroutine that listens to alpha channels and processes new messages."""
    try:
        from telethon import TelegramClient, events
    except ImportError:
        print("[alpha] telethon not installed — run: pip install telethon"); return

    if not cfg.telegram_api_id or not cfg.telegram_api_hash:
        print("[alpha] TELEGRAM_API_ID / TELEGRAM_API_HASH not set — alpha monitor disabled.")
        return

    session_path = cfg.alpha_session_file or "./data/alpha.session"
    client = TelegramClient(session_path, int(cfg.telegram_api_id), cfg.telegram_api_hash)

    from .research import research

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        msg = event.message.message
        if not msg or len(msg.strip()) < 5:
            return

        channel = getattr(event.chat, "username", "unknown")
        print(f"[alpha] new message from @{channel}: {msg[:80]}")

        # 1. Parse
        signal = parse_signal(msg, cfg)
        if not signal:
            return  # not a trade signal

        ticker = signal["ticker"]
        direction = signal["direction"]
        context = signal["context"]
        print(f"[alpha] signal: {ticker} {direction} ({signal['confidence']})")

        # 2. Research
        verdict = research(ticker, cfg)

        # 3. Route
        action = route_signal(ticker, verdict, direction, cfg, db)

        # 4. Notify
        header = (f"📡 Alpha signal from @{channel}\n"
                  f"Ticker: {ticker} · Direction: {direction} · Confidence: {signal['confidence']}\n"
                  f"Their thesis: {context}\n"
                  f"Action: {action}\n\n")
        _notify(cfg, header + verdict)

    await client.start()
    print(f"[alpha] listening to: {channels}")
    await client.run_until_disconnected()


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
