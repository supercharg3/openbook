"""System controller — the override state machine the Telegram commands drive.

Holds the live trading flags (halted, paused layers, reduce-risk window). The orchestrator
checks these before acting; the Telegram bot mutates them. Pure in-memory state + a few
predicates, so it is fully unit-testable. Persistence to SQLite `system_state` is done by the
orchestrator on change, so flags survive a restart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
REDUCE_RISK_DAYS = 7


@dataclass
class ControllerState:
    halted: bool = False                       # STOP — no new trades
    paused_layers: set[str] = field(default_factory=set)
    reduce_risk_until: datetime | None = None
    closing_all: bool = False                  # transient flag for CLOSE ALL


class SystemController:
    def __init__(self, state: ControllerState | None = None) -> None:
        self.state = state or ControllerState()

    # ── Override actions ─────────────────────────────────────────────────────
    def stop(self) -> str:
        self.state.halted = True
        return "🛑 STOPPED — no new trades. Open positions held. Send RESUME to continue."

    def close_all(self) -> str:
        self.state.closing_all = True
        self.state.halted = True
        return "⚠️ CLOSE ALL — closing every open position at market and halting."

    def pause(self, layer: str | None) -> str:
        if not layer:
            return "PAUSE needs a layer, e.g. 'PAUSE trend'. Layers: funding, news, trend, grid."
        self.state.paused_layers.add(layer)
        return f"⏸ Paused layer '{layer}'. Send RESUME to clear all pauses."

    def resume(self) -> str:
        self.state.halted = False
        self.state.closing_all = False
        cleared = ", ".join(sorted(self.state.paused_layers)) or "none"
        self.state.paused_layers.clear()
        return f"▶️ RESUMED. Trading active. Cleared paused layers: {cleared}."

    def reduce_risk(self, now: datetime | None = None) -> str:
        now = now or datetime.now(SGT)
        self.state.reduce_risk_until = now + timedelta(days=REDUCE_RISK_DAYS)
        return f"🪙 REDUCE RISK — position sizes halved until {self.state.reduce_risk_until:%Y-%m-%d}."

    # ── Predicates the orchestrator checks ───────────────────────────────────
    def can_open_new_trade(self) -> bool:
        return not self.state.halted

    def is_layer_paused(self, layer: str) -> bool:
        return layer in self.state.paused_layers

    def reduce_risk_active(self, now: datetime | None = None) -> bool:
        if self.state.reduce_risk_until is None:
            return False
        now = now or datetime.now(SGT)
        return now < self.state.reduce_risk_until
