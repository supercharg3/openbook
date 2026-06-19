#!/usr/bin/env bash
# Start all Openbook services locally (Mac / laptop).
# Run this after `bash setup.sh --verify` and filling in .env.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "✗  .venv not found. Run 'bash setup.sh' first."
  exit 1
fi
if [ ! -f ".env" ]; then
  echo "✗  .env not found. Run 'bash setup.sh' first."
  exit 1
fi

. .venv/bin/activate

echo ""
echo "Starting Openbook (paper trading mode)..."
echo "Press Ctrl+C to stop all services."
echo ""

cleanup() {
  echo ""
  echo "Stopping services..."
  kill "$PID_TELEGRAM" "$PID_TRADE" "$PID_DASHBOARD" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

python -m src.run_telegram  &  PID_TELEGRAM=$!
python -m src.run_trade     &  PID_TRADE=$!
python -m src.run_dashboard &  PID_DASHBOARD=$!

echo "  Telegram bot    → check your group for a startup message"
echo "  Trading loop    → running every 60s in paper mode"
echo "  Dashboard       → http://localhost:8080"
echo ""
echo "Send STATUS to your Telegram bot to confirm everything is live."
echo ""

wait
