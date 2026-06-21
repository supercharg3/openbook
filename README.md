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
| **Pairs (crypto)** | Out-of-sample-validated stat-arb — bets two correlated coins' spread will revert, market-direction-neutral | Safe, slow, the compounder |
| **Factor (stocks)** | Ranks stocks by momentum + quality, holds the best, benchmarked vs the right index | Steady, rules-based |
| **Swing (agentic)** | AI researches high-conviction stock/crypto bets once daily; hard floor + profit-lock + circuit breaker | Aggressive, one decision/day |
| **Degen (active crypto)** | Technical momentum signals — breakout + volume surge — on 20 volatile coins, fires every 15 minutes | Highest variance, most active |

Plus an **alpha channel monitor** (watches public Telegram channels, runs bear/bull research on every signal, routes to the right sleeve automatically — WAIT signals set a price watch and auto-enter when the price arrives), a **research tool** (`look into NVDA`), and a **conversational assistant**, all in your Telegram chat.

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

## Setup

**Step 1: clone the repo.** Step 2: open it in your AI coding tool and say *"set up Openbook."*
The AI reads `AGENTS.md` and runs the full setup with you — interview, config, keys, launch,
and a hands-on walkthrough of the commands. You never configure anything by hand.

### Which AI tool to use

| Tool | How to open this repo |
|---|---|
| **Claude Code** (recommended) | `cd openbook && claude` → say *"set up Openbook"* |
| **Cursor** | Open → File → Open Folder → select `openbook/` → say *"set up Openbook"* |
| **Codex CLI** | `cd openbook && codex` → say *"set up Openbook"* |
| **Gemini CLI** | `cd openbook && gemini` → say *"set up Openbook"* |
| **Any other agent** | Point it at the folder. It will find `AGENTS.md` automatically. |

```bash
git clone https://github.com/supercharg3/openbook.git
cd openbook
# then open your AI tool as above
```

The AI handles everything: runs `bash setup.sh`, edits `.env` for you, guides you through each
API key one at a time (it never sees your keys — you paste them yourself), verifies the install,
starts the bot, and teaches you the commands.

**Where to run it:**

| | Local Mac / laptop | 24/7 cloud server |
|---|---|---|
| **Best for** | Testing, trying it out | Always-on, 8am daily reports |
| **Cost** | Free | ~$6/mo (DigitalOcean) |
| **AI setup time** | ~20 min | ~90 min |
| **Guide** | The AI walks you through it | [DEPLOYMENT.md](DEPLOYMENT.md) |

> **No AI tool?** You can follow [DEPLOYMENT.md](DEPLOYMENT.md) manually — every command is
> documented there. But using an AI agent is significantly faster.

---

## What the AI does during setup

1. **Interviews you** — 3 questions: risk tolerance, goals, stocks/crypto preference
2. **Recommends sleeves** — explains which ones fit you and why, confirms before touching anything
3. **Runs `bash setup.sh`** — installs deps, creates `.env`
4. **Guides you through each API key** — one at a time, tells you exactly where to get it, you
   paste it yourself (the AI never sees your keys)
5. **Verifies** — runs the test suite, confirms everything passes
6. **Launches** — starts the bot, confirms you get a Telegram message back
7. **Teaches** — walks you through every command hands-on: `STATUS`, `look into NVDA`, `STOP`
8. **Sets your go-live gate** — you define what "proven" means before real money is possible

**Keys you'll need** (the AI tells you where to get each one):

| Key | Where | Cost | Required? |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | ~$0–2/mo | Yes |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram | Free | Yes |
| `ALPACA_API_KEY_ID` | alpaca.markets → Paper Trading | Free | For stock sleeves |
| `BINANCE_API_KEY` | binance.com (trade-only, **withdrawals OFF**) | Free | For crypto sleeves |
| `EXA_API_KEY` | exa.ai | Free tier | For research + news scan |

**The capital figures in `.env` are practice amounts — simulated paper money. You do not need
real funds to start.**

## How it stays honest

Every benchmark is the *right* one (each sleeve vs its true opponent, never vs zero). Drawdowns and
win-concentration are shown, not hidden. Everything is paper until you deliberately choose otherwise.
The point is to *learn how an autonomous agent trades*, transparently and on a leash, not to get rich.

---

*Built to be watched, not trusted blindly. Not financial advice.*
