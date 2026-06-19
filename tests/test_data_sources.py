import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.funding_arb import (  # noqa: E402
    FundingMonitor,
    FundingSnapshot,
    filter_liquid,
    is_liquid,
)
from src.news_scanner import (  # noqa: E402
    NewsItem,
    SignalScore,
    build_intent,
    parse_score,
    should_act,
    size_fraction_for_confidence,
)
from src.token_unlocks import (  # noqa: E402
    UnlockEvent,
    evaluate_unlock,
    is_material,
)


# ── Funding arb ──────────────────────────────────────────────────────────────
def test_liquidity_floor_filters_microcaps():
    assert is_liquid("BTC/USDT")
    assert is_liquid("ARB/USDT")
    assert not is_liquid("SKHYNIX/USDT")
    assert not is_liquid("SIREN/USDT")
    snaps = [
        FundingSnapshot("BTC/USDT", 0.001),
        FundingSnapshot("SKHYNIX/USDT", 0.01),   # juicy but illiquid → dropped
        FundingSnapshot("ETH/USDT", 0.0008),
        FundingSnapshot("STXX/USDT", 0.02),       # dropped
    ]
    kept = {s.symbol for s in filter_liquid(snaps)}
    assert kept == {"BTC/USDT", "ETH/USDT"}


def test_funding_rank_filters_and_sorts():
    m = FundingMonitor()
    snaps = [
        FundingSnapshot("BTC/USDT", 0.0001),   # below entry
        FundingSnapshot("ETH/USDT", 0.0008),
        FundingSnapshot("SOL/USDT", 0.0012),
    ]
    ranked = m.rank_opportunities(snaps)
    assert [s.symbol for s in ranked] == ["SOL/USDT", "ETH/USDT"]


def test_funding_entry_and_income_estimate():
    m = FundingMonitor()
    d = m.evaluate_entry(FundingSnapshot("SOL/USDT", 0.001), notional_usd=200.0)
    assert d.action == "enter"
    assert abs(d.estimated_8h_income_usd - 0.2) < 1e-9


def test_funding_exit_requires_two_low_windows():
    m = FundingMonitor()
    m.record_entry("SOL/USDT", 200.0, 0.001)
    # first low window → hold
    d1 = m.evaluate_exit(FundingSnapshot("SOL/USDT", 0.00001))
    assert d1.action == "hold"
    # second consecutive low window → exit
    d2 = m.evaluate_exit(FundingSnapshot("SOL/USDT", 0.00001))
    assert d2.action == "exit"


def test_funding_recovery_resets_streak():
    m = FundingMonitor()
    m.record_entry("SOL/USDT", 200.0, 0.001)
    m.evaluate_exit(FundingSnapshot("SOL/USDT", 0.00001))      # low (streak 1)
    m.evaluate_exit(FundingSnapshot("SOL/USDT", 0.0009))       # recovered → reset
    d = m.evaluate_exit(FundingSnapshot("SOL/USDT", 0.00001))  # low again (streak 1, not 2)
    assert d.action == "hold"


# ── News scanner ─────────────────────────────────────────────────────────────
def test_confidence_sizing_tiers():
    assert size_fraction_for_confidence(0.95) == 0.15
    assert size_fraction_for_confidence(0.85) == 0.10
    assert size_fraction_for_confidence(0.77) == 0.05
    assert size_fraction_for_confidence(0.70) == 0.0


def test_should_act_gates():
    good = SignalScore("long", 0.85, "SOL", "listing")
    assert should_act(good, price_drift_since_publish=0.001)
    # already moved too far
    assert not should_act(good, price_drift_since_publish=0.01)
    # neutral / no asset
    assert not should_act(SignalScore("neutral", 0.9, "NONE", ""), 0.0)
    # low confidence
    assert not should_act(SignalScore("long", 0.6, "SOL", ""), 0.0)


def test_build_intent():
    score = SignalScore("short", 0.92, "ARB", "unlock soon")
    item = NewsItem("t", "x", "http://e.x/1", "2026-06-17T00:00:00Z")
    intent = build_intent(score, item)
    assert intent is not None
    assert intent.size_fraction == 0.15
    assert intent.direction == "short"
    assert intent.source_url == "http://e.x/1"


def test_parse_score_handles_code_fence():
    raw = '```json\n{"direction": "long", "confidence": 0.8, "asset": "btc", "rationale": "x"}\n```'
    s = parse_score(raw)
    assert s.direction == "long"
    assert s.asset == "BTC"
    assert s.confidence == 0.8


# ── Token unlocks ────────────────────────────────────────────────────────────
def test_material_threshold():
    assert is_material(UnlockEvent("ARB", date(2026, 7, 1), 0.05, 1e6))
    assert not is_material(UnlockEvent("ARB", date(2026, 7, 1), 0.005, 1e6))


def test_unlock_enters_short_in_window():
    ev = UnlockEvent("ARB", date(2026, 6, 22), 0.04, 5e6)
    sig = evaluate_unlock(ev, today=date(2026, 6, 17), holding=False)  # 5 days out
    assert sig.action == "short"


def test_unlock_too_early():
    ev = UnlockEvent("ARB", date(2026, 7, 1), 0.04, 5e6)
    sig = evaluate_unlock(ev, today=date(2026, 6, 17), holding=False)  # 14 days out
    assert sig.action == "ignore"


def test_unlock_too_late():
    ev = UnlockEvent("ARB", date(2026, 6, 18), 0.04, 5e6)
    sig = evaluate_unlock(ev, today=date(2026, 6, 17), holding=False)  # 1 day out
    assert sig.action == "ignore"


def test_unlock_cover_after_event():
    ev = UnlockEvent("ARB", date(2026, 6, 14), 0.04, 5e6)
    sig = evaluate_unlock(ev, today=date(2026, 6, 17), holding=True)  # 3 days after
    assert sig.action == "cover"


def test_unlock_hold_short_through_window():
    ev = UnlockEvent("ARB", date(2026, 6, 17), 0.04, 5e6)
    sig = evaluate_unlock(ev, today=date(2026, 6, 17), holding=True)  # day of
    assert sig.action == "ignore"  # keep holding
