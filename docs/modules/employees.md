# Employee Management

**Status: ✅ built** (backend split 2026-08-13; dedicated UI shipped 2026-08-13)

## Purpose
Own the employee master: who works here, their profile/status/documents, the
leave bank and attendance summaries. Payroll consumes this and keeps only the
financial side (owner's decision — DECISIONS 2026-08-13).

## The split with Salary (as built)
| Employee Management edits | Salary → Pay Setup edits |
|---|---|
| Name, department, shift, joining date | Base salary |
| Active / left status | PF & ESI applicability |
| Overtime-eligible flag (leave scheme) | Remaining advance balance |
| Leave bank (add/subtract days) | (advances ledger in its own tab) |
| Documents (Aadhaar, agreements, …) | |
| New-employee creation (incl. one-time starting pay) | |

Attendance **entry** stays in Salary & Attendance (operator flow + two-machine
sync live there); this module shows the summaries.

## User flows
- Roster with search, department + working/left filters; click a row for the
  full record.
- Detail: profile facts, leave bank with **+ / −** adjustment (non-OT only;
  bank can't go negative), read-only Pay box pointing at Pay Setup, documents
  (upload with a label, view/download/delete; images auto-compressed), and
  attendance stats (financial-year totals + last 6 months).
- Add employee: profile fields plus a one-time "Starting pay" box (base,
  PF/ESI) — afterwards pay is managed in Salary.
- Deactivate/reactivate (history always kept; no hard delete).

## Implemented (file paths)
- UI: `salary-system/new_system/frontend/employees.html` + `frontend/employees.js`.
- Data: `backend/modules/employees/repo.py` (roster, attendance, leave bank +
  `adjust_leave`, documents via shared `backend/core/attachments.py`, sync
  payloads). Routes: `backend/modules/employees/router.py` — gated
  `require_module("salary", "employees")` (shared data), with documents +
  leave-adjust behind `require_module("employees")`.
- Files on disk: `data/employee_files/` (`core/paths.employee_files_dir()`),
  included in backup zips.
- Seed: `backend/modules/employees/seed.py`. Spec: `tests/test_users.py`
  (EmployeeModule class) + `tests/test_payroll.py` for the rules engine.

## Data model
`employee(...)` · `attendance_day` · `attendance_summary` · `leave_reset` ·
`sync_log` · **`employee_document(id, employee_id, label, filename, mime,
size_bytes, stored_name, uploaded_at)`** (cascade-deletes with the employee).

## Screens
guide-images: em-roster, em-detail, em-edit; pay-employees (the slimmed
Pay Setup); pay-attendance-grid / pay-attendance-single (entry, in Salary).

## Known bugs
None known.

## What's left
- [ ] Move attendance ENTRY here once the operator flow + sync move is designed
      (kiosk lands in Salary today) — ROADMAP Later.
- [ ] Per-employee `bonus_eligible` flag (retires name exclusions in rules.json).
- [ ] Payroll's employee-profile modal could merge into this module's detail.
