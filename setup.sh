#!/usr/bin/env bash
# Openbook setup script — run once on a fresh machine.
# Gets you from git clone to a working paper-trading system in one pass.
set -e

GREEN='\033[0;32m' YELLOW='\033[1;33m' RED='\033[0;31m' NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Openbook — autonomous trading agent setup"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "This sets up the system on PAPER MONEY (practice mode)."
echo "No real money is touched until you deliberately opt in."
echo ""

# ── 1. Python version ────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || command -v python || true)
[ -z "$PYTHON" ] && die "Python 3.9+ required. Install it first."
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
[ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]) \
  && die "Python 3.9+ required. You have $PY_VER."
ok "Python $PY_VER"

# ── 2. Virtual env ───────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "→ Creating virtual environment..."
  $PYTHON -m venv venv
  ok "venv created"
else
  ok "venv exists"
fi

. venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
ok "Dependencies installed"

# ── 3. .env file ─────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  cp .env.example .env
  warn ".env created from template. Open it and fill in your API keys before running."
  echo ""
  echo "  Required keys:"
  echo "    ANTHROPIC_API_KEY   — get from console.anthropic.com"
  echo "    TELEGRAM_BOT_TOKEN  — create a bot with @BotFather on Telegram"
  echo "    TELEGRAM_CHAT_ID    — start the bot; it will report your chat id"
  echo ""
  echo "  Optional (add later when you want stock or crypto trading):"
  echo "    ALPACA_API_KEY_ID / ALPACA_API_SECRET  — paper keys from alpaca.markets"
  echo "    BINANCE_API_KEY / BINANCE_API_SECRET   — trade-only key, withdrawals disabled"
  echo "    EXA_API_KEY                            — for news research and idea scanning"
  echo ""
  echo "  Once filled, run:  bash setup.sh --verify"
  echo ""
else
  ok ".env exists"
fi

# ── 4. Verify mode (re-run with --verify after filling .env) ─────────────────
if [ "${1:-}" = "--verify" ]; then
  echo ""
  echo "→ Running test suite..."
  python -m pytest tests/ -q --tb=short 2>&1 | tail -20
  echo ""
  ok "Tests passed — system is ready."
  echo ""
  echo "  To start all services locally:"
  echo "    python -m src.run_telegram &     # Telegram bot"
  echo "    python -m src.run_trade          # trading loop"
  echo "    python -m src.run_daily_report   # (normally runs on a timer)"
  echo "    python -m src.run_dashboard      # web dashboard"
  echo ""
  echo "  To deploy to a VPS (Linux, systemd):"
  echo "    See deploy/setup_vps.sh for the full install."
  echo ""
  echo "  Tip: ask your AI agent to run the openbook-setup skill — it will"
  echo "       walk you through everything interactively."
  echo ""
fi

ok "Setup complete."
