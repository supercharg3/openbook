"""Central configuration loaded from environment variables.

Nothing here imports network libraries, so it is safe to load in tests. Secrets are read
from the environment (populated from a local .env via python-dotenv) and never hard-coded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional in environments that inject env vars directly
    pass


def _get(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key, default)
    if val is None:
        return default
    val = val.strip()
    # Strip inline comments. python-dotenv does this, but systemd's EnvironmentFile parser does
    # NOT, so "KEY=2   # note" reaches us verbatim. Cut at the first whitespace-then-# and at a
    # leading # (a blank var whose only content is a comment).
    if val.startswith("#"):
        return default
    for sep in (" #", "\t#"):
        idx = val.find(sep)
        if idx != -1:
            val = val[:idx].rstrip()
    return val if val != "" else default


def _get_float(key: str, default: float) -> float:
    raw = _get(key)
    return float(raw) if raw is not None else default


def _get_int(key: str, default: int) -> int:
    raw = _get(key)
    return int(raw) if raw is not None else default


def _get_int_or_none(key: str) -> int | None:
    raw = _get(key)
    return int(raw) if raw is not None else None


@dataclass(frozen=True)
class Config:
    # Binance
    binance_api_key: str | None = field(default_factory=lambda: _get("BINANCE_API_KEY"))
    binance_api_secret: str | None = field(default_factory=lambda: _get("BINANCE_API_SECRET"))

    # Claude
    anthropic_api_key: str | None = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"))

    # Exa
    exa_api_key: str | None = field(default_factory=lambda: _get("EXA_API_KEY"))
    exa_polls_per_hour: int = field(default_factory=lambda: _get_int("EXA_POLLS_PER_HOUR", 2))

    # Telegram
    telegram_bot_token: str | None = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: _get("TELEGRAM_CHAT_ID"))
    # Forum-topic id (message_thread_id) the bot is scoped to. Blank = no topic / discovery mode.
    telegram_topic_id: int | None = field(default_factory=lambda: _get_int_or_none("TELEGRAM_TOPIC_ID"))

    # Trading mode + capital
    trading_mode: str = field(default_factory=lambda: _get("TRADING_MODE", "dry-run"))
    starting_capital_usd: float = field(default_factory=lambda: _get_float("STARTING_CAPITAL_USD", 500.0))
    reserve_buffer_usd: float = field(default_factory=lambda: _get_float("RESERVE_BUFFER_USD", 100.0))

    # Stock execution for the thesis sleeve — Alpaca (REST API; no gateway needed). Off by default.
    alpaca_enabled: bool = field(default_factory=lambda: _get("ALPACA_ENABLED", "0") == "1")
    alpaca_paper: bool = field(default_factory=lambda: _get("ALPACA_PAPER", "1") == "1")
    alpaca_api_key_id: str | None = field(default_factory=lambda: _get("ALPACA_API_KEY_ID"))
    alpaca_api_secret: str | None = field(default_factory=lambda: _get("ALPACA_API_SECRET"))
    # Total practice notional split across ALL enabled stock sleeves (envelope accounting).
    stock_sleeve_usd: float = field(default_factory=lambda: _get_float("STOCK_SLEEVE_USD", 10000.0))

    # ── Sleeve selection + budgets ────────────────────────────────────────────
    # Comma-separated list of which sleeves to run. Omit a name to disable it completely.
    # Valid names: pairs (crypto stat-arb), factor (diversified stocks), factor-ai (AI/semis), swing.
    sleeves_enabled: str = field(
        default_factory=lambda: _get("SLEEVES_ENABLED", "pairs,factor,factor-ai,swing"))
    # Swing sleeve: practice pot and the floor where the circuit breaker fires.
    swing_budget_usd: float = field(default_factory=lambda: _get_float("SWING_BUDGET_USD", 1000.0))
    # Floor as a fraction of swing_budget_usd (default 0.20 → halt at 80% loss).
    swing_floor_pct: float = field(default_factory=lambda: _get_float("SWING_FLOOR_PCT", 0.20))

    # Paths / scheduling
    db_path: str = field(default_factory=lambda: _get("DB_PATH", "./data/trading.db"))
    timezone: str = field(default_factory=lambda: _get("TIMEZONE", "Asia/Singapore"))
    daily_report_hour: int = field(default_factory=lambda: _get_int("DAILY_REPORT_HOUR", 8))

    @property
    def is_live(self) -> bool:
        return self.trading_mode.strip().lower() == "live"

    @property
    def sleeves_enabled_set(self) -> set[str]:
        """Which sleeves are active, as a set. Example: {"pairs", "factor", "swing"}."""
        return {s.strip().lower() for s in (self.sleeves_enabled or "").split(",") if s.strip()}

    @property
    def swing_floor_usd(self) -> float:
        """Dollar floor for the swing sleeve — halt line."""
        return self.swing_budget_usd * self.swing_floor_pct

    def stock_sleeve_budget(self, enabled_stock_sleeves: list[str]) -> float:
        """Per-sleeve budget: total stock capital split equally across enabled stock sleeves.

        Envelope guarantee: two sleeves each get half, so total exposure == stock_sleeve_usd.
        Pass the list of stock sleeve tags that are actually enabled.
        """
        n = len(enabled_stock_sleeves)
        return self.stock_sleeve_usd / n if n > 0 else self.stock_sleeve_usd

    def require_live_keys(self) -> None:
        """Fail fast if we're about to trade live without the keys to do so."""
        missing = [
            name
            for name, val in {
                "BINANCE_API_KEY": self.binance_api_key,
                "BINANCE_API_SECRET": self.binance_api_secret,
                "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
                "TELEGRAM_CHAT_ID": self.telegram_chat_id,
            }.items()
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Cannot run in LIVE mode — missing env vars: {', '.join(missing)}"
            )

    def startup_banner(self) -> str:
        """The message sent to Telegram every time the system starts (mode safety check)."""
        if self.is_live:
            return f"MODE: LIVE — ${self.starting_capital_usd:.0f} at risk"
        return "MODE: DRY-RUN — paper trading active"

    def ensure_data_dir(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)


# Singleton-style accessor
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
