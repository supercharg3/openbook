"""Entrypoint for the autonomous stock factor sleeves — runs once a day (systemd timer).

Runs TWO sleeves side by side so we can measure whether concentrating in AI beats diversifying:
  • Diversified — broad liquid large-caps, benchmarked vs the whole market (SPY)
  • AI & Semis  — concentrated AI/chip names, benchmarked vs the AI/chip sector (SMH)

Each sleeve: rank by momentum + quality, hold top 8 equal-weight, rebalance monthly, sell on
rank-drop, ATR catastrophe stop, red-flag veto. Paper, unlevered, on a leash until it beats its
benchmark over a real sample. Honest 'winning' for the AI sleeve = beating the sector, not riding it.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .config import get_config
from .database import Database
from .stock_factor import (
    LIQUID_UNIVERSE, AI_SEMI_UNIVERSE, SLEEVES, TARGET_POSITIONS, REBALANCE_TRADING_DAYS, atr,
    momentum_score, combined_select, rebalance_diff, catastrophe_stop_hit, compute_scorecard,
)

SGT = timezone(timedelta(hours=8))


def _today() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%d")


def _universe_for(tag: str) -> list[str]:
    return AI_SEMI_UNIVERSE if tag == "factor-ai" else LIQUID_UNIVERSE


def _fetch_history(tickers: list[str]) -> dict[str, dict]:
    import yfinance as yf
    data = yf.download(tickers, period="1y", interval="1d", progress=False,
                       group_by="ticker", auto_adjust=True, threads=True)
    out: dict[str, dict] = {}
    for t in tickers:
        try:
            df = data[t] if len(tickers) > 1 else data
            closes = [float(x) for x in df["Close"].dropna().tolist()]
            if closes:
                out[t] = {"close": closes,
                          "high": [float(x) for x in df["High"].dropna().tolist()],
                          "low": [float(x) for x in df["Low"].dropna().tolist()]}
        except Exception:
            continue
    return out


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()
    db = Database(cfg.db_path)
    if not (cfg.alpaca_enabled and cfg.alpaca_api_key_id):
        print("[stock_factor] Alpaca not enabled — nothing to do."); return

    # Only run sleeves the user has enabled. Envelope: split total stock budget equally.
    enabled = cfg.sleeves_enabled_set
    active_sleeves = [s for s in SLEEVES if s["tag"] in enabled]
    if not active_sleeves:
        print("[stock_factor] No stock sleeves enabled via SLEEVES_ENABLED."); return
    sleeve_budget = cfg.stock_sleeve_budget([s["tag"] for s in active_sleeves])

    from .alpaca import build_alpaca, AlpacaExecutionClient
    execu = AlpacaExecutionClient(build_alpaca(cfg), paper=cfg.alpaca_paper)

    all_tickers = sorted(set(LIQUID_UNIVERSE) | set(AI_SEMI_UNIVERSE) |
                         {s["benchmark"] for s in active_sleeves})
    hist = _fetch_history(all_tickers)

    results = [r for r in (_run_sleeve(cfg, db, execu, s, hist, sleeve_budget)
                           for s in active_sleeves) if r]
    if any(r["traded"] for r in results) or datetime.now(SGT).weekday() == 0:
        _notify(cfg, _format(results))
    for r in results:
        print(f"[stock_factor] {_today()} {r['sleeve']['tag']}: "
              f"holdings={list(r['holdings'])} value=${r['value']:.0f} traded={r['traded']}")


def _run_sleeve(cfg, db, execu, sleeve, hist, sleeve_budget: float = 10000.0) -> dict | None:
    tag, bench, universe = sleeve["tag"], sleeve["benchmark"], _universe_for(sleeve["tag"])
    today = _today()
    bclose = hist.get(bench, {}).get("close")
    if not bclose:
        return None
    bench_price = bclose[-1]
    last_price = {t: hist[t]["close"][-1] for t in universe if t in hist and hist[t].get("close")}
    holdings = {r["pair"]: r for r in db.open_positions() if str(r["strategy"]) == tag}
    actions = {"sell": [], "buy": [], "vetoed": []}
    stops = []

    # catastrophe stops (every run)
    for tkr, row in list(holdings.items()):
        h, px = hist.get(tkr), last_price.get(tkr)
        if not h or not px:
            continue
        if catastrophe_stop_hit(row["entry_price"], px, atr(h["high"], h["low"], h["close"])):
            closed = execu.close_position(_pos(row), "factor-stop")
            db.close_trade(row["id"], closed_at=datetime.now(timezone.utc).isoformat(),
                           exit_price=closed.exit_price, pnl_usd=closed.pnl_usd, pnl_pct=closed.pnl_pct,
                           fees_usd=0.0, exit_reason="catastrophe-stop")
            _credit_cash(db, tag, row, px); holdings.pop(tkr, None); stops.append(tkr)

    # monthly rebalance
    if _due_for_rebalance(db.get_state(f"{tag}_last_rebalance"), today):
        from .fundamentals import fetch_gpa
        from .risk_veto import red_flag_check
        scores = {t: momentum_score(hist[t]["close"]) for t in universe if t in hist}
        target = set(combined_select(scores, fetch_gpa(universe), TARGET_POSITIONS))
        diff = rebalance_diff(set(holdings), target)
        for tkr in diff["sell"]:
            row = holdings[tkr]; px = last_price.get(tkr, row["entry_price"])
            closed = execu.close_position(_pos(row), "factor-rebalance")
            db.close_trade(row["id"], closed_at=datetime.now(timezone.utc).isoformat(),
                           exit_price=closed.exit_price, pnl_usd=closed.pnl_usd, pnl_pct=closed.pnl_pct,
                           fees_usd=0.0, exit_reason="rank-drop")
            _credit_cash(db, tag, row, px); holdings.pop(tkr, None); actions["sell"].append(tkr)
        cash = _cash(db, tag, sleeve_budget)
        per = _sleeve_value(db, tag, holdings, last_price, sleeve_budget) / max(len(target), 1)
        for tkr in diff["buy"]:
            size = min(per, cash)
            if size < 25 or tkr not in last_price:
                continue
            v = red_flag_check(tkr, cfg)
            db.log_veto(today, tkr, v["vetoed"], v["reason"], last_price.get(tkr, 0.0))
            if v["vetoed"]:
                actions["vetoed"].append(tkr); continue
            pos = execu.open_position(tkr, "long", size, 1.0, tag)
            pos.db_id = db.open_trade(_rec(pos, tag, cfg)); cash -= pos.size_usd
            actions["buy"].append(tkr)
        _set_cash(db, tag, cash)
        db.set_state(f"{tag}_last_rebalance", today, datetime.now(timezone.utc).isoformat())
        holdings = {r["pair"]: r for r in db.open_positions() if str(r["strategy"]) == tag}

    # benchmark + NAV
    units = db.get_state(f"{tag}_bench_units")
    if units is None:
        units = sleeve_budget / bench_price
        db.set_state(f"{tag}_bench_units", str(units), datetime.now(timezone.utc).isoformat())
    bench_value = float(units) * bench_price
    sleeve_value = _sleeve_value(db, tag, holdings, last_price, sleeve_budget)
    db.record_sleeve_nav(tag, today, sleeve_value, bench_value)

    return {"sleeve": sleeve, "actions": actions, "stops": stops, "holdings": holdings,
            "value": sleeve_value, "bench": bench_value,
            "score": compute_scorecard(db.sleeve_nav_history(tag)),
            "traded": bool(actions["sell"] or actions["buy"] or stops or actions["vetoed"])}


# ── cash + position bookkeeping, namespaced per sleeve ────────────────────────
def _pos(row):
    from .execution import Position
    return Position(pair=row["pair"], side=row["side"], size_usd=row["size_usd"], leverage=1.0,
                    entry_price=row["entry_price"], strategy=row["strategy"],
                    opened_at=row["opened_at"], db_id=row["id"])


def _rec(pos, tag, cfg):
    from .database import TradeRecord
    return TradeRecord(pair=pos.pair, side=pos.side, strategy=tag, entry_price=pos.entry_price,
                       size_usd=pos.size_usd, leverage=1.0, opened_at=pos.opened_at,
                       is_paper=not (cfg.is_live and not cfg.alpaca_paper))


def _cash(db, tag, default: float = 10000.0) -> float:
    v = db.get_state(f"{tag}_cash")
    return float(v) if v is not None else default


def _set_cash(db, tag, value: float) -> None:
    db.set_state(f"{tag}_cash", str(max(0.0, value)), datetime.now(timezone.utc).isoformat())


def _credit_cash(db, tag, row, exit_price: float) -> None:
    cur = row["size_usd"] * (exit_price / row["entry_price"]) if row["entry_price"] else row["size_usd"]
    _set_cash(db, tag, _cash(db, tag) + cur)


def _sleeve_value(db, tag, holdings: dict, last_price: dict, default_cash: float = 10000.0) -> float:
    val = _cash(db, tag, default_cash)
    for tkr, row in holdings.items():
        px = last_price.get(tkr, row["entry_price"])
        val += row["size_usd"] * (px / row["entry_price"]) if row["entry_price"] else row["size_usd"]
    return val


def _due_for_rebalance(last: str | None, today: str) -> bool:
    if not last:
        return True
    try:
        return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days >= 28
    except Exception:
        return True


def _notify(cfg, text: str) -> None:
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(text); return
    try:
        import asyncio
        from telegram import Bot

        async def _send():
            kw = {"message_thread_id": cfg.telegram_topic_id} if cfg.telegram_topic_id else {}
            await Bot(cfg.telegram_bot_token).send_message(chat_id=cfg.telegram_chat_id, text=text, **kw)
        asyncio.run(_send())
    except Exception as e:
        print(f"[stock_factor] telegram failed: {e}\n{text}")


def _format(results) -> str:
    from .names import display
    lines = ["📊 Stock robots check-in (practice money, no borrowing)"]
    for r in results:
        s = r["sleeve"]; bench_name = "the S&P 500" if s["benchmark"] == "SPY" else "the AI/chip sector (SMH)"
        lines.append("")
        lines.append(f"— {s['name']} —")
        if r["actions"]["buy"]:
            lines.append(f"Bought: {', '.join(display(t) for t in r['actions']['buy'])}")
        if r["actions"]["sell"]:
            lines.append(f"Sold: {', '.join(display(t) for t in r['actions']['sell'])}")
        if r["actions"]["vetoed"]:
            lines.append(f"Skipped (safety flag): {', '.join(r['actions']['vetoed'])}")
        if r["stops"]:
            lines.append(f"Stopped out: {', '.join(r['stops'])}")
        lines.append(f"Holding {len(r['holdings'])}: {', '.join(r['holdings']) or 'cash'}")
        sc = r["score"]
        if "information_ratio" in sc:
            diff = sc["excess_return"] * 100
            verb = "ahead of" if diff > 0.1 else ("behind" if diff < -0.1 else "even with")
            lines.append(f"${r['value']:,.0f} now · {verb} {bench_name} by {abs(diff):.1f}%")
        else:
            lines.append(f"${r['value']:,.0f} · racing {bench_name}, too early to score")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
