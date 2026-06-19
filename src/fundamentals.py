"""Quality fundamentals from SEC EDGAR (free, no key) — gross-profits-to-assets per ticker.

GPA = GrossProfit / TotalAssets (Novy-Marx quality factor). Best-effort: any failure (EDGAR down,
ticker not found, a financial with no GrossProfit concept) simply omits that name, and the factor
engine falls back to momentum for it. Never raises.

NOTE: banks/insurers don't report GrossProfit, so GPA is naturally undefined for them — they get
selected by momentum alone. That's a known, acceptable v1 limitation.
"""
from __future__ import annotations

import json
import time
import urllib.request

# SEC requires a descriptive User-Agent with contact info for its APIs.
_UA = "openbook-trading-research (https://github.com/Supercharg3/openbook)"
_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
_CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"


def _get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return json.loads(raw)


def _cik_map() -> dict[str, int]:
    try:
        data = _get_json(_CIK_URL)
        return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
    except Exception:
        return {}


def _latest_value(cik: int, tag: str) -> float | None:
    try:
        data = _get_json(_CONCEPT.format(cik=cik, tag=tag))
        units = data.get("units", {}).get("USD", [])
        # prefer the most recent annual (FY) figure, else the most recent of any period
        annual = [u for u in units if u.get("fp") == "FY" and u.get("val") is not None]
        pick = (annual or [u for u in units if u.get("val") is not None])
        if not pick:
            return None
        pick.sort(key=lambda u: u.get("end", ""))
        return float(pick[-1]["val"])
    except Exception:
        return None


def fetch_gpa(tickers: list[str], throttle: float = 0.12) -> dict[str, float]:
    """Return {ticker: gross-profits-to-assets} for the names where EDGAR has the data."""
    cik = _cik_map()
    if not cik:
        return {}
    out: dict[str, float] = {}
    for t in tickers:
        c = cik.get(t.upper())
        if not c:
            continue
        gp = _latest_value(c, "GrossProfit")
        assets = _latest_value(c, "Assets")
        time.sleep(throttle)                      # stay well under SEC's 10 req/s limit
        if gp is not None and assets and assets > 0:
            out[t] = gp / assets
    return out
