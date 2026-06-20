# Openbook — Agent Instructions

This file tells AI coding agents (Claude Code, Codex, Cursor, Gemini, etc.) how to work in this
repo. Read it before doing anything else.

---

## If the user says "set up Openbook" (or similar)

Run a warm, adaptive onboarding interview. **Do not skip the interview or go straight to config.**
Read their expertise from how they answer: go slow and explain everything for a first-timer; be
terse and expose the knobs for a pro.

### Hard rules — never break these

1. **Honesty first.** Openbook has no proven edge, runs on paper by default, and can lose money if
   they go live. Say this up front. A lucky run is not proof of anything.
2. **Never ask for API keys.** Stay blind to secrets. For every key, tell them where to get it,
   then say *"open `.env` and paste it on the `XYZ=` line yourself."* You edit everything else;
   you never read or write their keys.
3. **Paper-first, always.** `TRADING_MODE=dry-run` and `ALPACA_PAPER=1`. Real money is a
   separate, deliberate opt-in after a gate. Never enable live trading during setup.
4. **Trade-only keys.** Tell them to create exchange keys with withdrawal disabled. Never touch
   on-chain wallet private keys.

### Step 1 — Frame + consent

Say: "This starts on practice money. No proven edge. It can lose money only if you later choose to
go live. The point is to watch an AI trade transparently and learn. Good to continue?"

### Step 2 — Interview (adapt depth to the user)

Ask conversationally — one or two questions at a time, not a form dump:

- **Risk:** "How would a 30% drop feel — fine, or stressful? Is this money you could lose?"
  Map to: conservative / balanced / aggressive.
- **Goals:** "Here to learn how this works? Build a slow safe experiment? Swing for upside? All?"
- **Assets:** "Comfortable with stocks, crypto, or both?"

### Step 3 — Recommend sleeves (don't enable everything)

Map their answers to a tailored set, explain why each one fits them:

| Profile | Sleeves to enable |
|---|---|
| Conservative / learning / stocks only | Factor (diversified stocks) only |
| Balanced / stocks + crypto | Factor + Pairs (market-neutral crypto) |
| Aggressive / wants upside / both | Add Swing (agentic conviction, one call/day) |
| Highest variance / very active | Also add Degen (crypto momentum, every 15 min — small separate budget, e.g. $300) |
| Pro | List all sleeves, let them choose allocations + universes; keep leverage capped |

Confirm picks before writing to config. Show them the plan (sleeves, budgets) before any file is
touched.

**Alpha channel monitor (optional — ask if aggressive or pro):** "Do you follow any public Telegram
channels with trade signals?" If yes, explain: the monitor watches them automatically, runs a
bear/bull research panel on every signal before anything trades, and WAIT signals set a price watch
that auto-enters when the price arrives. Ask for the channel username (part after `t.me/`), set
`ALPHA_CHANNELS=username` in `.env`. Multiple channels: comma-separated.

### Step 4 — Infrastructure

"This needs to run around the clock. Do you have an always-on machine, or should I walk you through
a ~$6/month cloud server?" Then set up whichever they have (launchd service on Mac, systemd on
Linux/VPS). See `DEPLOYMENT.md` for the VPS guide.

### Step 5 — Keys (paper-first, agent-blind)

Only request keys for the sleeves they chose:

| Key | Needed for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Always (the brain) | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Always (the interface) | @BotFather in Telegram |
| `ALPACA_KEY` + `ALPACA_SECRET` | Stocks (Factor, Swing) | alpaca.markets → Paper Trading |
| `BINANCE_API_KEY` + `BINANCE_API_SECRET` | Crypto (Pairs, Degen) | binance.com → API Management, withdrawal OFF |
| `EXA_API_KEY` | Research + scan features | exa.ai |

For each key: give the link and steps, then say *"paste it into `.env` on the `XYZ=` line
yourself."* Confirm `.env` is in `.gitignore` before proceeding.

### Step 6 — Go-live gate (before any real money)

Explain: "Before real money is even possible, the bot has to prove itself on paper. Let's set your
bar." Tailor to their risk profile — minimum clean trades, minimum track-record length, maximum
tolerable drawdown. Write these to config. Real-money mode stays OFF now.

### Step 7 — Launch + teach

1. Run the test suite: `python -m pytest tests/ -q`
2. Start in paper mode.
3. Send a test Telegram message so they see it live.
4. Walk them through commands hands-on:
   - `STATUS` — what's open, P&L, floor
   - `look into NVDA` — on-demand research
   - `scan` — scan Polymarket + news for ideas
   - `BUY NVDA 5%` / `CLOSE NVDA` — manual override
   - `REPORT weekly` / `REPORT monthly` — performance digests
   - `STOP` — emergency halt
5. Leave a short personalized summary: their sleeves, their gate, how to use it, and the honest
   reminder that it's paper and on a leash.

---

## General coding guidelines

- **Python 3.10+.** All source is in `src/`. Tests are in `tests/`.
- **No secrets in code.** Config lives in `.env` (git-ignored). Read via `src/config.py`.
- **Run tests before committing:** `python -m pytest tests/ -q` — all must pass.
- **Dry-run by default.** `TRADING_MODE=dry-run` means no real orders are placed. Never change
  this to `live` unless the user explicitly requests it and understands the consequences.
- **Systemd services on VPS:** `trading-alpha`, `trading-telegram`, `trading-swing` (timer),
  `trading-degen`. After deploying, restart with `systemctl restart trading-*`.
- **DB is SQLite** at `data/trading.db`. Schema is auto-migrated on startup via `src/database.py`.
- **Never commit `data/`, `.env`, or any `*.db` file.**
