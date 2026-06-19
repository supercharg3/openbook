"""Entrypoint: build the daily report from the SQLite trade log and send it to Telegram.

Triggered by trading-report.timer at 8am SGT. Reads real closed/open trades from the DB so the
report reflects actual activity. Market regime/outlook fields are filled from system_state,
which the orchestrator updates each cycle.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .config import get_config
from .database import Database
from .reporting import DailyReport, TradeLine, format_daily_report
from .risk_manager import DAILY_LOSS_LIMIT_PCT

SGT = timezone(timedelta(hours=8))


def _today_sgt() -> datetime:
    return datetime.now(SGT)


def build_report(db: Database, cfg, price_feed=None) -> DailyReport:
    now = _today_sgt()
    cutoff = (now - timedelta(hours=24)).astimezone(timezone.utc).isoformat()

    recent = [r for r in db.closed_trades(limit=50) if (r["closed_at"] or "") >= cutoff]
    trade_lines = [
        TradeLine(
            pair=r["pair"], side=r["side"], entry=r["entry_price"],
            exit=r["exit_price"], pnl_usd=r["pnl_usd"],
            reason=r["exit_reason"] or "", won=(r["pnl_usd"] or 0) > 0,
        )
        for r in recent
    ]

    # Crypto report excludes the stock factor sleeve (separate Alpaca account, its own check-in).
    open_rows = [r for r in db.open_positions() if not str(r["strategy"]).startswith("factor")]
    open_unreal = 0.0
    if price_feed is not None:
        from .execution import compute_pnl
        for r in open_rows:
            try:
                mark = price_feed.get_price(r["pair"])
                pnl, _ = compute_pnl(r["side"], r["entry_price"], mark, r["size_usd"], r["leverage"])
                open_unreal += pnl
            except Exception:
                pass
    capital = float(db.get_state("capital", str(cfg.starting_capital_usd)))
    capital_change = sum((r["pnl_usd"] or 0) for r in recent)
    mtd = float(db.get_state("mtd_pnl", "0"))
    daily_used = sum(min(0.0, r["pnl_usd"] or 0) for r in recent)

    return DailyReport(
        date_str=now.strftime("%Y-%m-%d"),
        capital=capital,
        capital_change=capital_change,
        mtd_pnl_usd=mtd,
        mtd_pnl_pct=(mtd / capital * 100) if capital else 0.0,
        trades=trade_lines,
        open_positions=len(open_rows),
        open_unrealized=open_unreal,  # live mark-to-market when a price feed is provided
        regime=db.get_state("regime", "UNKNOWN"),
        adx=float(db.get_state("adx", "0")),
        outlook=db.get_state("outlook", "") or "",
        daily_loss_used=daily_used,
        daily_loss_limit=DAILY_LOSS_LIMIT_PCT * capital,
        mode=cfg.trading_mode,
    )


async def _send(text: str, cfg) -> None:
    from telegram import Bot

    kwargs = {}
    if cfg.telegram_topic_id is not None:
        kwargs["message_thread_id"] = cfg.telegram_topic_id
    await Bot(cfg.telegram_bot_token).send_message(
        chat_id=cfg.telegram_chat_id, text=text, **kwargs
    )


def _check_gate(db, cfg) -> str | None:
    """Evaluate the go-live gate; return a one-time alert message the first time it clears."""
    from datetime import datetime, timedelta, timezone

    from .gate_check import evaluate_gate, format_gate_message

    if cfg.is_live or db.get_state("gate_alerted", "0") == "1":
        return None  # already live, or already alerted once
    now_iso = datetime.now(timezone(timedelta(hours=8))).isoformat()
    status = evaluate_gate(db, now_iso)
    if status.ready:
        db.set_state("gate_alerted", "1", now_iso)
        return format_gate_message(status)
    return None


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()
    db = Database(cfg.db_path)

    # Count this report (the gate's "daily reports generated" criterion).
    from datetime import datetime, timedelta, timezone
    now_iso = datetime.now(timezone(timedelta(hours=8))).isoformat()
    db.set_state("daily_report_count", str(int(db.get_state("daily_report_count", "0") or "0") + 1), now_iso)

    from .ccxt_feed import CcxtPriceFeed, build_binance
    price_feed = CcxtPriceFeed(build_binance(cfg.binance_api_key, cfg.binance_api_secret))
    report = build_report(db, cfg, price_feed)
    text = format_daily_report(report)
    gate_msg = _check_gate(db, cfg)

    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        asyncio.run(_send(text, cfg))
        if gate_msg:
            asyncio.run(_send(gate_msg, cfg))
    else:
        print(text)
        if gate_msg:
            print("\n" + gate_msg)


if __name__ == "__main__":
    main()
