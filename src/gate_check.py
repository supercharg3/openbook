"""Go-live gate checker.

Evaluates whether the paper-trading system has proven enough CORRECTNESS and SAFETY to risk the
first $500 -- per the council's synthesized gate. Deliberately has NO paper-profit threshold:
dry-run fills are idealized, so paper P&L is meaningless; the $500 live stage is the real
cost-discovery test. Runs inside the daily report job and alerts once when the gate first clears.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

MIN_ROUNDS = 10
MIN_DISTINCT_PAIRS = 4
MIN_DAILY_REPORTS = 3
LIVENESS_MAX_STALE_MIN = 30


@dataclass
class GateStatus:
    ready: bool
    checks: list = field(default_factory=list)   # (name, passed, detail)


def _pair_rounds(closed_rows, window_sec: int = 30):
    """Group a pair's two legs into one round when they close near-simultaneously.

    The legs are closed in separate calls, so their timestamps differ by milliseconds — keying on
    the exact timestamp wrongly split each round into two 'half-open' ones. We instead group legs of
    the same strategy whose closes fall within `window_sec` of each other (rounds are spaced far
    apart in time, so this is unambiguous).
    """
    by_strat: dict[str, list] = {}
    for r in closed_rows:
        strat = r["strategy"] or ""
        if strat.startswith("pairs:"):
            by_strat.setdefault(strat, []).append(r)

    rounds: dict = {}
    rid = 0
    for strat, legs in by_strat.items():
        legs.sort(key=lambda r: r["closed_at"] or "")
        current, last_t = [], None
        for r in legs:
            try:
                t = datetime.fromisoformat(r["closed_at"])
            except (ValueError, TypeError):
                t = None
            if current and last_t and t and (t - last_t).total_seconds() > window_sec:
                rounds[(strat, rid)] = current; rid += 1; current = []
            current.append(r); last_t = t
        if current:
            rounds[(strat, rid)] = current; rid += 1
    return rounds


def evaluate_gate(db, now_iso):
    closed = db.closed_trades(limit=5000)
    rounds = _pair_rounds(closed)
    n_rounds = len(rounds)
    half_open = sum(1 for legs in rounds.values() if len(legs) != 2)
    pair_legs = [r for r in closed if (r["strategy"] or "").startswith("pairs:")]
    distinct_pairs = len({r["strategy"] for r in pair_legs})
    reasons = {(r["exit_reason"] or "") for r in pair_legs}
    has_converged = any("converged" in e for e in reasons)
    has_stop = any(("stop" in e or "diverg" in e) for e in reasons)
    reports = int(db.get_state("daily_report_count", "0") or "0")

    last_cycle = db.get_state("last_cycle_at")
    alive, alive_detail = False, "no cycles recorded"
    if last_cycle:
        try:
            mins = (datetime.fromisoformat(now_iso) - datetime.fromisoformat(last_cycle)).total_seconds() / 60
            alive = 0 <= mins <= LIVENESS_MAX_STALE_MIN
            alive_detail = f"last cycle {mins:.0f} min ago"
        except ValueError:
            alive_detail = "unparseable timestamp"

    checks = [
        ("Completed pair rounds", n_rounds >= MIN_ROUNDS, f"{n_rounds}/{MIN_ROUNDS}"),
        ("Distinct pairs traded", distinct_pairs >= MIN_DISTINCT_PAIRS, f"{distinct_pairs}/{MIN_DISTINCT_PAIRS}"),
        ("Converged exit observed", has_converged, "seen" if has_converged else "not yet"),
        ("Stop/divergence exit observed", has_stop, "seen" if has_stop else "not yet"),
        ("Data integrity (no half-open rounds)", half_open == 0, "clean" if half_open == 0 else f"{half_open} half-open"),
        ("Daily reports generated", reports >= MIN_DAILY_REPORTS, f"{reports}/{MIN_DAILY_REPORTS}"),
        ("System alive", alive, alive_detail),
    ]
    return GateStatus(ready=all(p for _, p, _ in checks), checks=checks)


def format_gate_message(status):
    lines = ["GO-LIVE GATE CLEARED", "", "Automated checks (all passed):"]
    for name, passed, detail in status.checks:
        lines.append(f"  {'PASS' if passed else 'FAIL'} {name} -- {detail}")
    lines += [
        "",
        "Before you reply GO, confirm by hand:",
        "  - sent STOP, saw it halt, then RESUME",
        "  - MODE banner correct (currently DRY-RUN)",
        "  - read 3 daily reports, looked right",
        "",
        "Paper P&L is idealized (no slippage). The first $500 is a cost-discovery test, not "
        "expected profit. Max loss capped at $100 (20% drawdown halt).",
        "",
        "To go live: set TRADING_MODE=live + ALLOW_LIVE_ORDERS=1, fund $500, restart. Reply GO when ready.",
    ]
    return "\n".join(lines)
