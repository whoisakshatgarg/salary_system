"""App assembly: FastAPI instance, session/auth routes, self-update, backups,
and the static frontend mount. All business routes live in their modules:

    modules/employees   employee master, attendance, leave, offline sync
    modules/payroll     rules config, advances, salary pipeline, exports
    modules/inventory   raw-material heat register
    modules/customers   customer master + contacts
    modules/parts       drawing master, rate history, costing builder
    modules/orders      orders, stages, consignments, FY numbering
    modules/quotations  quotations + invoices, printable copies
    modules/outsourcing vendors, outgoing job orders, receipts, bought-out stock
    modules/settings    order format, units, operation rates, departments
    modules/users       accounts, module grants, the launcher's tile list

Run:  ../venv/bin/uvicorn backend.main:app --reload
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core import auth, db, edition, numbering, paths, update
from .core.deps import current_user, get_db, require_admin
from .core.rules import get_rules
from .core.version import __version__
from .modules import (customers, inventory, orders, outsourcing, parts,
                      quotations, settings, users)
from .modules.employees import seed
from .modules.employees.router import router as employees_router
from .modules.payroll.router import router as payroll_router

FRONTEND = paths.frontend_dir()
BACKUP_DIR = paths.backups_dir()

app = FastAPI(title="APEX THERMOCON Salary System")


@app.middleware("http")
async def cache_policy(request, call_next):
    """Our own pages/JS are never cached so edits show up immediately, but the
    vendored libraries ARE — they're 440 KB that every module navigation would
    otherwise re-download and re-parse (this is a multi-page app: each tile
    click is a full page load).

    NOTE: cached by filename. If you ever swap a vendored library, rename the
    file (e.g. alpine-3.15.js) instead of overwriting it.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api"):
        return response
    if path.startswith("/vendor/"):
        response.headers["Cache-Control"] = "public, max-age=604800"  # 7 days
    else:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    seed.seed()  # no-op if already populated
    inventory.ensure_defaults()  # first-run inventory dropdown lists
    inventory.backfill_suppliers()   # suppliers became a list after heats existed
    settings.ensure_defaults()   # first-run units / operations / order format
    with db.connect() as _c:     # customers added before codes existed
        customers.backfill_codes(_c)
        customers.recode_legacy_codes(_c)  # …and codes from the old 2-letter scheme
        numbering.ensure_seeds(_c)         # real-world counter starting points


app.include_router(employees_router)
app.include_router(payroll_router)
app.include_router(inventory.router)
app.include_router(inventory.check_router)   # /api/material/* — shared with quotes/orders
app.include_router(users.router)
app.include_router(settings.router)
app.include_router(customers.router)
app.include_router(parts.router)
app.include_router(orders.router)
app.include_router(quotations.router)
app.include_router(outsourcing.router)


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
    (inventory certificates/invoices, employee documents, drawings, order
    intake paperwork, vendor paperwork). Restore = unzip salary.db and every
    *_files/ folder back into the data folder."""
    tmp = Path(tempfile.mkdtemp()) / "salary.db"
    db.backup_to(tmp)
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "salary.db")
            for dirname, folder in (("inventory_files", paths.inventory_files_dir()),
                                    ("employee_files", paths.employee_files_dir()),
                                    ("drawing_files", paths.drawing_files_dir()),
                                    ("order_files", paths.order_files_dir()),
                                    ("outsourcing_files", paths.outsourcing_files_dir())):
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
# The user guide (/help/) — OWNER ONLY.
#
# It has to be a real route, not part of the static mount: StaticFiles serves
# whatever it holds to anyone who asks, and the whole point here is that the
# manual is the owner's. Declared BEFORE the mount so it wins.
#
# A refusal renders as a small page rather than raw JSON, because this is
# something a person opens in a browser, not an API a script calls.
# --------------------------------------------------------------------------- #
HELP_DIR = FRONTEND / "help"

_HELP_DENIED = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>User Guide — APEX THERMOCON</title>
<style>
 body {font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
       background:#f1f5f9; color:#1e293b; display:flex; align-items:center;
       justify-content:center; min-height:100vh; margin:0;}
 .card {background:#fff; border-radius:1rem; box-shadow:0 1px 3px rgba(0,0,0,.1);
        padding:2.5rem; max-width:30rem; text-align:center;}
 h1 {font-size:1.25rem; margin:0 0 .5rem;}
 p {color:#475569; line-height:1.6; font-size:.9375rem;}
 a {display:inline-block; margin-top:1.25rem; background:#1d4ed8; color:#fff;
    text-decoration:none; padding:.625rem 1.25rem; border-radius:.5rem;
    font-weight:600; font-size:.875rem;}
</style></head><body><div class="card">
 <h1>{{title}}</h1>
 <p>{{body}}</p>
 <a href="/">Back to Home</a>
</div></body></html>"""


def _help_guard(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="The user guide is owner-only")


@app.get("/help/", response_class=HTMLResponse)
@app.get("/help", response_class=HTMLResponse)
def help_index(user: dict = Depends(current_user)):
    _help_guard(user)
    page = HELP_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="The guide has not been built yet")
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/help/{asset:path}")
def help_asset(asset: str, user: dict = Depends(current_user)):
    """Screenshots and anything else under help/ — gated with the page itself,
    otherwise the pictures would be readable by anyone who guessed the URL."""
    _help_guard(user)
    target = (HELP_DIR / asset).resolve()
    # containment check: a crafted '../..' must not escape the folder
    if not str(target).startswith(str(HELP_DIR.resolve()) + "/") or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


def _help_page(title: str, body: str, status: int) -> HTMLResponse:
    return HTMLResponse(_HELP_DENIED.replace("{{title}}", title).replace("{{body}}", body),
                        status_code=status)


@app.exception_handler(401)
async def _unauthorised(request: Request, exc: HTTPException):
    """Someone who typed /help/ while signed out gets a page telling them so —
    a raw JSON body in the browser window helps nobody."""
    if request.url.path.startswith("/help"):
        return _help_page(
            "Sign in first",
            "The user guide is only available to the owner&rsquo;s account. "
            "Sign in from the Home screen.", 401)
    return JSONResponse({"detail": exc.detail}, status_code=401)


@app.exception_handler(403)
async def _forbidden(request: Request, exc: HTTPException):
    """A person who opened /help/ in a browser gets a page; everything else keeps
    the JSON body the UI's api() helper expects."""
    if request.url.path.startswith("/help"):
        return _help_page(
            "The user guide is the owner&rsquo;s",
            "This manual is only available to the owner&rsquo;s account. If you "
            "need to know how something works, ask them &mdash; they can look it "
            "up for you.", 403)
    return JSONResponse({"detail": exc.detail}, status_code=403)


# --------------------------------------------------------------------------- #
# Serve the SPA (must be mounted LAST so /api/* routes win)
# --------------------------------------------------------------------------- #
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
