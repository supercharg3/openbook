# AGENTS.md — Openbook Setup Runbook

This file is read automatically by AI coding agents (Claude Code, Codex, Cursor, Gemini, etc.).
**If a user says anything like "set up Openbook", "install this", "help me get started", or "walk
me through setup" — run this runbook top to bottom.** Do not ask them to read docs. Do not skip
steps. Do not improvise commands not listed here.

The goal: zero-to-running in one session, with the user only answering 3 interview questions and
pasting their own API keys. You handle everything else.

---

## Before you start

Check if you have terminal/shell access:
```
bash --version
```
If you are in a chat-only environment with no shell (e.g. a web chatbot), tell the user:
> "I need a coding environment to run setup commands. Open me in Claude Code, Cursor, or Codex
> CLI, point me at the repo folder, and say 'set up Openbook'."
Then stop. Do not proceed without shell access.

---

## Part 1 — The interview (do this first, always)

**Run this as a conversation, not a form.** Ask one or two questions at a time. Read their
expertise from how they answer and adjust your explanations accordingly.

### 1a. Honesty disclaimer (say this verbatim, every time)

> "Quick heads-up before we start: Openbook has no proven edge and runs on practice money by
> default. It can only lose real money if you deliberately switch it to live mode later — and
> there's a gate before that can happen. The value is watching an AI trade transparently so you
> can learn from it and decide if it earns your trust. Good to continue?"

If they say no, stop.

### 1b. Three questions (ask conversationally, not as a list)

**Q1 — Risk**
> "How would a 30% drop feel — fine, or stressful? And is this money you could genuinely afford
> to lose if things go wrong?"

Map to: **conservative** (stressed, can't afford loss) / **balanced** / **aggressive** (fine,
money they can lose).

**Q2 — Goals**
> "What are you here for — learning how an AI trading system works, building a slow safe
> experiment, swinging for big upside, or some mix?"

**Q3 — Assets**
> "Are you comfortable with stocks, crypto, or both?"

### 1c. Recommend sleeves (explain why, then confirm before touching anything)

Based on their answers, pick from this table and explain each choice in one sentence:

| Profile | Sleeves |
|---|---|
| Conservative / learning / stocks | `factor` only |
| Balanced, both assets | `pairs` + `factor` |
| Aggressive, wants upside | `pairs` + `factor` + `swing` |
| Highest variance, very active | All of the above + `degen` |
| Pro (knows what they want) | Let them choose; list all options |

Show them the plan:
> "Here's what I'll set up: [list sleeves]. [One sentence on what each does and why it fits them.]
> Capital budgets: [list amounts]. Does this feel right, or want to adjust anything?"

Wait for confirmation before writing any files.

**Alpha channel monitor** — ask if they picked aggressive or pro:
> "Do you follow any public Telegram channels with trade signals? The alpha monitor can watch
> them automatically — every signal goes through a research panel before anything trades, so it's
> not blindly copying calls. What's the channel username?"

If yes, note the channel username. If no, skip.

---

## Part 2 — Check what's already there

Run these before doing anything else:

```bash
python3 --version
```
Need 3.9+. If missing, tell them to install Python first (python.org/downloads or `brew install python3` on Mac).

```bash
ls .env 2>/dev/null && echo "exists" || echo "missing"
```
If `.env` exists, ask: "Looks like `.env` already exists — did you start setup before? Should I
continue from where you left off, or start fresh?"

---

## Part 3 — Run setup

```bash
bash setup.sh
```

Expected output ends with: `Setup complete. Fill in .env, then run: bash setup.sh --verify`

**If it fails:**
- `Python 3.9+ required` → tell them to install Python first
- `pip: command not found` → run `python3 -m ensurepip --upgrade`
- Any other error → paste the last 5 lines to the user and ask them to confirm before retrying

After setup runs, open `.env`:
```bash
cat .env
```

You will now fill in `.env` values based on the interview. **Do this for every non-secret value
yourself.** For API keys, tell the user exactly where to get each one and say: *"Open `.env` and
paste it on the `KEY_NAME=` line yourself. I will never ask to see it."*

---

## Part 4 — Configure .env

### 4a. Sleeves (write this yourself based on the interview)

```bash
# Write the SLEEVES_ENABLED line based on what you agreed in the interview.
# Options: pairs, factor, factor-ai, swing, degen
# Example for balanced profile: pairs,factor
```

Edit `.env` and set `SLEEVES_ENABLED=` to the agreed sleeve list.

### 4b. Capital budgets (write these yourself, confirm amounts with user first)

Default budgets are in `.env.example`. Adjust based on what they said in the interview:
- Conservative → keep defaults or lower them
- Aggressive → they may want to raise `SWING_BUDGET_USD` or `DEGEN_BUDGET_USD`

Tell them: "These are practice (paper) amounts — no real money required to start."

### 4c. Alpha channel (write this yourself if they said yes in the interview)

```bash
# Set ALPHA_CHANNELS= to the channel username they gave you
# Example: ALPHA_CHANNELS=paste_trade
```

### 4d. API keys — user pastes these themselves

Tell the user one at a time, in this order (only the keys their chosen sleeves need):

**Always required:**

1. **Anthropic key** (the AI brain)
   > "Go to console.anthropic.com → API keys → Create key. Copy it and paste it on the
   > `ANTHROPIC_API_KEY=` line in `.env`."

2. **Telegram bot** (the interface)
   > "Open Telegram and message @BotFather. Send `/newbot`, give it a name (e.g. 'My Trading Bot'),
   > pick a username ending in `bot`. It gives you a token — paste it on `TELEGRAM_BOT_TOKEN=`."
   >
   > "Then message @BotFather again: `/setprivacy` → pick your bot → Disable. This lets you
   > send commands without @-mentioning the bot every time."
   >
   > "Then create a Telegram group, add your bot to it, and go to Group Settings → Topics → enable
   > it. Make the bot an Admin (Settings → Administrators → add it). Create a topic called
   > 'Trading'. Leave `TELEGRAM_CHAT_ID` and `TELEGRAM_TOPIC_ID` blank for now — the bot reports
   > them to you on first run."

**If they have stock sleeves (factor, swing):**

3. **Alpaca paper keys** (stock trading, practice money)
   > "Go to alpaca.markets → sign up for free → Paper Trading section → API Keys → Generate.
   > The Key ID starts with 'PK'. Paste Key ID on `ALPACA_API_KEY_ID=` and Secret on
   > `ALPACA_API_SECRET=`. Then set `ALPACA_ENABLED=1` — I'll do that for you."

   Set `ALPACA_ENABLED=1` in `.env` yourself.

**If they have crypto sleeves (pairs, degen, swing):**

4. **Binance API key** (crypto trading — safety-critical step, go slow)
   > "This is the most important safety step. Go to binance.com → Profile → API Management →
   > Create API → 'System generated'. Name it 'trading-bot'.
   >
   > Permissions — set EXACTLY these:
   > ✅ Enable Reading
   > ✅ Enable Futures
   > ❌ Enable Withdrawals — leave OFF (this is critical)
   > ❌ Enable Spot & Margin Trading — leave OFF
   >
   > If you're running on a server (not your laptop), restrict the key to that server's IP address.
   >
   > Copy the API Key and Secret immediately — the secret is only shown once. Paste them on
   > `BINANCE_API_KEY=` and `BINANCE_API_SECRET=`."

**Optional (for research/scan features):**

5. **Exa key** (news search — powers `look into NVDA` and `scan`)
   > "Go to exa.ai → sign up → API Keys → copy it → paste on `EXA_API_KEY=`."

After each key is pasted, confirm: "Done? I'll move on."

---

## Part 5 — Verify and launch

```bash
bash setup.sh --verify
```

Expected: all tests pass, ends with "Ready to run."

**If tests fail:**
- `ImportError: No module named X` → run `pip install -r requirements.txt` then retry
- `ANTHROPIC_API_KEY not set` → the user hasn't pasted it yet, prompt them
- Any Binance/Alpaca error → check they set the keys correctly
- Other failures → paste the exact error to the user, diagnose together

Once tests pass:

```bash
bash start.sh
```

This starts the Telegram bot, trading loop, and dashboard together. Within ~30 seconds the bot
posts a startup message in their Telegram group.

Tell the user: "Now send `STATUS` to your Telegram bot. You should get a snapshot back with your
sleeves, capital, and open positions (all empty at first — that's normal)."

If they don't get a reply:
- Check `TELEGRAM_BOT_TOKEN` is correct (copy it fresh from @BotFather with `/mybots`)
- Check the bot is an Admin in the group
- Check they're messaging in the Trading topic, not the general chat

### 5a. Grab the Telegram IDs

The first time someone messages the bot, it replies with:
> "📌 This chat's id is -100XXXXXXXXX. This topic's id is YY."

Tell the user: "Paste those into `.env` — `TELEGRAM_CHAT_ID=-100XXXXXXXXX` and
`TELEGRAM_TOPIC_ID=YY`. Then restart with:"

```bash
bash start.sh
```

Now all bot messages are scoped to that one topic only.

---

## Part 6 — Teach the commands

Walk them through each command hands-on — tell them to try it now, then explain the output:

1. **`STATUS`** → "Shows what's open, your P&L, and the floor for each sleeve. Try it."
2. **`look into NVDA`** → "I research it — bull case, bear case, risk verdict. Try any ticker."
3. **`scan`** → "I scan Polymarket + recent news for trade ideas and stress-test them."
4. **`BUY NVDA 5%`** / **`CLOSE NVDA`** → "Manual override — you can queue a trade or close one."
5. **`REPORT weekly`** → "Performance digest — try it now to see the format (will be sparse today)."
6. **`STOP`** → "Emergency halt — closes nothing, just stops new trades. Use if something looks wrong."

---

## Part 7 — Set the go-live gate

Even though they won't go live today, set the gate now so the bar is clear:

> "Before real money is possible, the bot needs to prove itself on paper. Let's set your bar
> now — you can always raise it later."

Tailor to their risk profile:
- Conservative → ≥20 complete trades, ≥30 days track record, max 10% drawdown
- Balanced → ≥10 complete trades, ≥14 days, max 15% drawdown
- Aggressive → ≥10 complete trades, ≥7 days, max 20% drawdown

Write these into `.env` (or config if there's a gate config file). Tell them:
> "The bot will message you automatically when this gate clears. Going live is then a separate
> deliberate step — you set `TRADING_MODE=live` and `ALLOW_LIVE_ORDERS=1` yourself."

---

## Part 8 — Running 24/7 (ask if they want this now)

> "Right now it only runs while your computer is on. Want to set it up on a $6/month cloud server
> so it runs around the clock and sends you reports at 8am every day?"

If yes:
> "Follow DEPLOYMENT.md — it's a step-by-step guide for DigitalOcean. Takes about 60–90 minutes
> the first time. When you're ready, say 'help me deploy to a server' and I'll walk through it
> with you command by command."

If no:
> "No problem. Run `bash start.sh` whenever you want it running. The daily reports go out at 8am
> only when the process is alive, so you'll miss them on days it's not running."

---

## Part 9 — End of session summary

Give them a short, personalised summary:

> "Here's what we set up:
> - Sleeves: [list]
> - Capital: [amounts] (all practice money)
> - Go-live gate: [their criteria]
> - Commands to remember: STATUS · look into X · STOP
> - To run it: bash start.sh
>
> It's on paper, it's on a leash, and going live is a separate decision you make only if it earns
> your trust. Any questions before I go?"

---

## Troubleshooting reference (use when things break, don't ask the user to read this)

| Symptom | Fix |
|---|---|
| Bot doesn't reply | Check `TELEGRAM_BOT_TOKEN`. Run `bash start.sh` and look for startup errors. |
| `STATUS` returns nothing | Bot is running but wrong chat/topic — grab IDs as in Step 5a |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside the `.venv` |
| `ANTHROPIC_API_KEY not set` | User hasn't pasted the key; prompt them again |
| Binance `Invalid API-key` | Key permissions wrong or not yet active (takes 5 min after creation) |
| Alpaca `forbidden` | Using live keys instead of paper keys — they need the PK... key ID |
| Tests pass but no trades | Normal — pairs sleeve waits for a z-score signal. Check STATUS in 15–60 min. |
| `swing_cash` warnings | Normal on first run — swing sleeve initialises its budget on first cycle |

---

## General coding rules (for code changes, not setup)

- All source is in `src/`. Tests are in `tests/`. Run `python -m pytest tests/ -q` before committing.
- Config lives in `.env` (git-ignored). Never hardcode secrets or paths.
- `TRADING_MODE=dry-run` means no real orders. Never change this to `live` unless the user explicitly asks after understanding the risk.
- Systemd services on VPS: `trading-loop`, `trading-telegram`, `trading-swing` (timer), `trading-degen`, `trading-alpha`. After code changes: `systemctl restart trading-telegram trading-alpha` etc.
- DB is SQLite at `data/trading.db`, auto-migrated on startup via `src/database.py`.
- Never commit `data/`, `.env`, or `*.db`.
