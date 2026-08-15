# Parts & Pricing (drawing master)

**Status: ✅ built** (2026-08-14)

## Purpose
One record per customer drawing (number + revision): the part, its material
spec, the drawing files, the dated RATE HISTORY, and a per-operation costing
builder that rolls up to ₹/piece.

## User flows
- List with search + customer filter; latest rate (with kind chip) per row.
- Record: revision chips (+ Revision copies the master data; rates/files start
  fresh per revision), drawing-file uploads, rate history (quoted / agreed /
  revised, dated, newest first, deletable), costing builder — operation rows
  (₹/hr prefilled from Settings, snapshot on save), minutes per piece,
  material cost, margin %, live rollup — then **→ quote / → agreed** records
  the total into the rate history.
- **Costing workspace** (own full screen, opened from the drawing): the part and
  its rate history on the left with a revision switcher and the customer
  assignment; the operations table on the right with columns
  *operation · minutes · ₹/hour · add'l ₹/hour · effective ₹/hr · weightage · row ₹*.
  Every figure recalculates as you type. Row cost is
  `minutes ÷ 60 × (₹/hr + add'l ₹/hr) × weightage`.
  - **Additional margin is a rate, not a percentage** — rupees PER HOUR added on
    top of the operation's standard rate, so ₹400/hr + ₹50/hr bills at ₹450/hr.
    It is how you price a job that needs a better machine or a tighter tolerance
    without editing the shop-wide rate in Settings.
  - **Weightage** is the "counts more than its clock time" factor (setup spread
    over a batch, a second spindle, scrap allowance). Blank = 1.
- **Per-customer operation rates.** When the drawing is assigned to a customer,
  the workspace loads that customer's agreed rates instead of the Settings
  defaults, and marks the row with a ★ badge so it is obvious the price is
  negotiated rather than standard. Rates are kept on the customer (Customers →
  Rates tab), never on the drawing, so one edit reprices every part for that
  customer. An unassigned drawing — or a customer with no agreed rate for that
  operation — falls back to the Settings rate with no additional margin.
- **Part type** (2026-08-15): a broad family name on the drawing ("Piston
  rod", "Adapter") — additive by nature: the form's datalist offers every type
  ever saved (`/api/parts/refs` → `part_types`, a DISTINCT over drawings) and
  typing a new one adds it on save. Searchable in the list, shown as a dim
  chip beside the description. Regression to remember: `DrawingIn` silently
  DROPPED the field until it was added to the model — Pydantic eats unknown
  keys, so a new column needs SCHEMA + _MIGRATIONS + the route model.
- **Overall length / width (mm)** (2026-08-15): the finished part's envelope,
  off the drawing — optional, validated (>0, finite, <1e6), shown as
  "120 × 40 mm" in the record's Specification card, inherited by new
  revisions. Route model carries both (the Pydantic-drops-unknown-fields trap).
- **Files upload at creation**: the new-drawing form takes the drawing files
  directly; they POST to `/files` after the drawing is created. A failed
  upload downgrades to a toast (the drawing exists; the record can take the
  files again).
- Delete only while the drawing is on no order (else deactivate).

## Bill of materials
The costing's material cost can be priced from stock instead of typed from
memory. **+ Add material** opens one search box over heat number, grade,
material class and supplier (`GET /api/material/search`), which returns each
heat with its derived unit costs — `₹/rod = price_total ÷ rods_received`,
`₹/kg = price_rate_per_kg` (or `price_total ÷ total_weight_kg`).

That search lives on inventory's shared `/api/material` router, gated on ANY
of `inventory | quotations | orders | parts` — it returns derived PURCHASE
cost, so that grant set is the boundary on who can see what stock cost.

A BOM line is *material · ₹ per unit · parts obtained from one · ₹ per piece*.
The estimator enters **parts from one rod**, not a fraction: `qty_per_piece` is
derived as `1/N`, kept to 8 decimals and multiplied out only at the end. That
matters — rounding a third to 0.33 underprices every piece by 1% (₹1 485 instead
of ₹1 500 on a ₹4 500 rod), which is why `_check_ratio` exists alongside
`_check_money`.

When BOM lines exist they ARE the material cost and the manual box is disabled —
two sources of truth for one number is how they end up disagreeing. Everything
is snapshotted into `costing_material` on save (heat number, label, unit cost),
so reopening an old costing never silently reprices it when stock does.

## Implemented (file paths)
`backend/modules/parts.py` (data + `/api/parts/*` routes, grant `parts`) ·
UI `frontend/parts/index.html` + `parts.js` · files under `data/drawing_files/`
(`core/paths.drawing_files_dir()`, in backup zips, shared validation in
`core/attachments.py`) · spec `tests/test_workshop.py` (PartsSpec).

## Data model
`drawing(id, drawing_no, revision, customer_id, description, material_class,
grade, unit, active, UNIQUE(drawing_no, revision))` · `drawing_file` ·
`drawing_rate(drawing_id, kind, rate, rate_date, note)` ·
`costing(drawing_id, material_cost, margin_pct)` +
`costing_op(costing_id, operation, minutes, rate_per_hour, weightage,
extra_rate, cost)` — `extra_rate` is ₹ per hour; rollup totals derived on read. Plus
`costing_material(costing_id, heat_id, heat_number, material_label, unit,
unit_cost, qty_per_piece, cost)` — the bill of materials.
Rates and margins are SNAPSHOTTED into `costing_op` on save, so re-reading an
old costing never silently reprices it when Settings or a customer rate changes.

## Screens
guide-images: ws-part-detail, ws-costing-workspace, ws-bom.

## What's left
