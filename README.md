# 📖 Openbook

**An autonomous AI trading agent you can see right through.**

Openbook runs a transparent, autonomous trading agent that *researches markets, makes its own
decisions, and trades*, while explaining every move in plain English. You watch an AI reason about trades in real time, and you talk to it like a colleague, all from Telegram.

It starts on **practice money by default**. You don't configure it by hand: you point *your own AI
agent* at this repo and it **interviews you and sets the whole thing up for you**, tailored to your
risk tolerance, your goals, and the assets you're comfortable with.

> [!WARNING]
> **Read this first.** Openbook is an **educational / research framework**. It has **no proven
> edge**. It runs on **paper money by default** and only ever touches real money if *you* explicitly
> enable it. **You can lose money if you go live. This is not financial advice.** The value here is a
> *transparent, disciplined, autonomous system you can learn from*, not a promise of returns. A lucky
> run is not proof of anything. No warranty. Trade only what you can afford to lose.

---

## What it actually does

Openbook runs a few **strategy "sleeves"** side by side, each tagged, budgeted, and risk-managed
separately (think *separate envelopes in one account*). You pick which ones fit you:

| Sleeve | What it is | Vibe |
|---|---|---|
| **Market-Neutral (crypto)** | Out-of-sample-validated pairs trading, bets on two coins' spread reverting, market-direction-neutral | Safe, slow, the compounder |
| **Factor (stocks)** | Ranks stocks by momentum + quality, holds the best, benchmarked vs the right index | Steady, rules-based |
| **Swing (agentic)** | The AI researches high-conviction stock/crypto bets, capped per bet, with a hard floor + profit-lock + circuit breaker | Aggressive, transparent, high-variance |

Plus a **research tool** (`look into NVDA` → an honest bull/bear/risk verdict), an **idea scanner**,
and a **conversational assistant**, all in your Telegram chat.

## The experience

1. **Point your AI agent at this repo** and say *"set up Openbook for me."*
2. It **interviews you** — risk tolerance, goals, stocks/crypto/both — and recommends a setup just
   for you (it goes slow for first-timers, hands the knobs to pros).
3. It **walks you through API keys, paper-first.** It never sees your keys: it guides you to paste
   each one into your own `.env` file yourself.
4. It **launches on practice money**, pins your **live dashboard** in the chat, and teaches you the
   commands.
5. Later, *if* it earns your trust, you cross a **go-live gate** you set and opt into real money.

## Reporting

- **Telegram:** daily (light), weekly digest, monthly review, all in plain English, vs the *right*
  benchmark, with honest "too early to mean anything" flags.
- **Web dashboard:** equity curves, the benchmark race, the agent's live decision feed. **Private by
  default; public opt-in** (a public one is a shareable, transparent track record).

## Security (read the [agent-blind keys](#) rule)

- The setup agent **never asks you to paste keys into chat.** You place them in your own `.env`.
- Use **trade-only API keys (no withdrawal permission)** and **paper keys first**. A leaked key then
  can't drain you.
- **Never** put an on-chain wallet's private key on a server. Openbook only uses exchange keys that
  can't withdraw.

## Quick start (manual)

Prefer your agent to do this (see "The experience"), but by hand:

```bash
git clone <repo> openbook && cd openbook
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your keys YOURSELF
python -m pytest -q           # sanity check
python -m src.run_trade       # starts in dry-run (paper) by default
```

You'll need free/cheap keys: **Anthropic** (the brain), **Telegram** (a bot), and, depending on your
sleeves, **Alpaca** (stocks, paper), **Binance** (crypto data, trade-only), **Exa** (news).

## How it stays honest

Every benchmark is the *right* one (each sleeve vs its true opponent, never vs zero). Drawdowns and
win-concentration are shown, not hidden. Everything is paper until you deliberately choose otherwise.
The point is to *learn how an autonomous agent trades*, transparently and on a leash, not to get rich.

---

*Built to be watched, not trusted blindly. Not financial advice.*
