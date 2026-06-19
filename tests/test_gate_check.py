import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Database, TradeRecord  # noqa: E402
from src.gate_check import evaluate_gate, format_gate_message  # noqa: E402

NOW = "2026-06-17T12:00:00+08:00"


def _db():
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False); t.close()
    return Database(t.name)


def _add_round(db, name, closed_at, reason, pnl=0.1):
    for leg_pnl in (pnl, -pnl / 2):
        tid = db.open_trade(TradeRecord(pair="A/USDT", side="long", strategy=f"pairs:{name}",
                                        entry_price=1.0, size_usd=10.0, opened_at=closed_at))
        db.close_trade(tid, closed_at=closed_at, exit_price=1.0, pnl_usd=leg_pnl,
                       pnl_pct=0.01, fees_usd=0.0, exit_reason=reason)


def test_fresh_db_not_ready():
    db = _db()
    assert evaluate_gate(db, NOW).ready is False


def test_ready_when_all_criteria_met():
    db = _db()
    pairs = ["XRP~DOGE", "SOL~AVAX", "ETH~IMX", "XRP~GALA"]
    # 10 rounds across 4 pairs, both exit types
    for i in range(10):
        name = pairs[i % 4]
        reason = "pairs:converged" if i % 3 else "pairs:stop (diverged)"
        _add_round(db, name, f"2026-06-{i+1:02d}T00:00:00+00:00", reason)
    db.set_state("daily_report_count", "3", NOW)
    db.set_state("last_cycle_at", "2026-06-17T11:50:00+08:00", NOW)  # 10 min ago
    status = evaluate_gate(db, NOW)
    assert status.ready is True
    assert all(p for _, p, _ in status.checks)


def test_not_ready_if_stale():
    db = _db()
    pairs = ["XRP~DOGE", "SOL~AVAX", "ETH~IMX", "XRP~GALA"]
    for i in range(10):
        reason = "pairs:converged" if i % 3 else "pairs:stop (diverged)"
        _add_round(db, pairs[i % 4], f"2026-06-{i+1:02d}T00:00:00+00:00", reason)
    db.set_state("daily_report_count", "3", NOW)
    db.set_state("last_cycle_at", "2026-06-17T06:00:00+08:00", NOW)  # 6h ago → stale
    assert evaluate_gate(db, NOW).ready is False


def test_not_ready_missing_stop_exit():
    db = _db()
    pairs = ["XRP~DOGE", "SOL~AVAX", "ETH~IMX", "XRP~GALA"]
    for i in range(10):
        _add_round(db, pairs[i % 4], f"2026-06-{i+1:02d}T00:00:00+00:00", "pairs:converged")
    db.set_state("daily_report_count", "3", NOW)
    db.set_state("last_cycle_at", "2026-06-17T11:50:00+08:00", NOW)
    status = evaluate_gate(db, NOW)
    assert status.ready is False
    assert any(name == "Stop/divergence exit observed" and not passed
               for name, passed, _ in status.checks)


def test_message_renders():
    db = _db()
    msg = format_gate_message(evaluate_gate(db, NOW))
    assert "GATE CLEARED" in msg and "cost-discovery" in msg


def test_pair_rounds_groups_legs_closing_milliseconds_apart():
    from src.gate_check import _pair_rounds
    # the real bug: two legs of one round closed 81ms apart → must be ONE round, not two half-open
    rows = [
        {"strategy": "pairs:DOGE/USDT~IMX/USDT", "closed_at": "2026-06-17T20:51:43.677658+00:00"},
        {"strategy": "pairs:DOGE/USDT~IMX/USDT", "closed_at": "2026-06-17T20:51:43.596434+00:00"},
    ]
    rounds = _pair_rounds(rows)
    assert len(rounds) == 1
    assert all(len(legs) == 2 for legs in rounds.values())          # complete, no half-open


def test_pair_rounds_separates_distinct_rounds():
    from src.gate_check import _pair_rounds
    rows = [
        {"strategy": "pairs:A~B", "closed_at": "2026-06-17T20:51:43.600000+00:00"},
        {"strategy": "pairs:A~B", "closed_at": "2026-06-17T20:51:43.700000+00:00"},   # same round
        {"strategy": "pairs:A~B", "closed_at": "2026-06-17T22:10:00.000000+00:00"},   # later round
        {"strategy": "pairs:A~B", "closed_at": "2026-06-17T22:10:00.100000+00:00"},
    ]
    rounds = _pair_rounds(rows)
    assert len(rounds) == 2 and all(len(v) == 2 for v in rounds.values())
