# Architecture

## Stack

- **Backend:** Python 3.11, FastAPI 0.141, uvicorn. SQLite via stdlib `sqlite3` —
  one file, per-request connections, `PRAGMA foreign_keys=ON`,
  `check_same_thread=False` (each request owns its connection).
- **Frontend:** zero-build pages — Alpine.js 3 + Tailwind, both vendored in
  `frontend/vendor/` so everything works offline. No bundler, no npm.
- **Packaging:** PyInstaller onefile + pywebview (WebView2 window), two editions
  (`SALARY_EDITION=admin|operator`) from one codebase. CI at
  `.github/workflows/build-windows.yml` builds both exes and publishes GitHub
  Releases on `v*` tags; the app self-updates from those releases.
- **Tests:** stdlib unittest (`salary-system/new_system/tests/`), ~66 tests.
  Playwright (dev-only, in the venv) for browser E2E.

## Folder tree

```
salary_system/                        repo root
├── .github/workflows/                CI: Windows builds + Releases (tag-guarded)
├── docs/                             ← you are here (see START_HERE.md)
│   └── guide-images/                 screenshots used by USER_GUIDE.md
├── legacy/crude/                     superseded Tkinter prototype (reference only)
└── salary-system/
    ├── venv/                         local Python env (gitignored)
    └── new_system/                   the app (CI + packaging anchor — don't rename)
        ├── backend/
        │   ├── main.py               app assembly: session/auth, update, backup,
        │   │                         router includes, static mount — no business logic
        │   ├── core/                 infrastructure, no business logic
        │   │   ├── db.py             schema (all tables) + connect + column migrations
        │   │   ├── auth.py           PBKDF2 hashing + HMAC-signed session cookie
        │   │   ├── deps.py           get_db / current_user / require_admin /
        │   │   │                     require_module(key)  ← grant enforcement
        │   │   ├── registry.py       module registry (launcher tiles) — add modules here
        │   │   ├── rules.py          config/rules.json load/save (cached)
        │   │   ├── paths.py          dev vs frozen paths (data dir, config, files)
        │   │   ├── edition.py        admin vs operator edition (env var)
        │   │   ├── update.py         GitHub-Releases self-update (+ Windows updater bat)
        │   │   └── version.py        __version__ — CI tag must match
        │   └── modules/              one folder (or file) per business module
        │       ├── employees/        employee MASTER + attendance + leave + sync + seed
        │       │   ├── repo.py       SQL: roster, attendance, summaries, leave, sync data
        │       │   ├── router.py     /api/employees*, /api/attendance*, /api/sync/*, leave
        │       │   ├── sync.py       shared-folder JSON exchange (roster ⇄ attendance)
        │       │   └── seed.py       70 employees + 2 default accounts (+ grants backfill)
        │       ├── payroll/          the FINANCIAL side (consumes employees)
        │       │   ├── engine.py     pure rules engine (attendance→pay); no I/O
        │       │   ├── repo.py       SQL: advances, prepare→compute→publish, pay history
        │       │   ├── router.py     /api/rules, /api/advances*, /api/payroll/*, /api/pay,
        │       │   │                 /api/export/*
        │       │   └── exporters.py  CEO + Distribution .xlsx (legacy layouts)
        │       ├── inventory.py      heat register (single file until it grows)
        │       └── users.py          /api/modules (tiles) + /api/users* (accounts+grants)
        ├── frontend/
        │   ├── index.html + shell.js    login → Home launcher → Users & Access,
        │   │                            placeholders, update popup
        │   ├── payroll.html + app.js    Salary & Attendance SPA
        │   ├── inventory.html + inventory.js  Inventory SPA
        │   └── vendor/                  tailwind.js, alpine.js (offline)
        ├── config/                   rules.json (payroll policy) · sync.json · update.json
        ├── tests/                    test_payroll / test_inventory / test_users
        ├── data/                     runtime state, gitignored (salary.db, backups,
        │                             inventory_files/) — frozen builds use %APPDATA%
        └── apex_payroll.spec · build_windows.bat · run_admin.py · run_operator.py · desktop.py
```

## Conventions

- **Module shape:** a module starts as one file under `backend/modules/`, becomes a
  folder at its second file. Each module owns its routes (`router.py`, included from
  `main.py`) and its SQL (`repo.py`); URLs are flat `/api/...` (no per-module prefix
  except inventory's `/api/inventory/*`).
- **Dependency directions:** `core` never imports from `modules`. `payroll → employees`
  (payroll reads the master). The single allowed reverse import is
  `employees → payroll.engine` (pure functions, no I/O). New modules must not create
  cycles; when two modules need the same pure logic, it lives in an `engine.py`.
- **Auth & access:** stateless signed cookie (`core/auth.py`). Three gates in
  `core/deps.py`: `current_user` (any signed-in account), `require_admin`
  (admin role; also dead in Operator edition), `require_module(key)` (per-account
  grants; admins pass everything). Grants are a JSON list on `app_user.grants`;
  tiles come from `core/registry.py` via `/api/modules`.
- **Data:** every table is defined in `core/db.py` `SCHEMA` (CREATE IF NOT EXISTS —
  new tables apply on startup); post-ship column additions go in `_MIGRATIONS`.
  Derived values (stock remaining, attendance %) are computed on read, never stored
  as truth. Dropdown/select values are stored denormalized on records.
- **Errors:** data-layer functions raise `ValueError` for user mistakes; routers map
  them to HTTP 400. 401 = not signed in, 403 = no grant / wrong role.
- **Frontend:** one Alpine `x-data` object per page; `api()` helper always sends
  `X-Requested-With: apex-payroll`; modals that bind nullable models use
  `template x-if` (never `x-show`); money renders via `toLocaleString("en-IN")`;
  dates are local (never `toISOString`).
- **State-changing external actions** (publish payroll, update apply) confirm first
  and report outcomes via toasts.

## Run / dev / build

- Dev: `../venv/bin/uvicorn backend.main:app --reload` from `new_system/`
  (auto-creates + seeds `data/salary.db`). Force an edition:
  `SALARY_EDITION=operator`. Isolated data for experiments: `SALARY_DATA_DIR=/tmp/x`.
- **Rule: live smoke/E2E/screenshot runs MUST point `SALARY_DATA_DIR` at a
  scratch directory** — never the real `data/` (copy `data/salary.db` into the
  scratch dir first if realistic data is wanted). The unit tests already do this.
- Tests: `../venv/bin/python -m unittest discover -s tests`.
- Build/ship: see `salary-system/new_system/DEPLOY.md` (tag `vX.Y.Z` after bumping
  `backend/core/version.py`; CI publishes the Release the apps auto-update from).
