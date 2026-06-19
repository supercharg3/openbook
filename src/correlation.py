"""Correlation guard.

Two highly-correlated positions are really one bet with double the risk. Before opening a 2nd
or 3rd simultaneous position we check the 30-day Pearson correlation between the candidate pair
and every currently-open pair. If any exceeds the threshold, the new trade is blocked.

Correlations are computed nightly from ccxt OHLCV (daily closes) and cached in SQLite, so the
hot path is a cheap lookup. This module holds the pure math + the guard decision; the nightly
job lives in the orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CORRELATION_THRESHOLD = 0.6
CORRELATION_WINDOW_DAYS = 30


def pearson(returns_a: np.ndarray, returns_b: np.ndarray) -> float:
    """Pearson correlation of two equal-length return series. Returns 0.0 if undefined."""
    a = np.asarray(returns_a, dtype=float)
    b = np.asarray(returns_b, dtype=float)
    if len(a) != len(b) or len(a) < 2:
        raise ValueError("Series must be equal length and have >= 2 points")
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def daily_returns(closes: np.ndarray) -> np.ndarray:
    """Convert a series of daily closes to simple daily returns."""
    closes = np.asarray(closes, dtype=float)
    if len(closes) < 2:
        raise ValueError("Need at least 2 closes")
    return np.diff(closes) / closes[:-1]


def correlation_from_closes(closes_a: np.ndarray, closes_b: np.ndarray) -> float:
    return pearson(daily_returns(closes_a), daily_returns(closes_b))


@dataclass(frozen=True)
class CorrelationDecision:
    allowed: bool
    blocking_pair: str | None = None
    correlation: float | None = None
    reason: str = ""


def check_new_position(
    candidate_pair: str,
    open_pairs: list[str],
    correlation_lookup,                       # callable(a, b) -> float | None
    threshold: float = CORRELATION_THRESHOLD,
) -> CorrelationDecision:
    """Decide whether opening `candidate_pair` is allowed given currently-open pairs.

    correlation_lookup(a, b) returns the cached 30-day correlation or None if unknown.
    A None lookup is treated as "unknown → do not block" (we fail open, but the nightly job
    keeps the cache fresh for all watched pairs so this is rare).
    """
    for open_pair in open_pairs:
        if open_pair == candidate_pair:
            return CorrelationDecision(
                allowed=False,
                blocking_pair=open_pair,
                correlation=1.0,
                reason=f"Already holding {open_pair}",
            )
        corr = correlation_lookup(candidate_pair, open_pair)
        if corr is not None and abs(corr) > threshold:
            return CorrelationDecision(
                allowed=False,
                blocking_pair=open_pair,
                correlation=corr,
                reason=(
                    f"{candidate_pair} correlates {corr:+.2f} with open {open_pair} "
                    f"(> {threshold} limit)"
                ),
            )
    return CorrelationDecision(allowed=True, reason="No correlation conflict")
