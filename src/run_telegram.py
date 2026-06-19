"""Entrypoint: the Telegram override listener (long-lived systemd service).

Builds the SystemController and wires STATUS/REPORT providers to the live DB, then blocks on
the polling loop. On startup it sends the mode banner ("MODE: LIVE — $X at risk" or
"MODE: DRY-RUN — paper trading active") so there is never ambiguity about whether real money
is in play.
"""
from __future__ import annotations

import asyncio

from .ccxt_feed import CcxtPriceFeed, build_binance
from .config import get_config
from .controller import ControllerState, SystemController
from .database import Database
from .execution import compute_pnl
from .reporting import format_status
from .run_daily_report import build_report
from .reporting import format_daily_report
from .telegram_bot import TelegramInterface


def _unrealized(row, price_feed) -> float:
    """Live mark-to-market P&L for one open position; 0.0 if the price can't be fetched."""
    try:
        mark = price_feed.get_price(row["pair"])
        pnl, _ = compute_pnl(row["side"], row["entry_price"], mark, row["size_usd"], row["leverage"])
        return pnl
    except Exception:
        return 0.0


def _sd(x: float) -> str:
    """Signed dollar amount, e.g. +$0.99 / -$1.05."""
    return f"+${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}"


def _esc(s) -> str:
    """Escape the characters that matter for Telegram HTML (& < >). Company names have '&'."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _swing_status_lines(db, swing_rows, cfg) -> list[str]:
    """The aggressive Agentic Conviction swing sleeve: pot value, the protective floor, open bets."""
    from .names import display
    prices = {}
    tickers = [r["pair"] for r in swing_rows]
    if tickers:
        try:
            import yfinance as yf
            data = yf.download(tickers, period="5d", interval="1d", progress=False,
                               group_by="ticker", auto_adjust=True, threads=True)
            for t in tickers:
                try:
                    prices[t] = float((data[t] if len(tickers) > 1 else data)["Close"].dropna().iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass
    cash = float(db.get_state("swing_cash") or 1000.0)
    total = cash
    pos = []
    for r in swing_rows:
        p, entry, cost = prices.get(r["pair"]), r["entry_price"], r["size_usd"]
        if p and entry:
            val = cost * (p / entry); pnl = val - cost; total += val
            pos.append(f"  • {_esc(display(r['pair']))} — ${val:,.0f} {'🟢' if pnl >= 0 else '🔴'} {_sd(pnl)}")
        else:
            total += cost
            pos.append(f"  • {_esc(display(r['pair']))} — ${cost:,.0f}")
    hwm = float(db.get_state("swing_hwm") or cfg.swing_budget_usd)
    from .swing import floor_value
    floor = floor_value(cfg.swing_budget_usd, cfg.swing_floor_usd, hwm)
    halted = db.get_state("swing_halted") == "1"
    state = "⛔ HALTED (hit floor)" if halted else "running"
    return ["", "🎯 <b>SWING — agentic conviction</b> (aggressive)",
            f"<b>Pot:</b> ${total:,.0f}  [{state}]",
            f"<b>Floor:</b> ${floor:,.0f} (auto-halts here · started $1,000)",
            "<b>Bets:</b>"] + (pos or ["  • none open"])


def _degen_status_lines(db, degen_rows, cfg, price_feed) -> list[str]:
    """Hyper-active crypto momentum sleeve status."""
    from .names import display
    cash = float(db.get_state("degen_cash") or cfg.degen_budget_usd)
    total = cash
    pos_lines = []
    for r in degen_rows:
        u = _unrealized(r, price_feed)
        val = r["size_usd"] + u
        total += val
        pos_lines.append(f"  • {_esc(display(r['pair']))} — ${val:,.0f} "
                         f"{'🟢' if u >= 0 else '🔴'} {_sd(u)}")
    halted = db.get_state("degen_halted") == "1"
    state = "⛔ HALTED" if halted else "running · 15-min cycle"
    return ["", "🎰 <b>DEGEN — active crypto momentum</b>",
            f"<b>Pot:</b> ${total:,.0f}  [{state}]",
            f"<b>Floor:</b> ${cfg.degen_floor_usd:,.0f} (auto-halts here · started ${cfg.degen_budget_usd:,.0f})",
            "<b>Positions:</b>"] + (pos_lines or ["  • none open"])


def _stock_status_lines(db, stock_rows) -> list[str]:
    """Live status for BOTH Alpaca sleeves (Diversified + AI), each vs its own benchmark."""
    from .names import display
    from .stock_factor import SLEEVES
    tickers = [r["pair"] for r in stock_rows]
    benches = [s["benchmark"] for s in SLEEVES]
    prices = {}
    try:
        import yfinance as yf
        want = tickers + benches
        data = yf.download(want, period="5d", interval="1d", progress=False,
                           group_by="ticker", auto_adjust=True, threads=True)
        for t in set(want):
            try:
                df = data[t] if len(want) > 1 else data
                prices[t] = float(df["Close"].dropna().iloc[-1])
            except Exception:
                pass
    except Exception:
        pass

    start = 10000.0
    out = []
    for s in SLEEVES:
        tag, bench = s["tag"], s["benchmark"]
        rows = [r for r in stock_rows if str(r["strategy"]) == tag]
        if not rows and db.get_state(f"{tag}_bench_units") is None:
            continue                                       # sleeve not started yet
        total = float(db.get_state(f"{tag}_cash") or 0.0)
        pos_lines = []
        for r in rows:
            px, entry, cost = prices.get(r["pair"]), r["entry_price"], r["size_usd"]
            if px and entry:
                val = cost * (px / entry); pnl = val - cost; total += val
                pos_lines.append(f"  • {_esc(display(r['pair']))} — ${val:,.0f} "
                                 f"{'🟢' if pnl >= 0 else '🔴'} {_sd(pnl)}")
            else:
                total += cost
                pos_lines.append(f"  • {_esc(display(r['pair']))} — ${cost:,.0f}")
        units = float(db.get_state(f"{tag}_bench_units") or 0.0)
        bench_val = units * prices[bench] if units and prices.get(bench) else start
        bench_label = "S&amp;P 500" if bench == "SPY" else f"AI sector ({bench})"
        out += ["", f"🔵 <b>ALPACA — {_esc(s['name'])}</b>",
                f"<b>Capital:</b> ${total:,.0f}  [Practice Money]",
                f"<b>Returns:</b> {_sd(total - start)} since start · {bench_label} would be ${bench_val:,.0f}",
                "<b>Positions:</b>"] + pos_lines
    return out


def _load_controller_state(db: Database) -> ControllerState:
    """Rehydrate override flags from system_state so a restart preserves STOP/PAUSE."""
    state = ControllerState()
    state.halted = db.get_state("halted", "0") == "1"
    paused = db.get_state("paused_layers", "")
    state.paused_layers = set(filter(None, (paused or "").split(",")))
    return state


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()
    if cfg.is_live:
        cfg.require_live_keys()
    db = Database(cfg.db_path)

    controller = SystemController(_load_controller_state(db))
    price_feed = CcxtPriceFeed(build_binance(cfg.binance_api_key, cfg.binance_api_secret))

    def status_provider() -> str:
        # Both robots, one consistent layout, HTML bold + bullets. Separate accounts ($500 crypto on
        # Binance, $10k practice stocks on Alpaca). Company/coin names, not just tickers.
        from .names import display
        all_rows = db.open_positions()
        pairs_rows = [r for r in all_rows if str(r["strategy"]) == "pairs"]
        stock_rows = [r for r in all_rows if str(r["strategy"]).startswith("factor")]
        swing_rows = [r for r in all_rows if str(r["strategy"]) == "swing"]
        degen_rows = [r for r in all_rows if str(r["strategy"]) == "degen"]
        mode = "Practice Money" if not cfg.is_live else "LIVE — real money"
        capital = float(db.get_state("capital", str(cfg.starting_capital_usd)))
        mtd = float(db.get_state("mtd_pnl", "0"))
        reserve = float(db.get_state("reserve", "0"))

        out = [f"📍 <b>STATUS</b>", "",
               "🟡 <b>BINANCE — crypto pairs</b>",
               f"<b>Capital:</b> ${capital:,.2f}  [{mode}]",
               f"<b>Returns:</b> {_sd(mtd)} this month",
               f"<b>Profits:</b> ${reserve:,.2f} locked safe",
               "<b>Positions:</b>"]
        if pairs_rows:
            for r in pairs_rows:
                u = _unrealized(r, price_feed)
                out.append(f"  • {_esc(display(r['pair']))} — {r['side']} ${r['size_usd']:,.0f} "
                           f"{'🟢' if u >= 0 else '🔴'} {_sd(u)}")
        else:
            out.append("  • none open — waiting for a setup")

        if stock_rows:
            out += _stock_status_lines(db, stock_rows)
        if swing_rows or db.get_state("swing_hwm"):
            out += _swing_status_lines(db, swing_rows, cfg)
        if "degen" in cfg.sleeves_enabled_set:
            out += _degen_status_lines(db, degen_rows, cfg, price_feed)
        return "\n".join(out)

    def report_provider(period: str | None = None) -> str:
        if period == "weekly":
            from .weekly_report import build_weekly_report, format_weekly_report
            return format_weekly_report(build_weekly_report(db, cfg))
        if period == "monthly":
            from .weekly_report import build_monthly_report, format_monthly_report
            return format_monthly_report(build_monthly_report(db, cfg))
        return format_daily_report(build_report(db, cfg, price_feed))

    def assistant_provider(question: str) -> str:
        from datetime import datetime, timezone, timedelta
        from .thesis import parse_thesis_order
        order = parse_thesis_order(question)
        if order:
            action, pair, size_pct = order
            now = datetime.now(timezone(timedelta(hours=8))).isoformat()
            db.add_thesis_order(action, pair, size_pct, now)
            base = pair.split("/")[0]
            if action == "close":
                return f"Queued: CLOSE {base}. The engine will close it next cycle."
            is_lt = action.endswith("_lt")
            side = "BUY" if action.startswith("buy") else "SHORT"
            stop = "wide 35% stop, no take-profit (long-term hold)" if is_lt else "8% stop"
            return (f"Queued: {side} {base} {size_pct:.0f}% of capital (thesis sleeve, max 15%). "
                    f"The engine executes it next cycle with a {stop}. "
                    f"{'PAPER' if not cfg.is_live else 'LIVE'} mode. Reply CLOSE {base} to exit.")
        from .assistant import is_price_request, lookup_price, answer_question
        base = is_price_request(question)
        if base:
            return lookup_price(base, price_feed)   # live Binance price, no LLM guessing
        from .idea_scanner import is_scan_request, scan_ideas
        if is_scan_request(question):
            return scan_ideas(cfg)                  # scan Polymarket + news → stress-test → surface
        from .research import is_research_request, research
        subject = is_research_request(question)
        if subject:
            return research(subject, cfg)        # "look into X" → honest stress-test, no auto-trade
        return answer_question(question, db, cfg)

    def basket_approve() -> str:
        import os
        from . import basket
        data_dir = os.path.dirname(cfg.db_path) or "."
        if basket.promote_proposed(data_dir):
            return ("✅ New basket approved and promoted. It goes live automatically the next time "
                    "the book is flat (no open pairs), no restart needed.")
        return "No proposed basket to approve (run a re-screen first)."

    tg = TelegramInterface(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        controller=controller,
        status_provider=status_provider,
        report_provider=report_provider,
        topic_id=cfg.telegram_topic_id,
        assistant_provider=assistant_provider,
        basket_approve=basket_approve,
    )

    # Note: the startup MODE banner is sent by the trading-loop service (run_trade), not here,
    # to avoid a duplicate on every restart. In discovery mode we just note it in the log.
    if not cfg.telegram_chat_id:
        print("[run_telegram] discovery mode — message me in the Trading topic to get the ids")
    tg.run()


if __name__ == "__main__":
    main()
