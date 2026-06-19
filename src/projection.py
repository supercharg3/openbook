"""Monte Carlo returns model for the pairs book.

Simulates 20,000 possible futures of monthly returns at the chosen volatility, applying:
  - PROFIT CASHOUT ratchet: 40% of every new-high gain is swept to a safe reserve (never traded
    again) — locks in profit so a later drawdown can't erase it.
  - HARD STOP-LOSS: if trading equity falls 20% from its peak, halt and preserve the rest
    (conservative; the real system recalibrates and can resume, so reality tends to be better).
  - optional staged capital injections.

Reports percentile outcomes, locked-in profit, probability of ending below what you put in, and
how often the stop fires. Estimates only; live returns run below backtest.
"""
from __future__ import annotations
import numpy as np

CASHOUT_FRAC = 0.40
DD_STOP = 0.20


def run(initial, annual_return, annual_vol, months, injections, n=20000, seed=7):
    rng = np.random.default_rng(seed)
    mmean = (1 + annual_return) ** (1 / 12) - 1
    mvol = annual_vol / np.sqrt(12)
    eq = np.full(n, float(initial)); peak = eq.copy(); res = np.zeros(n)
    invested = np.full(n, float(initial)); stopped = np.zeros(n, bool); maxdd = np.zeros(n)
    snaps = {}
    for m in range(1, months + 1):
        if m in injections:
            amt = injections[m]
            eq[~stopped] += amt; peak[~stopped] += amt; invested += amt
        a = ~stopped
        r = rng.normal(mmean, mvol, n)
        eq[a] *= (1 + r[a])
        nh = a & (eq > peak)
        gain = eq[nh] - peak[nh]; sweep = CASHOUT_FRAC * gain
        res[nh] += sweep; eq[nh] -= sweep; peak[nh] = eq[nh]
        dd = np.where(peak > 0, 1 - eq / peak, 0.0)
        maxdd = np.maximum(maxdd, dd)
        hit = a & (dd >= DD_STOP)
        res[hit] += eq[hit]; eq[hit] = 0.0; stopped[hit] = True
        if m in (12, 24, 36):
            snaps[m] = dict(total=(eq + res).copy(), reserve=res.copy(),
                            invested=invested.copy(), maxdd=maxdd.copy(), stopped=stopped.copy())
    return snaps


def report(title, initial, annual_return, annual_vol, injections):
    snaps = run(initial, annual_return, annual_vol, 36, injections)
    print(f"\n{title}")
    print(f"  (return {annual_return*100:.0f}%/yr, vol {annual_vol*100:.0f}%/yr, "
          f"40% profit-cashout, 20% stop)")
    for m in (12, 24, 36):
        s = snaps[m]
        tot, inv = s["total"], s["invested"]
        p10, p50, p90 = np.percentile(tot, [10, 50, 90])
        loss_prob = float((tot < inv).mean() * 100)
        locked = float(np.median(s["reserve"]))
        stop_rate = float(s["stopped"].mean() * 100)
        med_dd = float(np.median(s["maxdd"]) * 100)
        print(f"  Year {m//12}: invested ${inv[0]:,.0f} | wealth p10 ${p10:,.0f} / "
              f"p50 ${p50:,.0f} / p90 ${p90:,.0f} | locked-safe ${locked:,.0f} | "
              f"chance below invested {loss_prob:.0f}% | stop fired {stop_rate:.0f}% | typ. drawdown {med_dd:.0f}%")


if __name__ == "__main__":
    print("=" * 78)
    print("RETURNS MODEL — 15% vol, profit-cashout + 20% stop-loss (20k simulations)")
    print("=" * 78)
    report("SCENARIO A: $500, no further injections", 500, 0.15, 0.15, {})
    report("SCENARIO B: staged injections to $5,000 total ($500 now, +$500 m2, +$1k m5, +$3k m9)",
           500, 0.15, 0.15, {2: 500, 5: 1000, 9: 3000})
    print("\n(Conservative model: stop = permanent halt+preserve. Real system recalibrates and")
    print(" resumes, so real outcomes tend to beat these. Live returns also run below backtest.)")
