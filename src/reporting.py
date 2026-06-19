"""Daily report + override command parsing (pure, testable).

The actual Telegram I/O lives in telegram_bot.py; everything here is string-in/string-out so it
can be unit-tested without a network. The daily report goes out at 8am SGT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Command(str, Enum):
    STOP = "STOP"
    CLOSE_ALL = "CLOSE ALL"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STATUS = "STATUS"
    REDUCE_RISK = "REDUCE RISK"
    REPORT = "REPORT"
    APPROVE_BASKET = "APPROVE BASKET"
    RUN_SWING = "RUN SWING"
    UNKNOWN = "UNKNOWN"


@dataclass
class ParsedCommand:
    command: Command
    argument: str | None = None     # e.g. the layer name for PAUSE


# Order matters: multi-word commands must be matched before their single-word prefixes
# (e.g. "REDUCE RISK" before nothing, "CLOSE ALL" before a hypothetical "CLOSE").
_COMMAND_PATTERNS = [
    ("APPROVE BASKET", Command.APPROVE_BASKET),
    ("RUN SWING", Command.RUN_SWING),
    ("CLOSE ALL", Command.CLOSE_ALL),
    ("REDUCE RISK", Command.REDUCE_RISK),
    ("STOP", Command.STOP),
    ("RESUME", Command.RESUME),
    ("STATUS", Command.STATUS),
    ("REPORT", Command.REPORT),
    ("PAUSE", Command.PAUSE),
]


def parse_command(text: str) -> ParsedCommand:
    """Parse an override message. Case-insensitive, tolerant of extra whitespace."""
    norm = " ".join(text.strip().upper().split())
    for pattern, cmd in _COMMAND_PATTERNS:
        if norm == pattern:
            return ParsedCommand(cmd)
        if cmd == Command.PAUSE and norm.startswith("PAUSE "):
            return ParsedCommand(Command.PAUSE, argument=norm[len("PAUSE "):].strip().lower())
        if cmd == Command.REPORT and norm.startswith("REPORT "):
            return ParsedCommand(Command.REPORT, argument=norm[len("REPORT "):].strip().lower())
    return ParsedCommand(Command.UNKNOWN, argument=text.strip() or None)


# ── Daily report ─────────────────────────────────────────────────────────────
@dataclass
class TradeLine:
    pair: str
    side: str               # long | short
    entry: float
    exit: float | None
    pnl_usd: float | None
    reason: str
    won: bool | None = None  # None = still open


@dataclass
class DailyReport:
    date_str: str
    capital: float
    capital_change: float
    mtd_pnl_usd: float
    mtd_pnl_pct: float
    trades: list[TradeLine] = field(default_factory=list)
    open_positions: int = 0
    open_unrealized: float = 0.0
    regime: str = "UNKNOWN"
    adx: float = 0.0
    outlook: str = ""
    daily_loss_used: float = 0.0
    daily_loss_limit: float = 0.0
    mode: str = "dry-run"


def _fmt_money(x: float) -> str:
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def format_daily_report(r: DailyReport) -> str:
    lines = [
        f"📊 Daily Report — {r.date_str}",
        f"Mode: {r.mode.upper()}",
        f"Capital: ${r.capital:,.2f} ({_fmt_money(r.capital_change)} vs yesterday)",
        f"MTD P&L: {_fmt_money(r.mtd_pnl_usd)} ({r.mtd_pnl_pct:+.1f}%)",
        "",
    ]
    if r.trades:
        lines.append("Trades (24h):")
        for t in r.trades:
            mark = "🟡" if t.won is None else ("✅" if t.won else "❌")
            exit_str = f"${t.exit:,.2f}" if t.exit is not None else "open"
            pnl_str = _fmt_money(t.pnl_usd) if t.pnl_usd is not None else "—"
            lines.append(
                f"  {mark} {t.pair} {t.side.upper()} | ${t.entry:,.2f} → {exit_str} "
                f"| {pnl_str} | {t.reason}"
            )
    else:
        lines.append("Trades (24h): none")
    lines += [
        "",
        f"Open: {r.open_positions} position(s) ({_fmt_money(r.open_unrealized)} unrealised)",
        f"Regime: {r.regime} (ADX {r.adx:.0f})",
    ]
    if r.outlook:
        lines.append(f"Outlook: {r.outlook}")
    risk_ok = "✅" if r.daily_loss_used < r.daily_loss_limit else "⚠️"
    lines.append(
        f"Risk: {risk_ok} daily loss ${abs(r.daily_loss_used):,.2f} / ${r.daily_loss_limit:,.2f} max"
    )
    return "\n".join(lines)


def format_status(capital: float, open_positions: list[dict], mtd_pnl: float, mode: str,
                  reserve: float = 0.0) -> str:
    lines = [
        f"📍 STATUS ({mode.upper()})",
        f"Capital: ${capital:,.2f}",
        f"Locked-safe profit: ${reserve:,.2f}",
        f"MTD P&L: {_fmt_money(mtd_pnl)}",
        f"Open positions: {len(open_positions)}",
    ]
    for p in open_positions:
        lines.append(
            f"  • {p['pair']} {p['side'].upper()} | size ${p['size_usd']:,.2f} "
            f"| {_fmt_money(p.get('unrealized', 0.0))}"
        )
    return "\n".join(lines)
