"""Telegram bot — thin I/O wrapper around reporting.py + controller.py.

Runs as its own systemd service (Restart=on-failure) so a crash auto-recovers. It:
  - listens for override commands from the user's chat only (chat-id allowlist)
  - dispatches them to the SystemController
  - sends the startup mode banner and the 8am daily report

The dispatch logic (`handle_command`) is pure and unit-tested; only `run()` touches the network,
so this file imports python-telegram-bot lazily.
"""
from __future__ import annotations

from typing import Callable

from .controller import SystemController
from .reporting import Command, ParsedCommand, parse_command


def should_handle_chat(incoming_chat_id: str, configured_chat_id: str | None) -> bool:
    """True if the message is from the allowlisted chat. Blank config = discovery mode (allow)."""
    if not configured_chat_id:
        return True
    return incoming_chat_id == configured_chat_id


def chat_discovery_note(incoming_chat_id: str, configured_chat_id: str | None) -> str | None:
    if configured_chat_id:
        return None
    return (f"📌 This chat's id is {incoming_chat_id}. Add TELEGRAM_CHAT_ID={incoming_chat_id} "
            "to .env (the group id is negative — that's expected).")


def should_handle_topic(incoming_thread_id: int | None, configured_topic_id: int | None) -> bool:
    """True if a message from `incoming_thread_id` is in scope.

    When no topic is configured (discovery mode), respond everywhere so the bot can report its
    topic id. Once a topic is set, only respond inside that exact topic.
    """
    if configured_topic_id is None:
        return True
    return incoming_thread_id == configured_topic_id


def topic_discovery_note(incoming_thread_id: int | None, configured_topic_id: int | None) -> str | None:
    """In discovery mode, tell the user this topic's id so they can lock the bot to it."""
    if configured_topic_id is not None:
        return None
    if incoming_thread_id is None:
        return ("📌 This is a plain chat (no topic). To scope me to a forum topic, message me "
                "inside it. Otherwise leave TELEGRAM_TOPIC_ID blank.")
    return (f"📌 This topic's id is {incoming_thread_id}. Add TELEGRAM_TOPIC_ID={incoming_thread_id} "
            "to .env and restart to lock me to this topic.")


def handle_command(
    text: str,
    controller: SystemController,
    status_provider: Callable[[], str],
    report_provider: Callable[[], str],
    assistant_provider: Callable[[str], str] | None = None,
    basket_approve: Callable[[], str] | None = None,
    run_swing: Callable[[], str] | None = None,
    clear_queue: Callable[[], str] | None = None,
) -> str:
    """Map an incoming message to a controller action and return the reply text.

    A message that is not a fixed command is treated as a free-form question and routed to the
    conversational assistant (if one is wired); otherwise it falls back to a help line.
    """
    parsed: ParsedCommand = parse_command(text)

    if parsed.command == Command.STOP:
        return controller.stop()
    if parsed.command == Command.CLOSE_ALL:
        return controller.close_all()
    if parsed.command == Command.PAUSE:
        return controller.pause(parsed.argument)
    if parsed.command == Command.RESUME:
        return controller.resume()
    if parsed.command == Command.REDUCE_RISK:
        return controller.reduce_risk()
    if parsed.command == Command.STATUS:
        return status_provider()
    if parsed.command == Command.REPORT:
        return report_provider(parsed.argument)
    if parsed.command == Command.APPROVE_BASKET:
        return basket_approve() if basket_approve else "Basket approval not available."
    if parsed.command == Command.RUN_SWING:
        return run_swing() if run_swing else "Swing runner not available."
    if parsed.command == Command.CLEAR_QUEUE:
        return clear_queue() if clear_queue else "Clear queue not available."
    if assistant_provider is not None:
        return assistant_provider(text)        # free-form question → Claude, grounded in real data
    return (
        "Commands: STOP, CLOSE ALL, PAUSE <layer>, RESUME, STATUS, REDUCE RISK, REPORT. "
        "You can also just ask me a question."
    )


class TelegramInterface:
    """Live Telegram wiring. Constructed by the orchestrator with the providers it owns."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        controller: SystemController,
        status_provider: Callable[[], str],
        report_provider: Callable[[str | None], str],
        topic_id: int | None = None,
        assistant_provider: Callable[[str], str] | None = None,
        basket_approve: Callable[[], str] | None = None,
        run_swing: Callable[[], str] | None = None,
        clear_queue: Callable[[], str] | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id else None   # None = discovery mode
        self.controller = controller
        self.status_provider = status_provider
        self.report_provider = report_provider
        self.topic_id = topic_id
        self.assistant_provider = assistant_provider
        self.basket_approve = basket_approve
        self.run_swing = run_swing
        self.clear_queue = clear_queue
        self._app = None

    def _build_app(self):
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            ContextTypes,
            MessageHandler,
            filters,
        )

        app = ApplicationBuilder().token(self.bot_token).build()

        async def on_message(update: "Update", _ctx: "ContextTypes.DEFAULT_TYPE") -> None:
            import asyncio

            incoming_chat = str(update.effective_chat.id)
            thread_id = update.message.message_thread_id
            # Allowlist (enforced once configured; open during discovery so the bot can report ids).
            if not should_handle_chat(incoming_chat, self.chat_id):
                return
            # Topic scoping: ignore other topics once a topic is configured.
            if not should_handle_topic(thread_id, self.topic_id):
                return
            text = update.message.text or ""
            # Free-form messages hit Claude; run them OFF the event loop so the bot stays responsive
            # to STOP/STATUS. Acknowledge ONLY when there'll be a real wait, and match the wording to
            # what actually runs: the bull/bear/risk panel only fires for research ("look into X").
            if parse_command(text).command == Command.UNKNOWN and self.assistant_provider is not None:
                from .thesis import parse_thesis_order
                from .assistant import is_price_request
                from .research import is_research_request
                from .idea_scanner import is_scan_request
                if parse_thesis_order(text) or is_price_request(text):
                    pass                                   # instant (queue order / live price) — no ack
                elif is_scan_request(text):
                    await update.message.reply_text(
                        "🔎 Scanning Polymarket + the news for ideas and stress-testing them, "
                        "this takes a few minutes...", message_thread_id=thread_id)
                elif is_research_request(text):
                    await update.message.reply_text(
                        "🔍 Researching that, up to a minute (bull / bear / risk panel weighing in)...",
                        message_thread_id=thread_id)
                else:
                    await update.message.reply_text("💭 thinking...", message_thread_id=thread_id)
            reply = await asyncio.to_thread(
                handle_command,
                text,
                self.controller,
                self.status_provider,
                self.report_provider,
                self.assistant_provider,
                self.basket_approve,
                self.run_swing,
                self.clear_queue,
            )
            notes = [
                n for n in (
                    chat_discovery_note(incoming_chat, self.chat_id),
                    topic_discovery_note(thread_id, self.topic_id),
                ) if n
            ]
            if notes:
                reply = "\n".join(notes) + "\n\n" + reply
            # Only STATUS uses HTML (for bold) — it's the one message we build with markup. Everything
            # else stays plain so untrusted text (news, answers) can never break message sending.
            parse_mode = "HTML" if parse_command(text).command == Command.STATUS else None
            await update.message.reply_text(reply, message_thread_id=thread_id, parse_mode=parse_mode)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
        return app

    async def send(self, text: str) -> None:
        """Push a message (startup banner, daily report, alerts) to the user's trading topic."""
        from telegram import Bot

        kwargs = {}
        if self.topic_id is not None:
            kwargs["message_thread_id"] = self.topic_id
        await Bot(self.bot_token).send_message(chat_id=self.chat_id, text=text, **kwargs)

    def run(self) -> None:
        """Blocking polling loop (the systemd service entrypoint)."""
        self._app = self._build_app()
        self._app.run_polling()
