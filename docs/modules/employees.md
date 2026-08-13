# Employee Management

**Status: 🟡 partial** — backend module owns the data (2026-08-13 split);
dedicated UI not built (tile is a placeholder; screens still live in the
payroll SPA).

## Purpose
Own the employee master: who works here, their profile/status/documents, daily
attendance and the leave bank. Payroll consumes this and keeps only the
financial side (owner's decision — DECISIONS 2026-08-13).

## User flows (today, via the Salary & Attendance tile)
- Add/edit employees, deactivate; per-employee profile with salary/attendance/
  advance history charts.
- Attendance: all-employees grid (rows × days, bulk save) or single-employee
  calendar; CEO can override computed metrics; January leave reset.
- Two-machine sync: operator exports attendance JSON, CEO imports (master of
  record); CEO exports roster, operator auto-imports.

## Implemented (file paths)
- Data: `salary-system/new_system/backend/modules/employees/repo.py` (roster,
  attendance days + summaries, leave bank, profile aggregation, sync payloads).
- Routes: `backend/modules/employees/router.py` (`/api/employees*`,
  `/api/attendance*`, `/api/leave/reset/*`, `/api/sync/*`).
- Sync engine: `backend/modules/employees/sync.py` (shared folder,
  hash-deduplicated, role-locked direction).
- Seed: `backend/modules/employees/seed.py` (70 employees, default accounts).
- UI (temporary home): Employees/Attendance/Sync screens inside
  `frontend/payroll.html` + `frontend/app.js`.

## Data model
`employee(id, name, dept, base_salary, pf_applicable, esi_applicable,
overtime_eligible, shift, rem_advance, leave_balance, date_joined, active)` ·
`attendance_day(employee_id, work_date, status P/A, overtime_hours)` ·
`attendance_summary(employee_id, period, present/penalty/OT/refreshment,
applied_rules JSON)` · `leave_reset(year)` · `sync_log`.
Attendance summarisation calls the pure engine in
`backend/modules/payroll/engine.py` (the one allowed reverse import).

## Screens
guide-images: pay-employees, pay-employee-profile, pay-attendance-grid,
pay-attendance-single, pay-sync.

## Known bugs
- payroll.html console errors on load (null-model bindings, pre-existing,
  UI recovers) — fix queued (ROADMAP Next).

## What's left
- [ ] Dedicated UI behind the Employee Management tile: employee list/profile,
      add/edit (non-financial fields), attendance entry moves here.
- [ ] Document uploads per employee (Aadhaar, agreements) — reuse inventory's
      attachment pattern (files on disk + zip backup).
- [ ] Field split with Salary: EM edits profile/status; Salary edits base,
      PF/ESI, advances (OPEN_QUESTIONS #2 records the default).
- [ ] Per-employee `bonus_eligible` flag (retires name exclusions in rules.json).
