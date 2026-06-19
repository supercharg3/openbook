"""The orchestrator loop — the central decision engine.

One `run_cycle()` does, in order:
  1. Rehydrate account state (equity, drawdown, realised P&L windows, leverage track record)
  2. Refresh market regime (ADX) for watched pairs → system_state (feeds the daily report)
  3. EXITS first (free up capacity): funding-arb decay, token-unlock covers
  4. ENTRIES: each signal layer proposes trades; every proposal must clear
        controller (halt / layer-pause) → correlation guard → risk manager → execute
  5. Recompute half-Kelly sizing from the trade log every 20 closed trades
  6. Persist state so a restart resumes cleanly

All exchange/network I/O is behind the injected PriceFeed + ExecutionClient, and the signal
layers are injected callables, so the whole loop is unit-testable with fakes. Layers 3/4
(trend/grid) execute inside Freqtrade; `sync_freqtrade_trades()` folds their closed trades into
the same SQLite log for unified reporting and Kelly stats.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .config import Config
from .controller import SystemController
from .correlation import check_new_position, correlation_from_closes
from .database import Database, TradeRecord
from .execution import ClosedTrade, ExecutionClient, Position, PriceFeed, compute_pnl
from .funding_arb import FundingMonitor, filter_liquid
from .circuit_breaker import DIVERGENCE_WINDOW_MIN, divergence_round_count, is_tripped
from .pair_health import evaluate_health, pair_round_pnls
from .pairs import PairsTrader, align
from .strategies import evaluate_mean_reversion, evaluate_trend, exit_signal
from .position_sizing import (
    RECOMPUTE_EVERY_N_TRADES,
    compute_stats_from_returns,
    half_kelly_fraction,
)
from .regime import regime_from_ohlcv
from .risk_manager import (
    DAILY_LOSS_LIMIT_PCT,
    MAX_ASSET_EXPOSURE_PCT,
    PER_TRADE_STOP_PCT,
    RiskManager,
    TradeProposal,
    AccountState,
    exposure_breach,
)

SGT = timezone(timedelta(hours=8))

# Default size fractions per layer, used until that strategy has enough history for Kelly.
FUNDING_DEFAULT_FRAC = 0.20      # market-neutral; risk manager trims to the 15% cap
NEWS_DEFAULT_FRAC = 0.05         # overridden per-intent by confidence tier
UNLOCK_DEFAULT_FRAC = 0.10
TREND_DEFAULT_FRAC = 0.10        # Layer 3 (DISABLED — see _run_trend_grid note)
GRID_DEFAULT_FRAC = 0.10         # Layer 4 (DISABLED)
TAKE_PROFIT_PCT = 0.15          # universal take-profit backstop (matches the plan's top ROI tier)
WATCHED_PAIRS = ["BTC/USDT", "ETH/USDT"]

# Market-neutral pairs (Layer 5) — the only directional-ish layer we kept, because it's the only
# one that cleared rigorous out-of-sample validation (see src/pairs.py screen).
VALIDATED_PAIRS = [
    # All 11 cleared both-halves out-of-sample validation over 360d (src/pairs.py --screen).
    # Mid-cap legs (GALA/IMX/SEI/GRT) are less liquid than majors — fine at small size, revisit
    # slippage as capital scales.
    ("XRP/USDT", "GALA/USDT"),   # Sharpe 2.21
    ("AVAX/USDT", "IMX/USDT"),   # Sharpe 2.19
    ("GRT/USDT", "SEI/USDT"),    # Sharpe 1.86
    ("IMX/USDT", "SEI/USDT"),    # Sharpe 1.81
    ("XRP/USDT", "DOGE/USDT"),   # Sharpe 1.72
    ("ETH/USDT", "GALA/USDT"),   # Sharpe 1.67
    ("SOL/USDT", "AVAX/USDT"),   # Sharpe 1.66
    ("XRP/USDT", "GRT/USDT"),    # Sharpe 1.63
    ("DOGE/USDT", "IMX/USDT"),   # Sharpe 1.63
    ("ETH/USDT", "IMX/USDT"),    # Sharpe 1.59
    ("XRP/USDT", "ARB/USDT"),    # Sharpe 1.51
]
# Per-pair notional fractions from the portfolio risk model (src/portfolio.py, 10% vol target).
# Recompute and update these whenever the basket changes: `python -m src.portfolio`.
PAIR_ALLOCATIONS: dict[str, float] = {   # from `python -m src.portfolio` (15% vol target, 11 pairs)
    "SOL/USDT~AVAX/USDT": 0.190,
    "XRP/USDT~DOGE/USDT": 0.153,
    "XRP/USDT~GALA/USDT": 0.129,
    "XRP/USDT~GRT/USDT": 0.123,
    "GRT/USDT~SEI/USDT": 0.116,
    "XRP/USDT~ARB/USDT": 0.112,
    "ETH/USDT~IMX/USDT": 0.105,
    "AVAX/USDT~IMX/USDT": 0.103,
    "ETH/USDT~GALA/USDT": 0.102,
    "IMX/USDT~SEI/USDT": 0.101,
    "DOGE/USDT~IMX/USDT": 0.087,
}
PAIRS_FRACTION = 0.08           # fallback per pair (kept modest with 11 pairs in the book)
PAIRS_WINDOW = 168              # rolling window (matches the validated backtest)
MAX_OPEN_PAIRS = 6              # allow real diversification across the larger basket
ENABLE_DIRECTIONAL_TA = False   # trend/grid proved to have no durable edge — off by default
ENABLE_NEWS = False             # directional news bets are unvalidated — off; keep the book pure
                                # market-neutral (validated pairs + funding arb)


@dataclass
class CycleResult:
    opened: list[Position]
    closed: list[ClosedTrade]
    skipped: list[str]           # human-readable reasons, for logging/debugging
    regime: str
    adx: float
    alerts: list[str] = field(default_factory=list)   # push to Telegram (e.g. circuit breaker)


class Orchestrator:
    def __init__(
        self,
        config: Config,
        db: Database,
        controller: SystemController,
        execution_client: ExecutionClient,
        price_feed: PriceFeed,
        *,
        funding_monitor: FundingMonitor | None = None,
        news_provider=None,        # callable() -> list[TradeIntent]
        unlock_provider=None,      # callable(today: date, held: set[str]) -> list[UnlockSignal]
        watched_pairs: list[str] | None = None,
        pairs: list[tuple[str, str]] | None = None,
        stock_exec: ExecutionClient | None = None,
    ) -> None:
        self.cfg = config
        self.db = db
        self.controller = controller
        self.exec = execution_client
        self.price_feed = price_feed
        # Stock thesis orders route here (IBKR). None = stock execution not wired yet → such
        # orders are rejected with a clear message rather than mis-sent to the crypto venue.
        self.stock_exec = stock_exec
        self.funding = funding_monitor or FundingMonitor()
        self.news_provider = news_provider
        self.unlock_provider = unlock_provider
        self.watched_pairs = watched_pairs or WATCHED_PAIRS
        self.is_paper = not config.is_live
        # Pairs (market-neutral) tracked in their OWN registry — they don't count toward the
        # directional position cap, and they deliberately bypass the correlation guard.
        # The live basket is config-driven (data/active_basket.json), so re-screening can refresh
        # it without a code change; tests pass `pairs` explicitly to bypass that.
        import os
        self._basket_dir = os.path.dirname(config.db_path) or "."
        self._basket_file = os.path.join(self._basket_dir, "active_basket.json")
        self._fixed_basket = pairs is not None   # tests pin the basket; skip hot-reload
        if pairs is not None:
            pair_list, self.pair_allocations = pairs, dict(PAIR_ALLOCATIONS)
        else:
            from .basket import load_active
            pair_list, self.pair_allocations = load_active(
                self._basket_dir, VALIDATED_PAIRS, PAIR_ALLOCATIONS)
        self._basket_mtime = os.path.getmtime(self._basket_file) if os.path.exists(self._basket_file) else 0.0
        self.pairs_trader = PairsTrader(pair_list, PAIRS_WINDOW)
        self._pair_symbols = {PairsTrader.name_of(a, b): (a, b) for a, b in pair_list}
        self.open_pairs: dict[str, dict] = {}   # name -> {"legA": Position, "legB": Position, "side"}
        # In-memory registry of single-leg (directional/funding/news) positions.
        self.open_positions: dict[int, Position] = {}
        self._rehydrate_positions()

    # ── State ────────────────────────────────────────────────────────────────
    def _rehydrate_positions(self) -> None:
        pair_rows: dict[str, list] = {}
        for row in self.db.open_positions():
            # The stock sleeves (factor*, swing) are SEPARATE processes with their own cadence and
            # exits, on Alpaca — the crypto loop must never load or manage their positions.
            strat = str(row["strategy"])
            if strat.startswith("factor") or strat == "swing":
                continue
            pos = Position(
                pair=row["pair"], side=row["side"], size_usd=row["size_usd"],
                leverage=row["leverage"], entry_price=row["entry_price"],
                strategy=row["strategy"], opened_at=row["opened_at"], db_id=row["id"],
            )
            if pos.strategy.startswith("pairs:"):
                pair_rows.setdefault(pos.strategy[len("pairs:"):], []).append(pos)
                continue
            self.open_positions[row["id"]] = pos
            if pos.strategy == "funding":
                self.funding.record_entry(pos.pair, pos.size_usd, 0.0)
        # Reassemble open pairs (two legs each) from the DB.
        for name, legs in pair_rows.items():
            if name not in self._pair_symbols or len(legs) != 2:
                continue
            a_sym, _ = self._pair_symbols[name]
            leg_a = next((p for p in legs if p.pair == a_sym), legs[0])
            leg_b = next((p for p in legs if p is not leg_a), legs[1])
            spread_side = "short_spread" if leg_a.side == "short" else "long_spread"
            self.open_pairs[name] = {"legA": leg_a, "legB": leg_b, "side": spread_side}
            self.pairs_trader.record_entry(name, spread_side)

    def _unrealized(self) -> float:
        from .execution import compute_pnl
        total = 0.0
        for pos in self.open_positions.values():
            mark = self.price_feed.get_price(pos.pair)
            pnl, _ = compute_pnl(pos.side, pos.entry_price, mark, pos.size_usd, pos.leverage)
            total += pnl
        return total

    def _realized_since(self, cutoff_iso: str) -> float:
        return sum(
            (r["pnl_usd"] or 0.0)
            for r in self.db.closed_trades(limit=200)
            if (r["closed_at"] or "") >= cutoff_iso
        )

    def _live_track_record(self) -> tuple[int, int]:
        """(live_trade_count, days_live) for the leverage gate."""
        live = [r for r in self.db.closed_trades(limit=1000) if r["is_paper"] == 0]
        count = len(live)
        start_iso = self.db.get_state("first_live_date")
        if start_iso:
            start = datetime.fromisoformat(start_iso).date()
            days = (datetime.now(SGT).date() - start).days
        else:
            days = 0
        return count, days

    def _account_state(self, now_sgt: datetime) -> AccountState:
        from .profit_reserve import tradeable_capital, update_reserve
        balance = self.exec.get_balance()
        equity = balance + self._unrealized()
        peak = max(float(self.db.get_state("peak_capital", str(equity))), equity)
        self.db.set_state("peak_capital", str(peak), now_sgt.isoformat())
        self.db.set_state("capital", f"{equity:.2f}", now_sgt.isoformat())

        # Profit cashout ratchet: lock a fraction of new-high gains into a reserve not re-risked.
        hwm = float(self.db.get_state("equity_hwm", str(self.cfg.starting_capital_usd)))
        reserve = float(self.db.get_state("reserve", "0"))
        hwm, reserve = update_reserve(equity, hwm, reserve)
        self.db.set_state("equity_hwm", f"{hwm:.4f}", now_sgt.isoformat())
        self.db.set_state("reserve", f"{reserve:.4f}", now_sgt.isoformat())
        tradeable = tradeable_capital(equity, reserve)

        day_cutoff = (now_sgt - timedelta(hours=24)).astimezone(timezone.utc).isoformat()
        week_cutoff = (now_sgt - timedelta(days=7)).astimezone(timezone.utc).isoformat()
        lev_exposure = sum(p.size_usd for p in self.open_positions.values() if p.leverage > 1.0)
        count, days = self._live_track_record()

        return AccountState(
            current_capital=equity,
            tradeable_capital=tradeable,
            peak_capital=peak,
            realized_pnl_today=self._realized_since(day_cutoff),
            realized_pnl_week=self._realized_since(week_cutoff),
            open_positions=len(self.open_positions),
            leveraged_exposure_usd=lev_exposure,
            live_trade_count=count,
            days_live=days,
            paused_strategies=set(self.controller.state.paused_layers),
            reduce_risk_until=self.controller.state.reduce_risk_until,
        )

    # ── Regime ───────────────────────────────────────────────────────────────
    def _refresh_regime(self, now_sgt: datetime) -> tuple[str, float]:
        primary = self.watched_pairs[0]
        try:
            ohlcv = self.price_feed.get_ohlcv(primary, timeframe="1h", limit=120)
            highs = [c[2] for c in ohlcv]
            lows = [c[3] for c in ohlcv]
            closes = [c[4] for c in ohlcv]
            regime, adx = regime_from_ohlcv(highs, lows, closes)
            self.db.set_state("regime", regime.value.upper(), now_sgt.isoformat())
            self.db.set_state("adx", f"{adx:.1f}", now_sgt.isoformat())
            return regime.value.upper(), adx
        except (ValueError, IndexError):
            return self.db.get_state("regime", "UNKNOWN"), float(self.db.get_state("adx", "0"))

    # ── Sizing ───────────────────────────────────────────────────────────────
    def _size_fraction(self, strategy: str, fallback: float) -> float:
        """Half-Kelly from this strategy's history once there's enough; else the fallback."""
        rows = self.db.closed_trades(strategy=strategy, limit=200)
        returns = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]
        if len(returns) < 10:
            return fallback
        stats = compute_stats_from_returns(returns)
        return half_kelly_fraction(stats)

    # ── Entry pipeline (shared by every layer) ───────────────────────────────
    def _try_open(self, proposal: TradeProposal, rm: RiskManager, now_sgt: datetime,
                  result: CycleResult) -> None:
        if self.controller.is_layer_paused(proposal.strategy):
            result.skipped.append(f"{proposal.strategy}: layer paused")
            return

        # Correlation guard against currently-open pairs.
        open_pairs = [p.pair for p in self.open_positions.values()]
        decision = check_new_position(
            proposal.pair, open_pairs, self.db.get_correlation
        )
        if not decision.allowed:
            result.skipped.append(f"{proposal.pair}: {decision.reason}")
            return

        risk = rm.evaluate(proposal, now_sgt=now_sgt)
        if not risk.approved:
            result.skipped.append(f"{proposal.pair}: {risk.reason}")
            return

        size = risk.adjusted_size_usd or proposal.size_usd
        pos = self.exec.open_position(
            proposal.pair, proposal.side, size, proposal.leverage, proposal.strategy
        )
        pos.db_id = self.db.open_trade(TradeRecord(
            pair=pos.pair, side=pos.side, strategy=pos.strategy,
            entry_price=pos.entry_price, size_usd=pos.size_usd, leverage=pos.leverage,
            opened_at=pos.opened_at, is_paper=self.is_paper,
        ))
        self.open_positions[pos.db_id] = pos
        if pos.strategy == "funding":
            self.funding.record_entry(pos.pair, size, 0.0)
        # Account state changes (open count, exposure) are re-derived next access; refresh now.
        rm.account.open_positions += 1
        if pos.leverage > 1.0:
            rm.account.leveraged_exposure_usd += size
        result.opened.append(pos)
        result.alerts.append(
            f"📈 Opened {pos.strategy} {pos.side.upper()} {pos.pair} ${pos.size_usd:.0f}")

    def _close(self, pos: Position, reason: str, result: CycleResult) -> None:
        # Stock thesis positions close on IBKR; everything else on the crypto venue.
        if pos.strategy.startswith("thesis") and self.stock_exec is not None:
            from .venues import classify_venue
            crypto_universe = {t for ab in self._pair_symbols.values() for t in ab}
            client = self.exec if classify_venue(pos.pair.split("/")[0], crypto_universe) == "crypto" \
                else self.stock_exec
        else:
            client = self.exec
        closed = client.close_position(pos, reason)
        self.db.close_trade(
            pos.db_id, closed_at=closed.closed_at, exit_price=closed.exit_price,
            pnl_usd=closed.pnl_usd, pnl_pct=closed.pnl_pct,
            fees_usd=closed.fee_usd, exit_reason=reason,
        )
        self.open_positions.pop(pos.db_id, None)
        if pos.strategy == "funding":
            self.funding.record_exit(pos.pair)
        result.closed.append(closed)
        result.alerts.append(
            f"📉 Closed {pos.strategy} {pos.pair} ({reason}): P&L ${closed.pnl_usd:+.2f}")

    # ── The cycle ────────────────────────────────────────────────────────────
    def _maybe_reload_basket(self, result) -> None:
        """Hot-swap to an approved new basket, but only when flat (no open pairs) — safe."""
        if self._fixed_basket or self.open_pairs:
            return
        import os
        if not os.path.exists(self._basket_file):
            return
        mtime = os.path.getmtime(self._basket_file)
        if mtime <= self._basket_mtime:
            return
        from .basket import load_active
        pair_list, self.pair_allocations = load_active(self._basket_dir, VALIDATED_PAIRS, PAIR_ALLOCATIONS)
        self.pairs_trader = PairsTrader(pair_list, PAIRS_WINDOW)
        self._pair_symbols = {PairsTrader.name_of(a, b): (a, b) for a, b in pair_list}
        self._basket_mtime = mtime
        result.alerts.append(f"🔁 New basket now live: {len(pair_list)} pairs.")

    def run_cycle(self, now_sgt: datetime | None = None) -> CycleResult:
        now_sgt = now_sgt or datetime.now(SGT)
        regime, adx = self._refresh_regime(now_sgt)
        result = CycleResult(opened=[], closed=[], skipped=[], regime=regime, adx=adx)
        self._maybe_reload_basket(result)

        account = self._account_state(now_sgt)
        rm = RiskManager(account)

        # 1. EXITS — always run, even when halted (we still manage open risk).
        self._manage_exits(result)            # universal SL/TP for single-leg positions
        self._run_funding_exits(result)
        self._run_pairs_exits(result)         # market-neutral pairs (z-score convergence/stop)
        self._run_unlock_exits(now_sgt, result)

        # 1b. Circuit breaker — if many pairs diverged at once (regime break), pause pair entries.
        breaker = self._check_circuit_breaker(now_sgt, result)

        # 1c. Discretionary thesis orders queued from chat (closes always; opens if not halted).
        self._process_thesis_orders(account, result)

        # 2. ENTRIES — only when not halted.
        if self.controller.can_open_new_trade():
            self._run_funding_entries(account, rm, now_sgt, result)
            if breaker:
                result.skipped.append("pairs paused: circuit breaker tripped (mass divergence)")
            else:
                self._run_pairs_entries(account, rm, now_sgt, result)
            if ENABLE_DIRECTIONAL_TA:         # off — trend/grid failed out-of-sample validation
                self._run_trend_grid(account, rm, now_sgt, result)
            if ENABLE_NEWS:                   # off — unvalidated directional bets
                self._run_news_entries(account, rm, now_sgt, result)
                self._run_unlock_entries(account, rm, now_sgt, result)
        else:
            result.skipped.append("entries skipped: system halted (STOP)")

        # 3. Kelly refresh cadence + state persistence.
        self._maybe_log_kelly_refresh()
        self._persist_controller_state(now_sgt)
        return result

    # ── Layer: funding arb ───────────────────────────────────────────────────
    def _run_funding_exits(self, result: CycleResult) -> None:
        try:
            snaps = {s.symbol: s for s in self.price_feed.get_funding_rates()}
        except Exception as e:  # network hiccup — skip this layer this cycle
            result.skipped.append(f"funding feed error: {e}")
            return
        for pos in list(self.open_positions.values()):
            if pos.strategy != "funding":
                continue
            snap = snaps.get(pos.pair)
            if snap is None:
                continue
            decision = self.funding.evaluate_exit(snap)
            if decision.action == "exit":
                self._close(pos, "funding decayed", result)

    def _run_funding_entries(self, account, rm, now_sgt, result) -> None:
        try:
            snaps = filter_liquid(self.price_feed.get_funding_rates())
        except Exception as e:
            result.skipped.append(f"funding feed error: {e}")
            return
        frac = self._size_fraction("funding", FUNDING_DEFAULT_FRAC)
        for snap in self.funding.rank_opportunities(snaps):
            if account.open_positions >= 3:
                break
            entry = self.funding.evaluate_entry(snap, account.tradeable_capital * frac)
            if entry.action != "enter":
                continue
            self._try_open(
                TradeProposal(
                    pair=snap.symbol, side="long", strategy="funding",
                    size_usd=account.tradeable_capital * frac, leverage=1.0,
                ),
                rm, now_sgt, result,
            )

    # ── Universal exit management (SL / TP / per-strategy signal) ─────────────
    def _mark_price(self, pos: Position) -> float:
        """Current mark for a position: stocks priced via Yahoo, crypto via the exchange feed."""
        if pos.strategy.startswith("thesis"):
            from .venues import classify_venue
            crypto_universe = {t for ab in self._pair_symbols.values() for t in ab}
            if classify_venue(pos.pair.split("/")[0], crypto_universe) == "stock":
                from .stocks import stock_quote
                try:
                    return float(stock_quote(pos.pair.split("/")[0]) or 0.0)
                except Exception:
                    return 0.0
        try:
            return float(self.price_feed.get_price(pos.pair))
        except Exception:
            return 0.0

    def _manage_exits(self, result: CycleResult) -> None:
        """Apply stop-loss + take-profit to every position, plus trend/grid signal exits.

        Funding positions are skipped here — their exit is funding-decay, handled separately.
        This is what makes paper (and live) positions actually close instead of drifting.
        """
        from .thesis import THESIS_LT_STOP_PCT
        for pos in list(self.open_positions.values()):
            if pos.strategy == "funding":
                continue
            mark = self._mark_price(pos)
            if not mark:
                continue                          # can't price it this cycle → skip, try next
            _, pnl_pct = compute_pnl(pos.side, pos.entry_price, mark, pos.size_usd, pos.leverage)
            # Long-term thesis holds ride volatility: wide stop, NO take-profit (let winners run).
            if pos.strategy == "thesis-lt":
                if pnl_pct <= -THESIS_LT_STOP_PCT:
                    self._close(pos, "sl", result)
                continue
            if pnl_pct <= -PER_TRADE_STOP_PCT:
                self._close(pos, "sl", result)
                continue
            if pnl_pct >= TAKE_PROFIT_PCT:
                self._close(pos, "tp", result)
                continue
            if pos.strategy in ("trend", "grid"):
                try:
                    closes = [c[4] for c in self.price_feed.get_ohlcv(pos.pair, "1h", 120)]
                    if exit_signal(pos.strategy, pos.side, closes):
                        self._close(pos, "signal", result)
                except (ValueError, IndexError):
                    pass

    # ── Layers 3 & 4: native trend + mean-reversion ──────────────────────────
    def _run_trend_grid(self, account, rm, now_sgt, result) -> None:
        # Fetch OHLCV once per watched pair, reuse it for both correlation caching and signals.
        data: dict[str, dict] = {}
        for pair in self.watched_pairs:
            try:
                ohlcv = self.price_feed.get_ohlcv(pair, "1h", 120)
                data[pair] = {
                    "highs": [c[2] for c in ohlcv],
                    "lows": [c[3] for c in ohlcv],
                    "closes": [c[4] for c in ohlcv],
                }
            except Exception as e:  # noqa: BLE001 - skip this pair for the cycle, keep going
                result.skipped.append(f"{pair}: ohlcv error ({e})")

        self._refresh_correlations(data, now_sgt)

        for pair, d in data.items():
            if account.open_positions >= 3:
                break
            try:
                _, adx = regime_from_ohlcv(d["highs"], d["lows"], d["closes"])
            except (ValueError, IndexError):
                continue
            sig = evaluate_trend(d["closes"], adx)
            if sig.action == "none":
                sig = evaluate_mean_reversion(d["closes"], adx)
            if sig.action == "none":
                continue
            default = TREND_DEFAULT_FRAC if sig.strategy == "trend" else GRID_DEFAULT_FRAC
            frac = self._size_fraction(sig.strategy, default)
            self._try_open(
                TradeProposal(
                    pair=pair, side=sig.action, strategy=sig.strategy,
                    size_usd=account.tradeable_capital * frac, leverage=1.0,
                ),
                rm, now_sgt, result,
            )

    def _refresh_correlations(self, data: dict, now_sgt) -> None:
        """Cache pairwise correlation between watched pairs so the correlation guard works.

        Without this the guard fails open and BTC + ETH (near-perfectly correlated) could both
        open as one doubled-up bet. Uses the OHLCV already fetched this cycle.
        """
        pairs = list(data.keys())
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                a, b = data[pairs[i]]["closes"], data[pairs[j]]["closes"]
                if len(a) != len(b) or len(a) < 2:
                    continue
                try:
                    corr = correlation_from_closes(a, b)
                    self.db.cache_correlation(pairs[i], pairs[j], corr, now_sgt.isoformat())
                except (ValueError, IndexError):
                    pass

    # ── Layer 5: market-neutral pairs (the validated edge) ───────────────────
    def _pair_windows(self, a_sym: str, b_sym: str):
        """Aligned recent closes for both legs. Returns (a_closes, b_closes) or None."""
        try:
            a_ohlcv = self.price_feed.get_ohlcv(a_sym, "1h", PAIRS_WINDOW + 5)
            b_ohlcv = self.price_feed.get_ohlcv(b_sym, "1h", PAIRS_WINDOW + 5)
        except Exception:
            return None
        ac, bc, _ = align(a_ohlcv, b_ohlcv)
        if len(ac) < PAIRS_WINDOW + 1:
            return None
        return ac, bc

    def _planned_pair_legs(self, name, a_sym, b_sym, side, beta, account):
        """Compute the two legs (symbol, side, notional) a pair entry WOULD open.

        Shared by the exposure check and _open_pair so they can't disagree.
        """
        price_a = self.price_feed.get_price(a_sym)
        price_b = self.price_feed.get_price(b_sym)
        frac = self.pair_allocations.get(name, PAIRS_FRACTION)   # risk-model sizing, with a fallback
        total = frac * account.tradeable_capital
        wa, wb = price_a, max(beta, 1e-9) * price_b
        denom = wa + wb
        notional_a = total * wa / denom
        notional_b = total * wb / denom
        # short_spread → A rich → short A / long B; long_spread → the reverse.
        side_a, side_b = ("short", "long") if side == "short_spread" else ("long", "short")
        return [(a_sym, side_a, notional_a), (b_sym, side_b, notional_b)]

    def _open_pair_legs(self) -> list:
        """Current open pair legs as (symbol, side, notional) for the exposure check."""
        legs = []
        for entry in self.open_pairs.values():
            for pos in (entry["legA"], entry["legB"]):
                legs.append((pos.pair, pos.side, pos.size_usd))
        return legs

    def _open_pair(self, name, a_sym, b_sym, side, beta, account, result) -> None:
        planned = self._planned_pair_legs(name, a_sym, b_sym, side, beta, account)
        legs = []
        for sym, leg_side, notional in planned:
            pos = self.exec.open_position(sym, leg_side, notional, 1.0, f"pairs:{name}")
            pos.db_id = self.db.open_trade(TradeRecord(
                pair=pos.pair, side=pos.side, strategy=pos.strategy,
                entry_price=pos.entry_price, size_usd=pos.size_usd, leverage=1.0,
                opened_at=pos.opened_at, is_paper=self.is_paper,
            ))
            legs.append(pos)
            result.opened.append(pos)
        self.open_pairs[name] = {"legA": legs[0], "legB": legs[1], "side": side}
        self.pairs_trader.record_entry(name, side)
        la, lb = legs
        result.alerts.append(
            f"📈 Opened pair {name}: {la.side.upper()} {la.pair} ${la.size_usd:.0f} + "
            f"{lb.side.upper()} {lb.pair} ${lb.size_usd:.0f}")

    def _close_pair(self, name, reason, result) -> None:
        entry = self.open_pairs.pop(name, None)
        if entry is None:
            return
        # ONE timestamp for both legs so a round groups as a single round (not two half-open ones)
        # in the gate checker and the pair-health monitor.
        round_at = datetime.now(timezone.utc).isoformat()
        pnl = 0.0
        for pos in (entry["legA"], entry["legB"]):
            closed = self.exec.close_position(pos, reason)
            self.db.close_trade(
                pos.db_id, closed_at=round_at, exit_price=closed.exit_price,
                pnl_usd=closed.pnl_usd, pnl_pct=closed.pnl_pct,
                fees_usd=closed.fee_usd, exit_reason=f"pairs:{reason}",
            )
            result.closed.append(closed)
            pnl += closed.pnl_usd
        self.pairs_trader.record_exit(name)
        result.alerts.append(f"📉 Closed pair {name} ({reason}): P&L ${pnl:+.2f}")

    def _run_pairs_exits(self, result: CycleResult) -> None:
        for name in list(self.open_pairs.keys()):
            a_sym, b_sym = self._pair_symbols[name]
            win = self._pair_windows(a_sym, b_sym)
            if win is None:
                continue
            ac, bc = win
            dec = self.pairs_trader.evaluate(
                a_sym, b_sym, ac[-(PAIRS_WINDOW + 1):-1], bc[-(PAIRS_WINDOW + 1):-1], ac[-1], bc[-1]
            )
            if dec.action == "exit":
                self._close_pair(name, dec.reason, result)

    def _pair_health(self, name: str):
        """Decay check for a pair, recomputed from its realized rounds in the trade log."""
        rows = self.db.closed_trades(strategy=f"pairs:{name}", limit=200)
        return evaluate_health(pair_round_pnls(rows))

    def _process_thesis_orders(self, account, result) -> None:
        """Execute discretionary thesis orders queued from chat. Capped sleeve + auto stop-loss.

        Routes by venue: crypto -> Binance (self.exec), stocks -> IBKR (self.stock_exec). Capital
        does not move between venues automatically; the sleeve cap is shared in bookkeeping only.
        """
        from .thesis import THESIS_SLEEVE_PCT
        from .venues import classify_venue
        crypto_universe = {t for ab in self._pair_symbols.values() for t in ab}
        for o in self.db.pending_thesis_orders():
            action, pair = o["action"], o["pair"]
            base = pair.split("/")[0]
            venue = classify_venue(base, crypto_universe)
            try:
                if action == "close":
                    found = False
                    for pos in list(self.open_positions.values()):
                        if pos.strategy.startswith("thesis") and pos.pair == pair:
                            self._close(pos, "thesis-manual", result); found = True
                    self.db.set_thesis_order_status(o["id"], "done")
                    if not found:
                        result.alerts.append(f"No open thesis position in {pair} to close.")
                    continue
                if not self.controller.can_open_new_trade():
                    self.db.set_thesis_order_status(o["id"], "rejected")
                    result.alerts.append(f"Thesis {pair} not placed — system is halted (STOP).")
                    continue
                client = self.exec if venue == "crypto" else self.stock_exec
                if client is None:                  # stock order but no IBKR wired yet
                    self.db.set_thesis_order_status(o["id"], "rejected")
                    result.alerts.append(
                        f"{base} is a stock — stock execution isn't connected yet (IBKR pending). "
                        f"I can research and price it, but can't place the order. Crypto theses work now.")
                    continue
                # Both short-term ('thesis') and long-term ('thesis-lt') share the one sleeve cap.
                used = sum(p.size_usd for p in self.open_positions.values()
                           if p.strategy.startswith("thesis"))
                size = (o["size_pct"] / 100.0) * account.current_capital
                cap = THESIS_SLEEVE_PCT * account.current_capital
                if used + size > cap + 1e-9:
                    self.db.set_thesis_order_status(o["id"], "rejected")
                    result.alerts.append(
                        f"Thesis {pair} rejected: sleeve full (cap ${cap:.0f}, used ${used:.0f}).")
                    continue
                is_lt = action.endswith("_lt")
                strategy = "thesis-lt" if is_lt else "thesis"
                side = "long" if action.startswith("buy") else "short"
                pos = client.open_position(pair, side, size, 1.0, strategy)
                pos.db_id = self.db.open_trade(TradeRecord(
                    pair=pos.pair, side=pos.side, strategy=strategy, entry_price=pos.entry_price,
                    size_usd=pos.size_usd, leverage=1.0, opened_at=pos.opened_at, is_paper=self.is_paper))
                self.open_positions[pos.db_id] = pos
                result.opened.append(pos)
                self.db.set_thesis_order_status(o["id"], "done")
                horizon = "LONG-TERM hold (wide 35% stop, no auto take-profit)" if is_lt \
                    else "short-term (8% stop, 15% take-profit)"
                result.alerts.append(
                    f"📈 Thesis {side.upper()} {pair} ${pos.size_usd:.0f} opened "
                    f"({'PAPER' if self.is_paper else 'LIVE'}) — {horizon}. "
                    f"Reply CLOSE {pair.split('/')[0]} to exit.")
            except Exception as e:
                self.db.set_thesis_order_status(o["id"], "rejected")
                result.alerts.append(f"Thesis {action} {pair} failed: {type(e).__name__} (could not price it?).")

    def _check_circuit_breaker(self, now_sgt, result) -> bool:
        """Trip if too many pairs hit a divergence-stop recently. Alerts on state change."""
        since = (now_sgt - timedelta(minutes=DIVERGENCE_WINDOW_MIN)).astimezone(
            timezone.utc).isoformat()
        count = divergence_round_count(self.db.closed_trades(limit=500), since)
        tripped = is_tripped(count)
        was_tripped = self.db.get_state("breaker_tripped", "0") == "1"
        self.db.set_state("breaker_tripped", "1" if tripped else "0", now_sgt.isoformat())
        if tripped and not was_tripped:
            result.alerts.append(
                f"🚨 CIRCUIT BREAKER TRIPPED: {count} pairs diverged in "
                f"{DIVERGENCE_WINDOW_MIN}min — possible regime break. New pair entries PAUSED. "
                f"Open positions still exit on their stops. Send CLOSE ALL to flatten, or review "
                f"and it clears automatically when divergence subsides.")
        elif was_tripped and not tripped:
            result.alerts.append("✅ Circuit breaker cleared — pair entries resumed.")
        return tripped

    def _run_pairs_entries(self, account, rm, now_sgt, result) -> None:
        # Respect the same portfolio-level halts as everything else.
        if rm.drawdown_breached() or rm.daily_limit_hit() or rm.weekly_limit_hit():
            return
        for a_sym, b_sym in self.pairs_trader.pairs:
            if len(self.open_pairs) >= MAX_OPEN_PAIRS:
                break
            name = PairsTrader.name_of(a_sym, b_sym)
            if name in self.open_pairs:
                continue
            # Health gate: skip pairs whose live edge has decayed (existing positions still exit).
            verdict = self._pair_health(name)
            if not verdict.healthy:
                result.skipped.append(f"{name}: {verdict.reason}")
                continue
            win = self._pair_windows(a_sym, b_sym)
            if win is None:
                continue
            ac, bc = win
            dec = self.pairs_trader.evaluate(
                a_sym, b_sym, ac[-(PAIRS_WINDOW + 1):-1], bc[-(PAIRS_WINDOW + 1):-1], ac[-1], bc[-1]
            )
            if dec.action == "enter":
                # Aggregate per-asset exposure cap: would this pair over-concentrate one coin
                # across all open legs? (XRP/IMX appear in several pairs.)
                planned = self._planned_pair_legs(name, a_sym, b_sym, dec.side, dec.beta, account)
                cap_usd = MAX_ASSET_EXPOSURE_PCT * account.tradeable_capital
                breach = exposure_breach(self._open_pair_legs(), planned, cap_usd)
                if breach:
                    asset, net = breach
                    result.skipped.append(
                        f"{name}: blocked — {asset} net exposure ${net:+.0f} would exceed "
                        f"${cap_usd:.0f} cap")
                    continue
                self._open_pair(name, a_sym, b_sym, dec.side, dec.beta, account, result)

    # ── Layer: news ──────────────────────────────────────────────────────────
    def _run_news_entries(self, account, rm, now_sgt, result) -> None:
        if not self.news_provider:
            return
        kelly = self._size_fraction("news", NEWS_DEFAULT_FRAC)
        for intent in self.news_provider():
            pair = f"{intent.asset}/USDT"
            # Use the smaller of the confidence-tier size and the Kelly size (once we have history).
            frac = min(intent.size_fraction, kelly) if kelly != NEWS_DEFAULT_FRAC else intent.size_fraction
            self._try_open(
                TradeProposal(
                    pair=pair, side=intent.direction, strategy="news",
                    size_usd=account.tradeable_capital * frac, leverage=1.0,
                ),
                rm, now_sgt, result,
            )

    # ── Layer: token unlocks ─────────────────────────────────────────────────
    def _run_unlock_entries(self, account, rm, now_sgt, result) -> None:
        if not self.unlock_provider:
            return
        held = {p.pair.split("/")[0] for p in self.open_positions.values()}
        frac = self._size_fraction("unlock", UNLOCK_DEFAULT_FRAC)
        for sig in self.unlock_provider(now_sgt.date(), held):
            if sig.action != "short":
                continue
            self._try_open(
                TradeProposal(
                    pair=f"{sig.symbol}/USDT", side="short", strategy="unlock",
                    size_usd=account.tradeable_capital * frac, leverage=1.0,
                ),
                rm, now_sgt, result,
            )

    def _run_unlock_exits(self, now_sgt, result) -> None:
        if not self.unlock_provider:
            return
        held = {p.pair.split("/")[0] for p in self.open_positions.values()}
        cover = {s.symbol for s in self.unlock_provider(now_sgt.date(), held) if s.action == "cover"}
        for pos in list(self.open_positions.values()):
            if pos.strategy == "unlock" and pos.pair.split("/")[0] in cover:
                self._close(pos, "unlock window closed", result)

    # ── Housekeeping ─────────────────────────────────────────────────────────
    def _maybe_log_kelly_refresh(self) -> None:
        total = len(self.db.closed_trades(limit=10000))
        if total and total % RECOMPUTE_EVERY_N_TRADES == 0:
            self.db.set_state("last_kelly_refresh_at_trades", str(total),
                              datetime.now(SGT).isoformat())

    def _persist_controller_state(self, now_sgt: datetime) -> None:
        # Heartbeat for the go-live gate's liveness check.
        self.db.set_state("last_cycle_at", now_sgt.isoformat(), now_sgt.isoformat())
        self.db.set_state("halted", "1" if self.controller.state.halted else "0", now_sgt.isoformat())
        self.db.set_state("paused_layers", ",".join(sorted(self.controller.state.paused_layers)),
                          now_sgt.isoformat())
        # mtd P&L for the report
        month_start = now_sgt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        mtd = self._realized_since(month_start.astimezone(timezone.utc).isoformat())
        self.db.set_state("mtd_pnl", f"{mtd:.2f}", now_sgt.isoformat())
