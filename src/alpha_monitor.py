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
        if not target:
            return "no-trade (WAIT but no price target found in verdict)"

        # Validate: fetch current price and check target direction makes sense
        current_px = None
        try:
            if venue == "crypto":
                from .ccxt_feed import build_binance
                ex = build_binance(None, None)
                sym = ticker if "/" in ticker else f"{ticker}/USDT"
                current_px = float(ex.fetch_ticker(sym)["last"])
            else:
                from .stocks import stock_quote
                current_px = stock_quote(ticker)
        except Exception:
            pass

        if current_px:
            # LONG "dips to X": target must be below current price
            # SHORT "rallies to X": target must be above current price
            if direction == "LONG" and target >= current_px:
                return (f"no-trade (WAIT target ${target:,.4g} is above current ${current_px:,.4g} "
                        f"— can't wait for a dip that already passed)")
            if direction == "SHORT" and target <= current_px:
                return (f"no-trade (WAIT target ${target:,.4g} is below current ${current_px:,.4g} "
                        f"— can't wait for a rally that already passed)")
            # Also reject if target is unrealistically far (>50% away) — bad parse
            ratio = target / current_px
            if ratio > 2.0 or ratio < 0.1:
                return (f"no-trade (WAIT target ${target:,.4g} vs current ${current_px:,.4g} "
                        f"— looks like a bad parse, skipping)")
        elif venue == "stock":
            # Can't price it at all — non-tradeable ticker (BRENTOIL, SP500, 2327.TW etc)
            return f"no-trade (can't fetch price for {ticker} — not a tradeable stock on Alpaca)"

        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        condition = "lte" if direction == "LONG" else "gte"
        watch_ticker = ticker if (venue == "stock" or "/" in ticker) else f"{ticker}/USDT"

        # Deduplicate: skip if an active watch already exists for this ticker+direction
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM price_watches WHERE ticker=? AND direction=? AND expires_at > ?",
                (watch_ticker, direction, now)
            ).fetchone()[0]
            if existing:
                return f"no-trade (already watching {watch_ticker} {direction} — deduped)"

            # Cap: max 10 active watches — drop the oldest if full
            active = conn.execute(
                "SELECT id FROM price_watches WHERE expires_at > ? ORDER BY created_at",
                (now,)
            ).fetchall()
            if len(active) >= 10:
                conn.execute("DELETE FROM price_watches WHERE id=?", (active[0][0],))

        db.add_price_watch(watch_ticker, direction, target, condition, sleeve, context, now, expires)
        return f"👀 watching for entry near ${target:,.4g} (expires 48h) — will auto-enter when price arrives"

    # BUY NOW — enter immediately
    if venue == "crypto":
        watch_ticker = ticker if "/" in ticker else f"{ticker}/USDT"
        db.set_state(f"degen_alpha_{watch_ticker}", f"{direction}|5.0|{now}", now)
        return "entering now → degen sleeve"

    if venue == "stock":
        import sqlite3
        from .run_swing import MAX_OPEN_BETS, THESIS_ORDER_TTL_HOURS
        QUEUE_CAP = 3
        CONF_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.67, "LOW": 0.33}

        def _score(conf: str, created_iso: str) -> float:
            """Higher = better. Conviction × remaining time value (decays linearly to 0 at TTL)."""
            try:
                age_h = (datetime.now(timezone.utc) -
                         datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
                         ).total_seconds() / 3600
            except Exception:
                age_h = THESIS_ORDER_TTL_HOURS
            recency = max(0.0, 1.0 - age_h / THESIS_ORDER_TTL_HOURS)
            return CONF_WEIGHT.get(conf.upper(), 0.67) * recency

        with sqlite3.connect(db.db_path) as conn:
            pending_rows = conn.execute(
                "SELECT id, created_at, pair, confidence FROM thesis_orders WHERE status='pending' ORDER BY created_at"
            ).fetchall()
            open_bets = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE closed_at IS NULL AND strategy='swing'"
            ).fetchone()[0]

        slots_free = MAX_OPEN_BETS - open_bets
        queue_len = len(pending_rows)
        new_score = _score(confidence, now)

        bumped_msg = ""
        if slots_free <= 0 and queue_len >= QUEUE_CAP:
            # Find the lowest-scored queued order
            scored = sorted(pending_rows, key=lambda r: _score(r[3], r[1]))
            weakest = scored[0]
            weakest_score = _score(weakest[3], weakest[1])
            if new_score > weakest_score:
                with sqlite3.connect(db.db_path) as conn:
                    conn.execute("UPDATE thesis_orders SET status='bumped' WHERE id=?", (weakest[0],))
                bumped_msg = f" (bumped {weakest[2]} {weakest[3]} score={weakest_score:.2f})"
                print(f"[alpha] queue full — bumped #{weakest[0]} ({weakest[2]}, score {weakest_score:.2f}), new signal {ticker} score {new_score:.2f}")
            else:
                print(f"[alpha] queue full — new signal {ticker} score {new_score:.2f} weaker than all queued, dropped")
                return f"dropped — queue full with higher-conviction signals (your score {new_score:.2f})"

        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "INSERT INTO thesis_orders (created_at, action, pair, size_pct, status, confidence) VALUES (?,?,?,?,?,?)",
                (now, "buy" if direction == "LONG" else "sell", ticker, 5.0, "pending", confidence.upper()),
            )

        try:
            from .run_swing import run_thesis_now
            from .config import get_config as _cfg
            run_thesis_now(_cfg(), db)
        except Exception as e:
            print(f"[alpha] immediate swing run failed: {e}")

        if slots_free <= 0:
            return f"queued ({min(queue_len+1, QUEUE_CAP)}/{QUEUE_CAP}, score {new_score:.2f}){bumped_msg} — executes within 24h or expires"
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


# ── Swing price-watch monitor (runs every poll cycle) ────────────────────────

def check_swing_watches(cfg, db) -> None:
    """Check stock price watches; execute when target is hit. Runs every 5-min poll cycle."""
    watches = [dict(w) for w in db.active_watches() if w["sleeve"] == "swing"]
    if not watches:
        return

    tickers = list({w["ticker"].split("/")[0] for w in watches})
    prices = {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period="1d", interval="5m", progress=False,
                           group_by="ticker", auto_adjust=True, threads=True)
        for t in tickers:
            try:
                if len(tickers) == 1:
                    prices[t] = float(data["Close"].dropna().values[-1])
                else:
                    prices[t] = float(data[t]["Close"].dropna().values[-1])
            except Exception:
                pass
    except Exception as e:
        print(f"[alpha] swing watch yfinance error: {e}")
        return

    import sqlite3
    for w in watches:
        base = w["ticker"].split("/")[0]
        px = prices.get(base)
        if not px:
            continue
        target = float(w["target_price"])
        condition = w["condition"]
        triggered = (condition == "lte" and px <= target) or (condition == "gte" and px >= target)
        if not triggered:
            continue

        direction = w["direction"]
        print(f"[alpha] swing watch triggered: {base} {direction} @ ${px:.2f} (target ${target})")
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "INSERT INTO thesis_orders (created_at, action, pair, size_pct, status) VALUES (?,?,?,?,?)",
                (now, "buy" if direction == "LONG" else "sell", base, 5.0, "pending"),
            )
        try:
            from .run_swing import run_thesis_now
            run_thesis_now(cfg, db)
        except Exception as e:
            print(f"[alpha] swing watch exec failed: {e}")
        # Expire the watch so it can't re-trigger
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("UPDATE price_watches SET expires_at=? WHERE ticker=?", (now, w["ticker"]))
        cond_str = "dipped to" if condition == "lte" else "rallied to"
        from .names import display as _display
        name = _display(base)
        _notify(cfg, (
            f"🎯 <b>Price Watch Hit</b>\n"
            f"<b>{name}</b> {direction} {cond_str} ${px:,.2f} (target ${target:,.4g})\n"
            f"Entering via swing sleeve."
        ))


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
                from .names import display as _display
                name = _display(ticker)
                ticker_label = f"{ticker} ({name})" if name != ticker else ticker
                header = (
                    f"📡 <b>Alpha Signal</b> · @{channel}\n\n"
                    f"{dir_emoji} <b>{ticker_label}</b> · {signal['direction']} · {signal['confidence'].lower()} confidence\n"
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
        try:
            check_swing_watches(cfg, db)
        except Exception as e:
            print(f"[alpha] swing watch check error: {e}")
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
