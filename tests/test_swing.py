import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.swing import floor_value, should_halt, risk_budget, size_bet  # noqa: E402

START, FLOOR0 = 1000.0, 200.0   # $1,000 pot, halt at $200 (down 80%)


def test_floor_starts_at_initial():
    assert floor_value(START, FLOOR0, high_water=1000.0) == 200.0
    assert floor_value(START, FLOOR0, high_water=800.0) == 200.0   # underwater → floor unchanged


def test_floor_ratchets_up_on_new_highs():
    # pot doubled to $2,000 → floor locks to 50% of peak = $1,000 (original stake protected)
    assert floor_value(START, FLOOR0, high_water=2000.0) == 1000.0
    # peak $4,000 → floor $2,000
    assert floor_value(START, FLOOR0, high_water=4000.0) == 2000.0


def test_circuit_breaker():
    assert should_halt(200.0, 200.0) is True       # at the floor → halt
    assert should_halt(199.0, 200.0) is True
    assert should_halt(250.0, 200.0) is False


def test_risk_budget_is_the_cushion():
    assert risk_budget(1000.0, 200.0) == 800.0      # can risk up to the cushion above the floor
    assert risk_budget(150.0, 200.0) == 0.0         # below floor → nothing


def test_bet_is_capped_fraction_of_cushion():
    # cushion $800, 25% → $200 bet; even a total loss leaves $800 (still above the $200 floor)
    assert size_bet(1000.0, 200.0, 0.25) == 200.0
