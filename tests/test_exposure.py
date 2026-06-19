import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.risk_manager import net_asset_exposure, exposure_breach  # noqa: E402


def test_net_exposure_accumulates_same_side():
    # XRP short in two pairs → net short doubles
    legs = [("XRP/USDT", "short", 50.0), ("DOGE/USDT", "long", 50.0),
            ("XRP/USDT", "short", 40.0), ("GALA/USDT", "long", 40.0)]
    exp = net_asset_exposure(legs)
    assert abs(exp["XRP"] - (-90.0)) < 1e-9
    assert abs(exp["DOGE"] - 50.0) < 1e-9


def test_opposite_sides_net_out():
    legs = [("XRP/USDT", "short", 50.0), ("XRP/USDT", "long", 50.0)]
    assert abs(net_asset_exposure(legs)["XRP"]) < 1e-9


def test_breach_detected():
    current = [("XRP/USDT", "short", 80.0), ("DOGE/USDT", "long", 80.0)]
    new = [("XRP/USDT", "short", 60.0), ("GALA/USDT", "long", 60.0)]  # XRP → -140
    breach = exposure_breach(current, new, cap_usd=125.0)
    assert breach is not None
    assert breach[0] == "XRP"
    assert abs(breach[1] - (-140.0)) < 1e-9


def test_no_breach_when_within_cap():
    current = [("XRP/USDT", "short", 50.0), ("DOGE/USDT", "long", 50.0)]
    new = [("SOL/USDT", "long", 50.0), ("AVAX/USDT", "short", 50.0)]  # different coins
    assert exposure_breach(current, new, cap_usd=125.0) is None
