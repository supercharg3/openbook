"""Entry point for the alpha channel monitor service.

Runs as a long-lived systemd service (not a timer). Stays connected to Telegram
and processes incoming messages from configured alpha channels in real time.
"""
from __future__ import annotations

import asyncio
from .config import get_config
from .database import Database
from .alpha_monitor import monitor


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()

    if not cfg.alpha_channels:
        print("[alpha] ALPHA_CHANNELS not set — nothing to monitor."); return

    db = Database(cfg.db_path)
    channels = [c.strip().lstrip("@") for c in cfg.alpha_channels.split(",") if c.strip()]
    print(f"[alpha] starting monitor for: {channels}")

    asyncio.run(monitor(cfg, db, channels))


if __name__ == "__main__":
    main()
