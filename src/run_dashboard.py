"""Entrypoint: start the web dashboard and pin its URL in Telegram.

Private by default — binds to 127.0.0.1:8080. Set DASHBOARD_PUBLIC=1 to open it to the
internet (accessible via http://VPS_IP:DASHBOARD_PORT). Set DASHBOARD_URL to override the
displayed URL (e.g. if you put it behind a reverse-proxy with a domain).
"""
from __future__ import annotations

import asyncio
import os

from .config import get_config
from .dashboard import build_app


def _public_url(cfg) -> str:
    if os.environ.get("DASHBOARD_URL"):
        return os.environ["DASHBOARD_URL"].strip()
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    if os.environ.get("DASHBOARD_PUBLIC", "0") == "1":
        # Try to detect the VPS public IP from the DO metadata endpoint; fall back to a note.
        import urllib.request
        try:
            ip = urllib.request.urlopen(
                "http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address",
                timeout=2,
            ).read().decode().strip()
            return f"http://{ip}:{port}"
        except Exception:
            return f"http://<your-vps-ip>:{port}"
    return f"localhost:{port}  (SSH tunnel: ssh -L {port}:localhost:{port} user@<vps-ip>)"


async def _pin_telegram(text: str, cfg) -> None:
    from telegram import Bot
    kwargs = {}
    if cfg.telegram_topic_id is not None:
        kwargs["message_thread_id"] = cfg.telegram_topic_id
    bot = Bot(cfg.telegram_bot_token)
    msg = await bot.send_message(chat_id=cfg.telegram_chat_id, text=text, **kwargs)
    try:
        await bot.pin_chat_message(chat_id=cfg.telegram_chat_id, message_id=msg.message_id)
    except Exception:
        pass  # pin needs admin rights; silently skip if we don't have them


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()

    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    is_public = os.environ.get("DASHBOARD_PUBLIC", "0") == "1"
    host = "0.0.0.0" if is_public else "127.0.0.1"

    url = _public_url(cfg)
    visibility = "public" if is_public else "private (localhost only)"
    msg = (
        f"📊 Dashboard running — {visibility}\n"
        f"{url}\n\n"
        f"Shows: equity curves, sleeve cards, recent agent decisions. "
        f"Auto-refreshes every 30s."
    )

    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        asyncio.run(_pin_telegram(msg, cfg))
    else:
        print(msg)

    app = build_app(cfg.db_path, cfg)
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
