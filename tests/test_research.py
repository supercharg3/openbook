import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.research import is_research_request  # noqa: E402


def test_detects_research_requests():
    assert is_research_request("look into Micron") == "Micron"
    assert is_research_request("research SOL") == "SOL"
    assert is_research_request("should i buy NVDA?") == "NVDA"
    assert is_research_request("analyze ARB") == "ARB"
    assert is_research_request("thesis on Ethereum") == "Ethereum"


def test_ignores_non_research():
    assert is_research_request("are we ok?") is None
    assert is_research_request("status") is None
    assert is_research_request("why is nothing happening") is None
