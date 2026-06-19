import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.alpaca import AlpacaExecutionClient  # noqa: E402


class _Order:
    def __init__(self, oid, price, qty):
        self.id = oid; self.status = "filled"; self.filled_avg_price = price; self.filled_qty = qty


class _Acct:
    cash = "100000"; buying_power = "100000"


class FakeAlpaca:
    """Stand-in for alpaca TradingClient."""
    def __init__(self, price): self.price = price; self.submitted = []; self.closed = []
    def get_account(self): return _Acct()
    def submit_order(self, req):
        self.submitted.append(req)
        # notional / price = fractional qty
        qty = round(req.notional / self.price, 6)
        self._last = _Order("oid1", self.price, qty)
        return self._last
    def get_order_by_id(self, oid): return self._last
    def close_position(self, symbol): self.closed.append(symbol)


def test_notional_fractional_buy():
    fa = FakeAlpaca(1000.0)
    c = AlpacaExecutionClient(fa, paper=True, price_fn=lambda s: 1000.0)
    pos = c.open_position("MU/USDT", "long", 25.0, 1.0, "thesis")
    # $25 of a $1000 stock = a fraction, ~$25 deployed (not a whole $1000 share)
    assert abs(pos.size_usd - 25.0) < 1.0
    assert pos.entry_price == 1000.0
    assert fa.submitted[0].notional == 25.0


def test_close_liquidates_and_computes_pnl():
    fa = FakeAlpaca(100.0)
    c = AlpacaExecutionClient(fa, paper=True, price_fn=lambda s: 100.0)
    pos = c.open_position("NVDA/USDT", "long", 50.0, 1.0, "thesis")
    c._price_fn = lambda s: 110.0      # +10%
    closed = c.close_position(pos, "thesis-manual")
    assert "NVDA" in fa.closed
    assert closed.pnl_usd > 0


def test_balance():
    assert AlpacaExecutionClient(FakeAlpaca(1.0)).get_balance() == 100000.0
