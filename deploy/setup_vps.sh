#!/usr/bin/env bash
# One-shot VPS provisioning for the AI trading system.
# Target: DigitalOcean Droplet, Ubuntu 22.04, Singapore region.
# Run as a sudo-capable user. Idempotent where practical.
set -euo pipefail

echo "==> 0. Create trader user (if not already present)"
if ! id "trader" &>/dev/null; then
  sudo adduser --disabled-password --gecos "" trader
  echo "    trader user created."
else
  echo "    trader user already exists."
fi

# Copy project files to trader's home if running as root
if [ "$(whoami)" = "root" ] && [ "$(pwd)" != "/home/trader/ai-trading-system" ]; then
  cp -r "$(pwd)" /home/trader/ai-trading-system
  chown -R trader:trader /home/trader/ai-trading-system
  echo "    Project copied to /home/trader/ai-trading-system"
fi

echo "==> 1. Timezone → Asia/Singapore (so the 8am report timer fires in SGT)"
sudo timedatectl set-timezone Asia/Singapore

echo "==> 2. System packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git ufw fail2ban curl
# Note: no Docker needed — all four strategy layers run natively in the Python process.

echo "==> 3. Firewall — SSH + HTTPS only (FreqUI stays bound to localhost)"
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo "==> 4. fail2ban (brute-force protection on SSH)"
sudo systemctl enable --now fail2ban

echo "==> 5. Python venv + deps"
cd "$(dirname "$0")/.."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo "==> 6. Data dir + SQLite init"
mkdir -p data
./.venv/bin/python -c "from src.config import get_config; from src.database import Database; Database(get_config().db_path); print('SQLite ready')"

echo "==> 7. systemd services"
SERVICES=(
  trading-loop.service
  trading-telegram.service
  trading-report.service trading-report.timer
  trading-weekly-report.service trading-weekly-report.timer
  trading-monthly-report.service trading-monthly-report.timer
  trading-dashboard.service
  trading-stock-factor.service trading-stock-factor.timer
  trading-swing.service trading-swing.timer
  trading-degen.service trading-degen.timer
  trading-alpha.service
  trading-rescreen.service trading-rescreen.timer
)
for svc in "${SERVICES[@]}"; do
  [ -f "deploy/$svc" ] && sudo cp "deploy/$svc" /etc/systemd/system/
done
sudo systemctl daemon-reload

# Core (always enabled)
sudo systemctl enable --now trading-loop.service
sudo systemctl enable --now trading-telegram.service
sudo systemctl enable --now trading-report.timer
sudo systemctl enable --now trading-weekly-report.timer
sudo systemctl enable --now trading-monthly-report.timer
sudo systemctl enable --now trading-dashboard.service

# Stock + swing (enable if Alpaca keys are set)
if grep -q "^ALPACA_API_KEY_ID=." .env 2>/dev/null; then
  sudo systemctl enable --now trading-stock-factor.timer
  sudo systemctl enable --now trading-swing.timer
  echo "    Alpaca detected — stock + swing sleeves enabled."
fi

# Degen (enable if Binance keys are set)
if grep -q "^BINANCE_API_KEY=." .env 2>/dev/null; then
  sudo systemctl enable --now trading-degen.timer
  echo "    Binance detected — degen sleeve enabled (15-min cycle)."
fi

# Alpha monitor (enable if Telegram API credentials and channels are set)
if grep -q "^TELEGRAM_API_ID=." .env 2>/dev/null && grep -q "^ALPHA_CHANNELS=." .env 2>/dev/null; then
  sudo systemctl enable --now trading-alpha.service
  echo "    Alpha monitor enabled — watching configured Telegram channels."
fi

# Rescreen (bi-weekly basket refresh)
if [ -f "deploy/trading-rescreen.timer" ]; then
  sudo systemctl enable --now trading-rescreen.timer
fi

cat <<'EOF'

==> DONE. Remaining manual steps:
    1. Fill in .env (Telegram token; Claude+Exa keys; Binance key only before going live)
    2. SSH-key-only login: set 'PasswordAuthentication no' in /etc/ssh/sshd_config, then
       sudo systemctl restart ssh
    3. Add an UptimeRobot HTTP(s) monitor against this droplet; alert to Telegram.
    4. Confirm the startup banner arrives in Telegram (MODE: DRY-RUN).
    5. Dashboard: private by default (localhost:8080). To open it:
         DASHBOARD_PUBLIC=1 in .env + sudo ufw allow 8080/tcp
       Or SSH tunnel: ssh -L 8080:localhost:8080 root@<vps-ip>

    DO NOT set TRADING_MODE=live until the paper-trading gate is cleared.
EOF
