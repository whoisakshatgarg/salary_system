"""Offline exchange between the Operator machine and the CEO machine.

Each machine runs its own app + SQLite DB. Data moves as stamped JSON files in a
shared cloud-synced folder (Google Drive / Dropbox / OneDrive — see
config/sync.json). Direction is fixed by role:

    CEO       writes  roster.json               → Operator imports it
    Operator  writes  attendance-<period>.json  → CEO imports it

The CEO's DB is the master of record: importing attendance there recomputes
summaries + leave banks (via repo.save_attendance). Imports are idempotent and
de-duplicated by file hash (sync_log). If no folder is configured/reachable, the
API falls back to plain download/upload.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from . import repo
from ...core import paths

CONFIG_PATH = paths.config_dir() / "sync.json"
APP_TAG = "apex-salary-sync"
VERSION = 1


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def folder() -> Path | None:
    p = (load_config().get("shared_folder") or "").strip()
    return Path(p).expanduser() if p else None


def status() -> dict:
    cfg = load_config()
    p = folder()
    exists = bool(p and p.is_dir())
    return {
        "configured": bool(p),
        "path": str(p) if p else "",
        "exists": exists,
        "writable": bool(exists and os.access(p, os.W_OK)),
        "auto_check": bool(cfg.get("auto_check_on_login", True)),
    }


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _envelope(type_: str, source: str, period, data: list) -> dict:
    return {
        "app": APP_TAG, "version": VERSION, "type": type_, "source": source,
        "period": period, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(data), "data": data,
    }


# --------------------------------------------------------------------------- #
# Build exports
# --------------------------------------------------------------------------- #
def build_attendance(conn, period: str) -> tuple[str, dict]:
    data = repo.export_attendance_data(conn, period)
    return f"attendance-{period}.json", _envelope("attendance", "operator", period, data)


def build_roster(conn) -> tuple[str, dict]:
    data = repo.export_roster_data(conn)
    return "roster.json", _envelope("roster", "ceo", None, data)


def write_to_folder(filename: str, env: dict) -> str | None:
    """Write the envelope into the shared folder; return the path, or None if no
    writable folder is configured (caller then offers a download)."""
    p = folder()
    if not (p and p.is_dir() and os.access(p, os.W_OK)):
        return None
    target = p / filename
    target.write_text(json.dumps(env, indent=2), encoding="utf-8")
    return str(target)


# --------------------------------------------------------------------------- #
# Discover + import
# --------------------------------------------------------------------------- #
def available(conn, role: str) -> list[dict]:
    """Sync files in the folder meant for this role that haven't been imported."""
    p = folder()
    if not (p and p.is_dir()):
        return []
    want_type, want_source = ("attendance", "operator") if role == "admin" else ("roster", "ceo")
    done = repo.imported_hashes(conn)
    out = []
    for fp in sorted(p.glob("*.json")):
        try:
            text = fp.read_text(encoding="utf-8")
            env = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        if env.get("app") != APP_TAG or env.get("type") != want_type or env.get("source") != want_source:
            continue
        h = _hash(text)
        if h in done:
            continue
        out.append({
            "filename": fp.name, "type": env["type"], "source": env["source"],
            "period": env.get("period"), "generated_at": env.get("generated_at"),
            "count": env.get("count"), "hash": h,
        })
    return out


def _apply(conn, env: dict, rules: dict, role: str, filename: str, file_hash: str) -> dict:
    if env.get("app") != APP_TAG:
        raise ValueError("This file is not a valid salary-sync file")
    type_ = env.get("type")
    if type_ == "attendance":
        if role != "admin":
            raise ValueError("Only the CEO imports attendance")
        res = repo.apply_attendance_import(conn, env["period"], env["data"], rules)
        summary = f"{res['applied']} employees" + (
            f", {len(res['skipped'])} skipped (unknown)" if res["skipped"] else "")
    elif type_ == "roster":
        if role == "admin":
            raise ValueError("The CEO owns the roster — import it on the operator machine")
        res = repo.apply_roster_import(conn, env["data"])
        summary = f"{res['applied']} employees"
    else:
        raise ValueError("Unknown sync file type")
    repo.record_import(conn, filename, file_hash, type_, env.get("source"), env.get("period"), summary)
    return {"type": type_, "summary": summary, "period": env.get("period")}


def import_from_folder(conn, filename: str, rules: dict, role: str) -> dict:
    p = folder()
    if not p:
        raise ValueError("Shared folder is not configured")
    fp = p / filename
    if not fp.is_file():
        raise ValueError("File not found in shared folder")
    text = fp.read_text(encoding="utf-8")
    return _apply(conn, json.loads(text), rules, role, filename, _hash(text))


def import_from_content(conn, env: dict, rules: dict, role: str) -> dict:
    """Manual fallback: import an uploaded envelope (no shared folder)."""
    text = json.dumps(env, sort_keys=True)
    return _apply(conn, env, rules, role, env.get("type", "upload") + " (upload)", _hash(text))
