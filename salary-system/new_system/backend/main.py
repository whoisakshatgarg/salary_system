"""FastAPI application: JSON API + serves the browser UI.

One process replaces the old ~20-script `os.system` maze. Auth is a stateless
signed cookie (see auth.py); SQL lives in repo.py; the calculation engine in
payroll.py; Excel in exporters.py. Run:

    ../venv/bin/uvicorn backend.main:app --reload
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import seed, sync
from .core import auth, db, edition, paths, update
from .core.rules import get_rules
from .core.version import __version__
from .modules import inventory
from .modules.payroll import repo
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
    inventory.ensure_defaults()  # seed the inventory dropdown lists (idempotent)


# --------------------------------------------------------------------------- #
# Dependencies (shared with feature routers — see deps.py)
# --------------------------------------------------------------------------- #
from .core.deps import current_user, get_db, require_admin  # noqa: E402

app.include_router(inventory.router)
app.include_router(payroll_router)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    username: str
    password: str


class EmployeeIn(BaseModel):
    name: str
    dept: str
    base_salary: int
    pf_applicable: bool = False
    esi_applicable: bool = False
    overtime_eligible: bool = False
    shift: str = "D"
    rem_advance: int = 0
    leave_balance: int | None = None
    date_joined: str | None = None


class DayIn(BaseModel):
    day: int
    status: str = "P"
    overtime: float | None = None


class AttendanceIn(BaseModel):
    employee_id: int
    period: str
    days: list[DayIn]


class SummaryOverrideIn(BaseModel):
    present_days: float | None = None
    penalty_days: int | None = None
    overtime_hours: float | None = None
    refreshment_days: int | None = None


class BulkEntryIn(BaseModel):
    employee_id: int
    days: list[DayIn]


class BulkAttendanceIn(BaseModel):
    period: str
    entries: list[BulkEntryIn]


class ImportRef(BaseModel):
    filename: str


class ImportContent(BaseModel):
    envelope: dict


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
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
# Employees
# --------------------------------------------------------------------------- #
@app.get("/api/employees")
def employees(active_only: bool = True, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.list_employees(conn, active_only=active_only)


@app.get("/api/employees/{emp_id}")
def employee(emp_id: int, user: dict = Depends(current_user), conn=Depends(get_db)):
    emp = repo.get_employee(conn, emp_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


@app.get("/api/employee-profile/{emp_id}")
def employee_profile(emp_id: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    try:
        return repo.employee_profile(conn, emp_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/employees")
def create_employee(body: EmployeeIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    new_id = repo.create_employee(conn, body.model_dump(), get_rules())
    return repo.get_employee(conn, new_id)


@app.put("/api/employees/{emp_id}")
def update_employee(emp_id: int, body: EmployeeIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    if not repo.get_employee(conn, emp_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    repo.update_employee(conn, emp_id, body.model_dump())
    return repo.get_employee(conn, emp_id)


@app.post("/api/employees/{emp_id}/active")
def set_active(emp_id: int, active: bool, user: dict = Depends(require_admin), conn=Depends(get_db)):
    repo.set_employee_active(conn, emp_id, active)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
@app.get("/api/attendance/{emp_id}")
def get_attendance(emp_id: int, period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.get_attendance(conn, emp_id, period)


@app.post("/api/attendance")
def save_attendance(body: AttendanceIn, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return repo.save_attendance(
            conn, body.employee_id, body.period,
            [d.model_dump() for d in body.days], get_rules(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/attendance/{emp_id}/{period}/override")
def override_attendance(emp_id: int, period: str, body: SummaryOverrideIn,
                        user: dict = Depends(require_admin), conn=Depends(get_db)):
    try:
        return repo.override_summary(conn, emp_id, period, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/attendance-fy/{emp_id}")
def attendance_fy(emp_id: int, fy_start: int, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_fy_stats(conn, emp_id, fy_start)


@app.get("/api/attendance-status/{period}")
def attendance_status(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_status(conn, period)


@app.get("/api/attendance-grid/{period}")
def attendance_grid(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_grid(conn, period, include_stats=(user["role"] == "admin"))


@app.post("/api/attendance/bulk")
def save_attendance_bulk(body: BulkAttendanceIn, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return repo.save_attendance_bulk(
            conn, body.period,
            [{"employee_id": e.employee_id, "days": [d.model_dump() for d in e.days]} for e in body.entries],
            get_rules(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/attendance/calculate")
def calculate_attendance(body: BulkAttendanceIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.compute_attendance_grid(
        conn, body.period,
        [{"employee_id": e.employee_id, "days": [d.model_dump() for d in e.days]} for e in body.entries],
        get_rules(),
    )


@app.get("/api/attendance-summaries/{period}")
def attendance_summaries(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_summaries(conn, period)


@app.get("/api/attendance-history/{emp_id}")
def attendance_history(emp_id: int, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_history(conn, emp_id)


# --------------------------------------------------------------------------- #
# Leave reset (admin)
# --------------------------------------------------------------------------- #
@app.post("/api/leave/reset/{year}")
def leave_reset(year: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.leave_reset(conn, year, get_rules())


# --------------------------------------------------------------------------- #
# Offline sync (shared folder, JSON files)
# --------------------------------------------------------------------------- #
@app.get("/api/sync/status")
def sync_status(user: dict = Depends(current_user)):
    return {**sync.status(), "role": user["role"]}


@app.get("/api/sync/available")
def sync_available(user: dict = Depends(current_user), conn=Depends(get_db)):
    return sync.available(conn, user["role"])


@app.post("/api/sync/export/attendance/{period}")
def sync_export_attendance(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    filename, env = sync.build_attendance(conn, period)
    path = sync.write_to_folder(filename, env)
    return {"written_to_folder": path is not None, "path": path, "filename": filename, "envelope": env}


@app.post("/api/sync/export/roster")
def sync_export_roster(user: dict = Depends(require_admin), conn=Depends(get_db)):
    filename, env = sync.build_roster(conn)
    path = sync.write_to_folder(filename, env)
    return {"written_to_folder": path is not None, "path": path, "filename": filename, "envelope": env}


@app.post("/api/sync/import")
def sync_import(body: ImportRef, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return sync.import_from_folder(conn, body.filename, get_rules(), user["role"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sync/import-file")
def sync_import_file(body: ImportContent, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return sync.import_from_content(conn, body.envelope, get_rules(), user["role"])
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid sync file: {e}")


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
    """A COMPLETE backup: consistent salary.db snapshot + every inventory
    attachment file. Restore = unzip salary.db (and inventory_files/) back into
    the app's data folder."""
    tmp = Path(tempfile.mkdtemp()) / "salary.db"
    db.backup_to(tmp)
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "salary.db")
            for f in sorted(paths.inventory_files_dir().iterdir()):
                if f.is_file():
                    z.write(f, f"inventory_files/{f.name}")
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
