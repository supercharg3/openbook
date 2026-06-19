"""Backtester for the native price-based strategies (trend + mean-reversion).

Replays the EXACT signal logic from strategies.py over historical OHLCV — no separate strategy
definition to drift out of sync. Decisions at bar i use only data through bar i (no look-ahead);
entries fill at that bar's close, stops/take-profits fill intrabar against the bar's high/low.

Funding arb and news aren't backtested here (funding history + news are not cleanly reproducible)
— this validates the directional alpha, which is what the plan's "Sharpe > 1.5, drawdown < 15%"
gate is about.

Usage:
    python -m src.backtest BTC/USDT ETH/USDT --days 180 --timeframe 1h
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np

from .regime import compute_adx
from .strategies import evaluate_mean_reversion, evaluate_trend, exit_signal

LOOKBACK = 200            # bars of context fed to the indicators each step
STOP_PCT = 0.08
TAKE_PROFIT_PCT = 0.15
ROUND_TRIP_FEE = 0.0007   # 0.02% maker entry + 0.05% taker exit, as a fraction
POSITION_FRACTION = 0.10  # capital fraction per trade, for the equity curve / drawdown


@dataclass
class Trade:
    pair: str
    strategy: str
    side: str
    entry: float
    exit: float
    pnl_pct: float        # net of fees, on notional
    reason: str


@dataclass
class BacktestResult:
    pair: str
    bars: int
    period_days: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def returns(self) -> list[float]:
        return [t.pnl_pct for t in self.trades]

    def stats(self) -> dict:
        rets = np.array(self.returns)
        if len(rets) == 0:
            return {"trades": 0}
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        gross_win = wins.sum()
        gross_loss = -losses.sum()
        # Equity curve with fixed fractional sizing → max drawdown + total return
        equity = [1.0]
        for r in rets:
            equity.append(equity[-1] * (1 + POSITION_FRACTION * r))
        equity = np.array(equity)
        peak = np.maximum.accumulate(equity)
        max_dd = float((1 - equity / peak).max())
        per_trade_sharpe = float(rets.mean() / rets.std()) if rets.std() > 0 else 0.0
        # Annualise: treat each trade as a period, scale by trades-per-year.
        years = max(self.period_days / 365.0, 1e-9)
        trades_per_year = len(rets) / years
        annualized_sharpe = per_trade_sharpe * np.sqrt(trades_per_year)
        return {
            "trades": len(rets),
            "win_rate": float(len(wins) / len(rets)),
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "expectancy": float(rets.mean()),
            "sharpe": float(annualized_sharpe),
            "max_drawdown": max_dd,
            "total_return": float(equity[-1] - 1.0),
        }


def backtest_pair(pair: str, ohlcv: list[list[float]]) -> BacktestResult:
    highs = [c[2] for c in ohlcv]
    lows = [c[3] for c in ohlcv]
    closes = [c[4] for c in ohlcv]
    period_days = (ohlcv[-1][0] - ohlcv[0][0]) / 86_400_000 if len(ohlcv) > 1 else 0.0
    result = BacktestResult(pair=pair, bars=len(ohlcv), period_days=period_days)
    pos = None  # {"side", "entry", "strategy"}

    for i in range(LOOKBACK, len(ohlcv)):
        hi, lo, close = highs[i], lows[i], closes[i]
        w_closes = closes[i - LOOKBACK + 1 : i + 1]
        w_highs = highs[i - LOOKBACK + 1 : i + 1]
        w_lows = lows[i - LOOKBACK + 1 : i + 1]

        if pos is not None:
            exit_price, reason = None, None
            if pos["side"] == "long":
                sl, tp = pos["entry"] * (1 - STOP_PCT), pos["entry"] * (1 + TAKE_PROFIT_PCT)
                if lo <= sl:
                    exit_price, reason = sl, "sl"
                elif hi >= tp:
                    exit_price, reason = tp, "tp"
            else:
                sl, tp = pos["entry"] * (1 + STOP_PCT), pos["entry"] * (1 - TAKE_PROFIT_PCT)
                if hi >= sl:
                    exit_price, reason = sl, "sl"
                elif lo <= tp:
                    exit_price, reason = tp, "tp"
            if exit_price is None and exit_signal(pos["strategy"], pos["side"], np.array(w_closes)):
                exit_price, reason = close, "signal"
            if exit_price is not None:
                direction = 1.0 if pos["side"] == "long" else -1.0
                gross = direction * (exit_price - pos["entry"]) / pos["entry"]
                result.trades.append(Trade(
                    pair=pair, strategy=pos["strategy"], side=pos["side"],
                    entry=pos["entry"], exit=exit_price,
                    pnl_pct=gross - ROUND_TRIP_FEE, reason=reason,
                ))
                pos = None

        if pos is None:
            try:
                adx = compute_adx(np.array(w_highs), np.array(w_lows), np.array(w_closes))
            except ValueError:
                continue
            sig = evaluate_trend(np.array(w_closes), adx)
            if sig.action == "none":
                sig = evaluate_mean_reversion(np.array(w_closes), adx)
            if sig.action != "none":
                pos = {"side": sig.action, "entry": close, "strategy": sig.strategy}

    return result


def format_report(results: list[BacktestResult]) -> str:
    lines = ["", "=" * 64, "BACKTEST REPORT", "=" * 64]
    gate_pass = True
    for r in results:
        s = r.stats()
        lines.append(f"\n{r.pair}  ({r.bars} bars)")
        if s["trades"] == 0:
            lines.append("  no trades")
            continue
        lines += [
            f"  trades         {s['trades']}",
            f"  win rate       {s['win_rate']*100:.1f}%",
            f"  avg win/loss   +{s['avg_win']*100:.2f}% / {s['avg_loss']*100:.2f}%",
            f"  profit factor  {s['profit_factor']:.2f}   (gate > 1.3)",
            f"  expectancy     {s['expectancy']*100:+.3f}% per trade",
            f"  Sharpe (ann.)  {s['sharpe']:.2f}   (gate > 1.5)",
            f"  max drawdown   {s['max_drawdown']*100:.1f}%   (gate < 15%)",
            f"  total return   {s['total_return']*100:+.1f}%  (10% sizing, 180d)",
        ]
        if s["profit_factor"] < 1.3 or s["sharpe"] < 1.5 or s["max_drawdown"] > 0.15:
            gate_pass = False
            lines.append("  ⚠️  does NOT clear the gate")
        else:
            lines.append("  ✅ clears the gate")
    lines += ["", "=" * 64,
              "VERDICT: " + ("✅ strategies show edge" if gate_pass else
                             "⚠️ needs tuning — see flagged pairs"),
              "=" * 64]
    return "\n".join(lines)


def fetch_history(exchange, pair: str, timeframe: str, total_bars: int) -> list[list[float]]:
    """Page through ccxt fetch_ohlcv to assemble `total_bars` of history."""
    settle = pair if ":" in pair else f"{pair}:USDT"
    all_bars: list[list[float]] = []
    limit = 1000
    since = None
    # Walk backwards from now by fetching pages and prepend-stitching.
    bars = exchange.fetch_ohlcv(settle, timeframe=timeframe, limit=limit)
    all_bars = bars
    while len(all_bars) < total_bars and len(bars) == limit:
        since = all_bars[0][0] - limit * _tf_ms(timeframe)
        bars = exchange.fetch_ohlcv(settle, timeframe=timeframe, since=since, limit=limit)
        if not bars:
            break
        all_bars = bars + [b for b in all_bars if b[0] > bars[-1][0]]
    return all_bars[-total_bars:]


def _tf_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    n = int(timeframe[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="*", default=["BTC/USDT", "ETH/USDT"])
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--timeframe", default="1h")
    args = ap.parse_args()

    from .ccxt_feed import build_binance

    exchange = build_binance(None, None)
    bars = args.days * (24 if args.timeframe.endswith("h") else 1) * (
        int(60 / int(args.timeframe[:-1])) if args.timeframe.endswith("m") else 1
    )
    results = []
    for pair in args.pairs:
        hist = fetch_history(exchange, pair, args.timeframe, bars)
        results.append(backtest_pair(pair, hist))
    print(format_report(results))


if __name__ == "__main__":
    main()
