"""Automated re-screening — keeps the pairs edge fresh.

Re-runs the wide out-of-sample pair screen, recomputes portfolio allocations, and writes a
PROPOSED basket (data/proposed_basket.json) plus a Telegram summary. A human approves before it
goes live (reply APPROVE BASKET), so a bad screen can never auto-deploy. Run monthly by a timer.
"""
from __future__ import annotations
from itertools import combinations

UNIVERSE = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","DOGE/USDT","ADA/USDT",
            "AVAX/USDT","LINK/USDT","DOT/USDT","LTC/USDT","ATOM/USDT","NEAR/USDT","INJ/USDT",
            "UNI/USDT","AAVE/USDT","ARB/USDT","OP/USDT","SUI/USDT","APT/USDT","FIL/USDT",
            "TIA/USDT","SEI/USDT","GALA/USDT","IMX/USDT","GRT/USDT","RUNE/USDT","LDO/USDT",
            # widened (18 Jun) — more liquid perps with ~360d history; screen skips any too-new/illiquid
            "ICP/USDT","ETC/USDT","FTM/USDT","HBAR/USDT","ALGO/USDT","VET/USDT","AXS/USDT",
            "SAND/USDT","MANA/USDT","FLOW/USDT","EGLD/USDT","XLM/USDT","XTZ/USDT","THETA/USDT",
            "CHZ/USDT","CRV/USDT","SNX/USDT","MKR/USDT","DYDX/USDT","ENS/USDT","STX/USDT",
            "FET/USDT","AR/USDT","KAVA/USDT","ROSE/USDT","EOS/USDT","IOTA/USDT","ZIL/USDT",
            "CFX/USDT","GMT/USDT","APE/USDT","RENDER/USDT","PENDLE/USDT","JTO/USDT"]
DAYS = 360


def find_robust_pairs():
    from .backtest import fetch_history
    from .ccxt_feed import build_binance
    from .pairs import PairsTrader, align, split_validate, backtest_pair
    ex = build_binance(None, None)
    cache = {}
    def hist(s):
        if s not in cache:
            cache[s] = fetch_history(ex, s, "1h", DAYS * 24)
        return cache[s]
    robust = []
    for a, b in combinations(UNIVERSE, 2):
        try:
            ac, bc, ts = align(hist(a), hist(b))
        except Exception:
            continue
        if len(ac) < 400:
            continue
        full, ok, _, _ = split_validate(PairsTrader.name_of(a, b), ac, bc, ts)
        if ok:
            res = backtest_pair(PairsTrader.name_of(a, b), ac, bc, ts)
            robust.append((a, b, [t.pnl_pct for t in res.trades], res.period_days,
                           full.stats()["sharpe"]))
    return robust


def main():
    from .config import get_config
    from .pairs import PairsTrader
    from .portfolio import allocate
    from . import basket
    import os

    cfg = get_config()
    data_dir = os.path.dirname(cfg.db_path) or "."
    robust = find_robust_pairs()
    if not robust:
        _notify(cfg, "Re-screen ran: 0 robust pairs found this round (kept current basket).")
        return
    pair_stats = [(PairsTrader.name_of(a, b), rets, days) for a, b, rets, days, _ in robust]
    allocs, port_vol, lev = allocate(pair_stats)
    pairs = [(a, b) for a, b, _, _, _ in robust]
    alloc_map = {al.name: round(al.fraction, 4) for al in allocs}
    basket.save(data_dir, basket.PROPOSED, pairs, alloc_map,
                meta={"port_vol": round(port_vol, 4), "leverage": round(lev, 2)})
    top = [f"  {a.split('/')[0]}~{b.split('/')[0]}" for a, b, _, _, _ in
           sorted(robust, key=lambda x: -x[4])[:12]]

    if not cfg.is_live:
        # Practice money → self-refresh automatically. It only
        # swaps in once no trade is open, so it never disrupts a live position.
        basket.promote_proposed(data_dir)
        lines = [f"🔁 I refreshed the pairs by myself (practice money): now watching {len(pairs)} "
                 f"pairs. It swaps in automatically the next time nothing's mid-trade.", ""]
        lines += top
        lines.append("\n(When real money is in, I'll ask you before changing the list.)")
    else:
        lines = [f"🔁 New pair list ready: {len(pairs)} pairs that passed the test. Top ones:", ""]
        lines += top
        lines.append("\nReply APPROVE BASKET to use it, or ignore to keep the current one.")
    _notify(cfg, "\n".join(lines))


def _notify(cfg, text):
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(text); return
    try:
        import asyncio
        from telegram import Bot
        kw = {"message_thread_id": cfg.telegram_topic_id} if cfg.telegram_topic_id else {}
        asyncio.run(Bot(cfg.telegram_bot_token).send_message(chat_id=cfg.telegram_chat_id, text=text, **kw))
    except Exception as e:
        print(f"[rescreen] notify failed: {e}\n{text}")


if __name__ == "__main__":
    main()
