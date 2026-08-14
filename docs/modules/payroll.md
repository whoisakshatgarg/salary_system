# Salary & Attendance (payroll)

**Status: ✅ built** (the original product; reorganised 2026-08-13 into the
financial module — the employee master now lives in modules/employees).

## Purpose
Turn a month of attendance into published pay: rules-driven attendance
summaries, advances ledger, salary prepare→calculate→publish, pay history, and
the two legacy-layout Excel exports.

## User flows
1. Operator fills the month's attendance (grid or calendar; deadline reminder
   banner until complete) → syncs to the CEO machine.
2. CEO: Calculate Salary → Prepare (one editable row per employee, ⚠ badges
   where penalty rules fired) → set PF/ESI (bulk buttons) / advances recovery →
   Calculate → Publish (writes pay, posts advance ledger, updates attendance
   record with CEO's discretionary numbers).
3. Exports: CEO sheet + Distribution slip (.xlsx) per period. Advances issued
   any time (cheque+cash must equal amount). January leave reset.

## Business rules (encoded in `config/rules.json`, locked by tests)
Paid Sundays; 8 OT hours = one day's base; refreshment day = present non-Sunday
with OT ≥ 3h (flat ₹8); non-OT employees draw a 12-day leave bank; OT-eligible
employees get penalty days (highest of consecutive-run / monthly tiers);
CNC full-attendance bonus ₹200 with name exclusions; PF/ESI manual amounts;
`total = round(base·att% + OT + refreshment + bonus − PF − ESI − advance)`.

## Implemented (file paths)
- Engine (pure): `salary-system/new_system/backend/modules/payroll/engine.py` —
  `summarize_attendance`, `compute_salary`. Spec: `tests/test_payroll.py`.
- Data: `backend/modules/payroll/repo.py` (advances CR/DR, prepare/compute/
  publish, pay history). Routes: `backend/modules/payroll/router.py`.
- Excel: `backend/modules/payroll/exporters.py` (`/api/export/{ceo|distribution}/{period}`).
- Rules config: `backend/core/rules.py` + raw editor in the SPA's Rules tab.
- UI: `frontend/payroll/index.html` + `payroll.js` (served at `/payroll/`). The employee screen here
  is **Pay Setup** (base salary, PF/ESI, advance — via `PUT /api/employees/{id}/pay`);
  profiles/documents/leave live in Employee Management. Attendance entry stays
  here (operator flow + sync).

## Data model
`pay(employee_id, period, base, base_att, pf, esi, overtime_*, refreshment_*,
attendance_percentage, penalty_days, adv_deducted, gross, bonus, total, cheque,
cash, old/new_advance, published_at)` · `advance(employee_id, amount, txn_date,
type CR/DR, cheque, cash, note)`. Periods are `YYYY-MM` everywhere.

## Screens
guide-images: pay-dashboard, pay-salary-table, pay-history, pay-exports,
pay-rules, pay-advances.

## Known bugs
- Console errors on page load (pre-existing null-model bindings; UI recovers) —
  ROADMAP Next.
- Excel layouts never byte-matched against a real office sample
  (OPEN_QUESTIONS #3).

## What's left
- [ ] Hand attendance ENTRY to the EM module (operator flow + sync move — ROADMAP Later).
- [ ] Friendlier rules editor (Settings module) instead of raw JSON.
- [ ] PDF salary slips; audit trail of publishes (ROADMAP).
