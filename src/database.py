"""SQLite persistence layer.

Three tables back the whole system:
  - trades              : every closed (and open) trade, the source of truth for P&L + Kelly
  - strategy_performance: per-strategy rolling stats, used for the auto-pause rule
  - correlation_cache   : nightly 30-day Pearson correlations between watched assets

SQLite is enough at this scale (single VPS, low write rate) and costs nothing.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at       TEXT    NOT NULL,           -- ISO8601 UTC
    closed_at       TEXT,                       -- NULL while open
    pair            TEXT    NOT NULL,           -- e.g. BTC/USDT
    side            TEXT    NOT NULL,           -- long | short
    strategy        TEXT    NOT NULL,           -- layer / strategy name
    entry_price     REAL    NOT NULL,
    exit_price      REAL,
    size_usd        REAL    NOT NULL,           -- notional at entry
    leverage        REAL    NOT NULL DEFAULT 1,
    pnl_usd         REAL,                       -- realised, NULL while open
    pnl_pct         REAL,                       -- realised %, NULL while open
    fees_usd        REAL    NOT NULL DEFAULT 0,
    exit_reason     TEXT,                       -- tp | sl | signal | manual | regime
    is_paper        INTEGER NOT NULL DEFAULT 1  -- 1 = dry-run, 0 = live
);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_closed   ON trades(closed_at);

CREATE TABLE IF NOT EXISTS strategy_performance (
    strategy        TEXT    PRIMARY KEY,
    trades_count    INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    avg_win_pct     REAL    NOT NULL DEFAULT 0,
    avg_loss_pct    REAL    NOT NULL DEFAULT 0,
    last_20_ev      REAL    NOT NULL DEFAULT 0,   -- expected value over last 20 trades
    paused          INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS correlation_cache (
    pair_a          TEXT    NOT NULL,
    pair_b          TEXT    NOT NULL,
    correlation     REAL    NOT NULL,            -- 30-day Pearson
    computed_at     TEXT    NOT NULL,
    PRIMARY KEY (pair_a, pair_b)
);

CREATE TABLE IF NOT EXISTS system_state (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS thesis_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    action          TEXT    NOT NULL,           -- buy | sell | close
    pair            TEXT    NOT NULL,
    size_pct        REAL    NOT NULL DEFAULT 0, -- % of thesis sleeve (for buy/sell)
    status          TEXT    NOT NULL DEFAULT 'pending'   -- pending | done | rejected
);

CREATE TABLE IF NOT EXISTS factor_nav (
    day             TEXT    PRIMARY KEY,         -- YYYY-MM-DD
    sleeve_value    REAL    NOT NULL,            -- factor sleeve mark-to-market
    spy_value       REAL    NOT NULL             -- shadow SPY, same cash flows (the benchmark)
);

CREATE TABLE IF NOT EXISTS sleeve_nav (
    sleeve          TEXT    NOT NULL,            -- 'factor' (diversified) | 'factor-ai'
    day             TEXT    NOT NULL,
    value           REAL    NOT NULL,            -- sleeve mark-to-market
    bench_value     REAL    NOT NULL,            -- its benchmark (SPY for diversified, SMH for AI)
    PRIMARY KEY (sleeve, day)
);

CREATE TABLE IF NOT EXISTS veto_log (
    day             TEXT    NOT NULL,            -- rebalance day
    ticker          TEXT    NOT NULL,
    vetoed          INTEGER NOT NULL,            -- 1 = red-flag veto, 0 = passed
    reason          TEXT,
    price_at        REAL                         -- price when judged (to measure: did vetoes underperform?)
);

CREATE TABLE IF NOT EXISTS price_watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    expires_at      TEXT    NOT NULL,            -- auto-expire after 7 days
    ticker          TEXT    NOT NULL,            -- e.g. DOGE/USDT or NVDA
    direction       TEXT    NOT NULL,            -- LONG | SHORT
    target_price    REAL    NOT NULL,            -- enter when price reaches this level
    condition       TEXT    NOT NULL,            -- 'lte' = buy dip (long) | 'gte' = buy rally (short)
    sleeve          TEXT    NOT NULL,            -- 'degen' | 'swing'
    context         TEXT,                        -- original channel signal context
    status          TEXT    NOT NULL DEFAULT 'watching',  -- watching | triggered | expired
    triggered_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_watches_status ON price_watches(status);

CREATE TABLE IF NOT EXISTS alpha_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    channel         TEXT    NOT NULL,            -- which channel sourced this signal
    ticker          TEXT    NOT NULL,
    direction       TEXT    NOT NULL,            -- LONG | SHORT
    confidence      TEXT    NOT NULL DEFAULT 'MEDIUM',
    context         TEXT,
    msg_id          INTEGER NOT NULL,            -- original Telegram message id
    matched         INTEGER NOT NULL DEFAULT 0, -- 1 = confirmed by a second channel
    researched      INTEGER NOT NULL DEFAULT 0  -- 1 = research_alpha already ran
);
CREATE INDEX IF NOT EXISTS idx_alpha_signals_ticker ON alpha_signals(ticker, direction, created_at);
"""


@dataclass
class TradeRecord:
    pair: str
    side: str
    strategy: str
    entry_price: float
    size_usd: float
    opened_at: str
    leverage: float = 1.0
    is_paper: bool = True
    id: int | None = None
    closed_at: str | None = None
    exit_price: float | None = None
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    fees_usd: float = 0.0
    exit_reason: str | None = None


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ── Trades ───────────────────────────────────────────────────────────────
    def open_trade(self, t: TradeRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (opened_at, pair, side, strategy, entry_price, size_usd, leverage, is_paper)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (t.opened_at, t.pair, t.side, t.strategy, t.entry_price,
                 t.size_usd, t.leverage, 1 if t.is_paper else 0),
            )
            return int(cur.lastrowid)

    def close_trade(self, trade_id: int, *, closed_at: str, exit_price: float,
                    pnl_usd: float, pnl_pct: float, fees_usd: float,
                    exit_reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE trades
                   SET closed_at=?, exit_price=?, pnl_usd=?, pnl_pct=?, fees_usd=?, exit_reason=?
                   WHERE id=?""",
                (closed_at, exit_price, pnl_usd, pnl_pct, fees_usd, exit_reason, trade_id),
            )

    def open_positions(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return list(conn.execute("SELECT * FROM trades WHERE closed_at IS NULL"))

    def closed_trades(self, *, strategy: str | None = None, limit: int | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM trades WHERE closed_at IS NOT NULL"
        params: list = []
        if strategy:
            q += " AND strategy=?"
            params.append(strategy)
        q += " ORDER BY closed_at DESC"
        if limit:
            q += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            return list(conn.execute(q, params))

    # ── System state (key/value) ─────────────────────────────────────────────
    def set_state(self, key: str, value: str, updated_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, updated_at),
            )

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    # ── Correlation cache ────────────────────────────────────────────────────
    def cache_correlation(self, pair_a: str, pair_b: str, corr: float, computed_at: str) -> None:
        a, b = sorted((pair_a, pair_b))
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO correlation_cache (pair_a, pair_b, correlation, computed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(pair_a, pair_b) DO UPDATE SET
                     correlation=excluded.correlation, computed_at=excluded.computed_at""",
                (a, b, corr, computed_at),
            )

    def get_correlation(self, pair_a: str, pair_b: str) -> float | None:
        a, b = sorted((pair_a, pair_b))
        with self._conn() as conn:
            row = conn.execute(
                "SELECT correlation FROM correlation_cache WHERE pair_a=? AND pair_b=?",
                (a, b),
            ).fetchone()
            return row["correlation"] if row else None

    # ── Price watches (WAIT signals that monitor for a better entry) ────────────
    def add_price_watch(self, ticker: str, direction: str, target_price: float,
                        condition: str, sleeve: str, context: str,
                        created_at: str, expires_at: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO price_watches
                   (created_at, expires_at, ticker, direction, target_price, condition, sleeve, context)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (created_at, expires_at, ticker, direction, target_price, condition, sleeve, context),
            )
            return int(cur.lastrowid)

    def active_watches(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return list(conn.execute(
                "SELECT * FROM price_watches WHERE status='watching' ORDER BY created_at"
            ))

    def trigger_watch(self, watch_id: int, triggered_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE price_watches SET status='triggered', triggered_at=? WHERE id=?",
                (triggered_at, watch_id),
            )

    def expire_watches(self, now: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE price_watches SET status='expired' WHERE status='watching' AND expires_at < ?",
                (now,),
            )
            return cur.rowcount

    # ── Thesis order queue (chat enqueues, trading loop executes) ─────────────
    def add_thesis_order(self, action: str, pair: str, size_pct: float, created_at: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO thesis_orders (created_at, action, pair, size_pct) VALUES (?, ?, ?, ?)",
                (created_at, action, pair, size_pct),
            )
            return int(cur.lastrowid)

    def pending_thesis_orders(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return list(conn.execute(
                "SELECT * FROM thesis_orders WHERE status='pending' ORDER BY id"))

    def set_thesis_order_status(self, order_id: int, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE thesis_orders SET status=? WHERE id=?", (status, order_id))

    # ── Factor-sleeve NAV history (the shadow-SPY benchmark) ──────────────────
    def record_factor_nav(self, day: str, sleeve_value: float, spy_value: float) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO factor_nav (day, sleeve_value, spy_value) VALUES (?, ?, ?)
                   ON CONFLICT(day) DO UPDATE SET sleeve_value=excluded.sleeve_value,
                     spy_value=excluded.spy_value""",
                (day, sleeve_value, spy_value),
            )

    def factor_nav_history(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT day, sleeve_value, spy_value FROM factor_nav ORDER BY day")
            return [dict(r) for r in rows]

    def record_sleeve_nav(self, sleeve: str, day: str, value: float, bench_value: float) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sleeve_nav (sleeve, day, value, bench_value) VALUES (?, ?, ?, ?)
                   ON CONFLICT(sleeve, day) DO UPDATE SET value=excluded.value,
                     bench_value=excluded.bench_value""",
                (sleeve, day, value, bench_value),
            )

    def sleeve_nav_history(self, sleeve: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT day, value AS sleeve_value, bench_value AS spy_value FROM sleeve_nav "
                "WHERE sleeve=? ORDER BY day", (sleeve,))
            return [dict(r) for r in rows]

    def log_veto(self, day: str, ticker: str, vetoed: bool, reason: str, price_at: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO veto_log (day, ticker, vetoed, reason, price_at) VALUES (?, ?, ?, ?, ?)",
                (day, ticker, 1 if vetoed else 0, reason, price_at),
            )

    def veto_history(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM veto_log ORDER BY day")]
