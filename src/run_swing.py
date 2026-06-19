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
MAX_OPEN_BETS = 3
TAKE_PROFIT, STOP = 0.40, 0.30    # aggressive per-bet exits; the floor is the real backstop
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
                closes = (data[t] if len(tickers) > 1 else data)["Close"].dropna().tolist()
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
    for t, r in list(bets.items()):
        p = px.get(t, {}).get("px")
        if not p or not r["entry_price"]:
            continue
        ret = p / r["entry_price"] - 1
        if ret >= TAKE_PROFIT or ret <= -STOP:
            exec_for(t).close_position(_pos(r), "swing-exit")
            db.close_trade(r["id"], closed_at=_now(), exit_price=p, pnl_usd=r["size_usd"] * ret,
                           pnl_pct=ret, fees_usd=0.0, exit_reason="take-profit" if ret > 0 else "stop")
            cash += bet_value(r); bets.pop(t)
            emoji = "✅" if ret > 0 else "🔴"
            msgs.append(f"{emoji} <b>{t}</b> sold {ret*100:+.0f}% · {'target hit' if ret > 0 else 'cut the loss'}")

    # 3. one new reasoned bet across BOTH stocks and crypto, if there's room
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
