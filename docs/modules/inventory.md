# Raw Material Inventory (heat register)

**Status: ✅ built** (2026-08-06; grant-gated since the shell, 2026-08-13)

## Purpose
Traceability for rod/bar stock: one record per incoming HEAT (mill batch),
from purchase to the orders it fed or the rejection that sent it back.

## User flows
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
user-owned after).

## Screens
guide-images: inv-stock, inv-heat-detail, inv-new-heat, inv-usage-log, inv-lists.

## Known bugs
None known.

## What's left
- [ ] Link `heat_movement.order_id` to real orders when Order Tracking ships
      (free text today by decision).
- [ ] Optional: low-stock indicators / reorder hints (not requested yet).
