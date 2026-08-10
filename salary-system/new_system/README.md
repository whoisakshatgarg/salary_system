# APEX THERMOCON — Salary System (new_system)

A clean rewrite of `old_system/Payroll-System`. Same business rules, none of the
pain: **no MySQL** (single SQLite file), no 20-script-`os.system`-maze, no
hardcoded credentials, no plaintext passwords, no schema drift — and a friendly,
dynamic browser UI instead of pixel-positioned Tkinter windows.

## Why these choices

| Old system | New system | Why |
|---|---|---|
| MySQL server (root/123456 hardcoded) | **SQLite** file at `data/salary.db` | Zero setup/server, real SQL, copy-to-backup |
| ~20 Tkinter scripts launched via `os.system` | **FastAPI** backend + one browser SPA | One process, real routing, shareable UI |
| Plaintext `admin` table | PBKDF2-hashed `app_user` (stdlib) | Security; still nothing to install |
| Rules hardcoded across files (names, 200, 12, 8…) | **`config/rules.json`** | Policy = data, not code ("dynamic") |
| Only monthly summary stored | Daily `attendance_day` rows kept | Auditable, recomputable |
| Month stored as English name *and* `YYYY-MM` | Uniform `YYYY-MM` everywhere | Fixes the export month-mismatch bug |
| `emp_id = MAX(id)+1` (race) | `AUTOINCREMENT` PK | Correct, concurrent-safe |

## Layout

```
new_system/
├── config/rules.json     # all configurable business rules (defaults = old system)
├── backend/
│   ├── payroll.py        # ★ pure calculation engine (attendance → pay, no I/O)
│   ├── rules.py          # load/save rules.json
│   ├── db.py             # SQLite schema + connection (stdlib sqlite3)
│   ├── auth.py           # PBKDF2 password hashing (stdlib)
│   ├── seed.py           # migrate the 70 real employees + default logins
│   ├── exporters.py      # CEO + Distribution .xlsx   (TODO — needs openpyxl)
│   └── main.py           # FastAPI app + JSON API + serves the UI   (TODO)
├── frontend/             # zero-build SPA (Tailwind + Alpine via CDN)   (TODO)
├── data/                 # salary.db lives here (gitignore)
├── tests/test_payroll.py # executable spec of the business rules (17 tests, green)
└── requirements.txt
```

`★` and the data layer are **done and tested today** using only the standard
library. `TODO` items are the next phase (they need `pip install`).

## Business rules (all in `config/rules.json`)

All editable in `config/rules.json`; each is locked by a test in
`tests/test_payroll.py`.

- **Sundays** are paid weekly-offs and never count as absences.
- **Overtime / refreshment** apply to *any* employee who logs OT (8 OT hours =
  one day's base pay; a present non-Sunday day with OT ≥ 3 hrs is a refreshment
  day paying a flat rate). The per-employee `overtime_eligible` flag only selects
  the leave/penalty scheme below.
- **Non-overtime-eligible** employees: a **12-day/year paid-leave bank** (reset
  every January); absences beyond the bank are unpaid.
- **Overtime-eligible** employees: no bank; **penalty days = consecutive-run
  penalty + monthly-volume tier, stacked**:
  - +1 per maximal run of **≥3 consecutive** non-Sunday absences (a 6-in-a-row is
    one run = +1; a paid Sunday breaks a run).
  - tier on **total** non-Sunday absences: 4–6 → +1, 7–12 → +2, **13+** → +3.
- Flat **CNC perfect-attendance bonus** (₹200), with name exclusions (slated to
  become a per-employee flag).

Bugs fixed vs the legacy code: December days-in-month; the "only fires at exactly
3 absences" consecutive bug; the ">18 absences falls through to zero" tier bug;
and the current-vs-previous-month export mismatch (uniform `YYYY-MM` everywhere).

## Run

```bash
# 1. core works with the stdlib only — no install needed:
../venv/bin/python -m unittest discover -s tests       # 17 tests
../venv/bin/python -m backend.seed                     # create + populate data/salary.db

# 2. web app (next phase, after deps land):
../venv/bin/pip install -r requirements.txt
../venv/bin/uvicorn backend.main:app --reload
```

### Two machines (operator + CEO) — offline sync

The operator and CEO each run their own copy (own `salary.db`) and exchange data
as JSON files through a **shared cloud folder** (Google Drive / Dropbox /
OneDrive). The CEO's machine is the **master of record**. Point both apps at the
folder in **`config/sync.json`** (`shared_folder`); leave it blank to fall back to
manual download/upload. Then in the **Sync / Exchange** tab:

- **Operator** → *Export attendance* writes `attendance-<month>.json`; the **CEO**
  imports it (recomputes summaries/leave/penalty on import) and runs payroll.
- **CEO** → *Export roster* writes `roster.json`; the **operator** imports it to
  keep the employee list in sync.

On login each side auto-checks the folder. Behaviour differs by role:

- **CEO**: if last month's attendance is waiting, a **Yes/No prompt** offers to
  import it; other incoming files show in a banner. The Sync tab also has a
  **Database backup** card — *Back up now* (timestamped copy in `data/backups/`)
  and *Download a copy* (consistent SQLite online backup, safe while running).
- **Operator**: a roster update is **imported automatically** (silent, with a
  toast). And a **deadline reminder** banner shows until the previous month's
  attendance is complete — *"Fill May 2026 attendance · 60/70 done · due by the
  7th — N days left / overdue"* (the entitlement is the 7th of each month).

Imports are idempotent (de-duplicated by file hash) and direction is role-locked
(only the CEO imports attendance; only the operator imports the roster). Endpoints
under `/api/sync/*`, `/api/backup*`, `/api/attendance-status/*`; import history in
the `sync_log` table. Both DBs start from the same seed so employee IDs line up
from day one.

### Roles & logins (created by the seed — CHANGE THESE)

| Login | Role | Can do |
|---|---|---|
| `admin` / `admin123` | **CEO** | Everything: employees, advances, salary calc + publish, exports, rules — and may override attendance metrics at calc time |
| `operator` / `operator123` | **Attendance operator** | Attendance entry/view only. Cannot see advances, salaries, exports, or rules |

The operator records each month's attendance for every employee; the CEO then
runs the salary calculation.

The **attendance editor** has two modes:
- **All employees (grid)** — default; a spreadsheet of every employee (rows) ×
  every day (columns). Everyone defaults to Present; the operator just toggles
  absences, types overtime where needed, and hits **Save all** (one bulk
  request). Department filter, name search, and per-row "all P" / "OT…"
  shortcuts keep it fast and low-error. Sticky header row + employee column.
- **Single employee** — a vertical **calendar**: each column is a week, rows run
  **Mon→Sun** (read a column top-to-bottom, then the next column is the next
  week). Each day has a P/A toggle with an **overtime box beside it**.

Either way the **CEO** sees the computed summary as editable (paid days, penalty
days, OT, refreshment) with a **Save adjustments** button, and a ⚠ box lists
which penalty rules fired automatically (with dates).

In the CEO's **salary table** the attendance columns (paid **Days**, **Att%**,
**Penalty** days, **OT hrs**, **Refr**) are likewise editable — a ⚠ badge marks
rows where a penalty rule fired (hover for the rule and dates), and the CEO can
add/remove punishment days at discretion. Final numbers are written back to the
attendance record and the exported slip on **Publish**.

## Status / roadmap

- [x] SQLite schema + connection layer
- [x] Configurable rules file
- [x] Pure payroll engine (attendance, leave, salary, bonus, advances)
- [x] Employee + user migration (70 employees)
- [x] Rule test suite (17 green)
- [x] FastAPI JSON API (24 routes: employees, attendance, advances, calculate, publish, exports, rules)
- [x] CEO + Distribution `.xlsx` exporters (legacy layout; needs a real sample to byte-match)
- [x] Browser UI (login, dashboard, employees, attendance grid, advances, salary table, history, exports, rules)
- [x] Auth/session + role gating (admin vs temp)
- [x] Self-update from GitHub Releases (`backend/version.py` + `backend/update.py` + `config/update.json`; release flow in `DEPLOY.md`)

### Verified end-to-end (smoke test)

Login → role-gating (temp blocked from payroll/exports) → attendance for OT & non-OT
employees (leave bank, refreshment days, penalties) → advance issue → payroll
prepare/calculate/publish (salary formula + CNC bonus exclusion + advance
recovery) → both Excel exports open with correct numbers. See `tests/` for the
rule-level spec.

### Possible next steps

- Byte-match the two `.xlsx` against a real sample sheet.
- Per-employee `bonus_eligible` flag (retire the hardcoded name exclusions).
- Bulk attendance entry / CSV import; printable PDF slips.
- Change-password UI; audit log of who published which period.
