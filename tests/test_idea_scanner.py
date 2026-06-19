import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.idea_scanner import is_scan_request, _match, _reverse_index  # noqa: E402


def test_scan_request_detection():
    assert is_scan_request("scan for ideas")
    assert is_scan_request("any ideas?")
    assert is_scan_request("what should i buy")
    assert not is_scan_request("look into MU")
    assert not is_scan_request("status")


def test_match_finds_names_and_tickers_not_noise():
    idx = _reverse_index()
    # company name + ticker should match
    assert "MU" in _match("Will Micron Q3 revenue beat?", idx)
    assert "NVDA" in _match("NVDA is reportedly...", idx)
    # short ambiguous tickers shouldn't fire on random prose
    hits = _match("the value of a good visa application process", idx)
    assert "V" not in hits   # 'V' ticker skipped (too short); 'Visa' name needs the word 'visa'
