import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.controller import SGT, SystemController  # noqa: E402
from src.reporting import (  # noqa: E402
    Command,
    DailyReport,
    TradeLine,
    format_daily_report,
    parse_command,
)
from src.telegram_bot import (  # noqa: E402
    chat_discovery_note,
    handle_command,
    should_handle_chat,
    should_handle_topic,
    topic_discovery_note,
)


# ── Command parsing ──────────────────────────────────────────────────────────
def test_parse_simple_commands():
    assert parse_command("stop").command == Command.STOP
    assert parse_command("  Status ").command == Command.STATUS
    assert parse_command("close all").command == Command.CLOSE_ALL
    assert parse_command("reduce risk").command == Command.REDUCE_RISK
    assert parse_command("report").command == Command.REPORT
    assert parse_command("resume").command == Command.RESUME


def test_parse_pause_with_arg():
    p = parse_command("PAUSE trend")
    assert p.command == Command.PAUSE
    assert p.argument == "trend"


def test_parse_pause_without_arg():
    p = parse_command("pause")
    assert p.command == Command.PAUSE
    assert p.argument is None


def test_parse_unknown():
    assert parse_command("buy me lambo").command == Command.UNKNOWN


# ── Controller actions ───────────────────────────────────────────────────────
def test_stop_blocks_new_trades():
    c = SystemController()
    assert c.can_open_new_trade()
    c.stop()
    assert not c.can_open_new_trade()


def test_resume_clears_stop_and_pauses():
    c = SystemController()
    c.stop()
    c.pause("trend")
    c.resume()
    assert c.can_open_new_trade()
    assert not c.is_layer_paused("trend")


def test_pause_specific_layer():
    c = SystemController()
    c.pause("grid")
    assert c.is_layer_paused("grid")
    assert not c.is_layer_paused("trend")


def test_reduce_risk_window():
    c = SystemController()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=SGT)
    c.reduce_risk(now=now)
    assert c.reduce_risk_active(now=now + timedelta(days=3))
    assert not c.reduce_risk_active(now=now + timedelta(days=8))


def test_close_all_sets_flags():
    c = SystemController()
    c.close_all()
    assert c.state.closing_all
    assert not c.can_open_new_trade()


# ── Dispatch (telegram_bot.handle_command) ───────────────────────────────────
def test_handle_command_dispatches_to_controller():
    c = SystemController()
    status_called = {"n": 0}
    report_called = {"n": 0}

    def status():
        status_called["n"] += 1
        return "STATUS_OUT"

    def report():
        report_called["n"] += 1
        return "REPORT_OUT"

    assert "STOPPED" in handle_command("stop", c, status, report)
    assert not c.can_open_new_trade()
    assert handle_command("status", c, status, report) == "STATUS_OUT"
    assert handle_command("report", c, status, report) == "REPORT_OUT"
    assert "Paused" in handle_command("pause news", c, status, report)
    assert c.is_layer_paused("news")
    assert "ask me a question" in handle_command("nonsense", c, status, report)


# ── Report formatting ────────────────────────────────────────────────────────
def test_format_daily_report_contains_key_fields():
    r = DailyReport(
        date_str="2026-06-17",
        capital=512.34,
        capital_change=12.34,
        mtd_pnl_usd=12.34,
        mtd_pnl_pct=2.4,
        trades=[
            TradeLine("BTC/USDT", "long", 60000, 61200, 1.0, "RSI+sentiment", won=True),
            TradeLine("ETH/USDT", "short", 3000, 2940, -0.5, "ADX too low", won=False),
        ],
        open_positions=1,
        open_unrealized=2.0,
        regime="TRENDING",
        adx=34,
        outlook="BTC consolidating above support.",
        daily_loss_used=-3.0,
        daily_loss_limit=15.0,
        mode="dry-run",
    )
    out = format_daily_report(r)
    assert "Daily Report — 2026-06-17" in out
    assert "DRY-RUN" in out
    assert "BTC/USDT LONG" in out
    assert "✅" in out and "❌" in out
    assert "TRENDING" in out


def test_format_daily_report_no_trades():
    r = DailyReport("2026-06-17", 500, 0, 0, 0)
    out = format_daily_report(r)
    assert "Trades (24h): none" in out


# ── Topic / chat scoping ─────────────────────────────────────────────────────
def test_should_handle_topic_scoping():
    # discovery mode (no topic configured) → handle everything
    assert should_handle_topic(5, None)
    assert should_handle_topic(None, None)
    # configured → only the matching topic
    assert should_handle_topic(5, 5)
    assert not should_handle_topic(7, 5)
    assert not should_handle_topic(None, 5)


def test_topic_discovery_note():
    # discovery mode reports the id
    assert "5" in topic_discovery_note(5, None)
    assert "TELEGRAM_TOPIC_ID=5" in topic_discovery_note(5, None)
    # once configured, no note
    assert topic_discovery_note(5, 5) is None
    # plain chat, no topic
    assert "plain chat" in topic_discovery_note(None, None)


def test_should_handle_chat_allowlist():
    assert should_handle_chat("-100123", None)         # discovery → allow
    assert should_handle_chat("-100123", "-100123")    # match
    assert not should_handle_chat("-100999", "-100123")  # wrong chat blocked


def test_chat_discovery_note():
    note = chat_discovery_note("-100123", None)
    assert "TELEGRAM_CHAT_ID=-100123" in note
    assert chat_discovery_note("-100123", "-100123") is None


def test_handle_command_routes_unknown_to_assistant():
    c = SystemController()
    seen = {}
    def assistant(q):
        seen["q"] = q
        return "ASSISTANT_ANSWER"
    # A free-form question routes to the assistant
    out = handle_command("why did you open XRP~DOGE?", c, lambda: "S", lambda: "R", assistant)
    assert out == "ASSISTANT_ANSWER"
    assert seen["q"] == "why did you open XRP~DOGE?"
    # Known commands still take priority over the assistant
    assert "STOPPED" in handle_command("stop", c, lambda: "S", lambda: "R", assistant)


def test_approve_basket_command():
    from src.reporting import Command, parse_command
    assert parse_command("approve basket").command == Command.APPROVE_BASKET
    c = SystemController()
    out = handle_command("APPROVE BASKET", c, lambda: "S", lambda: "R",
                         assistant_provider=lambda q: "A", basket_approve=lambda: "BASKET_OK")
    assert out == "BASKET_OK"
