"""Entry point for the alpha channel monitor service.

Polls configured public Telegram channels every 5 minutes.
No Telegram account or credentials needed — reads the public t.me/s/ web page.
"""
from __future__ import annotations

from .config import get_config
from .database import Database
from .alpha_monitor import run_loop


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()

    if not cfg.alpha_channels:
        print("[alpha] ALPHA_CHANNELS not set — nothing to monitor."); return

    db = Database(cfg.db_path)
    channels = [c.strip().lstrip("@") for c in cfg.alpha_channels.split(",") if c.strip()]
    print(f"[alpha] starting monitor for: {channels}")

    run_loop(channels, cfg, db)


if __name__ == "__main__":
    main()
