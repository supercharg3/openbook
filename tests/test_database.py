import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Database, TradeRecord  # noqa: E402


def _db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Database(tmp.path if hasattr(tmp, "path") else tmp.name)


def test_open_and_close_trade_roundtrip():
    db = _db()
    tid = db.open_trade(TradeRecord(
        pair="BTC/USDT", side="long", strategy="trend",
        entry_price=60000.0, size_usd=50.0, opened_at="2026-06-17T00:00:00Z",
    ))
    assert tid > 0
    assert len(db.open_positions()) == 1
    assert len(db.closed_trades()) == 0

    db.close_trade(tid, closed_at="2026-06-17T02:00:00Z", exit_price=61200.0,
                   pnl_usd=1.0, pnl_pct=0.02, fees_usd=0.05, exit_reason="tp")
    assert len(db.open_positions()) == 0
    closed = db.closed_trades()
    assert len(closed) == 1
    assert abs(closed[0]["pnl_usd"] - 1.0) < 1e-9


def test_closed_trades_filter_by_strategy():
    db = _db()
    for strat in ("trend", "trend", "grid"):
        tid = db.open_trade(TradeRecord(
            pair="ETH/USDT", side="long", strategy=strat,
            entry_price=3000.0, size_usd=30.0, opened_at="2026-06-17T00:00:00Z",
        ))
        db.close_trade(tid, closed_at="2026-06-17T01:00:00Z", exit_price=3030.0,
                       pnl_usd=0.3, pnl_pct=0.01, fees_usd=0.01, exit_reason="signal")
    assert len(db.closed_trades(strategy="trend")) == 2
    assert len(db.closed_trades(strategy="grid")) == 1


def test_state_key_value():
    db = _db()
    assert db.get_state("regime", "unknown") == "unknown"
    db.set_state("regime", "trending", "2026-06-17T00:00:00Z")
    assert db.get_state("regime") == "trending"
    db.set_state("regime", "ranging", "2026-06-17T01:00:00Z")  # upsert
    assert db.get_state("regime") == "ranging"


def test_correlation_cache_is_order_independent():
    db = _db()
    db.cache_correlation("ETH/USDT", "BTC/USDT", 0.82, "2026-06-17T00:00:00Z")
    assert abs(db.get_correlation("BTC/USDT", "ETH/USDT") - 0.82) < 1e-9
    assert abs(db.get_correlation("ETH/USDT", "BTC/USDT") - 0.82) < 1e-9
    assert db.get_correlation("SOL/USDT", "BTC/USDT") is None
