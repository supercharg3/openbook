import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.stock_factor import (  # noqa: E402
    momentum_score, rank_and_select, rebalance_diff, equal_weights, catastrophe_stop_hit,
)


def test_momentum_score_basics():
    closes = [100.0] * 199 + [120.0]            # below 200d MA? price 120 > mean(~100.1) → above
    s = momentum_score(closes)
    assert s["above_ma"] is True
    assert 0.99 < s["proximity"] <= 1.0          # 120 is the high → proximity ~1.0
    # not enough history
    assert momentum_score([100.0] * 50) is None


def test_momentum_below_ma():
    closes = [200.0] * 199 + [100.0]            # price 100 well below the ~199.5 MA
    s = momentum_score(closes)
    assert s["above_ma"] is False


def test_rank_and_select_filters_and_ranks():
    scores = {
        "A": {"price": 1, "proximity": 0.99, "above_ma": True},
        "B": {"price": 1, "proximity": 0.95, "above_ma": True},
        "C": {"price": 1, "proximity": 0.999, "above_ma": False},   # excluded (below MA)
        "D": {"price": 1, "proximity": 0.80, "above_ma": True},
    }
    assert rank_and_select(scores, n=2) == ["A", "B"]               # ranked by proximity, C filtered
    assert "C" not in rank_and_select(scores, n=4)


def test_rank_holds_cash_when_few_eligible():
    scores = {"A": {"price": 1, "proximity": 0.9, "above_ma": False}}
    assert rank_and_select(scores, n=8) == []                       # nothing eligible → all cash


def test_rebalance_diff():
    d = rebalance_diff({"A", "B", "C"}, {"B", "C", "D"})
    assert d["sell"] == ["A"] and d["buy"] == ["D"] and d["hold"] == ["B", "C"]


def test_equal_weights():
    assert equal_weights(["A", "B", "C", "D"]) == {t: 0.25 for t in "ABCD"}
    assert equal_weights([]) == {}


def test_catastrophe_stop():
    # -25% from entry breaches the 20% max-loss seatbelt
    assert catastrophe_stop_hit(100.0, 75.0, atr_value=0.0) is True
    # -5% is within tolerance
    assert catastrophe_stop_hit(100.0, 95.0, atr_value=0.0) is False


def test_scorecard_beats_and_lags():
    from src.stock_factor import compute_scorecard
    # sleeve outperforms SPY steadily → positive excess + IR
    rows = []
    s, p = 10000.0, 10000.0
    for i in range(260):
        wiggle = 0.004 if i % 2 == 0 else -0.003          # alternating noise → nonzero variance
        s *= (1.0012 + wiggle); p *= (1.0005 + wiggle)
        rows.append({"day": str(i), "sleeve_value": s, "spy_value": p})
    sc = compute_scorecard(rows)
    assert sc["excess_return"] > 0 and sc["information_ratio"] > 0
    assert "gate_cleared" in sc
    # too little data → no verdict
    assert compute_scorecard(rows[:1])["verdict"] == "not enough data yet"


def test_combined_select_blends_and_falls_back():
    from src.stock_factor import combined_select
    scores = {
        "A": {"price": 1, "proximity": 0.99, "above_ma": True},   # best momentum
        "B": {"price": 1, "proximity": 0.95, "above_ma": True},
        "C": {"price": 1, "proximity": 0.90, "above_ma": True},   # best quality
        "D": {"price": 1, "proximity": 0.85, "above_ma": False},  # filtered (below MA)
    }
    gpa = {"A": 0.10, "B": 0.20, "C": 0.40}                       # C highest quality
    sel = combined_select(scores, gpa, n=2, quality_weight=0.5)
    assert "D" not in sel and len(sel) == 2                       # trend filter + blend
    # no quality data → pure momentum
    assert combined_select(scores, {}, n=2) == ["A", "B"]


def test_sleeves_and_ai_universe():
    from src.stock_factor import SLEEVES, AI_SEMI_UNIVERSE
    tags = {s["tag"] for s in SLEEVES}
    assert tags == {"factor", "factor-ai"}
    benches = {s["tag"]: s["benchmark"] for s in SLEEVES}
    assert benches["factor"] == "SPY" and benches["factor-ai"] == "SMH"
    assert "NVDA" in AI_SEMI_UNIVERSE and "MU" in AI_SEMI_UNIVERSE and len(AI_SEMI_UNIVERSE) >= 20
