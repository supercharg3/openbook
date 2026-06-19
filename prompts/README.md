# Openbook — prompts

These are the core prompts that drive the agentic reasoning layer. They are extracted here
so they are readable, auditable, and improvable without digging into code.

| File | Used by | What it does |
|---|---|---|
| `research-panel.md` | `src/research.py` | Bull/bear/risk panel that stress-tests any trade idea |
| `risk-veto.md` | `src/risk_veto.py` | Hard red-flag check (fraud, SEC, delisting) before any trade |
| `idea-scanner.md` | `src/idea_scanner.py` | Prompt for surface-level idea generation from news + Polymarket |
| `assistant.md` | `src/assistant.py` | System prompt explaining the bot's role and what it knows |

The **onboarding skill** (the adaptive interview that sets up new users) lives in
`skills/openbook-setup/SKILL.md` — it is a Claude Code skill, not a raw prompt.
