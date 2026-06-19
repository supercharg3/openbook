import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.risk_veto import red_flag_check  # noqa: E402


class _Cfg:
    anthropic_api_key = None
    exa_api_key = None
    claude_model = "x"


def test_fails_open_without_keys():
    # No screener configured → must NOT veto (fail open so a research gap never blocks a pick)
    out = red_flag_check("AAPL", _Cfg())
    assert out["vetoed"] is False
    assert "reason" in out
