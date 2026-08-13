"""Where files live — works both in development and as a packaged app.

Run from source, paths are simple (`new_system/data`, `new_system/config`, …).
But once PyInstaller freezes everything into a single `.exe`, the bundle is a
read-only temp directory: we can read the UI and the *default* config from it,
but we must NOT write there. So the writable data (the SQLite DB, the working
config the CEO can edit, backups, the session secret) goes into a normal
per-user folder instead:

    Windows   %APPDATA%\\APEX Payroll\\
    macOS     ~/Library/Application Support/APEX Payroll/
    Linux     ~/.local/share/APEX Payroll/

On first launch that folder is empty, so the app creates the schema, seeds the
employees, and copies the bundled default config into it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "APEX Payroll"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle (the packaged .exe/.app)."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only bundled resources (frontend, default config templates, seed).

    Frozen: PyInstaller extracts bundled data files under ``sys._MEIPASS``.
    Source: the ``new_system/`` project root.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    # backend/core/paths.py -> backend/core -> backend -> new_system
    return Path(__file__).resolve().parent.parent.parent


def data_dir() -> Path:
    """Writable per-user folder for the DB, working config, backups, secret.

    Override with the ``SALARY_DATA_DIR`` env var (handy for tests / a portable
    install). In source mode it stays ``new_system/data`` so dev is unchanged.
    """
    override = os.environ.get("SALARY_DATA_DIR")
    if override:
        base = Path(override)
    elif is_frozen():
        if sys.platform == "win32":
            root = Path(os.environ.get("APPDATA") or Path.home())
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        base = root / APP_DIR_NAME
    else:
        base = Path(__file__).resolve().parent.parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def frontend_dir() -> Path:
    """The browser UI (read-only, safe to serve straight from the bundle)."""
    return resource_dir() / "frontend"


def config_dir() -> Path:
    """The *working* config directory (rules.json, sync.json) — must be writable.

    Frozen: a `config/` under the per-user data dir, seeded once from the
    bundled defaults. Source: the project's `config/` (already writable).
    """
    if is_frozen():
        d = data_dir() / "config"
        d.mkdir(parents=True, exist_ok=True)
        _seed_default_config(d)
        return d
    return resource_dir() / "config"


def db_path() -> Path:
    return data_dir() / "salary.db"


def backups_dir() -> Path:
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def secret_path() -> Path:
    return data_dir() / ".session_secret"


def inventory_files_dir() -> Path:
    """Attachment files for the inventory module (certificates, invoices).
    NOTE: lives OUTSIDE salary.db by design — a full backup is salary.db PLUS
    this folder."""
    d = data_dir() / "inventory_files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed_default_config(dest: Path) -> None:
    """Copy bundled default config into the writable dir, only if missing."""
    src = resource_dir() / "config"
    for name in ("rules.json", "sync.json", "update.json"):
        s, d = src / name, dest / name
        if s.exists() and not d.exists():
            shutil.copyfile(s, d)
