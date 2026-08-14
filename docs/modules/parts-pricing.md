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
  *operation · minutes · ₹/hour · time ₹ · weightage · additional margin % · row ₹*.
  Every figure recalculates as you type. Row cost is
  `minutes ÷ 60 × ₹/hr × weightage × (1 + additional margin %)` — weightage is
  the "counts more than its clock time" factor (setup spread over a batch, a
  second spindle, scrap allowance); the additional margin marks up that one
  operation on top of the costing's overall margin.
- Delete only while the drawing is on no order (else deactivate).

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
extra_margin_pct, cost)` — rollup
totals derived on read.

## Screens
guide-images: ws-part-detail.

## What's left
- [ ] Auto material cost from Inventory heat rates (ROADMAP Later).
