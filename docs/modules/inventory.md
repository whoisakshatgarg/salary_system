# Raw Material Inventory (heat register)

**Status: ✅ built** (2026-08-06; grant-gated since the shell, 2026-08-13)

## Purpose
Traceability for rod/bar stock: one record per incoming HEAT (mill batch),
from purchase to the orders it fed or the rejection that sent it back.

## User flows
- **Adding is a full screen, not a popup** (`+ New heat`): one delivery, a row
  per piece, each row carrying its OWN heat number — a delivery routinely mixes
  heats and they must never be merged. `POST /api/inventory/intake` groups the
  rows by heat number and writes every heat in ONE transaction, so a delivery is
  either recorded or it isn't. **Editing** an existing heat keeps the modal.
- **Material Check** tab: the feasibility calculation described below.
- New heat from the mill certificate: heat number, supplier, class/grade/shape
  (extensible dropdowns with inline "+ Add new…"), size, rods, weight, prices,
  chemical composition rows, notes; attach certificates/invoices after saving
  (images auto-compressed client-side; PDFs pass through).
- Usage log per heat: issue to an order (Order ID required) or reject to
  supplier; can't exceed remaining; one-click "Reject remaining batch"; delete
  an entry to undo. Status derives: In stock / Consumed / Rejected.
- Find steel: text search, class/shape/status filters, composition range
  (element + min–max %), four sorts. Global usage log traces any Order ID back
  to its heats. Stat strip: heats, rods in stock, pro-rata ₹ value, issued.
- Lists tab: manage the four dropdown sets (deleting never touches history).

## Implemented (file paths)
- Everything: `salary-system/new_system/backend/modules/inventory.py`
  (data functions + `/api/inventory/*` router; grant `inventory` via
  `core/deps.require_module`). Spec: `tests/test_inventory.py` (33 tests incl.
  race/validation regressions).
- UI: `frontend/inventory/index.html` + `inventory.js` (served at `/inventory/`) (own page).
- Files on disk: `data/inventory_files/` via `core/paths.inventory_files_dir()`;
  included in backup zips (`backend/main.py` `_write_backup_zip`).

## Data model
`heat(id, heat_number UNIQUE, date_received, supplier, material_class, grade,
shape, size_section, rods_received, total_weight_kg, rack, price_total,
price_rate_per_kg, notes)` · `heat_composition(heat_id, element, percent)` ·
`heat_movement(heat_id, mv_date, type issue|reject, order_id, rods, weight_kg,
remarks)` — remaining is ALWAYS derived · `heat_attachment(heat_id, kind,
filename, mime, stored_name)` · `inv_option(kind, value)` (seeded once,
user-owned after) · `heat_piece(heat_id, length_mm, diameter_mm, quantity,
note)` — the individual bars under a heat.

Piece rows are OPTIONAL. A heat without them still works and can be checked by
quantity; it just can't be checked by dimension. When piece rows do exist their
quantities ARE the rod count (`rods_received` is set from their sum on save), so
the stock figure and the feasibility answer can never disagree.

## Manufacturability check
`check_material(conn, req)` answers "can we actually make this from the rack?"
It is **advisory** — it reserves nothing, so two quotations checked a minute
apart are both told the same bar is free. Stock only ever moves via the usage
log.

Two methods:
- **dimension** — `parts_from_piece(length, part_length, margin)` returns the
  whole parts one physical piece yields: `floor(length / (part_length + margin))`.
  The margin is the parting-off/facing allowance eaten by EACH part; diameter is
  a filter (a piece qualifies when its Ø is at least the part's).
- **quantity** — just the pieces on the rack, for material whose dimensions
  don't decide the answer.

**Never volume ÷ volume.** Three 10-unit bars yield 3 × floor(10/3) = 9 parts of
length 3, not 10 — the leftover unit on each bar is scrap because offcuts can't
be welded together. This is the single most important property of the module and
`PiecesAndFeasibility.test_spec_example` pins it.

The answer is always broken down **heat by heat**, because two bars of the same
size from different heats are different steel: "9 parts" is only useful next to
"3 from H1001, 2 from H1002, 4 from H1003". Heats with no piece dimensions are
reported explicitly ("33 rods on the rack, but no piece dimensions recorded")
rather than silently omitted.

Consumption is recorded against the heat, not a specific bar, so availability
applies it to the piece rows in receipt order (FIFO). Deterministic, and the
piece totals always agree with the heat's `remaining`.

Exposed at `POST /api/material/check` on its own router with
`require_module("inventory", "quotations", "orders")` — the quotation and order
screens offer the same check, and their users don't hold the inventory grant.

## Screens
guide-images: inv-stock, inv-heat-detail, inv-new-heat, inv-usage-log, inv-lists.

## Known bugs
None known.

## What's left
- [ ] Link `heat_movement.order_id` to real orders when Order Tracking ships
      (free text today by decision).
- [ ] Optional: low-stock indicators / reorder hints (not requested yet).
