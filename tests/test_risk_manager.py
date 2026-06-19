import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.risk_manager import (  # noqa: E402
    SGT,
    AccountState,
    RiskManager,
    TradeProposal,
    is_weekend_tradfi_blackout,
    max_leverage_allowed,
)


def base_account(**kw):
    defaults = dict(current_capital=500.0, peak_capital=500.0)
    defaults.update(kw)
    return AccountState(**defaults)


def simple_proposal(**kw):
    defaults = dict(pair="BTC/USDT", side="long", strategy="trend", size_usd=50.0)
    defaults.update(kw)
    return TradeProposal(**defaults)


# ── Leverage gates ───────────────────────────────────────────────────────────
def test_leverage_locked_at_start():
    assert max_leverage_allowed(0, 0) == 1.0
    assert max_leverage_allowed(29, 100) == 1.0     # not enough trades
    assert max_leverage_allowed(100, 10) == 1.0     # not enough days


def test_leverage_2x_needs_both_trades_and_time():
    assert max_leverage_allowed(30, 28) == 2.0
    assert max_leverage_allowed(50, 27) == 1.0      # 1 day short
    assert max_leverage_allowed(29, 60) == 1.0      # 1 trade short


def test_leverage_3x_gate():
    assert max_leverage_allowed(90, 90) == 3.0
    assert max_leverage_allowed(89, 90) == 2.0


# ── Portfolio halts ──────────────────────────────────────────────────────────
def test_drawdown_blocks_everything():
    acct = base_account(current_capital=400.0, peak_capital=500.0)  # 20% dd
    rm = RiskManager(acct)
    assert rm.drawdown_breached()
    assert not rm.evaluate(simple_proposal()).approved


def test_daily_loss_limit_blocks():
    acct = base_account(realized_pnl_today=-15.0)  # 3% of 500
    rm = RiskManager(acct)
    assert not rm.evaluate(simple_proposal()).approved


def test_weekly_loss_limit_blocks():
    acct = base_account(realized_pnl_week=-35.0)  # 7% of 500
    rm = RiskManager(acct)
    assert not rm.evaluate(simple_proposal()).approved


# ── Position / open caps ─────────────────────────────────────────────────────
def test_max_open_positions():
    acct = base_account(open_positions=3)
    rm = RiskManager(acct)
    assert not rm.evaluate(simple_proposal()).approved


def test_position_size_trimmed_to_cap():
    acct = base_account()
    rm = RiskManager(acct)
    d = rm.evaluate(simple_proposal(size_usd=200.0))  # 40% > 15% cap
    assert d.approved
    assert abs(d.adjusted_size_usd - 75.0) < 1e-9     # 15% of 500


def test_paused_strategy_blocked():
    acct = base_account(paused_strategies={"trend"})
    rm = RiskManager(acct)
    assert not rm.evaluate(simple_proposal(strategy="trend")).approved


# ── Leverage in evaluate() ───────────────────────────────────────────────────
def test_leverage_request_above_unlock_blocked():
    acct = base_account(live_trade_count=0, days_live=0)
    rm = RiskManager(acct)
    assert not rm.evaluate(simple_proposal(leverage=2.0)).approved


def test_portfolio_leverage_cap_trims():
    # cap = 30% of 500 = 150; already 120 leveraged → only 30 of room
    acct = base_account(live_trade_count=100, days_live=100, leveraged_exposure_usd=120.0)
    rm = RiskManager(acct)
    d = rm.evaluate(simple_proposal(size_usd=75.0, leverage=2.0))
    assert d.approved
    assert abs(d.adjusted_size_usd - 30.0) < 1e-9


def test_reduce_risk_halves_size():
    future = datetime.now(SGT) + timedelta(days=3)
    acct = base_account(reduce_risk_until=future)
    rm = RiskManager(acct)
    d = rm.evaluate(simple_proposal(size_usd=75.0))  # normal cap 75; halved → 37.5
    assert d.approved
    assert abs(d.adjusted_size_usd - 37.5) < 1e-9


# ── Weekend TradFi rule ──────────────────────────────────────────────────────
def test_weekend_blackout_windows():
    fri_4pm = datetime(2026, 6, 19, 16, 0, tzinfo=SGT)   # Friday before 5pm
    fri_6pm = datetime(2026, 6, 19, 18, 0, tzinfo=SGT)   # Friday after 5pm
    sat = datetime(2026, 6, 20, 12, 0, tzinfo=SGT)
    mon = datetime(2026, 6, 22, 9, 0, tzinfo=SGT)
    assert not is_weekend_tradfi_blackout(fri_4pm)
    assert is_weekend_tradfi_blackout(fri_6pm)
    assert is_weekend_tradfi_blackout(sat)
    assert not is_weekend_tradfi_blackout(mon)


def test_leveraged_tradfi_blocked_on_weekend():
    acct = base_account(live_trade_count=100, days_live=100)
    rm = RiskManager(acct)
    sat = datetime(2026, 6, 20, 12, 0, tzinfo=SGT)
    p = simple_proposal(pair="NVDA/USDT", leverage=2.0, is_tradfi=True)
    assert not rm.evaluate(p, now_sgt=sat).approved
    # but spot (1x) TradFi is fine on weekend
    p_spot = simple_proposal(pair="NVDA/USDT", leverage=1.0, is_tradfi=True)
    assert rm.evaluate(p_spot, now_sgt=sat).approved


def test_clean_trade_approved():
    acct = base_account()
    rm = RiskManager(acct)
    d = rm.evaluate(simple_proposal(size_usd=50.0))
    assert d.approved
    assert d.adjusted_size_usd == 50.0  # within cap, returned as-is
    assert d.was_trimmed is False
