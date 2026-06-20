"""Agentic Conviction swing sleeve — reasoned-aggressive, PAPER, Alpaca stocks (v1).

Floor-and-swing control (src/swing.py): a hard floor + an account-level circuit breaker + a
profit-lock ratchet. Each run:
  1. mark the pot, ratchet the floor, and HALT everything if the pot touches the floor;
  2. manage exits on open bets (aggressive take-profit / stop);
  3. if there's room, RESEARCH one high-conviction candidate and take a capped bet,
     logging the reasoning in plain English (this log IS the testimonial).

HONEST: no validated edge — high variance by design. The floor is the only guarantee (we won't lose
the whole pot); it is not a promise of profit. Paper until it earns trust; then it's a config flip.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .config import get_config
from .database import Database
from .swing import floor_value, should_halt, risk_budget, size_bet
from .venues import classify_venue

SGT = timezone(timedelta(hours=8))
MAX_OPEN_BETS = 8
TAKE_PROFIT = 0.40          # let winners run
STOP = 0.15                 # hard stop — catalyst plays don't need 30% room
TIME_STOP_DAYS = 5          # cut if older than this AND return < TIME_STOP_LOSS
TIME_STOP_LOSS = -0.05      # -5% after 5 days = not working, free the slot
DEAD_MONEY_DAYS = 10        # cut if older than this AND return < DEAD_MONEY_GAIN
DEAD_MONEY_GAIN = 0.10      # <+10% after 10 days = opportunity cost too high
THESIS_ORDER_TTL_HOURS = 24 # queued orders older than this are stale — skip them

# Futures tickers Alpaca can't trade — skip at order time, don't guess a stock match
FUTURES_BLOCKLIST = {
    "CL", "GC", "SI", "ES", "NQ", "YM", "RTY", "ZB", "ZN", "ZF", "ZT",
    "ZC", "ZW", "ZS", "ZM", "ZL", "NG", "HO", "RB", "HG", "PL", "PA",
    "LE", "GF", "HE", "KC", "CT", "CC", "SB", "OJ", "LBS",
}
# Reasoned-aggressive watchlists: liquid, high-beta names the agent forms convictions on.
STOCK_WATCHLIST = ["NVDA", "AMD", "PLTR", "CRDO", "ARM", "SMCI", "MU", "MRVL", "AVGO", "TSM",
                   "ASML", "COIN", "MSTR", "ANET", "DELL", "AVAV", "RKLB", "IONQ"]
CRYPTO_WATCHLIST = ["SOL/USDT", "AVAX/USDT", "SUI/USDT", "SEI/USDT", "INJ/USDT", "ARB/USDT",
                    "NEAR/USDT", "APT/USDT", "RENDER/USDT", "TIA/USDT", "LINK/USDT", "FET/USDT"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stock_prices(tickers: list[str]) -> dict:
    """Latest price + ~3-month momentum for each stock ticker (one batched yfinance call)."""
    out = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
        data = yf.download(tickers, period="3mo", interval="1d", progress=False,
                           group_by="ticker", auto_adjust=True, threads=True)
        for t in tickers:
            try:
                closes = data[t]["Close"].dropna().tolist()
                if closes:
                    out[t] = {"px": float(closes[-1]), "venue": "stock",
                              "mom": float(closes[-1] / closes[0] - 1) if closes[0] else 0.0}
            except Exception:
                continue
    except Exception:
        pass
    return out


def _crypto_prices(symbols: list[str], ex) -> dict:
    """Latest price + ~3-month momentum for each crypto pair via Binance OHLCV."""
    out = {}
    for s in symbols:
        try:
            ohlcv = ex.fetch_ohlcv(s, "1d", limit=90)
            closes = [c[4] for c in ohlcv if c[4]]
            if closes:
                out[s] = {"px": float(closes[-1]), "venue": "crypto",
                          "mom": float(closes[-1] / closes[0] - 1) if closes[0] else 0.0}
        except Exception:
            continue
    return out


def main() -> None:
    cfg = get_config(); cfg.ensure_data_dir()
    db = Database(cfg.db_path)
    if "swing" not in cfg.sleeves_enabled_set:
        print("[swing] disabled via SLEEVES_ENABLED."); return
    if not (cfg.alpaca_enabled and cfg.alpaca_api_key_id):
        print("[swing] Alpaca not enabled."); return
    if db.get_state("swing_halted") == "1":
        print("[swing] halted (floor hit) — standing down."); return

    START = cfg.swing_budget_usd
    FLOOR0 = cfg.swing_floor_usd

    # Two venues: stocks on Alpaca, crypto on Binance. Each bet routes to its own client.
    from .alpaca import build_alpaca, AlpacaExecutionClient
    from .ccxt_feed import CcxtPriceFeed, build_binance
    from .execution import DryRunExecutionClient, CcxtExecutionClient
    import os
    stock_exec = AlpacaExecutionClient(build_alpaca(cfg), paper=cfg.alpaca_paper)
    ex = build_binance(cfg.binance_api_key, cfg.binance_api_secret)
    crypto_feed = CcxtPriceFeed(ex)
    crypto_exec = (CcxtExecutionClient(ex, crypto_feed, os.environ.get("ALLOW_LIVE_ORDERS") == "1")
                   if cfg.is_live else DryRunExecutionClient(cfg.swing_budget_usd, crypto_feed))

    def exec_for(symbol):
        return crypto_exec if classify_venue(symbol.split("/")[0]) == "crypto" else stock_exec

    bets = {r["pair"]: r for r in db.open_positions() if str(r["strategy"]) == "swing"}
    stock_syms = [s for s in set(list(bets) + STOCK_WATCHLIST) if classify_venue(s.split("/")[0]) == "stock"]
    crypto_syms = [s for s in set(list(bets) + CRYPTO_WATCHLIST) if classify_venue(s.split("/")[0]) == "crypto"]
    px = {**_stock_prices(stock_syms), **_crypto_prices(crypto_syms, ex)}
    cash = float(db.get_state("swing_cash") or START)

    def bet_value(r):
        p = px.get(r["pair"], {}).get("px") or r["entry_price"]
        return r["size_usd"] * (p / r["entry_price"]) if r["entry_price"] else r["size_usd"]

    value = cash + sum(bet_value(r) for r in bets.values())
    hwm = max(float(db.get_state("swing_hwm") or START), value)
    db.set_state("swing_hwm", str(hwm), _now())
    floor = floor_value(START, FLOOR0, hwm)
    msgs = []

    # 1. circuit breaker
    if should_halt(value, floor):
        for t, r in bets.items():
            exec_for(t).close_position(_pos(r), "swing-halt")
            db.close_trade(r["id"], closed_at=_now(), exit_price=px.get(t, {}).get("px", r["entry_price"]),
                           pnl_usd=bet_value(r) - r["size_usd"], pnl_pct=0.0, fees_usd=0.0,
                           exit_reason="circuit-breaker")
        db.set_state("swing_halted", "1", _now())
        _notify(cfg, f"⛔ Swing sleeve hit its floor (${floor:,.0f}). Flattened everything and halted, "
                     f"as designed. The pot is protected; it stops here.")
        return

    # 2. manage exits
    now_dt = datetime.now(timezone.utc)
    for t, r in list(bets.items()):
        p = px.get(t, {}).get("px")
        if not p or not r["entry_price"]:
            continue
        ret = p / r["entry_price"] - 1
        # Determine exit reason (priority: hard stop > TP > time stop > dead money)
        exit_reason = None
        if ret <= -STOP:
            exit_reason = ("stop", f"🔴 hard stop hit {ret*100:+.0f}%")
        elif ret >= TAKE_PROFIT:
            exit_reason = ("take-profit", f"✅ target hit {ret*100:+.0f}%")
        else:
            try:
                opened = datetime.fromisoformat(r["opened_at"].replace("Z", "+00:00"))
                age_days = (now_dt - opened).total_seconds() / 86400
                if age_days >= TIME_STOP_DAYS and ret < TIME_STOP_LOSS:
                    exit_reason = ("time-stop", f"⏱ {age_days:.0f}d old, {ret*100:+.0f}% — not working")
                elif age_days >= DEAD_MONEY_DAYS and ret < DEAD_MONEY_GAIN:
                    exit_reason = ("dead-money", f"💤 {age_days:.0f}d old, {ret*100:+.0f}% — opportunity cost")
            except Exception:
                pass
        if exit_reason:
            reason_tag, msg_sfx = exit_reason
            exec_for(t).close_position(_pos(r), f"swing-{reason_tag}")
            db.close_trade(r["id"], closed_at=_now(), exit_price=p, pnl_usd=r["size_usd"] * ret,
                           pnl_pct=ret, fees_usd=0.0, exit_reason=reason_tag)
            cash += bet_value(r); bets.pop(t)
            from .names import display as _nd
            msgs.append(f"<b>{_nd(t)}</b> exited · {msg_sfx}")

    # 3a. thesis orders from alpha channel signals — process first, they're explicit calls
    import sqlite3
    raw_conn = sqlite3.connect(cfg.db_path)
    raw_conn.row_factory = sqlite3.Row
    pending = raw_conn.execute(
        "SELECT * FROM thesis_orders WHERE status='pending'"
    ).fetchall()
    raw_conn.close()

    # Execute highest-conviction, freshest signals first
    _cw = {"HIGH": 1.0, "MEDIUM": 0.67, "LOW": 0.33}
    def _order_score(o):
        try:
            age_h = (datetime.now(timezone.utc) -
                     datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
                     ).total_seconds() / 3600
        except Exception:
            age_h = THESIS_ORDER_TTL_HOURS
        recency = max(0.0, 1.0 - age_h / THESIS_ORDER_TTL_HOURS)
        return _cw.get((o["confidence"] or "MEDIUM").upper(), 0.67) * recency
    pending = sorted(pending, key=_order_score, reverse=True)

    for order in pending:
        if len(bets) >= MAX_OPEN_BETS or risk_budget(value, floor) < 50:
            break
        ticker = order["pair"]
        action = order["action"]   # "buy" (LONG) or "sell" (SHORT)
        side = "long" if action == "buy" else "short"

        # Reject futures tickers — Alpaca can't trade them
        base = ticker.split("/")[0].upper()
        if base in FUTURES_BLOCKLIST:
            with sqlite3.connect(cfg.db_path) as c:
                c.execute("UPDATE thesis_orders SET status='failed' WHERE id=?", (order["id"],))
            print(f"[swing] {ticker} is a futures contract — skipped")
            continue

        # Expire stale signals — alpha calls go cold fast
        try:
            created = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            if age_h > THESIS_ORDER_TTL_HOURS:
                with sqlite3.connect(cfg.db_path) as c:
                    c.execute("UPDATE thesis_orders SET status='expired' WHERE id=?", (order["id"],))
                print(f"[swing] {ticker} order expired after {age_h:.0f}h — skipped")
                continue
        except Exception:
            pass

        if ticker in bets:
            # already holding — mark done, skip
            with sqlite3.connect(cfg.db_path) as c:
                c.execute("UPDATE thesis_orders SET status='skipped' WHERE id=?", (order["id"],))
            continue
        # fetch price if not already in px
        venue = classify_venue(ticker.split("/")[0])
        if ticker not in px:
            if venue == "stock":
                px.update(_stock_prices([ticker]))
            else:
                px.update(_crypto_prices([ticker], ex))
        if ticker not in px:
            continue  # can't price it, leave pending for next cycle
        size = round(min(order["size_pct"] / 100 * value, size_bet(value, floor)), 2)
        if cash < size:
            break
        try:
            pos = exec_for(ticker).open_position(ticker, side, size, 1.0, "swing")
        except Exception as e:
            print(f"[swing] order failed for {ticker}: {e}")
            with sqlite3.connect(cfg.db_path) as c:
                c.execute("UPDATE thesis_orders SET status='failed' WHERE id=?", (order["id"],))
            continue
        pos.db_id = db.open_trade(_rec(pos, cfg))
        cash -= pos.size_usd
        bets[ticker] = {"pair": ticker, "side": side, "strategy": "swing",
                        "entry_price": pos.entry_price, "size_usd": pos.size_usd,
                        "opened_at": pos.opened_at, "id": pos.db_id}
        with sqlite3.connect(cfg.db_path) as c:
            c.execute("UPDATE thesis_orders SET status='executed' WHERE id=?", (order["id"],))
        from .names import display as _display
        msgs.append(f"📡 <b>{_display(ticker)}</b> ${size:.0f} · alpha signal · {side}")

    # 3b. one new reasoned bet from watchlist if there's still room
    if len(bets) < MAX_OPEN_BETS and risk_budget(value, floor) >= 50:
        candidates = [t for t in (STOCK_WATCHLIST + CRYPTO_WATCHLIST) if t not in bets and t in px]
        candidates.sort(key=lambda t: px[t]["mom"], reverse=True)      # rank by momentum, research the top
        if candidates:
            cand = candidates[0]
            from .research import research
            verdict = research(cand.split("/")[0], cfg)               # research the base (SOL, NVDA)
            vl = verdict.upper()
            # Aggressive momentum: bet on the leader UNLESS the panel flags a serious red flag (AVOID).
            # The floor is the real risk control; the panel just vetoes disasters.
            if "AVOID" not in vl:
                size = round(size_bet(value, floor), 2)               # capped: 25% of the cushion
                pos = exec_for(cand).open_position(cand, "long", size, 1.0, "swing")
                pos.db_id = db.open_trade(_rec(pos, cfg)); cash -= pos.size_usd
                tag = "strong conviction" if "BUY NOW" in vl else "momentum leader, no red flag"
                why = verdict.split("WHY:", 1)[-1].split("INVALIDATION")[0].strip()[:220] if "WHY:" in verdict else ""
                msgs.append(f"🎯 Opened <b>{cand}</b> ${size:.0f} · {tag}\n<i>{why}</i>")
            else:
                msgs.append(f"⏭ Passed on <b>{cand}</b> · panel flagged AVOID")

    db.set_state("swing_cash", str(max(0.0, cash)), _now())
    db.record_sleeve_nav("swing", datetime.now(SGT).strftime("%Y-%m-%d"), value, floor)
    if msgs:
        head = (f"🎯 <b>Swing Sleeve</b> · PAPER\n\n"
                f"<b>Pot</b> ${value:,.0f}  <b>Floor</b> ${floor:,.0f}  <b>Holding</b> {len(bets)}\n\n")
        _notify(cfg, head + "\n".join("• " + m for m in msgs))
    print(f"[swing] pot=${value:.0f} floor=${floor:.0f} bets={list(bets)} {msgs}")


def run_thesis_now(cfg, db) -> None:
    """Fast path — execute pending thesis orders immediately without the watchlist research pass.

    Called by the alpha monitor right after a BUY NOW stock signal is routed. Runs exits + thesis
    orders only; skips the daily watchlist pick so it's cheap (no Exa/Claude calls).
    """
    if "swing" not in cfg.sleeves_enabled_set:
        return
    if db.get_state("swing_halted") == "1":
        return

    START = cfg.swing_budget_usd
    FLOOR0 = cfg.swing_floor_usd

    from .alpaca import build_alpaca, AlpacaExecutionClient
    from .ccxt_feed import CcxtPriceFeed, build_binance
    from .execution import DryRunExecutionClient, CcxtExecutionClient
    import os, sqlite3

    stock_exec = AlpacaExecutionClient(build_alpaca(cfg), paper=cfg.alpaca_paper)
    ex = build_binance(cfg.binance_api_key, cfg.binance_api_secret)
    crypto_feed = CcxtPriceFeed(ex)
    crypto_exec = (CcxtExecutionClient(ex, crypto_feed, os.environ.get("ALLOW_LIVE_ORDERS") == "1")
                   if cfg.is_live else DryRunExecutionClient(cfg.swing_budget_usd, crypto_feed))

    def exec_for(symbol):
        return crypto_exec if classify_venue(symbol.split("/")[0]) == "crypto" else stock_exec

    bets = {r["pair"]: r for r in db.open_positions() if str(r["strategy"]) == "swing"}
    cash = float(db.get_state("swing_cash") or START)

    def bet_value(r):
        p = px.get(r["pair"], {}).get("px") or r["entry_price"]
        return r["size_usd"] * (p / r["entry_price"]) if r["entry_price"] else r["size_usd"]

    # price only the tickers we actually need
    _raw = sqlite3.connect(cfg.db_path)
    _raw.row_factory = sqlite3.Row
    orders = _raw.execute("SELECT * FROM thesis_orders WHERE status='pending'").fetchall()
    _raw.close()
    _cw2 = {"HIGH": 1.0, "MEDIUM": 0.67, "LOW": 0.33}
    def _oscore(o):
        try:
            age_h = (datetime.now(timezone.utc) -
                     datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
                     ).total_seconds() / 3600
        except Exception:
            age_h = THESIS_ORDER_TTL_HOURS
        return _cw2.get((o["confidence"] or "MEDIUM").upper(), 0.67) * max(0.0, 1.0 - age_h / THESIS_ORDER_TTL_HOURS)
    orders = sorted(orders, key=_oscore, reverse=True)

    need = list({o["pair"] for o in orders} | set(bets.keys()))
    stock_syms = [s for s in need if classify_venue(s.split("/")[0]) == "stock"]
    crypto_syms = [s for s in need if classify_venue(s.split("/")[0]) == "crypto"]
    px = {**_stock_prices(stock_syms), **_crypto_prices(crypto_syms, ex)}

    value = cash + sum(bet_value(r) for r in bets.values())
    hwm = max(float(db.get_state("swing_hwm") or START), value)
    db.set_state("swing_hwm", str(hwm), _now())
    floor = floor_value(START, FLOOR0, hwm)
    msgs = []

    if should_halt(value, floor):
        for t, r in bets.items():
            exec_for(t).close_position(_pos(r), "swing-halt")
            db.close_trade(r["id"], closed_at=_now(), exit_price=px.get(t, {}).get("px", r["entry_price"]),
                           pnl_usd=bet_value(r) - r["size_usd"], pnl_pct=0.0, fees_usd=0.0,
                           exit_reason="circuit-breaker")
        db.set_state("swing_halted", "1", _now())
        _notify(cfg, f"⛔ Swing sleeve hit its floor (${floor:,.0f}). Halted.")
        return

    for order in orders:
        if len(bets) >= MAX_OPEN_BETS or risk_budget(value, floor) < 50:
            break
        ticker = order["pair"]
        side = "long" if order["action"] == "buy" else "short"

        base = ticker.split("/")[0].upper()
        if base in FUTURES_BLOCKLIST:
            with sqlite3.connect(cfg.db_path) as c:
                c.execute("UPDATE thesis_orders SET status='failed' WHERE id=?", (order["id"],))
            print(f"[swing/thesis] {ticker} is a futures contract — skipped")
            continue

        try:
            created = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            if age_h > THESIS_ORDER_TTL_HOURS:
                with sqlite3.connect(cfg.db_path) as c:
                    c.execute("UPDATE thesis_orders SET status='expired' WHERE id=?", (order["id"],))
                print(f"[swing/thesis] {ticker} order expired after {age_h:.0f}h — skipped")
                continue
        except Exception:
            pass

        if ticker in bets:
            with sqlite3.connect(cfg.db_path) as c:
                c.execute("UPDATE thesis_orders SET status='skipped' WHERE id=?", (order["id"],))
            continue
        venue = classify_venue(ticker.split("/")[0])
        if ticker not in px:
            if venue == "stock":
                px.update(_stock_prices([ticker]))
            else:
                px.update(_crypto_prices([ticker], ex))
        if ticker not in px:
            continue
        size = round(min(order["size_pct"] / 100 * value, size_bet(value, floor)), 2)
        if cash < size:
            break
        try:
            pos = exec_for(ticker).open_position(ticker, side, size, 1.0, "swing")
        except Exception as e:
            print(f"[swing/thesis] order failed for {ticker}: {e}")
            with sqlite3.connect(cfg.db_path) as c:
                c.execute("UPDATE thesis_orders SET status='failed' WHERE id=?", (order["id"],))
            continue
        pos.db_id = db.open_trade(_rec(pos, cfg))
        cash -= pos.size_usd
        bets[ticker] = {"pair": ticker, "side": side, "strategy": "swing",
                        "entry_price": pos.entry_price, "size_usd": pos.size_usd,
                        "opened_at": pos.opened_at, "id": pos.db_id}
        with sqlite3.connect(cfg.db_path) as c:
            c.execute("UPDATE thesis_orders SET status='executed' WHERE id=?", (order["id"],))
        from .names import display as _display
        msgs.append(f"📡 <b>{_display(ticker)}</b> ${size:.0f} · alpha signal · {side}")

    db.set_state("swing_cash", str(max(0.0, cash)), _now())
    value = cash + sum(bet_value(r) for r in bets.values())
    db.record_sleeve_nav("swing", __import__("datetime").datetime.now(SGT).strftime("%Y-%m-%d"), value, floor)

    if msgs:
        head = (f"🎯 <b>Swing Sleeve</b> · PAPER · alpha entry\n\n"
                f"<b>Pot</b> ${value:,.0f}  <b>Floor</b> ${floor:,.0f}  <b>Holding</b> {len(bets)}\n\n")
        _notify(cfg, head + "\n".join("• " + m for m in msgs))
    print(f"[swing/thesis] executed {len(msgs)} order(s)")


def _pos(row):
    from .execution import Position
    return Position(pair=row["pair"], side=row["side"], size_usd=row["size_usd"], leverage=1.0,
                    entry_price=row["entry_price"], strategy=row["strategy"],
                    opened_at=row["opened_at"], db_id=row["id"])


def _rec(pos, cfg):
    from .database import TradeRecord
    return TradeRecord(pair=pos.pair, side=pos.side, strategy="swing", entry_price=pos.entry_price,
                       size_usd=pos.size_usd, leverage=1.0, opened_at=pos.opened_at,
                       is_paper=not (cfg.is_live and not cfg.alpaca_paper))


def _notify(cfg, text: str) -> None:
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(text); return
    try:
        import asyncio
        from telegram import Bot

        async def _s():
            kw = {"message_thread_id": cfg.telegram_topic_id} if cfg.telegram_topic_id else {}
            await Bot(cfg.telegram_bot_token).send_message(chat_id=cfg.telegram_chat_id, text=text, parse_mode="HTML", **kw)
        asyncio.run(_s())
    except Exception as e:
        print(f"[swing] notify failed: {e}\n{text}")


if __name__ == "__main__":
    main()
