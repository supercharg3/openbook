import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.venues import classify_venue  # noqa: E402


def test_crypto_vs_stock():
    assert classify_venue("SOL") == "crypto"
    assert classify_venue("BTC") == "crypto"
    assert classify_venue("HYPE") == "crypto"
    assert classify_venue("MU") == "stock"
    assert classify_venue("NVDA") == "stock"
    assert classify_venue("CRDO") == "stock"


def test_basket_universe_extends_crypto():
    # A coin not in the defaults but in the live basket still routes to crypto.
    assert classify_venue("FOO") == "stock"
    assert classify_venue("FOO", {"FOO"}) == "crypto"
