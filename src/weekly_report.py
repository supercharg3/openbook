"""Weekly and monthly report generators — pure logic, no I/O.

Weekly: sent every Sunday 8am SGT (or on-demand via `REPORT weekly`).
Monthly: sent 1st of month 8am SGT (or on-demand via `REPORT monthly`).

Both read only from the SQLite DB; no network calls here. The entrypoints
(run_weekly_report.py, run_monthly_report.py) handle Telegram dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


SGT = timezone(timedelta(hours=8))


def _now_sgt() -> datetime:
    return datetime.now(SGT)


def _fmt(x: float) -> str:
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def _pct(val: float, base: float) -> str:
    if not base:
        return "—"
    p = (val - base) / base * 100
    return f"{p:+.1f}%"


# ── Weekly report ─────────────────────────────────────────────────────────────

@dataclass
class SleeveWeek:
    name: str
    tag: str
    start_value: float
    end_value: float
    bench_start: float
    bench_end: float
    bench_label: str
    closed_trades: int
    wins: int
    total_pnl_usd: float


@dataclass
class WeeklyReport:
    week_ending: str          # YYYY-MM-DD
    days_live: int
    sleeves: list[SleeveWeek] = field(default_factory=list)
    crypto_closed: int = 0
    crypto_wins: int = 0
    crypto_pnl_usd: float = 0.0
    crypto_start_capital: float = 0.0
    crypto_end_capital: float = 0.0
    mtd_pnl_usd: float = 0.0
    is_early: bool = False     # < 4 weeks running → add caveat


def build_weekly_report(db, cfg) -> WeeklyReport:
    from .database import Database
    assert isinstance(db, Database)

    now = _now_sgt()
    week_ago = (now - timedelta(days=7)).astimezone(timezone.utc).isoformat()
    start_iso = db.get_state("system_start") or week_ago

    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    days_live = max(1, (now.astimezone(timezone.utc) - start_dt.astimezone(timezone.utc)).days)

    # Crypto pairs sleeve
    all_closed = db.closed_trades()
    week_closed = [r for r in all_closed
                   if (r["closed_at"] or "") >= week_ago
                   and not str(r["strategy"]).startswith("factor")
                   and str(r["strategy"]) != "swing"]
    crypto_pnl = sum((r["pnl_usd"] or 0) for r in week_closed)
    crypto_wins = sum(1 for r in week_closed if (r["pnl_usd"] or 0) > 0)

    capital_start = float(db.get_state("capital_week_start") or cfg.starting_capital_usd)
    capital_end = float(db.get_state("capital") or cfg.starting_capital_usd)
    mtd = float(db.get_state("mtd_pnl") or 0.0)

    # Factor sleeves — use sleeve_nav history for the 7-day window
    from .stock_factor import SLEEVES
    sleeve_weeks: list[SleeveWeek] = []
    for s in SLEEVES:
        history = db.sleeve_nav_history(s["tag"])
        if not history:
            continue
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        before = [h for h in history if h["day"] < cutoff]
        after = [h for h in history if h["day"] >= cutoff]
        if not after:
            continue
        start_row = before[-1] if before else after[0]
        end_row = after[-1]
        week_factor_closed = [r for r in all_closed
                              if (r["closed_at"] or "") >= week_ago
                              and str(r["strategy"]) == s["tag"]]
        bench_label = "S&P 500" if s["benchmark"] == "SPY" else f"AI sector ({s['benchmark']})"
        sleeve_weeks.append(SleeveWeek(
            name=s["name"],
            tag=s["tag"],
            start_value=start_row["sleeve_value"],
            end_value=end_row["sleeve_value"],
            bench_start=start_row["spy_value"],
            bench_end=end_row["spy_value"],
            bench_label=bench_label,
            closed_trades=len(week_factor_closed),
            wins=sum(1 for r in week_factor_closed if (r["pnl_usd"] or 0) > 0),
            total_pnl_usd=sum((r["pnl_usd"] or 0) for r in week_factor_closed),
        ))

    return WeeklyReport(
        week_ending=now.strftime("%Y-%m-%d"),
        days_live=days_live,
        sleeves=sleeve_weeks,
        crypto_closed=len(week_closed),
        crypto_wins=crypto_wins,
        crypto_pnl_usd=crypto_pnl,
        crypto_start_capital=capital_start,
        crypto_end_capital=capital_end,
        mtd_pnl_usd=mtd,
        is_early=(days_live < 28),
    )


def format_weekly_report(r: WeeklyReport) -> str:
    lines = [
        f"📅 Weekly Report — week ending {r.week_ending}",
        f"System live: {r.days_live} days",
        "",
        "── CRYPTO (Binance pairs) ──",
        f"Capital: ${r.crypto_end_capital:,.2f}  ({_pct(r.crypto_end_capital, r.crypto_start_capital)} vs last week)",
        f"Trades this week: {r.crypto_closed}  ({r.crypto_wins} wins / {r.crypto_closed - r.crypto_wins} losses)",
        f"P&L this week: {_fmt(r.crypto_pnl_usd)}",
        f"Month-to-date: {_fmt(r.mtd_pnl_usd)}",
    ]
    for s in r.sleeves:
        sleeve_ret = _pct(s.end_value, s.start_value)
        bench_ret = _pct(s.bench_end, s.bench_start)
        delta = (s.end_value / s.start_value - s.bench_end / s.bench_start) * 100 if s.bench_start else 0
        delta_str = f"{delta:+.1f}pp vs {s.bench_label}"
        lines += [
            "",
            f"── STOCKS — {s.name} ──",
            f"This week: {sleeve_ret}  (benchmark {bench_ret}) — {delta_str}",
            f"Trades: {s.closed_trades}  ({s.wins} wins)",
            f"P&L: {_fmt(s.total_pnl_usd)}",
        ]
    if r.is_early:
        lines += [
            "",
            "⚠️  Too early to judge — less than 4 weeks of data. Any return so far is noise, not signal.",
        ]
    lines += [
        "",
        "Reply REPORT monthly for the full monthly review.",
    ]
    return "\n".join(lines)


# ── Monthly report ────────────────────────────────────────────────────────────

@dataclass
class SleeveMonth:
    name: str
    tag: str
    start_value: float
    end_value: float
    bench_start: float
    bench_end: float
    bench_label: str
    closed_trades: int
    wins: int
    total_pnl_usd: float
    max_dd_pct: float       # worst intra-month drawdown %


@dataclass
class MonthlyReport:
    month_label: str          # e.g. "June 2026"
    days_live: int
    sleeves: list[SleeveMonth] = field(default_factory=list)
    crypto_closed: int = 0
    crypto_wins: int = 0
    crypto_pnl_usd: float = 0.0
    crypto_start_capital: float = 0.0
    crypto_end_capital: float = 0.0
    crypto_max_dd_pct: float = 0.0
    verdict: str = ""
    is_early: bool = False


def _max_drawdown(values: list[float]) -> float:
    """Peak-to-trough drawdown as a positive % (0 = no drawdown)."""
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak * 100
            max_dd = max(max_dd, dd)
    return max_dd


def build_monthly_report(db, cfg) -> MonthlyReport:
    from .database import Database
    assert isinstance(db, Database)

    now = _now_sgt()
    month_ago = (now - timedelta(days=30)).astimezone(timezone.utc).isoformat()
    start_iso = db.get_state("system_start") or month_ago
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    days_live = max(1, (now.astimezone(timezone.utc) - start_dt.astimezone(timezone.utc)).days)
    month_label = now.strftime("%B %Y")

    all_closed = db.closed_trades()
    month_closed = [r for r in all_closed
                    if (r["closed_at"] or "") >= month_ago
                    and not str(r["strategy"]).startswith("factor")
                    and str(r["strategy"]) != "swing"]
    crypto_pnl = sum((r["pnl_usd"] or 0) for r in month_closed)
    crypto_wins = sum(1 for r in month_closed if (r["pnl_usd"] or 0) > 0)

    capital_start = float(db.get_state("capital_month_start") or cfg.starting_capital_usd)
    capital_end = float(db.get_state("capital") or cfg.starting_capital_usd)

    # Reconstruct daily crypto equity curve from trades
    daily_capital: dict[str, float] = {}
    running = capital_start
    for r in sorted(month_closed, key=lambda x: x["closed_at"] or ""):
        d = (r["closed_at"] or "")[:10]
        running += (r["pnl_usd"] or 0)
        daily_capital[d] = running
    crypto_dd = _max_drawdown(list(daily_capital.values())) if daily_capital else 0.0

    # Factor sleeves
    from .stock_factor import SLEEVES
    sleeve_months: list[SleeveMonth] = []
    for s in SLEEVES:
        history = db.sleeve_nav_history(s["tag"])
        if not history:
            continue
        cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        before = [h for h in history if h["day"] < cutoff]
        after = [h for h in history if h["day"] >= cutoff]
        if not after:
            continue
        start_row = before[-1] if before else after[0]
        end_row = after[-1]
        month_factor = [r for r in all_closed
                        if (r["closed_at"] or "") >= month_ago
                        and str(r["strategy"]) == s["tag"]]
        bench_label = "S&P 500" if s["benchmark"] == "SPY" else f"AI sector ({s['benchmark']})"
        nav_values = [h["sleeve_value"] for h in after]
        sleeve_months.append(SleeveMonth(
            name=s["name"],
            tag=s["tag"],
            start_value=start_row["sleeve_value"],
            end_value=end_row["sleeve_value"],
            bench_start=start_row["spy_value"],
            bench_end=end_row["spy_value"],
            bench_label=bench_label,
            closed_trades=len(month_factor),
            wins=sum(1 for r in month_factor if (r["pnl_usd"] or 0) > 0),
            total_pnl_usd=sum((r["pnl_usd"] or 0) for r in month_factor),
            max_dd_pct=_max_drawdown(nav_values),
        ))

    # Honest verdict
    verdict = _verdict(crypto_pnl, capital_start, crypto_dd, days_live, sleeve_months)

    return MonthlyReport(
        month_label=month_label,
        days_live=days_live,
        sleeves=sleeve_months,
        crypto_closed=len(month_closed),
        crypto_wins=crypto_wins,
        crypto_pnl_usd=crypto_pnl,
        crypto_start_capital=capital_start,
        crypto_end_capital=capital_end,
        crypto_max_dd_pct=crypto_dd,
        verdict=verdict,
        is_early=(days_live < 60),
    )


def _verdict(pnl: float, capital: float, dd: float, days_live: int,
             sleeves: list[SleeveMonth]) -> str:
    if days_live < 14:
        return ("Not enough history — less than two weeks running. Come back next month.")
    pnl_pct = pnl / capital * 100 if capital else 0
    lines = []
    if dd > 15:
        lines.append(f"Drawdown hit {dd:.1f}% this month — above comfort. Review position sizing.")
    elif dd > 8:
        lines.append(f"Drawdown reached {dd:.1f}% — within limits but worth watching.")
    else:
        lines.append(f"Drawdown held at {dd:.1f}% — controlled.")

    if pnl_pct > 3:
        lines.append(f"Crypto returned {pnl_pct:+.1f}% — a strong month. Don't extrapolate one month.")
    elif pnl_pct > 0:
        lines.append(f"Crypto returned {pnl_pct:+.1f}% — modest but positive.")
    elif pnl_pct > -5:
        lines.append(f"Crypto returned {pnl_pct:+.1f}% — a flat/down month. Pairs stat-arb has losing months; that's normal.")
    else:
        lines.append(f"Crypto returned {pnl_pct:+.1f}% — a hard month. Check if any pairs have decayed (run a re-screen).")

    for s in sleeves:
        sleeve_ret = (s.end_value / s.start_value - 1) * 100 if s.start_value else 0
        bench_ret = (s.bench_end / s.bench_start - 1) * 100 if s.bench_start else 0
        delta = sleeve_ret - bench_ret
        if delta > 1:
            lines.append(f"{s.name}: beat {s.bench_label} by {delta:+.1f}pp this month.")
        elif delta > -1:
            lines.append(f"{s.name}: tracked {s.bench_label} closely ({delta:+.1f}pp) — in line with expectations.")
        else:
            lines.append(f"{s.name}: lagged {s.bench_label} by {abs(delta):.1f}pp — check holdings for concentration or laggards.")

    if days_live < 60:
        lines.append("Overall: still early (under 2 months). Don't draw conclusions yet.")

    return "  ".join(lines)


def format_monthly_report(r: MonthlyReport) -> str:
    lines = [
        f"📊 Monthly Review — {r.month_label}",
        f"System live: {r.days_live} days",
        "",
        "── CRYPTO (Binance pairs) ──",
        f"Capital: ${r.crypto_end_capital:,.2f}  ({_pct(r.crypto_end_capital, r.crypto_start_capital)} this month)",
        f"Benchmark: no market-directional risk by design (neutral, not vs S&P 500)",
        f"Trades: {r.crypto_closed}  ({r.crypto_wins} wins / {r.crypto_closed - r.crypto_wins} losses)",
        f"P&L: {_fmt(r.crypto_pnl_usd)}",
        f"Max drawdown: {r.crypto_max_dd_pct:.1f}%",
    ]
    for s in r.sleeves:
        sleeve_ret = _pct(s.end_value, s.start_value)
        bench_ret = _pct(s.bench_end, s.bench_start)
        delta = (s.end_value / s.start_value - s.bench_end / s.bench_start) * 100 if s.bench_start else 0
        lines += [
            "",
            f"── STOCKS — {s.name} ──",
            f"This month: {sleeve_ret}  (benchmark {bench_ret}) — {delta:+.1f}pp",
            f"Max drawdown: {s.max_dd_pct:.1f}%",
            f"Trades: {s.closed_trades}  ({s.wins} wins)",
            f"P&L: {_fmt(s.total_pnl_usd)}",
        ]
    lines += [
        "",
        "── VERDICT ──",
        r.verdict,
    ]
    if r.is_early:
        lines += [
            "",
            "⚠️  Under 2 months of live data. Returns above are real but too early to be statistically meaningful.",
        ]
    return "\n".join(lines)
