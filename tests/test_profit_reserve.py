import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.profit_reserve import update_reserve, tradeable_capital  # noqa: E402


def test_reserve_grows_only_on_new_highs():
    hwm, res = update_reserve(510, 500, 0.0, frac=0.30)
    assert abs(res - 3.0) < 1e-9 and hwm == 510
    # a dip does not change the reserve
    hwm, res = update_reserve(505, hwm, res, frac=0.30)
    assert abs(res - 3.0) < 1e-9 and hwm == 510
    # a new high adds more
    hwm, res = update_reserve(530, hwm, res, frac=0.30)
    assert abs(res - 9.0) < 1e-9 and hwm == 530


def test_reserve_never_decreases_through_drawdown():
    hwm, res = 500.0, 0.0
    for eq in [600, 550, 500, 450]:   # one big high then a deep drawdown
        hwm, res = update_reserve(eq, hwm, res, frac=0.30)
    assert abs(res - 30.0) < 1e-9    # locked from the $600 high, untouched by the drop


def test_tradeable_excludes_reserve():
    assert tradeable_capital(1000, 120) == 880
    assert tradeable_capital(100, 200) == 0.0   # never negative
