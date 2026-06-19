# Deployment Runbook

Do these in order. Nothing here risks money — the system stays in **paper trading** (dry-run)
until the readiness gate is cleared. Estimated time: ~60–90 minutes.

Legend: 🖥️ = on your Mac · ☁️ = on the droplet (via SSH) · 🌐 = in a browser

---

## Phase 1 — Telegram bot + topics group (10 min) 🌐

The bot reports to you and takes your overrides, scoped to a **Trading topic** inside a group.

**1a. Create the bot:**
1. In Telegram, open **@BotFather** → `/newbot`. Name it (e.g. "Satay Trading"), username ending
   in `bot` (e.g. `satay_trading_bot`).
2. Copy the **token** it gives you → `TELEGRAM_BOT_TOKEN`.

**1b. Let the bot read plain commands** (so `STOP` works without @-mentioning it):
3. @BotFather → `/setprivacy` → pick your bot → **Disable**.

**1c. Create the group with topics:**
4. New Group → add your bot → create the group.
5. Group settings → enable **Topics**. Then make the bot an **Admin** (group settings →
   Administrators → add your bot).
6. Create a topic called **Trading**.

**Note:** the chat id and topic id are discovered automatically at deploy time — you leave both
blank in `.env` at first, send the bot a message in the Trading topic, and it replies with both
ids to paste in. So nothing more to collect here.

✅ You now have: `TELEGRAM_BOT_TOKEN` (chat + topic ids come during Phase 7)

---

## Phase 2 — Claude + Exa keys (5 min) 🌐

2a. **Claude:** console.anthropic.com → API Keys → Create Key → copy → `ANTHROPIC_API_KEY`.
    (You already use Claude, so you may have one.)

2b. **Exa:** dashboard.exa.ai → sign up → API Keys → copy → `EXA_API_KEY`.
    (Free tier is plenty at our 2-polls/hour rate.)

✅ You now have: `ANTHROPIC_API_KEY`, `EXA_API_KEY`

---

## Phase 3 — DigitalOcean droplet (15 min) 🌐☁️

3a. **Create account:** digitalocean.com → sign up → add a payment method. Turn on 2FA
    (Account → Security).

3b. **Create the droplet:**
   - Create → Droplets
   - Region: **Singapore (SGP1)**
   - Image: **Ubuntu 22.04 (LTS) x64**
   - Size: **Basic → Regular → $6/mo** (1 GB / 1 vCPU)
   - Authentication: **SSH Key** (recommended). Click "New SSH Key" and follow the panel —
     on your Mac, in Terminal:
     ```bash
     cat ~/.ssh/id_ed25519.pub   # if this errors, first run: ssh-keygen -t ed25519
     ```
     Paste that whole line into DigitalOcean. (Password auth also works but SSH key is safer.)
   - Hostname: `trading`
   - Click **Create Droplet**. After ~30s you get an **IP address** (e.g. `203.0.113.45`). Copy it.

3c. **Log in to confirm it works:** 🖥️
   ```bash
   ssh root@203.0.113.45        # use YOUR droplet IP
   ```
   You should land in a `root@trading:~#` prompt. Type `exit` for now.

✅ You now have: the **droplet IP**

---

## Phase 4 — Binance account + trade-only API key (15 min) 🌐

⚠️ **The single most important safety step.** The key must NOT be able to withdraw.

4a. **Account:** binance.com → sign up (or log in). Enable **2FA with an authenticator app**
    (not SMS). Complete identity verification if prompted (needed to trade futures).

4b. **Enable Futures:** open the Futures section once and accept the agreement (this activates
    the futures wallet the bot trades in).

4c. **Create the API key:** Profile → API Management → Create API → "System generated".
   - Label it `trading-bot`.
   - **Permissions — set EXACTLY these:**
     - ✅ Enable Reading
     - ✅ Enable Futures
     - ❌ **Enable Withdrawals → leave OFF** (this is the critical one)
     - ❌ Enable Spot & Margin Trading → off for now (we trade futures)
   - **Restrict access to trusted IPs only** → paste your **droplet IP** from Phase 3.
   - Copy the **API Key** and **Secret Key** immediately (the secret is shown once).

✅ You now have: `BINANCE_API_KEY`, `BINANCE_API_SECRET` (trade-only, IP-locked)

---

## Phase 5 — Get the code onto the droplet (10 min) 🖥️☁️

5a. **Copy the project up** (from your Mac, simplest method — rsync over SSH):
   ```bash
   rsync -avz --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
     ~/openbook/ root@<your-vps-ip>:/root/openbook/
   ```
   (Re-run this same command anytime you change code locally and want to update the droplet.)

5b. **Create a non-root user** to run the bot (the systemd units expect user `trader`): ☁️
   ```bash
   ssh root@203.0.113.45
   adduser --disabled-password --gecos "" trader
   cp -r /root/ai-trading-system /home/trader/ai-trading-system
   chown -R trader:trader /home/trader/ai-trading-system
   ```

---

## Phase 6 — Configure secrets (5 min) ☁️

On the droplet:
```bash
cd /home/trader/ai-trading-system
cp .env.example .env
nano .env        # paste in every value you collected above
```
Fill in: the 2 Binance values, `TELEGRAM_BOT_TOKEN`, Claude, Exa.
**Leave `TELEGRAM_CHAT_ID` and `TELEGRAM_TOPIC_ID` BLANK** — the bot fills these for you in Phase 7.
**Leave `TRADING_MODE=dry-run`** — do not change this yet.
Save in nano: `Ctrl+O`, `Enter`, `Ctrl+X`. Then lock the file down:
```bash
chmod 600 .env
chown trader:trader .env
```

---

## Phase 7 — Run setup + go live in PAPER mode (10 min) ☁️

```bash
cd /home/trader/ai-trading-system
sudo bash deploy/setup_vps.sh
```
This installs Python deps, the firewall, fail2ban, the SQLite DB, and the three systemd services
(trading loop, Telegram listener, 8am report).

Then **harden SSH** (the script reminds you):
```bash
sudo nano /etc/ssh/sshd_config      # set:  PasswordAuthentication no
sudo systemctl restart ssh
```

Start Freqtrade (the technical-strategy engine) in dry-run:
```bash
cd /home/trader/ai-trading-system/freqtrade && docker compose up -d
```

✅ **Verification + grab the two ids:**
1. In the **Trading topic**, send the bot any message (e.g. `STATUS`).
2. It replies with a 📌 note containing **this chat's id** (a negative number) and **this topic's id**.
3. Put them into `.env`:
   ```bash
   nano .env      # set TELEGRAM_CHAT_ID=-100...  and  TELEGRAM_TOPIC_ID=<topic id>
   sudo systemctl restart trading-loop trading-telegram
   ```
4. Now you should get **`MODE: DRY-RUN — paper trading active`** posted *into the Trading topic*.
   Send `STATUS` and `REPORT` — replies should land in that topic only. The whole pipeline is
   live (on fake money), scoped to your Trading topic.

---

## Phase 8 — Backtest the strategies (15 min) ☁️

Keep only strategies that pass the gate (Sharpe > 1.5, drawdown < 15%):
```bash
cd /home/trader/ai-trading-system/freqtrade
docker compose run --rm freqtrade download-data --days 200 -t 15m
docker compose run --rm freqtrade backtesting --strategy RsiTrendStrategy --timerange 20251201-
docker compose run --rm freqtrade backtesting --strategy EmaCrossStrategy --timerange 20251201-
docker compose run --rm freqtrade backtesting --strategy BreakoutStrategy --timerange 20251201-
```
Note the Sharpe + max drawdown for each. Disable any that fail (tell me the numbers and I'll
adjust the config to run only the winners).

---

## Phase 9 — Add monitoring + let it run (5 min, then wait) 🌐

9a. **UptimeRobot** (free): uptimerobot.com → add monitor → type "Ping", host = your droplet IP →
    alert contact = (optional) email/Telegram. Tells you if the droplet ever goes down.

9b. **Let paper trading run.** Read the 8am report daily. The gate to go live with real $500:
    ≥30 trades, positive expectancy, Sharpe > 1.2, max drawdown < 15%, profit factor > 1.3,
    overrides confirmed working, ≥14 days. When it clears, we flip `TRADING_MODE=live` together.

---

## Daily commands (reference) ☁️
```bash
sudo systemctl status trading-loop       # is the loop running?
journalctl -u trading-loop -f            # live logs
sudo systemctl restart trading-loop      # restart after a code update
```

## Accessing the dashboard

The dashboard runs on port 8080, private by default (`DASHBOARD_PUBLIC=0`). Three ways to reach it:

### Option A — Tailscale (recommended, no domain needed)
Tailscale creates a private network between your devices. No open ports, no passwords, works from your phone too.

1. **On the droplet:**
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up
   ```
   Open the link it prints and sign in at tailscale.com.

2. **On your Mac/phone:** download from tailscale.com/download, sign in with the same account.

3. **Get the droplet's Tailscale IP:**
   ```bash
   tailscale ip -4   # e.g. 100.x.x.x
   ```

4. Open `http://100.x.x.x:8080` in your browser. Bookmark it. Done.

### Option B — Cloudflare Tunnel (best if you have a domain)
Gives you a real `https://dashboard.yourdomain.com` URL, HTTPS included, protected behind Cloudflare Access (Google login). Free. Requires a domain pointed at Cloudflare.

```bash
# On the droplet:
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared -y
cloudflared tunnel login
cloudflared tunnel create openbook
cloudflared tunnel route dns openbook dashboard.yourdomain.com
cloudflared tunnel run --url http://localhost:8080 openbook
```

### Option C — SSH tunnel (no installs, one-off access)
```bash
ssh -L 8080:localhost:8080 root@<your-vps-ip> -N
```
Then open `http://localhost:8080`. Close the terminal to disconnect.

---

## Going live later (DO NOT do this until the gate clears)
1. Set `TRADING_MODE=live` and `ALLOW_LIVE_ORDERS=1` in `.env`
2. Fund $500 USDT into the Binance **Futures** wallet
3. `sudo systemctl restart trading-loop` → confirm the `MODE: LIVE — $500 at risk` banner
