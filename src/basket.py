"""Config-driven pairs basket — lets the engine refresh its pairs without code changes.

The live basket (pairs + per-pair allocations) lives in data/active_basket.json. The orchestrator
loads it at startup; if absent, it falls back to the hardcoded defaults in orchestrator.py. The
re-screening job writes data/proposed_basket.json; approving it promotes proposed -> active.
This is how the edge stays fresh: screen -> validate -> propose -> approve -> live.
"""
from __future__ import annotations
import json
import os

ACTIVE = "active_basket.json"
PROPOSED = "proposed_basket.json"


def _path(data_dir, name):
    return os.path.join(data_dir, name)


def load_active(data_dir, default_pairs, default_allocs):
    """Return (pairs:list[tuple], allocations:dict). Falls back to defaults if no file."""
    p = _path(data_dir, ACTIVE)
    if not os.path.exists(p):
        return list(default_pairs), dict(default_allocs)
    try:
        with open(p) as f:
            d = json.load(f)
        pairs = [tuple(x) for x in d["pairs"]]
        return pairs, dict(d["allocations"])
    except Exception:
        return list(default_pairs), dict(default_allocs)


def save(data_dir, name, pairs, allocations, meta=None):
    with open(_path(data_dir, name), "w") as f:
        json.dump({"pairs": [list(p) for p in pairs], "allocations": allocations,
                   "meta": meta or {}}, f, indent=2)


def promote_proposed(data_dir) -> bool:
    """Copy proposed_basket.json -> active_basket.json. Returns True on success."""
    src, dst = _path(data_dir, PROPOSED), _path(data_dir, ACTIVE)
    if not os.path.exists(src):
        return False
    with open(src) as f:
        d = json.load(f)
    with open(dst, "w") as f:
        json.dump(d, f, indent=2)
    return True
