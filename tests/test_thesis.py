import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.thesis import parse_thesis_order  # noqa: E402


def test_parse_orders():
    assert parse_thesis_order("BUY MU 10%") == ("buy", "MU/USDT", 10.0)
    assert parse_thesis_order("short SOL") == ("sell", "SOL/USDT", 5.0)
    assert parse_thesis_order("close mu") == ("close", "MU/USDT", 5.0)
    assert parse_thesis_order("buy ARB 7.5") == ("buy", "ARB/USDT", 7.5)


def test_long_term_holds():
    assert parse_thesis_order("BUY MU 10% hold") == ("buy_lt", "MU/USDT", 10.0)
    assert parse_thesis_order("buy MU 8% long term") == ("buy_lt", "MU/USDT", 8.0)
    assert parse_thesis_order("short sol 5% lt") == ("sell_lt", "SOL/USDT", 5.0)
    # 'hold' on a close is meaningless → stays a plain close
    assert parse_thesis_order("close mu") == ("close", "MU/USDT", 5.0)


def test_ignores_non_orders():
    assert parse_thesis_order("close all") is None      # fixed command, not a thesis order
    assert parse_thesis_order("how are we doing") is None
    assert parse_thesis_order("status") is None
