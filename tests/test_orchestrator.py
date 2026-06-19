import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.orchestrator as orch_mod  # noqa: E402


@pytest.fixture
def directional_ta():
    """Enable the (off-by-default) trend/grid layer for tests that exercise it."""
    orch_mod.ENABLE_DIRECTIONAL_TA = True
    try:
        yield
    finally:
        orch_mod.ENABLE_DIRECTIONAL_TA = False


@pytest.fixture
def enable_news():
    """Enable the (off-by-default) news + unlock layers for tests that exercise them."""
    orch_mod.ENABLE_NEWS = True
    try:
        yield
    finally:
        orch_mod.ENABLE_NEWS = False

from src.config import Config  # noqa: E402
from src.controller import SystemController  # noqa: E402
from src.database import Database  # noqa: E402
from src.execution import DryRunExecutionClient  # noqa: E402
from src.funding_arb import FundingMonitor, FundingSnapshot  # noqa: E402
from src.news_scanner import TradeIntent  # noqa: E402
from src.orchestrator import SGT, Orchestrator  # noqa: E402
from src.token_unlocks import UnlockSignal  # noqa: E402
from datetime import date  # noqa: E402


NOW = datetime(2026, 6, 17, 12, 0, tzinfo=SGT)


class FakePriceFeed:
    def __init__(self, prices, funding=None, ohlcv_closes=None, ohlcv_by_symbol=None):
        self.prices = prices
        self.funding = funding or []
        self.ohlcv_closes = ohlcv_closes      # optional explicit close series
        self.ohlcv_by_symbol = ohlcv_by_symbol or {}   # per-symbol close series (for pairs)

    def get_price(self, pair):
        return self.prices.get(pair, 100.0)

    def get_funding_rates(self):
        return list(self.funding)

    def get_ohlcv(self, pair, timeframe="1d", limit=60):
        if pair in self.ohlcv_by_symbol:
            cs = self.ohlcv_by_symbol[pair]
            return [[i, c, c + 0.5, c - 0.5, c, 1000.0] for i, c in enumerate(cs)]
        if self.ohlcv_closes is not None:
            return [[i, c, c + 0.5, c - 0.5, c, 1000.0] for i, c in enumerate(self.ohlcv_closes)]
        # Default: synthetic strong uptrend → valid ADX (trending), RSI pinned high (no entry).
        return [[i, 100 + i, 100 + i + 0.5, 100 + i - 0.5, 100 + i, 1000.0] for i in range(limit)]


def _db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Database(tmp.name)


def _orch(prices, funding=None, news=None, unlocks=None, controller=None, balance=500.0,
          ohlcv_closes=None, ohlcv_by_symbol=None, pairs=None):
    feed = FakePriceFeed(prices, funding, ohlcv_closes, ohlcv_by_symbol)
    db = _db()
    cfg = Config()  # dry-run defaults
    exec_client = DryRunExecutionClient(balance, feed)
    return Orchestrator(
        cfg, db, controller or SystemController(), exec_client, feed,
        funding_monitor=FundingMonitor(),
        news_provider=(lambda: news) if news is not None else None,
        unlock_provider=(lambda today, held: unlocks or []) if unlocks is not None else None,
        pairs=pairs if pairs is not None else [],   # default: no pairs (keep other tests isolated)
    ), db


# ── Funding arb ──────────────────────────────────────────────────────────────
def test_funding_entry_opens_position():
    funding = [FundingSnapshot("BTC/USDT", 0.001)]  # above entry threshold
    orch, db = _orch({"BTC/USDT": 60000.0}, funding=funding)
    res = orch.run_cycle(NOW)
    assert len(res.opened) == 1
    assert res.opened[0].strategy == "funding"
    assert len(db.open_positions()) == 1


def test_funding_exit_after_two_low_windows():
    orch, db = _orch({"BTC/USDT": 60000.0}, funding=[FundingSnapshot("BTC/USDT", 0.001)])
    orch.run_cycle(NOW)  # opens
    # Now funding decays; needs two consecutive low windows to exit.
    orch.price_feed.funding = [FundingSnapshot("BTC/USDT", 0.00001)]
    orch.run_cycle(NOW + timedelta(minutes=30))   # low window 1 → hold
    assert len(db.open_positions()) == 1
    res = orch.run_cycle(NOW + timedelta(minutes=60))  # low window 2 → exit
    assert len(res.closed) == 1
    assert len(db.open_positions()) == 0


# ── News ─────────────────────────────────────────────────────────────────────
def test_news_intent_opens_position(enable_news):
    intent = TradeIntent(asset="SOL", direction="long", confidence=0.9,
                         size_fraction=0.10, rationale="listing", source_url="http://x")
    orch, db = _orch({"SOL/USDT": 150.0}, news=[intent])
    res = orch.run_cycle(NOW)
    opened = [p for p in res.opened if p.strategy == "news"]
    assert len(opened) == 1
    assert opened[0].pair == "SOL/USDT"
    assert opened[0].side == "long"


# ── Controller / risk gating ─────────────────────────────────────────────────
def test_halt_blocks_entries_but_allows_exits():
    funding = [FundingSnapshot("BTC/USDT", 0.001)]
    orch, db = _orch({"BTC/USDT": 60000.0}, funding=funding)
    orch.run_cycle(NOW)                       # open a funding position
    assert len(db.open_positions()) == 1
    orch.controller.stop()                    # STOP
    # decay funding so the exit path fires while halted
    orch.price_feed.funding = [FundingSnapshot("BTC/USDT", 0.00001)]
    orch.run_cycle(NOW + timedelta(minutes=30))
    res = orch.run_cycle(NOW + timedelta(minutes=60))
    assert len(db.open_positions()) == 0      # exit ran despite halt
    assert any("halted" in s for s in res.skipped)


def test_paused_layer_skips_that_layer(enable_news):
    intent = TradeIntent("SOL", "long", 0.9, 0.10, "x", "http://x")
    c = SystemController()
    c.pause("news")
    orch, db = _orch({"SOL/USDT": 150.0}, news=[intent], controller=c)
    res = orch.run_cycle(NOW)
    assert not any(p.strategy == "news" for p in res.opened)
    assert any("layer paused" in s for s in res.skipped)


def test_correlation_blocks_second_correlated_position(enable_news):
    # Open BTC via funding, cache high BTC/ETH correlation, then an ETH news intent is blocked.
    funding = [FundingSnapshot("BTC/USDT", 0.001)]
    intent = TradeIntent("ETH", "long", 0.9, 0.10, "x", "http://x")
    orch, db = _orch({"BTC/USDT": 60000.0, "ETH/USDT": 3000.0}, funding=funding, news=[intent])
    db.cache_correlation("BTC/USDT", "ETH/USDT", 0.85, NOW.isoformat())
    res = orch.run_cycle(NOW)
    assert any(p.pair == "BTC/USDT" for p in res.opened)
    assert not any(p.pair == "ETH/USDT" for p in res.opened)
    assert any("correlat" in s.lower() for s in res.skipped)


def test_funding_skips_illiquid_microcaps():
    # A juicy-but-illiquid microcap must be filtered out; the liquid pair still opens.
    funding = [
        FundingSnapshot("SKHYNIX/USDT", 0.02),   # highest funding, but illiquid → skipped
        FundingSnapshot("BTC/USDT", 0.001),       # liquid → opens
    ]
    orch, db = _orch({"SKHYNIX/USDT": 0.5, "BTC/USDT": 60000.0}, funding=funding)
    res = orch.run_cycle(NOW)
    opened_pairs = {p.pair for p in res.opened}
    assert "BTC/USDT" in opened_pairs
    assert "SKHYNIX/USDT" not in opened_pairs


def test_max_three_open_positions():
    funding = [
        FundingSnapshot("BTC/USDT", 0.003),
        FundingSnapshot("ETH/USDT", 0.002),
        FundingSnapshot("SOL/USDT", 0.0015),
        FundingSnapshot("ARB/USDT", 0.001),
    ]
    prices = {"BTC/USDT": 60000, "ETH/USDT": 3000, "SOL/USDT": 150, "ARB/USDT": 1.2}
    orch, db = _orch(prices, funding=funding)
    res = orch.run_cycle(NOW)
    assert len(db.open_positions()) == 3      # capped at 3
    assert len(res.opened) == 3


# ── Token unlocks ────────────────────────────────────────────────────────────
def test_unlock_short_and_cover(enable_news):
    short_sig = [UnlockSignal("ARB", "short", date(2026, 6, 22), 5, "unlock soon")]
    orch, db = _orch({"ARB/USDT": 1.2}, unlocks=short_sig)
    res = orch.run_cycle(NOW)
    assert any(p.strategy == "unlock" and p.side == "short" for p in res.opened)

    # Now the unlock window closes → cover.
    orch.unlock_provider = lambda today, held: [
        UnlockSignal("ARB", "cover", date(2026, 6, 14), -3, "window closed")
    ]
    res2 = orch.run_cycle(NOW + timedelta(days=5))
    assert len(res2.closed) == 1
    assert len(db.open_positions()) == 0


# ── Native trend / grid + exit management ────────────────────────────────────
def _uptrend_pullback():
    up = list(np.linspace(100, 130, 60))
    pull = list(np.linspace(130, 126, 8))
    return up + pull


def test_trend_entry_opens_long(directional_ta):
    orch, db = _orch({"BTC/USDT": 126.0, "ETH/USDT": 126.0}, ohlcv_closes=_uptrend_pullback())
    res = orch.run_cycle(NOW)
    trend = [p for p in res.opened if p.strategy == "trend"]
    assert len(trend) == 1                       # BTC opens; ETH blocked (identical → corr 1.0)
    assert trend[0].side == "long"
    # correlation got cached this cycle
    assert db.get_correlation("BTC/USDT", "ETH/USDT") is not None


def test_stop_loss_closes_position(directional_ta):
    orch, db = _orch({"BTC/USDT": 100.0, "ETH/USDT": 100.0}, ohlcv_closes=_uptrend_pullback())
    res1 = orch.run_cycle(NOW)
    assert any(p.strategy == "trend" for p in res1.opened)
    # Price gaps down 10% (> 8% stop). Halt to isolate the exit from re-entry.
    orch.price_feed.prices["BTC/USDT"] = 90.0
    orch.controller.stop()
    res2 = orch.run_cycle(NOW + timedelta(hours=1))
    assert any(c.reason == "sl" for c in res2.closed)


def test_take_profit_closes_position(directional_ta):
    orch, db = _orch({"BTC/USDT": 100.0, "ETH/USDT": 100.0}, ohlcv_closes=_uptrend_pullback())
    orch.run_cycle(NOW)
    orch.price_feed.prices["BTC/USDT"] = 116.0   # +16% > 15% take-profit
    orch.controller.stop()
    res = orch.run_cycle(NOW + timedelta(hours=1))
    assert any(c.reason == "tp" for c in res.closed)


# ── Market-neutral pairs (Layer 5) ───────────────────────────────────────────
def _stretched_pair(stretch_last=105.0):
    window = list(100 + np.sin(np.arange(168) / 5.0))   # mean ~100, small variance
    a_closes = window + [stretch_last]                  # last bar stretched → high z
    b_closes = [100.0] * 169
    return a_closes, b_closes


def test_pair_enters_both_legs_market_neutral():
    a_closes, b_closes = _stretched_pair(105.0)
    orch, db = _orch(
        {"XRP/USDT": 105.0, "DOGE/USDT": 100.0},
        ohlcv_by_symbol={"XRP/USDT": a_closes, "DOGE/USDT": b_closes},
        pairs=[("XRP/USDT", "DOGE/USDT")],
    )
    res = orch.run_cycle(NOW)
    legs = [p for p in res.opened if p.strategy.startswith("pairs:")]
    assert len(legs) == 2                                   # two legs opened together
    sides = {p.pair: p.side for p in legs}
    assert sides["XRP/USDT"] == "short" and sides["DOGE/USDT"] == "long"   # A rich → short A / long B
    assert len(orch.open_pairs) == 1
    # Pairs must NOT consume the directional position cap.
    assert len(orch.open_positions) == 0


def test_pair_exits_on_convergence():
    a_closes, b_closes = _stretched_pair(105.0)
    orch, db = _orch(
        {"XRP/USDT": 105.0, "DOGE/USDT": 100.0},
        ohlcv_by_symbol={"XRP/USDT": a_closes, "DOGE/USDT": b_closes},
        pairs=[("XRP/USDT", "DOGE/USDT")],
    )
    orch.run_cycle(NOW)
    assert len(orch.open_pairs) == 1
    # Spread converges → both legs close.
    orch.price_feed.ohlcv_by_symbol["XRP/USDT"][-1] = 100.0
    orch.price_feed.prices["XRP/USDT"] = 100.0
    res = orch.run_cycle(NOW + timedelta(hours=1))
    assert len(orch.open_pairs) == 0
    pair_closes = [c for c in res.closed if c.position.strategy.startswith("pairs:")]
    assert len(pair_closes) == 2


def test_decayed_pair_is_skipped():
    from src.database import TradeRecord
    a_closes, b_closes = _stretched_pair(105.0)   # z>2 → would normally enter
    orch, db = _orch(
        {"XRP/USDT": 105.0, "DOGE/USDT": 100.0},
        ohlcv_by_symbol={"XRP/USDT": a_closes, "DOGE/USDT": b_closes},
        pairs=[("XRP/USDT", "DOGE/USDT")],
    )
    # Seed 12 losing rounds (each = 2 legs sharing a close time) → pair has decayed.
    for i in range(12):
        ts = f"2026-06-{i+1:02d}T00:00:00Z"
        for leg_pnl in (-0.3, -0.2):
            tid = db.open_trade(TradeRecord(
                pair="XRP/USDT", side="short", strategy="pairs:XRP/USDT~DOGE/USDT",
                entry_price=1.0, size_usd=10.0, opened_at=ts, is_paper=True))
            db.close_trade(tid, closed_at=ts, exit_price=1.0, pnl_usd=leg_pnl,
                           pnl_pct=-0.03, fees_usd=0.0, exit_reason="pairs:converged")
    res = orch.run_cycle(NOW)
    assert not any(p.strategy.startswith("pairs:") for p in res.opened)   # health gate blocks it
    assert any("decayed" in s or "paused" in s for s in res.skipped)


def test_pair_rehydrates_after_restart():
    a_closes, b_closes = _stretched_pair(105.0)
    feed_kw = {"ohlcv_by_symbol": {"XRP/USDT": a_closes, "DOGE/USDT": b_closes},
               "pairs": [("XRP/USDT", "DOGE/USDT")]}
    orch, db = _orch({"XRP/USDT": 105.0, "DOGE/USDT": 100.0}, **feed_kw)
    orch.run_cycle(NOW)
    assert len(orch.open_pairs) == 1
    # New orchestrator on the SAME db → should reload the open pair (both legs) from disk.
    feed = FakePriceFeed({"XRP/USDT": 105.0, "DOGE/USDT": 100.0},
                         ohlcv_by_symbol={"XRP/USDT": a_closes, "DOGE/USDT": b_closes})
    orch2 = Orchestrator(Config(), db, SystemController(),
                         DryRunExecutionClient(500.0, feed), feed,
                         funding_monitor=FundingMonitor(),
                         pairs=[("XRP/USDT", "DOGE/USDT")])
    assert len(orch2.open_pairs) == 1
    assert orch2.pairs_trader.held.get("XRP/USDT~DOGE/USDT") == "short_spread"


# ── State persistence ────────────────────────────────────────────────────────
def test_cycle_persists_capital_and_regime():
    orch, db = _orch({"BTC/USDT": 60000.0})
    orch.run_cycle(NOW)
    assert db.get_state("capital") is not None
    assert db.get_state("regime") == "TRENDING"   # synthetic uptrend
    assert db.get_state("peak_capital") is not None


def test_exposure_cap_blocks_second_same_coin_pair():
    # Two pairs both sharing XRPX, both stretched so both want to short XRPX. With a low cap,
    # the first opens and the second is blocked to avoid concentrating XRPX exposure.
    window = list(100 + np.sin(np.arange(168) / 5.0))
    xrp_stretched = window + [108.0]      # XRPX rich → short_spread (short XRPX)
    flat = [100.0] * 169
    pairs = [("XRPX/USDT", "DOGEX/USDT"), ("XRPX/USDT", "GALAX/USDT")]
    orch, db = _orch(
        {"XRPX/USDT": 108.0, "DOGEX/USDT": 100.0, "GALAX/USDT": 100.0},
        ohlcv_by_symbol={"XRPX/USDT": xrp_stretched, "DOGEX/USDT": flat, "GALAX/USDT": flat},
        pairs=pairs,
    )
    orig = orch_mod.MAX_ASSET_EXPOSURE_PCT
    orch_mod.MAX_ASSET_EXPOSURE_PCT = 0.05   # $25 cap on $500 → second XRPX pair breaches
    try:
        res = orch.run_cycle(NOW)
    finally:
        orch_mod.MAX_ASSET_EXPOSURE_PCT = orig
    assert len(orch.open_pairs) == 1                          # only the first opened
    assert any("exposure" in s for s in res.skipped)          # second blocked by the cap


def test_circuit_breaker_pauses_pairs_on_mass_divergence():
    from src.database import TradeRecord
    a_closes, b_closes = _stretched_pair(105.0)   # XRP~DOGE would normally enter
    orch, db = _orch(
        {"XRP/USDT": 105.0, "DOGE/USDT": 100.0},
        ohlcv_by_symbol={"XRP/USDT": a_closes, "DOGE/USDT": b_closes},
        pairs=[("XRP/USDT", "DOGE/USDT")],
    )
    # Seed 3 divergence-stop rounds within the breaker window (NOW=04:00 UTC, window back to 02:00).
    for nm in ("A~B", "C~D", "E~F"):
        for leg in (-0.4, -0.3):
            tid = db.open_trade(TradeRecord(pair="X/USDT", side="short", strategy=f"pairs:{nm}",
                                            entry_price=1.0, size_usd=10.0,
                                            opened_at="2026-06-17T03:00:00+00:00"))
            db.close_trade(tid, closed_at="2026-06-17T03:00:00+00:00", exit_price=1.0,
                           pnl_usd=leg, pnl_pct=-0.04, fees_usd=0.0,
                           exit_reason="pairs:stop (diverged)")
    res = orch.run_cycle(NOW)
    assert not any(p.strategy.startswith("pairs:") for p in res.opened)       # pairs paused
    assert any("circuit breaker" in s.lower() for s in res.skipped)
    assert any("CIRCUIT BREAKER" in a for a in res.alerts)                    # alert emitted once


def test_thesis_order_execute_cap_and_close():
    orch, db = _orch({"SOL/USDT": 100.0})
    db.add_thesis_order("buy", "SOL/USDT", 10.0, "2026-06-17T00:00:00+00:00")
    res = orch.run_cycle(NOW)
    assert any(p.strategy == "thesis" and p.pair == "SOL/USDT" for p in res.opened)
    # Sleeve cap: a 50% order on top of the 10% already used exceeds the 15% cap → rejected.
    db.add_thesis_order("buy", "SOL/USDT", 50.0, "2026-06-17T00:00:00+00:00")
    res2 = orch.run_cycle(NOW)
    assert any("sleeve full" in a for a in res2.alerts)
    # Close it.
    db.add_thesis_order("close", "SOL/USDT", 0, "2026-06-17T00:00:00+00:00")
    res3 = orch.run_cycle(NOW)
    assert any(c.position.strategy == "thesis" for c in res3.closed)


def test_long_term_thesis_wide_stop_no_take_profit():
    orch, db = _orch({"SOL/USDT": 100.0})
    db.add_thesis_order("buy_lt", "SOL/USDT", 10.0, "2026-06-17T00:00:00+00:00")
    orch.run_cycle(NOW)
    assert any(p.strategy == "thesis-lt" for p in orch.open_positions.values())
    # -12%: a short-term thesis (8% stop) would exit here; a long-term hold (35%) rides it.
    orch.price_feed.prices["SOL/USDT"] = 88.0
    orch.run_cycle(NOW)
    assert any(p.strategy == "thesis-lt" for p in orch.open_positions.values())
    # +30%: no take-profit on a long-term hold; let the winner run.
    orch.price_feed.prices["SOL/USDT"] = 130.0
    orch.run_cycle(NOW)
    assert any(p.strategy == "thesis-lt" for p in orch.open_positions.values())
    # -40%: beyond the wide 35% stop → it finally closes.
    orch.price_feed.prices["SOL/USDT"] = 60.0
    orch.run_cycle(NOW)
    assert not any(p.strategy == "thesis-lt" for p in orch.open_positions.values())


def test_stock_thesis_rejected_without_stock_venue():
    orch, db = _orch({"MU/USDT": 100.0})          # no stock_exec configured
    db.add_thesis_order("buy", "MU/USDT", 5.0, "2026-06-17T00:00:00+00:00")
    res = orch.run_cycle(NOW)
    assert not any(p.pair == "MU/USDT" for p in res.opened)
    assert any("stock execution isn't connected" in a for a in res.alerts)


def test_thesis_routes_crypto_and_stock_to_right_venue():
    from src.execution import DryRunExecutionClient
    feed = FakePriceFeed({"SOL/USDT": 150.0, "MU/USDT": 100.0})
    stock_client = DryRunExecutionClient(1000.0, feed)
    orch, db = _orch({"SOL/USDT": 150.0, "MU/USDT": 100.0})
    orch.stock_exec = stock_client
    db.add_thesis_order("buy", "SOL/USDT", 5.0, "2026-06-17T00:00:00+00:00")   # crypto -> self.exec
    db.add_thesis_order("buy", "MU/USDT", 5.0, "2026-06-17T00:00:00+00:00")    # stock  -> stock_exec
    res = orch.run_cycle(NOW)
    opened = {p.pair for p in res.opened}
    assert "SOL/USDT" in opened and "MU/USDT" in opened
    # The stock leg drew its entry fee from the IBKR (stock) client, not the crypto balance.
    assert stock_client.get_balance() < 1000.0
