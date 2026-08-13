"""Employee-management module routes — the employee MASTER and attendance.

Owns: the roster (add/edit/deactivate, profile), daily attendance entry and
summaries, the annual leave reset, and the two-machine offline sync (the sync
payloads are roster + attendance, both this module's data). Financial fields
(advances, PF/ESI amounts, pay) belong to modules/payroll, which consumes this
module. Routes moved verbatim from main.py during the 2026-08 reorganization;
paths unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...core.deps import current_user, get_db, require_admin
from ...core.rules import get_rules
from . import repo, sync

router = APIRouter()


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
def update_employee(emp_id: int, body: EmployeeIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    if not repo.get_employee(conn, emp_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    repo.update_employee(conn, emp_id, body.model_dump())
    return repo.get_employee(conn, emp_id)


@router.post("/api/employees/{emp_id}/active")
def set_active(emp_id: int, active: bool, user: dict = Depends(require_admin), conn=Depends(get_db)):
    repo.set_employee_active(conn, emp_id, active)
    return {"ok": True}


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
