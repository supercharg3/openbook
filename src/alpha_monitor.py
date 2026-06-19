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

PARSE_PROMPT = """You are extracting ALL trading signals from a Telegram alpha channel message.
A single message may contain multiple ticker calls — extract every one.

Message:
{message}

For EACH distinct ticker call, output one SIGNAL block. Confidence guide:
  HIGH = explicit call ("buy X", "long X", price target given)
  MEDIUM = directional lean with reasoning ("X breaking out", news implying direction)
  LOW = ticker mentioned, weak directional view

Format — repeat this block once per ticker:
SIGNAL
TICKER: <uppercase ticker, e.g. BTC, NVDA, ETH>
DIRECTION: <LONG | SHORT>
CONFIDENCE: <HIGH | MEDIUM | LOW>
CONTEXT: <one sentence — the key reason or catalyst>
END

If the message has no financial asset at all (memes, admin, off-topic), output only: NONE"""


def parse_signals(message: str, cfg) -> list[dict]:
    """Use Claude Haiku to extract ALL signals from a message. Returns a list (may be empty)."""
    if not cfg.anthropic_api_key:
        return []
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=cfg.anthropic_api_key)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": PARSE_PROMPT.format(message=message[:800])}],
        )
        text = r.content[0].text if r.content else ""
        if text.strip().upper() == "NONE":
            return []
        signals = []
        for block in re.split(r'\bSIGNAL\b', text):
            result = {}
            for line in block.strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    result[k.strip().upper()] = v.strip()
            ticker = result.get("TICKER", "NONE").strip().upper()
            direction = result.get("DIRECTION", "NONE").strip().upper()
            if ticker == "NONE" or ticker == "" or direction not in ("LONG", "SHORT"):
                continue
            signals.append({
                "ticker": ticker,
                "direction": direction,
                "confidence": result.get("CONFIDENCE", "LOW").strip().upper(),
                "context": result.get("CONTEXT", ""),
            })
        return signals
    except Exception as e:
        print(f"[alpha] parse_signals error: {e}")
        return []


# ── Routing ───────────────────────────────────────────────────────────────────

def _parse_wait_price(verdict: str) -> float | None:
    """Extract the target price from a WAIT verdict. e.g. 'WAIT - near $0.12' → 0.12"""
    import re
    m = re.search(r'WAIT[^$]*\$([0-9,.]+)', verdict, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def route_signal(ticker: str, verdict: str, direction: str, confidence: str, context: str, db) -> str:
    """Queue the trade in the right sleeve. Returns a description of the action taken.

    AVOID   → hard block, no trade.
    WAIT    → set a price watch; enter automatically when price reaches the target.
    BUY NOW → enter immediately at full size.
    """
    verdict_up = verdict.upper()
    if "AVOID" in verdict_up:
        return "no-trade (panel says AVOID — hard pass)"

    venue = classify_venue(ticker)
    now = datetime.now(timezone.utc).isoformat()
    sleeve = "degen" if venue == "crypto" else "swing"

    if "WAIT" in verdict_up:
        target = _parse_wait_price(verdict)
        if target:
            from datetime import timedelta
            expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            # For longs: buy when price dips to target (lte). For shorts: buy when price rises to target (gte).
            condition = "lte" if direction == "LONG" else "gte"
            # Normalise ticker to exchange format for crypto
            watch_ticker = ticker if "/" in ticker else f"{ticker}/USDT"
            db.add_price_watch(watch_ticker, direction, target, condition, sleeve, context, now, expires)
            return f"👀 watching for entry near ${target:,.4g} (expires 7d) — will auto-enter when price arrives"
        else:
            return "no-trade (WAIT but no price target found in verdict)"

    # BUY NOW — enter immediately
    if venue == "crypto":
        watch_ticker = ticker if "/" in ticker else f"{ticker}/USDT"
        db.set_state(f"degen_alpha_{watch_ticker}", f"{direction}|5.0|{now}", now)
        return "entering now → degen sleeve"

    if venue == "stock":
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO thesis_orders (created_at, action, pair, size_pct, status) VALUES (?,?,?,?,?)",
                (now, "buy" if direction == "LONG" else "sell", ticker, 5.0, "pending"),
            )
        return "entering now → swing sleeve"

    return "no-trade (unknown asset type)"


# ── Alpha-context research (lighter gate than the general look-into command) ──

ALPHA_JUDGE = """You are reviewing a trade idea sourced from a curated alpha channel. The idea has
already been human-filtered; your job is to catch genuine disasters, not gatekeep good setups.

Default stance: trade it UNLESS you see a clear red flag (blowup risk, market-wide panic, outright
fraud signal, or the thesis is factually wrong based on the data). WAIT is fine for timing; AVOID
is for real danger. Don't default to AVOID just because the edge is uncertain — that's every trade.

Output (plain text, concise for a phone):
VERDICT: [BUY NOW] | [WAIT - near $X] | [AVOID]
WHY: 1-2 lines — the key bull case and the one thing that could break it
INVALIDATION: what proves it wrong in one line
CONFIDENCE: low / medium / high"""


def research_alpha(subject: str, direction: str, context: str, cfg) -> str:
    """Lighter research pass for alpha channel signals — catches disasters, doesn't gatekeep."""
    if not cfg.anthropic_api_key:
        return "Research unavailable (no ANTHROPIC_API_KEY)."
    try:
        from anthropic import Anthropic
        from .research import gather_context, _context_str, _price_line, ROLES
        from .assistant import _plain
        from concurrent.futures import ThreadPoolExecutor

        client = Anthropic(api_key=cfg.anthropic_api_key)
        ctx_data = gather_context(subject, cfg)
        ctx = _context_str(subject, ctx_data)
        price_hdr = _price_line(ctx_data)

        def ask(system, user, mt=300):
            r = client.messages.create(model=cfg.claude_model, max_tokens=mt, system=system,
                                       messages=[{"role": "user", "content": user}])
            return r.content[0].text if r.content else ""

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {role: pool.submit(ask, prompt, ctx) for role, prompt in ROLES.items()}
            views = {role: f.result() for role, f in futures.items()}

        judge_input = (
            f"Alpha channel direction: {direction}\nChannel context: {context}\n\n"
            f"{ctx}\n\nBULL:\n{views['Bull']}\nBEAR:\n{views['Bear']}\nRISK:\n{views['Risk']}"
        )
        verdict = ask(ALPHA_JUDGE, judge_input, mt=400)
        return _plain(f"🔬 {subject} ({direction})\n{price_hdr}\n\n{verdict}")
    except Exception as e:
        return f"Research failed ({type(e).__name__})."


# ── Main poll loop ────────────────────────────────────────────────────────────

def poll_once(channels: list[str], cfg, db) -> None:
    """One poll pass across all configured channels. Called every POLL_INTERVAL seconds."""

    for channel in channels:
        messages = fetch_messages(channel)
        if not messages:
            continue

        last_id = int(db.get_state(f"alpha_last_id_{channel}") or 0)
        new_messages = [m for m in messages if m["id"] > last_id]

        for msg in new_messages:
            print(f"[alpha] @{channel}/{msg['id']}: {msg['text'][:80]}")
            signals = parse_signals(msg["text"], cfg)
            print(f"[alpha]   → {len(signals)} signal(s) found")
            for signal in signals:
                ticker = signal["ticker"]
                verdict = research_alpha(ticker, signal["direction"], signal["context"], cfg)
                action = route_signal(ticker, verdict, signal["direction"], signal["confidence"], signal["context"], db)
                dir_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
                header = (
                    f"📡 <b>Alpha Signal</b> · @{channel}\n\n"
                    f"{dir_emoji} <b>{ticker}</b> · {signal['direction']} · {signal['confidence'].lower()} confidence\n"
                    f"<i>{signal['context']}</i>\n\n"
                    f"<b>Action:</b> {action}\n\n"
                    f"{'─' * 20}\n"
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
                chat_id=cfg.telegram_chat_id, text=text[:4000], parse_mode="HTML", **kw)
        asyncio.run(_s())
    except Exception as e:
        print(f"[alpha] notify failed: {e}")
