"""Load and persist the configurable payroll rules (`config/rules.json`).

Keeping the rules in a JSON file (rather than hardcoded, as the old system did)
is what makes the new system "dynamic": policy changes — bonus amount, leave
entitlement, refreshment rate, which departments earn a bonus — are edits to
data, not code, and can later be exposed in the UI.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import paths

RULES_PATH = paths.config_dir() / "rules.json"


@lru_cache(maxsize=1)
def get_rules(path: str | Path | None = None) -> dict:
    """Return the parsed rules dict (cached). Pass `path` in tests to override."""
    p = Path(path) if path else RULES_PATH
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_rules(path: str | Path | None = None) -> dict:
    """Uncached read — use when you intend to mutate and save."""
    p = Path(path) if path else RULES_PATH
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save_rules(rules: dict, path: str | Path | None = None) -> None:
    p = Path(path) if path else RULES_PATH
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2, ensure_ascii=False)
    get_rules.cache_clear()
