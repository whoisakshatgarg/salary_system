"""Employee-management module routes — the employee MASTER and attendance.

Owns: the roster (add/edit/deactivate, profile), daily attendance entry and
summaries, the annual leave reset, and the two-machine offline sync (the sync
payloads are roster + attendance, both this module's data). Financial fields
(advances, PF/ESI amounts, pay) belong to modules/payroll, which consumes this
module. Routes moved verbatim from main.py during the 2026-08 reorganization;
paths unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...core import paths
from ...core.attachments import header_filename, response_mime
from ...core.deps import current_user, get_db, require_admin, require_module
from ...core.rules import get_rules
from . import repo, sync

# Router-level gate: EVERY route here needs the 'salary' OR 'employees' grant
# (the roster/attendance data serves both modules; admins pass implicitly; the
# Operator edition is allowed via the salary key). Without this, any signed-in
# account could read the roster — salaries included — over the API even when
# its launcher showed no tile. Admin-only routes keep require_admin on top;
# EM-only surfaces (documents, leave adjustment) add require_module("employees").
router = APIRouter(dependencies=[Depends(require_module("salary", "employees"))])


class EmployeeIn(BaseModel):
    """Creation only — the one moment everything is set together."""
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
    # optional — how this person is paid; free text on purpose (an account
    # number is an identifier, not a number)
    bank_name: str = ""
    bank_account_no: str = ""
    bank_ifsc: str = ""


class EmployeeProfileIn(BaseModel):
    """EM's edit form — the people side. Pay fields deliberately absent so a
    stale profile form can never revert Pay Setup (and vice versa). Bank
    details ride with the profile: EM owns who the person is and where their
    pay goes; Pay Setup owns how much."""
    name: str
    dept: str
    shift: str = "D"
    overtime_eligible: bool = False
    date_joined: str | None = None
    bank_name: str = ""
    bank_account_no: str = ""
    bank_ifsc: str = ""


class EmployeePayIn(BaseModel):
    """Salary → Pay Setup's edit form — the money side only."""
    base_salary: int
    pf_applicable: bool = False
    esi_applicable: bool = False
    rem_advance: int = 0


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
# Employees
# --------------------------------------------------------------------------- #
@router.get("/api/employees")
def employees(active_only: bool = True, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.list_employees(conn, active_only=active_only)


@router.get("/api/employees/{emp_id}")
def employee(emp_id: int, user: dict = Depends(current_user), conn=Depends(get_db)):
    emp = repo.get_employee(conn, emp_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


@router.get("/api/employee-profile/{emp_id}")
def employee_profile(emp_id: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    try:
        return repo.employee_profile(conn, emp_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/employees")
def create_employee(body: EmployeeIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    new_id = repo.create_employee(conn, body.model_dump(), get_rules())
    return repo.get_employee(conn, new_id)


@router.put("/api/employees/{emp_id}")
def update_employee_profile(emp_id: int, body: EmployeeProfileIn,
                            user: dict = Depends(require_admin), conn=Depends(get_db)):
    if not repo.get_employee(conn, emp_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    repo.update_employee_profile(conn, emp_id, body.model_dump())
    return repo.get_employee(conn, emp_id)


@router.put("/api/employees/{emp_id}/pay")
def update_employee_pay(emp_id: int, body: EmployeePayIn,
                        user: dict = Depends(require_admin), conn=Depends(get_db)):
    if not repo.get_employee(conn, emp_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    repo.update_employee_pay(conn, emp_id, body.model_dump())
    return repo.get_employee(conn, emp_id)


@router.post("/api/employees/{emp_id}/active")
def set_active(emp_id: int, active: bool, user: dict = Depends(require_admin), conn=Depends(get_db)):
    repo.set_employee_active(conn, emp_id, active)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Documents + leave bank (Employee Management surfaces)
# --------------------------------------------------------------------------- #
class LeaveAdjustIn(BaseModel):
    delta: int


@router.get("/api/employees/{emp_id}/documents")
def documents(emp_id: int, user: dict = Depends(require_module("employees")),
              conn=Depends(get_db)):
    return repo.list_documents(conn, emp_id)


@router.post("/api/employees/{emp_id}/documents")
def upload_documents(emp_id: int, label: str = Form(""),
                     files: list[UploadFile] = File(...),
                     user: dict = Depends(require_module("employees")),
                     conn=Depends(get_db)):
    # Sync def on purpose: the sqlite connection lives in the threadpool.
    items = [(f.filename or "", f.content_type or "", f.file.read()) for f in files]
    try:
        return repo.save_documents(conn, emp_id, label, items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/employee-documents/{document_id}")
def view_document(document_id: int, download: bool = False,
                  user: dict = Depends(require_module("employees")),
                  conn=Depends(get_db)):
    row = repo.get_document(conn, document_id)
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    path = paths.employee_files_dir() / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File is missing on disk")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=response_mime(row["stored_name"]),
        headers={"Content-Disposition":
                 f'{disposition}; filename="{header_filename(row["filename"])}"'},
    )


@router.delete("/api/employee-documents/{document_id}")
def delete_document(document_id: int,
                    user: dict = Depends(require_module("employees")),
                    conn=Depends(get_db)):
    try:
        return repo.delete_document(conn, document_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/employees/{emp_id}/leave-adjust")
def leave_adjust(emp_id: int, body: LeaveAdjustIn,
                 user: dict = Depends(require_module("employees")),
                 conn=Depends(get_db)):
    try:
        return repo.adjust_leave(conn, emp_id, body.delta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
@router.get("/api/attendance/{emp_id}")
def get_attendance(emp_id: int, period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.get_attendance(conn, emp_id, period)


@router.post("/api/attendance")
def save_attendance(body: AttendanceIn, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return repo.save_attendance(
            conn, body.employee_id, body.period,
            [d.model_dump() for d in body.days], get_rules(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/attendance/{emp_id}/{period}/override")
def override_attendance(emp_id: int, period: str, body: SummaryOverrideIn,
                        user: dict = Depends(require_admin), conn=Depends(get_db)):
    try:
        return repo.override_summary(conn, emp_id, period, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/attendance-fy/{emp_id}")
def attendance_fy(emp_id: int, fy_start: int, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_fy_stats(conn, emp_id, fy_start)


@router.get("/api/attendance-status/{period}")
def attendance_status(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_status(conn, period)


@router.get("/api/attendance-grid/{period}")
def attendance_grid(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_grid(conn, period, include_stats=(user["role"] == "admin"))


@router.post("/api/attendance/bulk")
def save_attendance_bulk(body: BulkAttendanceIn, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return repo.save_attendance_bulk(
            conn, body.period,
            [{"employee_id": e.employee_id, "days": [d.model_dump() for d in e.days]} for e in body.entries],
            get_rules(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/attendance/calculate")
def calculate_attendance(body: BulkAttendanceIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.compute_attendance_grid(
        conn, body.period,
        [{"employee_id": e.employee_id, "days": [d.model_dump() for d in e.days]} for e in body.entries],
        get_rules(),
    )


@router.get("/api/attendance-summaries/{period}")
def attendance_summaries(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_summaries(conn, period)


@router.get("/api/attendance-history/{emp_id}")
def attendance_history(emp_id: int, user: dict = Depends(current_user), conn=Depends(get_db)):
    return repo.attendance_history(conn, emp_id)


# --------------------------------------------------------------------------- #
# Leave reset (admin)
# --------------------------------------------------------------------------- #
@router.post("/api/leave/reset/{year}")
def leave_reset(year: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.leave_reset(conn, year, get_rules())


# --------------------------------------------------------------------------- #
# Offline sync (shared folder, JSON files)
# --------------------------------------------------------------------------- #
@router.get("/api/sync/status")
def sync_status(user: dict = Depends(current_user)):
    return {**sync.status(), "role": user["role"]}


@router.get("/api/sync/available")
def sync_available(user: dict = Depends(current_user), conn=Depends(get_db)):
    return sync.available(conn, user["role"])


@router.post("/api/sync/export/attendance/{period}")
def sync_export_attendance(period: str, user: dict = Depends(current_user), conn=Depends(get_db)):
    filename, env = sync.build_attendance(conn, period)
    path = sync.write_to_folder(filename, env)
    return {"written_to_folder": path is not None, "path": path, "filename": filename, "envelope": env}


@router.post("/api/sync/export/roster")
def sync_export_roster(user: dict = Depends(require_admin), conn=Depends(get_db)):
    filename, env = sync.build_roster(conn)
    path = sync.write_to_folder(filename, env)
    return {"written_to_folder": path is not None, "path": path, "filename": filename, "envelope": env}


@router.post("/api/sync/import")
def sync_import(body: ImportRef, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return sync.import_from_folder(conn, body.filename, get_rules(), user["role"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/sync/import-file")
def sync_import_file(body: ImportContent, user: dict = Depends(current_user), conn=Depends(get_db)):
    try:
        return sync.import_from_content(conn, body.envelope, get_rules(), user["role"])
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid sync file: {e}")
