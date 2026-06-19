"""Degen sleeve runner — fires every 15 minutes via systemd timer.

Manages exits on all open degen bets, then looks for new entry signals.
All paper until ALLOW_LIVE_ORDERS=1 and TRADING_MODE=live.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .config import get_config
from .database import Database, TradeRecord
from .degen import (
    WATCHLIST, MAX_BETS, TAKE_PROFIT, STOP_LOSS, REVERSAL_BARS,
    fetch_signals, bet_size, should_halt,
)
from .ccxt_feed import build_binance, CcxtPriceFeed
from .execution import DryRunExecutionClient, CcxtExecutionClient, Position

SGT = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()
    db = Database(cfg.db_path)

    if "degen" not in cfg.sleeves_enabled_set:
        print("[degen] disabled via SLEEVES_ENABLED."); return
    if db.get_state("degen_halted") == "1":
        print("[degen] halted (floor hit) — standing down."); return

    ex = build_binance(cfg.binance_api_key, cfg.binance_api_secret)
    feed = CcxtPriceFeed(ex)
    import os
    exec_client = (CcxtExecutionClient(ex, feed, os.environ.get("ALLOW_LIVE_ORDERS") == "1")
                   if cfg.is_live else DryRunExecutionClient(cfg.degen_budget_usd, feed))

    START = cfg.degen_budget_usd
    FLOOR = cfg.degen_floor_usd
    cash = float(db.get_state("degen_cash") or START)

    # ── open positions ────────────────────────────────────────────────────────
    bets = {r["pair"]: dict(r) for r in db.open_positions() if str(r["strategy"]) == "degen"}

    # ── get signals for watchlist + open positions ────────────────────────────
    all_syms = list(set(WATCHLIST) | set(bets.keys()))
    signals = fetch_signals(all_syms, ex)

    def current_px(sym: str) -> float | None:
        return signals[sym]["px"] if sym in signals else None

    def bet_value(r: dict) -> float:
        px = current_px(r["pair"])
        if px and r["entry_price"]:
            return r["size_usd"] * (px / r["entry_price"])
        return r["size_usd"]

    pot = cash + sum(bet_value(r) for r in bets.values())
    msgs = []

    # ── circuit breaker ───────────────────────────────────────────────────────
    if should_halt(pot, FLOOR):
        for sym, r in list(bets.items()):
            px = current_px(sym) or r["entry_price"]
            pos = _make_pos(r)
            exec_client.close_position(pos, "degen-halt")
            db.close_trade(r["id"], closed_at=_now(), exit_price=px,
                           pnl_usd=bet_value(r) - r["size_usd"], pnl_pct=0.0,
                           fees_usd=0.0, exit_reason="circuit-breaker")
        db.set_state("degen_halted", "1", _now())
        _notify(cfg, f"⛔ Degen sleeve hit its floor (${FLOOR:,.0f}). Halted.")
        return

    # ── manage exits ──────────────────────────────────────────────────────────
    for sym, r in list(bets.items()):
        px = current_px(sym)
        if not px or not r["entry_price"]:
            continue
        ret = px / r["entry_price"] - 1
        sig = signals.get(sym, {})
        reversal = px < sig.get("reversal_low", 0) if sig else False

        reason = None
        if ret >= TAKE_PROFIT:
            reason = "take-profit"
        elif ret <= -STOP_LOSS:
            reason = "stop-loss"
        elif reversal:
            reason = "reversal"

        if reason:
            pos = _make_pos(r)
            exec_client.close_position(pos, f"degen-{reason}")
            pnl = r["size_usd"] * ret
            db.close_trade(r["id"], closed_at=_now(), exit_price=px,
                           pnl_usd=pnl, pnl_pct=ret, fees_usd=0.0, exit_reason=reason)
            cash += bet_value(r)
            bets.pop(sym)
            emoji = "✅" if ret > 0 else "🔴"
            msgs.append(f"{emoji} Closed {sym} {ret*100:+.1f}% ({reason}).")

    # ── new entries ───────────────────────────────────────────────────────────
    if len(bets) < MAX_BETS:
        slots = MAX_BETS - len(bets)
        # rank entry candidates by atr_pct desc (most volatile first)
        candidates = [
            (sym, sig) for sym, sig in signals.items()
            if sym not in bets and sig.get("entry_ok") and sym in WATCHLIST
        ]
        candidates.sort(key=lambda x: x[1]["atr_pct"], reverse=True)

        for sym, sig in candidates[:slots]:
            size = bet_size(pot)
            if cash < size:
                break
            pos = exec_client.open_position(sym, "long", size, 1.0, "degen")
            rec = TradeRecord(
                pair=sym, side="long", strategy="degen",
                entry_price=pos.entry_price, size_usd=pos.size_usd,
                opened_at=pos.opened_at or _now(), leverage=1.0,
                is_paper=not (cfg.is_live and os.environ.get("ALLOW_LIVE_ORDERS") == "1"),
            )
            pos.db_id = db.open_trade(rec)
            cash -= pos.size_usd
            pot = cash + sum(bet_value(r) for r in bets.values()) + pos.size_usd
            bets[sym] = {"pair": sym, "side": "long", "strategy": "degen",
                         "entry_price": pos.entry_price, "size_usd": pos.size_usd,
                         "opened_at": pos.opened_at, "id": pos.db_id}
            msgs.append(
                f"🎰 Entered {sym} ${size:.0f} — breakout + volume surge "
                f"(ATR {sig['atr_pct']*100:.2f}%)."
            )

    db.set_state("degen_cash", str(max(0.0, cash)), _now())
    db.record_sleeve_nav("degen", datetime.now(SGT).strftime("%Y-%m-%d"), pot, FLOOR)

    if msgs:
        head = (f"🎰 Degen sleeve (PAPER, ${START:,.0f} start)\n"
                f"Pot ${pot:,.0f} · floor ${FLOOR:,.0f} · {len(bets)} open\n")
        _notify(cfg, head + "\n".join("• " + m for m in msgs))

    print(f"[degen] pot=${pot:.0f} floor={FLOOR:.0f} bets={list(bets)} msgs={msgs}")


def _make_pos(r: dict) -> Position:
    return Position(
        pair=r["pair"], side=r["side"], size_usd=r["size_usd"], leverage=1.0,
        entry_price=r["entry_price"], strategy=r["strategy"],
        opened_at=r.get("opened_at"), db_id=r.get("id"),
    )


def _notify(cfg, text: str) -> None:
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(text); return
    try:
        import asyncio
        from telegram import Bot

        async def _s():
            kw = {"message_thread_id": cfg.telegram_topic_id} if cfg.telegram_topic_id else {}
            await Bot(cfg.telegram_bot_token).send_message(
                chat_id=cfg.telegram_chat_id, text=text, **kw)
        asyncio.run(_s())
    except Exception as e:
        print(f"[degen] notify failed: {e}\n{text}")


if __name__ == "__main__":
    main()
