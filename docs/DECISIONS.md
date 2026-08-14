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

## 2026-08-14 (later still) — Quotations/invoices, customer codes, costing workspace
- **Bug**: the payroll sidebar badge read the app EDITION, so signing in as the
  operator account inside the normal app still showed "CEO / Admin". It now
  reads the signed-in role.
- **Performance**: measured rather than guessed — APIs 2-4 ms, pages 43-72 ms,
  the 2 873-cell attendance grid 354 ms. Nothing pathological. The one
  structural cost was the no-cache middleware re-serving 440 KB of vendored
  Tailwind/Alpine on every navigation (this is a multi-page app), so
  `/vendor/*` is now cached for 7 days — cached BY FILENAME, so swapping a
  vendored library means renaming the file.
- **Customer codes**: abbreviation + serial (AC01, AC02), assigned inside the
  same transaction as the insert, `customer.code` UNIQUE as the backstop, and
  backfilled on startup for customers created before codes existed.
- **Costing gains weightage and a per-operation additional margin**, and moves
  into its own full-screen workspace (part + revisions + customer on the left,
  operations table on the right). One formula, `parts.op_cost`, is shared by the
  live UI figure and the stored value so they cannot disagree.
- **The additional margin is ₹ per hour, not a percentage.** It is added to the
  operation's rate, so the row is `minutes ÷ 60 × (rate + extra) × weightage`.
  A job shop quotes in rupees per hour — "this one runs on the CNC, charge ₹50
  more an hour" is the sentence the estimator actually says, and it stays
  comparable to the shop rate it sits next to. A percentage would have made two
  operations with the same uplift show different numbers. The column
  `costing_op.extra_margin_pct` was replaced by `extra_rate`; `db._RETIRED`
  drops the old column best-effort so an existing database converges without a
  manual migration step.
- **Agreed rates live on the customer, not the drawing.**
  `customer_operation_rate` holds `(rate_per_hour, extra_rate)` per operation,
  and `/api/parts/refs?customer_id=` merges them over the Settings defaults,
  flagging each overridden row `custom` so the UI can badge it. Rates are
  negotiated per customer and apply to every part they order, so storing them
  per drawing would mean re-entering the same number on each new part and
  leaving the old ones stale after a renegotiation. Saved costings keep their
  snapshot, so repricing is never retroactive.
- **Quotations & Invoices** as a new module/tile sharing one `document` table
  (a `kind` distinguishes them); invoices prefill from an order's items.
  Printing is HTML + the browser's "Save as PDF" — no PDF dependency, works
  offline, and the layout stays editable by anyone who can read HTML.
- **Inventory gains piece-level rows, and feasibility is counted per piece.**
  `heat_piece(length_mm, diameter_mm, quantity)` sits under the heat, and
  `parts_from_piece` = `floor(length / (part_length + margin))`. Explicitly NOT
  total volume ÷ part volume: offcuts can't be welded together, so three 10-unit
  bars make 9 parts of 3, not 10. Piece rows are optional — a heat without them
  is still checkable by quantity — but when present their quantities ARE the rod
  count, so stock and feasibility can't disagree.
- **The tolerance/margin is length per part.** Each part consumes
  `part_length + margin` (the parting-off and facing allowance), and diameter is
  a plain filter: a bar qualifies when its Ø is at least the part's. A single
  number in the same units as the dimension beside it, rather than a percentage
  of stock or a second diameter allowance, because that is the number an
  estimator already has in their head.
- **Heat numbers are never merged.** Two bars of the same size from different
  heats are different steel, so they stay separate records and every feasibility
  answer is broken down heat by heat. A heat with no piece dimensions is
  reported as such rather than dropped, so stock never silently disappears from
  an answer.
- **The check is advisory, not a reservation.** It reserves nothing and never
  blocks a save; stock still leaves only through the usage log. Reserving would
  need an allocation record, an expiry, a release path and a way to see what is
  held — a much bigger feature than "can we make this?", and the wrong default
  for a shop where the answer is usually yes.
- **Consumption maps to piece rows FIFO.** The usage log records rods against
  the HEAT, not a specific bar, so availability applies consumption to piece
  rows in receipt order. Deterministic, needs no new schema, and the piece
  totals always reconcile with the heat's derived `remaining`.
- **Adding opens a full screen; editing keeps the modal.** A delivery is a dozen
  bars across several heat numbers and does not fit in a dialog. Implemented as
  an in-page `fixed inset-0 z-[60]` view (the costing-workspace pattern), not a
  new URL — `registry.py` keeps one entry point per module, and nothing in this
  codebase parses a query string. Inventory, Quotations and Orders follow this;
  the other modules still use their add-modal.
- **A customer record is a full window, not a dialog.** Profile + growth chart +
  order history + rate card is more than a modal can hold. Same in-page
  `fixed inset-0` treatment as the costing workspace and the inventory add
  screen, with a Back button; the edit form still layers a modal on top (z-50
  over z-40).
- **Chemistry belongs to the piece row, not the delivery.** Composition is the
  reason heat numbers are kept apart in the first place, so each row on the
  incoming-material screen carries its own. Rows sharing a heat number share one
  analysis (first non-empty wins) and the delivery-level block stays as a
  fallback for rows that supply none.
- **Supplier became a learned dropdown** (`inv_option` kind `supplier`) rather
  than free text, so the same mill isn't spelled three ways across a year of
  deliveries. Nothing is seeded — the list is built from what is typed — and
  `backfill_suppliers()` seeds it from existing heats on startup.
- **Prefilled `<select>`s need `x-effect` + `$nextTick`.** Alpine applies
  `x-model` before `x-for` has rendered the options, so a select bound to saved
  data renders BLANK while holding the right value — and a blind Save then wipes
  the field. `x-effect="v && $nextTick(() => $el.value = v)"` re-applies it after
  the options exist. `x-effect` alone is not enough: it runs too early and never
  retries. This fixed the reported "Edit shows Customer as —" on quotations as
  well as the inventory edit form.
- **Delivery plans hang off the order ITEM, and the remainder is derived.** A
  quantity only means something against the item it is a quantity of, and
  storing "the rest" as a fourth row would let it drift out of step with the
  first three. `unplanned = item qty − Σ planned`, computed on read; a plan that
  exceeds the item is refused.
- **Deadlines are bucketed server-side** (overdue / 7 days / 31 days) so the
  Home panel renders lists rather than doing date arithmetic in a template.
  Fully-shipped orders are excluded — an order with nothing left to send is not
  a deadline. The shell fetches it fail-closed so accounts without the orders
  grant just see no panel rather than an error.
- **Shipment progress is derived from consignment lines**, never stored on the
  order. An order delivered in six instalments over four months is the normal
  case here, and a stored "shipped" column would need updating from four
  different places.
- **The BOM prices material from inventory, and snapshots it.** Unit costs are
  derived from what the steel actually cost (`price_total ÷ rods_received`), and
  copied into `costing_material` on save for the same reason operation rates are
  snapshotted: reopening a quote must not silently reprice it.
- **BOM quantity is entered as "parts from one rod", not as a fraction.** The
  stored `qty_per_piece` is `1/N` at 8 decimal places, multiplied out to paise
  only at the end. Reusing `_check_money` here rounded one third to 0.33 and
  underpriced every piece by 1% — hence a separate `_check_ratio`.
- **When BOM lines exist they ARE the material cost**, and the manual box is
  disabled. Leaving both editable is how the two end up disagreeing.
- **The order record is a full window and its form fills the screen for edit as
  well as add** — an order carries items, a delivery plan, shipments and a stage
  history. Orders are the exception to "edit stays a modal": the form is the
  same one used to add, and switching surfaces between the two would be arbitrary.
- **An order's bill of materials is derived from its parts, not stored on the
  order.** `required = qty_per_piece × item qty`, rolled up by heat number and
  read against what the usage log has actually issued. Storing it would mean a
  second copy to keep in step with every re-costing and every quantity change;
  deriving it means the answer is always current, and the costing it came from
  is named so it can be traced. Items that cannot contribute (no drawing, no
  costing, hand-typed material cost) are listed with the reason — a rollup that
  silently under-reports material is worse than no rollup.
- **A material requisition is issued and frozen, not rendered live.** The
  on-screen rollup is derived and moves whenever a drawing is re-costed; a sheet
  in the store keeper's hand must not. So issuing copies every figure into
  `material_doc`/`material_doc_line` under its own per-FY number, and re-costing
  afterwards produces a NEW requisition rather than altering the old one. The
  order number and customer name are snapshotted too, so the document outlives a
  deleted order. This is the same reasoning as snapshotting rates into a costing,
  applied to a piece of paper that leaves the office.
- **The in-app guide is scoped to the signed-in account.** An operator who can
  only open Inventory should not have to scroll past payroll and invoicing to
  find the chapter they need. Each chapter declares its grant key **in the guide
  itself** (`<!-- access: salary -->` under the heading) rather than in a table
  in the generator, so renumbering or reordering chapters cannot silently
  detach the mapping; a test asserts every chapter has one and that the keys are
  real grants. `general` always shows, `admin` only to admins, and signed-out
  falls back to general-only so logging out is not a way around it.
  Deliberately **presentational, not a security boundary** — the generated HTML
  still contains every chapter. It declutters the manual; the thing that
  actually protects data is `require_module` on each route. If the guide ever
  needs to be a real boundary, the page would have to be assembled server-side
  per request.
- **The user guide is owner-only, enforced server-side.** Scoping it per grant
  was the first ask; the owner then narrowed it to their account alone. Hiding
  the tile would not have been enough — `/help/` was part of the StaticFiles
  mount, which serves whatever it holds to anyone who guesses a URL. So it has
  real routes declared before the mount and gated on the admin role, covering
  the screenshots as well as the page: a picture readable by URL would leak the
  same content. Refusals render as a page, not JSON, because this is something a
  person opens in a browser. The per-chapter `access` markers stay: they cost
  nothing now (admins hold every grant) and mean the guide can be reopened to
  other roles by relaxing one guard.
- **The guide's scoping fails CLOSED.** An adversarial pass found the page
  shipped every chapter unhidden and only hid them once `/api/modules` answered
  — sub-frame when idle, but ~100 ms with 14 painted frames under load, and
  *indefinitely* if the call hung. Blocks with an access key are now hidden by
  CSS until the script marks the page `.scoped`, so a slow or hung answer shows
  nothing rather than everything. A `pageshow` handler re-runs the decision when
  the browser restores the page from bfcache, since the restored DOM carries a
  decision made under a session that may since have ended.

