"""Self-update from GitHub Releases.

How a new version reaches the two laptops:

1. The developer bumps ``backend/core/version.py``, pushes, and tags ``v<version>``.
2. GitHub Actions builds both ``.exe``s and attaches them to a GitHub Release.
3. On every launch each app calls :func:`check` (via ``/api/update/check``),
   which compares its own version against the newest release tag of the repo
   configured in ``config/update.json``. If a newer one exists, the UI shows an
   "Update available" prompt; otherwise nothing appears.
4. If the user clicks **Update**, :func:`apply` downloads the matching ``.exe``
   asset, drops a tiny updater batch script next to it, and exits the app. The
   script waits for the process to die (Windows locks a running ``.exe``),
   copies the new build over the old one, and relaunches it. Data is untouched:
   the DB/config live in the per-user folder, not in the ``.exe``.

Everything uses the standard library only. Network failures, missing releases,
rate limits, and private repos all degrade to "no popup" rather than an error
dialog — an offline salary app must never nag about the internet.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import edition, paths
from .version import __version__

CONFIG_PATH = paths.config_dir() / "update.json"
API_TIMEOUT_S = 8
# A real PyInstaller build is tens of MB; refuse to swap in anything tiny
# (a truncated download, an HTML error page saved as .exe, ...).
MIN_PLAUSIBLE_EXE_BYTES = 5_000_000


def load_config() -> dict:
    """Never raises — a broken/unreadable config just means defaults."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def version_tuple(s: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' / '1.2.3-beta' -> (1, 2, 3): tolerant numeric compare.

    Each dotted segment contributes its LEADING digits ('3b1' -> 3, 'beta' -> 0);
    all segments count, and trailing zeros are trimmed so '1.2.3.0' == '1.2.3'.
    """
    s = (s or "").strip().lstrip("vV")
    out: list[int] = []
    for part in s.replace("-", ".").split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    while len(out) > 3 and out[-1] == 0:
        out.pop()
    while len(out) < 3:
        out.append(0)
    return tuple(out)


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub 302s asset downloads to S3, which rejects requests that still
    carry the GitHub token — drop Authorization when the redirect changes host
    (urllib otherwise forwards every header to the new URL)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_host = urllib.parse.urlsplit(req.full_url).hostname
            new_host = urllib.parse.urlsplit(newurl).hostname
            if old_host != new_host:
                new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_StripAuthOnRedirect)


def _open(url: str, *, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": "apex-payroll-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    token = (load_config().get("github_token") or "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return _OPENER.open(req, timeout=API_TIMEOUT_S)  # noqa: S310 (https only)


def _platform_ok() -> bool:
    """Self-swap only works for the packaged Windows .exe."""
    return paths.is_frozen() and sys.platform == "win32"


def check() -> dict:
    """Compare this build against the newest GitHub Release. Never raises."""
    cfg = load_config()
    result = {
        "configured": False,
        "auto_check": bool(cfg.get("auto_check_on_start", True)),
        "current": __version__,
        "latest": None,
        "update_available": False,
        "can_apply": False,
        "notes": "",
        "published_at": None,
        "asset": None,
        "error": None,
    }
    repo = (cfg.get("github_repo") or "").strip().strip("/")
    if not repo:
        return result
    result["configured"] = True

    try:
        with _open(f"https://api.github.com/repos/{repo}/releases/latest") as resp:
            rel = json.load(resp)
    except urllib.error.HTTPError as e:
        # 404 = no releases yet (or a private repo without a token) — that is
        # simply "no update", not an error worth surfacing on every launch.
        if e.code != 404:
            result["error"] = f"GitHub answered {e.code} while checking for updates"
        return result
    except Exception as e:
        result["error"] = f"Could not check for updates: {e}"
        return result

    tag = (rel.get("tag_name") or rel.get("name") or "").strip()
    result["latest"] = tag.lstrip("vV") or None
    result["notes"] = (rel.get("body") or "").strip()[:2000]
    result["published_at"] = rel.get("published_at")

    want = edition.edition()  # 'admin' | 'operator' — pick this app's asset
    for a in rel.get("assets", []):
        name = (a.get("name") or "").lower()
        if name.endswith(".exe") and want in name:
            result["asset"] = {
                "name": a["name"],
                "api_url": a["url"],  # works for public AND token'd private repos
                "size": a.get("size"),
            }
            break

    result["update_available"] = bool(
        result["latest"] and version_tuple(tag) > version_tuple(__version__)
    )
    result["can_apply"] = bool(
        result["update_available"] and result["asset"] and _platform_ok()
    )
    return result


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #
# The updater script, with three hard requirements learned the hard way:
# * Pure ASCII, and NO paths baked into the text: cmd.exe parses .bat files in
#   the OEM codepage (never UTF-8), so an embedded path with a non-ASCII
#   Windows username would turn to mojibake and every copy would fail. Paths
#   and the PID travel via environment variables instead, which cmd expands as
#   Unicode end-to-end (and which survive literal % in paths).
# * ping is the delay (timeout.exe refuses to run in a detached console); a
#   running .exe is write-locked on Windows, so "copy succeeds" == "app quit".
# * Every exit path must leave the user with a working, running app: back up
#   the old exe first, restore it if the swap fails, and ALWAYS relaunch.
_UPDATER_BAT = """@echo off
rem APEX Payroll self-updater - written by the app, safe to delete.
rem Inputs via environment: APEX_NEW, APEX_TARGET, APEX_PID.

:wait
tasklist /FI "PID eq %APEX_PID%" 2>nul | find " %APEX_PID% " >nul
if errorlevel 1 goto backup
ping -n 2 127.0.0.1 >nul
goto wait

:backup
copy /y "%APEX_TARGET%" "%APEX_TARGET%.bak" >nul 2>&1

:swap
copy /y "%APEX_NEW%" "%APEX_TARGET%" >nul 2>&1
if not errorlevel 1 goto done
set /a tries+=1
if %tries% GEQ 60 goto fail
ping -n 2 127.0.0.1 >nul
goto swap

:done
del "%APEX_NEW%" >nul 2>&1
del "%APEX_TARGET%.bak" >nul 2>&1
start "" "%APEX_TARGET%"
exit /b 0

:fail
rem Could not swap (locked/quarantined/read-only). Put the old exe back
rem exactly as it was and reopen it - the update is offered again next launch.
copy /y "%APEX_TARGET%.bak" "%APEX_TARGET%" >nul 2>&1
del "%APEX_TARGET%.bak" >nul 2>&1
del "%APEX_NEW%" >nul 2>&1
start "" "%APEX_TARGET%"
exit /b 1
"""


def apply() -> dict:
    """Download the new build, hand over to the updater script, and exit."""
    info = check()
    if info["error"]:
        raise ValueError(info["error"])
    if not info["update_available"]:
        raise ValueError("You already have the latest version")
    if not _platform_ok():
        raise ValueError(
            "Self-update only works in the installed Windows app. "
            "This copy runs from source — pull the latest code instead."
        )
    if not info["asset"]:
        raise ValueError(
            "The new release has no .exe for this app yet — "
            "wait for the build to finish and try again"
        )

    updates_dir = paths.data_dir() / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    new_exe = updates_dir / info["asset"]["name"]
    try:
        # URLError/HTTPError/timeouts and disk-write failures are all OSError.
        with _open(info["asset"]["api_url"], accept="application/octet-stream") as resp, \
                open(new_exe, "wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as e:
        new_exe.unlink(missing_ok=True)  # never leave a partial download behind
        raise ValueError(
            f"The download failed partway — check the internet connection and try again ({e})"
        )

    if new_exe.stat().st_size < MIN_PLAUSIBLE_EXE_BYTES:
        new_exe.unlink(missing_ok=True)
        raise ValueError("The downloaded file looks incomplete — update aborted")

    target = Path(sys.executable)
    bat = updates_dir / "apply_update.bat"
    # ascii encoding is load-bearing: it makes any future non-ASCII edit to the
    # template fail HERE, loudly, instead of as codepage mojibake on the user's
    # machine mid-update (see the note above _UPDATER_BAT).
    bat.write_text(_UPDATER_BAT, encoding="ascii")
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "DETACHED_PROCESS", 0))
    subprocess.Popen(  # noqa: S603 — our own script, path under our data dir
        ["cmd", "/c", str(bat)],
        env={**os.environ,
             "APEX_NEW": str(new_exe),
             "APEX_TARGET": str(target),
             "APEX_PID": str(os.getpid())},
        creationflags=flags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Give the HTTP response time to reach the browser, then die so the .exe
    # unlocks. The updater script takes it from here.
    threading.Timer(1.5, os._exit, args=(0,)).start()
    return {
        "ok": True,
        "version": info["latest"],
        "message": "Update downloaded — the app will now close and reopen itself.",
    }
