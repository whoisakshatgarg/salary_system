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
- **Tests:** stdlib unittest (`salary-system/new_system/tests/`), 100+ tests.
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
        │   │   ├── attachments.py    shared file validation (mime allowlist, size caps)
        │   │   ├── auth.py           PBKDF2 hashing + HMAC-signed session cookie
        │   │   ├── deps.py           get_db / current_user / require_admin /
        │   │   │                     require_module(*keys) ← grant enforcement (ANY of the keys)
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
        │       ├── inventory.py      heat register + piece-level stock + the
        │       │                     manufacturability check (2nd router: /api/material/*)
        │       ├── customers.py      customer master + contacts + agreed operation rates
        │       ├── parts.py          drawing master, rate history, costing + bill of materials
        │       ├── orders.py         orders, stages, consignments, delivery plans,
        │       │                     deadlines, FY numbering
        │       ├── quotations.py     quotations + invoices (one table, a `kind`) + print view
        │       ├── settings.py       config: order format, units, operation rates, departments
        │       └── users.py          /api/modules (tiles) + /api/users* (accounts+grants)
        ├── frontend/                 ONE FOLDER PER MODULE (URL = folder)
        │   ├── index.html            the shell: login → Home launcher (entry point)
        │   ├── shell/shell.js        launcher, Users & Access, update popup,
        │   │                          order-deadline panel on Home
        │   ├── payroll/              index.html + payroll.js   → /payroll/
        │   ├── employees/            index.html + employees.js → /employees/
        │   ├── inventory/            index.html + inventory.js → /inventory/
        │   ├── customers/ · parts/ · orders/ · quotations/ · settings/  → /<module>/
        │   ├── help/                GENERATED user guide (index.html + images/)
        │   │                        → /help/ — build with tools/build_help.py
        │   └── vendor/               tailwind.js, alpine.js (offline, shared:
        │                             pages load it as /vendor/…)
        ├── config/                   rules.json (payroll policy) · sync.json · update.json
        ├── tools/build_help.py       docs/USER_GUIDE.md → frontend/help/ (stdlib only)
        ├── tests/                    test_payroll / test_inventory / test_users /
        │                             test_workshop / test_help_build
        ├── data/                     runtime state, gitignored (salary.db, backups,
        │                             inventory_files/, employee_files/, drawing_files/) — frozen
        │                             builds use %APPDATA%
        └── apex_payroll.spec · build_windows.bat · run_admin.py · run_operator.py · desktop.py
```

## Conventions

- **Module shape:** backend — a module starts as one file under `backend/modules/`,
  becomes a folder at its second file. Frontend — every module owns a folder
  `frontend/<module>/` with `index.html` + `<module>.js`, served at `/<module>/`.
- **One entry point per module:** a module is reachable only from its launcher
  tile (`core/registry.py`). Pages never link sideways into another module — the
  single cross-module link on any page is Home (top-left). This is why the
  payroll dashboard no longer carries its own Inventory shortcut. `/help/` is the
  one page that is NOT a module: no registry entry, no grant, reachable from its
  Home tile and directly by URL, because someone who is stuck needs to read it.
  Each module owns its routes (`router.py`, included from
  `main.py`) and its SQL (`repo.py`). Newer modules namespace their routes with a
  router prefix (`/api/{inventory,customers,parts,orders,settings}/*`); the
  original payroll/employee routes stay flat (`/api/employees`, `/api/attendance*`,
  …) because moving them would break installed clients.
  **One documented exception to one-router-per-module:** `inventory.py` also
  exports `check_router` (`/api/material/*`) gated on
  `require_module("inventory", "quotations", "orders")`, because the material
  availability check and the bill-of-materials stock search are offered from
  three screens whose users do not all hold the inventory grant.
- **Dependency directions:** `core` never imports from `modules`. `payroll → employees`
  (payroll reads the master). The single allowed reverse import is
  `employees → payroll.engine` (pure functions, no I/O). New modules must not create
  cycles; when two modules need the same pure logic, it lives in an `engine.py`.
- **Auth & access:** stateless signed cookie (`core/auth.py`). Three gates in
  `core/deps.py`: `current_user` (any signed-in account), `require_admin`
  (admin role; also dead in Operator edition), `require_module(*keys)` (per-account grants; admins pass
  everything; SEVERAL keys means a route shared between modules and the account
  needs any ONE of them — unknown keys fail at import, and the Operator edition
  refuses every key except `salary` regardless). Grants are a JSON list on `app_user.grants`;
  tiles come from `core/registry.py` via `/api/modules`. Cross-module reference
  data (customer/drawing/unit pickers) is served per consumer behind that
  consumer's own grant — `/api/orders/refs`, `/api/parts/refs` — never from one
  shared open endpoint, so pricing can't leak past a module's grant.
- **Data:** every table is defined in `core/db.py` `SCHEMA` (CREATE IF NOT EXISTS —
  new tables apply on startup); post-ship column additions go in `_MIGRATIONS`,
  and a column that shipped and was then withdrawn goes in `_RETIRED`, which
  `_migrate()` best-effort DROPs (older SQLite just keeps it — harmless).
  Derived values are computed on read, never stored as truth: stock remaining,
  attendance %, an order's shipped/pending quantities (summed from consignment
  lines), and the unplanned balance of a delivery plan (item qty − Σ planned).
  Snapshots are the deliberate opposite and are marked as such — operation rates,
  agreed customer rates and bill-of-materials unit costs are COPIED into the
  costing at save time so reopening an old quote never silently reprices it. Dropdown/select values are stored denormalized on records.
- **Errors:** data-layer functions raise `ValueError` for user mistakes; routers map
  them to HTTP 400. 401 = not signed in, 403 = no grant / wrong role.
- **Frontend:** one Alpine `x-data` object per page; `api()` helper always sends
  `X-Requested-With: apex-payroll`; modals that bind nullable models use
  `template x-if` (never `x-show`); a `<select>` prefilled from saved data needs
  `x-effect="v && $nextTick(() => $el.value = v)"` or it renders blank while
  holding the right value (Alpine applies `x-model` before `x-for` has made the
  options); money renders via `toLocaleString("en-IN")`; dates are local (never
  `toISOString`).
- **Adding vs editing:** ADDING opens a full screen (`fixed inset-0 z-[60]`, the
  costing-workspace pattern) in Inventory, Quotations and Orders; EDITING keeps
  the modal — except Orders, where the same form fills the screen either way.
  Customer and order RECORDS are full windows too. z-index ladder: detail 40,
  form modal 50, full-screen view 60, toast 70.
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
