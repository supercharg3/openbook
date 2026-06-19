"""Entrypoint: the main trading loop (systemd long-lived service).

Wires real ccxt market data + execution to the Orchestrator and runs one cycle every
CYCLE_SECONDS. Paper trading uses live public market data with simulated fills; live mode uses
the guarded CcxtExecutionClient.

Safety:
  - Live mode fails fast if keys are missing (config.require_live_keys()).
  - Live orders stay blocked until ALLOW_LIVE_ORDERS=1 is set (smoke-test first).
  - A startup banner ("MODE: LIVE/DRY-RUN") is sent so there is never doubt about real money.
"""
from __future__ import annotations

import os
import time

from .ccxt_feed import CcxtPriceFeed, build_binance
from .config import get_config
from .controller import ControllerState, SystemController
from .database import Database
from .execution import CcxtExecutionClient, DryRunExecutionClient
from .funding_arb import FundingMonitor
from .orchestrator import Orchestrator

CYCLE_SECONDS = int(os.environ.get("CYCLE_SECONDS", "60"))


def _load_controller_state(db: Database) -> ControllerState:
    state = ControllerState()
    state.halted = db.get_state("halted", "0") == "1"
    paused = db.get_state("paused_layers", "")
    state.paused_layers = set(filter(None, (paused or "").split(",")))
    return state


def _build_news_provider(cfg, price_feed):
    """Wire the Exa→Haiku news scanner if its keys are present; else no news layer.

    Throttled to EXA_POLLS_PER_HOUR so the 60s trading loop doesn't hammer Exa/Claude on every
    cycle. Between polls it returns [] (no news signals), which the orchestrator handles cleanly.
    Errors are swallowed to [] so a flaky news call never kills a trading cycle.
    """
    if not (cfg.exa_api_key and cfg.anthropic_api_key):
        return None
    from anthropic import Anthropic
    from exa_py import Exa

    from .news_scanner import NewsScanner

    def price_drift(asset: str, published_iso: str) -> float:
        # Conservative: if we can't measure drift, assume none (the should_act gate still applies).
        return 0.0

    scanner = NewsScanner(
        exa_client=Exa(cfg.exa_api_key),
        claude_client=Anthropic(api_key=cfg.anthropic_api_key),
        model=cfg.claude_model,
        price_drift_fn=price_drift,
    )

    interval = 3600.0 / max(1, cfg.exa_polls_per_hour)
    state = {"last": None}

    def throttled_scan():
        now = time.monotonic()
        if state["last"] is not None and (now - state["last"]) < interval:
            return []
        state["last"] = now
        try:
            return scanner.scan()
        except Exception as e:
            print(f"[news] scan failed (skipping this poll): {e}")
            return []

    return throttled_scan


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()
    db = Database(cfg.db_path)

    if "pairs" not in cfg.sleeves_enabled_set:
        print("[run_trade] 'pairs' sleeve not in SLEEVES_ENABLED — exiting."); return

    if cfg.is_live:
        cfg.require_live_keys()

    exchange = build_binance(cfg.binance_api_key, cfg.binance_api_secret)
    price_feed = CcxtPriceFeed(exchange)

    if cfg.is_live:
        allow_orders = os.environ.get("ALLOW_LIVE_ORDERS", "0") == "1"
        exec_client = CcxtExecutionClient(exchange, price_feed, allow_live_orders=allow_orders)
    else:
        exec_client = DryRunExecutionClient(cfg.starting_capital_usd, price_feed)

    # Optional stock execution for the thesis sleeve (separate venue; crypto core stays on Binance).
    # Alpaca REST — best-effort: if it fails to connect, log and run crypto-only rather than crash.
    stock_exec = None
    if cfg.alpaca_enabled:
        try:
            from .alpaca import build_alpaca, AlpacaExecutionClient
            stock_exec = AlpacaExecutionClient(build_alpaca(cfg), paper=cfg.alpaca_paper)
            print(f"[run_trade] Alpaca connected ({'paper' if cfg.alpaca_paper else 'LIVE'})")
        except Exception as e:
            print(f"[run_trade] Alpaca connect failed ({type(e).__name__}: {e}); stocks disabled this run")

    controller = SystemController(_load_controller_state(db))
    orch = Orchestrator(
        cfg, db, controller, exec_client, price_feed,
        funding_monitor=FundingMonitor(),
        news_provider=_build_news_provider(cfg, price_feed),
        unlock_provider=None,   # wired once a CoinGecko/TokenUnlocks fetcher is configured
        stock_exec=stock_exec,
    )

    # Startup banner (mode safety check) — best-effort; never block the loop on it.
    _send_banner(cfg)

    print(f"[run_trade] starting loop — mode={cfg.trading_mode}, cycle={CYCLE_SECONDS}s")
    while True:
        try:
            result = orch.run_cycle()
            if result.opened or result.closed:
                print(f"[run_trade] opened={len(result.opened)} closed={len(result.closed)} "
                      f"regime={result.regime} adx={result.adx:.0f}")
            for alert in result.alerts:        # circuit breaker etc. — push immediately
                _send_text(alert, cfg)
        except Exception as e:  # never let one bad cycle kill the loop; systemd also restarts us
            print(f"[run_trade] cycle error: {e}")
        time.sleep(CYCLE_SECONDS)


def _send_text(text: str, cfg) -> None:
    """Best-effort Telegram push (alerts). Never raises into the loop."""
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(f"[run_trade] (alert) {text}")
        return
    try:
        import asyncio

        from telegram import Bot

        kwargs = {}
        if cfg.telegram_topic_id is not None:
            kwargs["message_thread_id"] = cfg.telegram_topic_id
        asyncio.run(Bot(cfg.telegram_bot_token).send_message(
            chat_id=cfg.telegram_chat_id, text=text, **kwargs))
    except Exception as e:
        print(f"[run_trade] alert send failed: {e}")


def _send_banner(cfg) -> None:
    _send_text(cfg.startup_banner(), cfg)


if __name__ == "__main__":
    main()
