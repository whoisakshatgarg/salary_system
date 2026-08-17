# START HERE — APEX THERMOCON Workshop ERP

One offline-first app for a precision machining workshop (steel/brass/copper parts
against customer drawings): **login → Home launcher → one tile per module**, with
per-account access managed by the owner. Runs on a single PC per user as a packaged
Windows `.exe` (PyInstaller + pywebview); two laptops exchange data through a shared
cloud folder. Self-updates from GitHub Releases.

**Stack:** Python 3.11 · FastAPI · SQLite (stdlib, one file) · Alpine.js + Tailwind
(vendored, zero-build) · unittest · Playwright for E2E · GitHub Actions builds.

**Run it:** `cd salary-system/new_system && ../venv/bin/uvicorn backend.main:app --reload`
→ http://127.0.0.1:8000, sign in `admin` / `admin123`.
Tests: `../venv/bin/python -m unittest discover -s tests`.

## Module status

| Module | Tile | Status | Doc |
|---|---|---|---|
| App shell (login, launcher, Users & Access) | — | ✅ built | [modules/shell.md](modules/shell.md) |
| Salary & Attendance (payroll) | ₹ | ✅ built | [modules/payroll.md](modules/payroll.md) |
| Raw Material Inventory (heats, piece-level stock, chemistry, suppliers, material check) | ⛭ | ✅ built | [modules/inventory.md](modules/inventory.md) |
| Employee Management (people, documents, leave bank) | 👥 | ✅ built | [modules/employees.md](modules/employees.md) |
| Order Tracking (stages, consignments, delivery plans, deadlines) | 🗂 | ✅ built | [modules/orders.md](modules/orders.md) |
| Parts & Pricing (drawings, rate history, costing, bill of materials) | 📐 | ✅ built | [modules/parts-pricing.md](modules/parts-pricing.md) |
| Quotations & Invoices (printable copies) | 🧾 | ✅ built | [modules/quotations.md](modules/quotations.md) |
| Customers (codes, order history, growth, agreed per-operation rates) | 🏢 | ✅ built | [modules/customers.md](modules/customers.md) |
| Settings (order format, units, op rates, departments) | ⚙ | ✅ built | [modules/settings.md](modules/settings.md) |
| Self-update / backups / two-machine sync | — | ✅ built (shared services) | [ARCHITECTURE.md](ARCHITECTURE.md) |
| In-app User Guide (`/help/`, generated, **owner-only**) | 📖 | ✅ built | [USER_GUIDE.md](USER_GUIDE.md) |

## Reading order for a new session

1. This file.
2. [ARCHITECTURE.md](ARCHITECTURE.md) — folder tree, conventions, how things talk.
3. [DECISIONS.md](DECISIONS.md) + [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) — what's settled vs defaulted.
4. [ROADMAP.md](ROADMAP.md) — what to build next (Now / Next / Later) and the
   known-but-unfixed defects ([QA-FINDINGS.md](QA-FINDINGS.md) is the full sweep).
5. [UI-STYLE.md](UI-STYLE.md) — the design system every screen follows
   (instrument tables, dot-chips, link-tabs; includes the Alpine traps).
6. The module doc for whatever you're touching (`modules/<name>.md`).
7. End-user behaviour reference: [USER_GUIDE.md](USER_GUIDE.md) (illustrated).
   It is also the SOURCE for the in-app guide at `/help/` — after editing it run
   `python tools/build_help.py` and commit the regenerated `frontend/help/`.
   Every `## ` chapter must be followed by an `<!-- access: KEY -->` marker (a
   grant key, or `general` / `admin`) — that is what scopes the guide to the
   signed-in account. `tests/test_help_build.py` fails if one is missing or names
   a grant that doesn't exist.

**Rules of the road:** code is the source of truth — verify docs against it and fix
docs when they drift. Every session must end by updating this file's status table,
ROADMAP.md, OPEN_QUESTIONS.md and DECISIONS.md.
