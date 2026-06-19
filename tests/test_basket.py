import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import basket  # noqa: E402


def test_load_falls_back_to_defaults():
    d = tempfile.mkdtemp()
    pairs, allocs = basket.load_active(d, [("A/USDT","B/USDT")], {"A/USDT~B/USDT": 0.1})
    assert pairs == [("A/USDT","B/USDT")] and allocs == {"A/USDT~B/USDT": 0.1}


def test_save_and_load_roundtrip():
    d = tempfile.mkdtemp()
    basket.save(d, basket.ACTIVE, [("X/USDT","Y/USDT")], {"X/USDT~Y/USDT": 0.2})
    pairs, allocs = basket.load_active(d, [], {})
    assert pairs == [("X/USDT","Y/USDT")] and allocs == {"X/USDT~Y/USDT": 0.2}


def test_promote_proposed():
    d = tempfile.mkdtemp()
    basket.save(d, basket.PROPOSED, [("P/USDT","Q/USDT")], {"P/USDT~Q/USDT": 0.3})
    assert basket.promote_proposed(d) is True
    pairs, allocs = basket.load_active(d, [], {})
    assert pairs == [("P/USDT","Q/USDT")]
    # nothing proposed in a fresh dir -> False
    assert basket.promote_proposed(tempfile.mkdtemp()) is False
