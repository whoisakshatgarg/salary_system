# Decisions log

Dated, newest last. Every entry: what was decided + why. (Questions still open
live in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).)

## 2026-08-06 — Inventory module scoping
- **Attachments on disk, not DB blobs** (owner's choice): files under
  `data/inventory_files/`, metadata in SQLite. Consequence: backups are zips of
  DB + files (implemented).
- **Order ID is free text** on inventory usage-log entries — no orders table
  existed. Will become a real reference when Order Tracking ships.
- **Heat fully editable** including heat number (surrogate `heat.id` under a
  unique `heat_number`); delete only while no movements.
- **Inventory admin-only** at the time (now grant-based, see 2026-08-13).

## 2026-08-08 → 10 — Deployment & updates
- **Self-update via GitHub Releases** (`whoisakshatgarg/salary_system`): check on
  every launch, popup only when newer, one-click swap with rollback; CI tag must
  match `backend/core/version.py`.
- **Backups are .zip** (salary.db + inventory files); pre-existing `.db` backups
  still listed.

## 2026-08-13 — Product scoping session (module map approved)
- **Target product:** one login → Home launcher → tiles per module; modules:
  Salary & Attendance, Inventory, Employee Management, Order Tracking,
  Parts & Pricing, Customers, Settings (+ admin-only Users & Access).
- **Employee Management owns the employee master and attendance**; Salary keeps
  the financial side (PF/ESI amounts, base-salary edits at payroll time,
  advances, publish). Rationale: HR data outlives payroll; tables were already
  separate. Backend split done (`modules/employees/`); dedicated UI pending.
- **Shipments are not a module** — a consignment entity + tab inside Order
  Tracking (partial shipments and multi-order consignments make shipment↔order
  many-to-many).
- **Pricing Tracker grows into Parts & Pricing (drawing master)**: drawing ID +
  revision + customer + rate history; Orders reference it.
- **Access model** (owner's choice): per-account module grants managed by the
  owner in Users & Access; launcher shows only granted tiles; server enforces via
  `require_module`. Admin role implicitly has everything. **Ships with only the
  2 existing accounts** (admin, operator); `temp` removed from the seed.
- **Deployment stays single-PC + SQLite** (packaged exe, offline-first,
  shared-folder sync). LAN/cloud deferred.
- **Order lifecycle: full 7 stages** (Enquiry → Quote → PO → Production → QC →
  Dispatch → Payment), stages skippable.
- **Pricing model: "as extensive as you can"** — per-operation costing that rolls
  up to ₹/piece totals, plus dated rate history per drawing+customer (quoted /
  agreed / revised). Planned for the Parts & Pricing build.
- **Consignment fields: standard GST set** — date, transporter, LR number,
  e-way bill no., invoice no., delivery confirmation; vehicle no. + freight ₹
  optional; lines link order items with quantities.
- **Order numbers auto per financial year with a configurable format** (template
  in Settings, e.g. `ORD-{FY}-{SEQ}`); customer's own PO number is a separate
  field.
- **Units configurable & searchable** — comprehensive unit list, searchable
  dropdowns, managed in Settings.
- **Salary rules unchanged** — already encoded in `config/rules.json` and locked
  by tests; no re-scoping needed.
- **Reorg executed** (5 commits): `backend/core/` + `backend/modules/{employees,
  payroll,inventory,users}`; `crude/` → `/legacy/`; empty `old_system` gitlink
  removed; zero URL/behavior changes (verified per step).
- **Shell built** (login, launcher, placeholders, Users & Access, update popup
  moved to shell); payroll SPA relocated to `/payroll.html`.
- **Post-review hardening** (32-agent adversarial review): the `salary` grant is
  now enforced server-side (whole employees router behind `require_module`);
  `current_user` re-checks the DB every request so deleted/demoted accounts lose
  access instantly (not after the 7-day cookie); last-admin guards made atomic;
  username charset restricted (token safety); operator kiosk regained the
  launch-time update check; shared period/row helpers deduplicated.
