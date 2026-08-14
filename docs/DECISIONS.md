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

## 2026-08-13 (later) — Employee Management UI shipped
- Dedicated page (`frontend/employees.html`) behind the 👥 tile; payroll's
  Employees screen slimmed to **Pay Setup** (base/PF/ESI/advance only) — the
  agreed people/money split is now real in the UI.
- Employee documents: files on disk (`data/employee_files/`), shared
  validation in `core/attachments.py` (inventory refactored onto it), included
  in backup zips.
- Leave bank gets explicit +/− adjustment (never negative; OT-eligible have
  none). New-employee creation lives in EM with a one-time starting-pay box.
- Access: shared roster/attendance routes accept salary OR employees grants
  (`require_module` is multi-key now); documents + leave adjust need the
  employees grant.
- E2E caught a latent API bug: `leave_balance: null` crashed employee creation
  (int(None)); fixed + regression-tested.

## 2026-08-14 — All remaining tiles built (Customers, Parts & Pricing, Orders, Settings)
- **Settings** (`backend/modules/settings.py` + `frontend/settings.html`):
  order-number format (`{FY}`/`{YYYY}`/`{SEQ}`, live preview), searchable units
  list (~50 seeded), machining operations with editable ₹/hour, departments.
  Seeding is first-run-only; reads need the settings grant, writes are admin.
- **Customers** (`modules/customers.py`): master + contact persons; delete only
  while unreferenced, otherwise deactivate.
- **Parts & Pricing** (`modules/parts.py`): drawing_no+revision master (new
  revision = fresh record; pricing history is per revision), drawing files on
  disk (`drawing_files/`, in backup zips), dated rate history
  (quoted/agreed/revised), and the extensive costing model the owner asked
  for: operations × minutes × ₹/hr (rates snapshot at entry) + material +
  margin → ₹/piece, recordable straight into the rate history.
- **Orders** (`modules/orders.py`): 7 skippable stages with a logged history;
  items reference drawings (latest rate prefills); order numbers atomic per
  FY from the configurable format; heat traceability by joining inventory
  movements on the order number; consignments with the GST field set, lines
  spanning multiple orders, partial shipments, over-ship refused, delivered
  flag; orders locked against deletion while shipped.
- **Reference data for form pickers is grant-gated per consumer**
  (`/api/orders/refs`, `/api/parts/refs`): an early shared `/api/lists` leaked
  drawing prices + operation rates to any signed-in account (review catch) and
  was removed the same day.
- E2E caught & fixed: parts rate/costing boxes used x-show over nullable
  models (console errors); customer create now opens the record.
- Post-review hardening (22-agent pass, 18 confirmed): removed the /api/lists
  price leak; order-number formats must contain {FY} (per-FY sequence would
  collide across years) and collisions return a friendly 400; finite+bounded
  numeric guards on operation rates and costing money; one shared rollup for
  displayed and recorded costing totals; BEGIN IMMEDIATE on drawing
  save/delete; consignment order-search hits the server; drawing switch
  replaces the prefyled rate; restore docs cover all *_files folders.

## 2026-08-14 (later) — Frontend reorganised by module; one entry point per module
- **`frontend/<module>/index.html` + `<module>.js`, served at `/<module>/`** —
  the flat pile of `*.html`/`*.js` made it hard to see which files belong to
  which tab. `vendor/` stays shared (pages load `/vendor/…`); the shell keeps
  `index.html` at the root because it is the entry point, with its script in
  `shell/`.
- **A module is reachable ONLY from its launcher tile.** The payroll dashboard
  still carried a "Raw Material Inventory" card from before the shell existed —
  a second door into a module that already has a tile. Removed, along with
  `openInventory()` and the inventory-specific `open_inventory` pywebview hook
  (the generic `open_path` covers viewing files). An audit of every page found
  no other sideways link: the only cross-module link anywhere is Home.
- **Home moved to the top-left of every page**, beside the APEX/module name
  (it was buried bottom-right in the header, or bottom of the payroll sidebar).
  `?back=1` and the `showBack` flag are gone — Home is always there.
