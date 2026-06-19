"""Entrypoint: build and send the monthly report.

Triggered by trading-monthly-report.timer (1st of month, 08:10 SGT). Also callable on-demand
via `REPORT monthly` in the Telegram bot.
"""
from __future__ import annotations

import asyncio

from .config import get_config
from .database import Database
from .weekly_report import build_monthly_report, format_monthly_report


async def _send(text: str, cfg) -> None:
    from telegram import Bot
    kwargs = {}
    if cfg.telegram_topic_id is not None:
        kwargs["message_thread_id"] = cfg.telegram_topic_id
    await Bot(cfg.telegram_bot_token).send_message(
        chat_id=cfg.telegram_chat_id, text=text, **kwargs
    )


def main() -> None:
    cfg = get_config()
    cfg.ensure_data_dir()
    db = Database(cfg.db_path)
    text = format_monthly_report(build_monthly_report(db, cfg))
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        asyncio.run(_send(text, cfg))
    else:
        print(text)


if __name__ == "__main__":
    main()
