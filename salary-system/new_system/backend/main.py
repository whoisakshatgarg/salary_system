"""App assembly: FastAPI instance, session/auth routes, self-update, backups,
and the static frontend mount. All business routes live in their modules:

    modules/employees   employee master, attendance, leave, offline sync
    modules/payroll     rules config, advances, salary pipeline, exports
    modules/inventory   raw-material heat register

Run:  ../venv/bin/uvicorn backend.main:app --reload
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core import auth, db, edition, paths, update
from .core.deps import current_user, get_db, require_admin
from .core.rules import get_rules
from .core.version import __version__
from .modules import inventory, users
from .modules.employees import seed
from .modules.employees.router import router as employees_router
from .modules.payroll.router import router as payroll_router

FRONTEND = paths.frontend_dir()
BACKUP_DIR = paths.backups_dir()

app = FastAPI(title="APEX THERMOCON Salary System")


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    """Never cache the UI assets so edits always show up (this is a local app)."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    seed.seed()  # no-op if already populated
    inventory.ensure_defaults()  # first-run inventory dropdown lists


app.include_router(employees_router)
app.include_router(payroll_router)
app.include_router(inventory.router)
app.include_router(users.router)


# --------------------------------------------------------------------------- #
# Auth / session
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    username: str
    password: str


@app.get("/api/edition")
def app_edition():
    """Public: lets the UI know which app it is before anyone signs in."""
    return {"edition": edition.edition(), "version": __version__}


@app.post("/api/login")
def login(body: LoginIn, response: Response, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT * FROM app_user WHERE username=?", (body.username,)
    ).fetchone()
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if edition.is_operator_edition() and row["role"] != "operator":
        raise HTTPException(
            status_code=403,
            detail="This is the Operator app — admin sign-in is disabled here.",
        )
    token = auth.create_token(row["username"], row["role"])
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=7 * 86400)
    return {"username": row["username"], "role": row["role"]}


@app.post("/api/kiosk-login")
def kiosk_login(response: Response, conn=Depends(get_db)):
    """Operator app only: sign in as the operator with no password prompt.

    Safe because this edition can never hold an admin session (login + every
    admin route are blocked above), and it runs on a single dedicated laptop.
    """
    if not edition.is_operator_edition():
        raise HTTPException(status_code=403, detail="Kiosk sign-in is only in the Operator app")
    row = conn.execute(
        "SELECT * FROM app_user WHERE role='operator' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="No operator account is configured")
    token = auth.create_token(row["username"], "operator")
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=7 * 86400)
    return {"username": row["username"], "role": "operator"}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/api/meta")
def meta(user: dict = Depends(current_user)):
    r = get_rules()
    return {
        "company_name": r.get("company_name"),
        "currency_symbol": r.get("currency_symbol"),
        "departments": r.get("departments", []),
        "role": user["role"],
        "edition": edition.edition(),
        "version": __version__,
    }


# --------------------------------------------------------------------------- #
# Self-update (GitHub Releases). Public on purpose: the popup must be able to
# appear on the login screen too, and the server only binds to 127.0.0.1.
# --------------------------------------------------------------------------- #
@app.get("/api/update/check")
def update_check():
    """Never raises — offline/misconfigured just means 'no update'."""
    return update.check()


@app.post("/api/update/apply")
def update_apply(request: Request):
    # Anti-CSRF: a malicious website can make the browser POST to 127.0.0.1,
    # but it cannot attach this custom header without a CORS preflight, which
    # this server never grants. Our own UI always sends it (see app.js api()).
    if request.headers.get("x-requested-with") != "apex-payroll":
        raise HTTPException(status_code=403, detail="Cross-site request refused")
    try:
        return update.apply()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------- #
# Database backup (CEO)
# --------------------------------------------------------------------------- #
def _write_backup_zip(dest: Path) -> None:
    """A COMPLETE backup: consistent salary.db snapshot + every on-disk file
    (inventory certificates/invoices AND employee documents). Restore = unzip
    salary.db, inventory_files/ and employee_files/ back into the data folder."""
    tmp = Path(tempfile.mkdtemp()) / "salary.db"
    db.backup_to(tmp)
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "salary.db")
            for dirname, folder in (("inventory_files", paths.inventory_files_dir()),
                                    ("employee_files", paths.employee_files_dir())):
                for f in sorted(folder.iterdir()):
                    if f.is_file():
                        z.write(f, f"{dirname}/{f.name}")
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/api/backup/list")
def backup_list(user: dict = Depends(require_admin)):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(list(BACKUP_DIR.glob("salary-*.zip"))
                   + list(BACKUP_DIR.glob("salary-*.db")),  # pre-inventory backups
                   key=lambda f: f.name, reverse=True)
    return {
        "dir": str(BACKUP_DIR),
        "backups": [{"file": f.name, "size_kb": round(f.stat().st_size / 1024)} for f in files[:15]],
    }


@app.post("/api/backup")
def backup_now(user: dict = Depends(require_admin)):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"salary-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    _write_backup_zip(dest)
    return {"file": dest.name, "size_kb": round(dest.stat().st_size / 1024)}


@app.get("/api/backup/download")
def backup_download(user: dict = Depends(require_admin)):
    tmp = Path(tempfile.mkdtemp()) / "salary-backup.zip"
    _write_backup_zip(tmp)
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    fname = f"salary-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# --------------------------------------------------------------------------- #
# Serve the SPA (must be mounted LAST so /api/* routes win)
# --------------------------------------------------------------------------- #
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
