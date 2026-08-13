# Parts & Pricing (drawing master)

**Status: ❌ not started** (tile + placeholder exist; scoped 2026-08-13 —
this module absorbs the original "Pricing Tracker" idea)

## Purpose
One record per customer drawing/part ID: what it is, what it's made from, and
what it has ever cost — the master that both quoting and Order Tracking hang off.

## Scope (decided — owner wants the extensive model)
- **Drawing master:** drawing/part ID + revision, customer, description,
  material spec (class/grade linkable to inventory's lists), drawing file
  attachments (PDF/scans — reuse inventory's attachment pattern).
- **Rate history per drawing+customer:** dated entries (quoted / agreed /
  revised) with notes — the "what did we charge last time" answer.
- **Per-operation costing that rolls up to ₹/piece:** routing of operations
  (turning, milling, drilling…), each costed (time × rate), plus material cost
  (weight × ₹/kg, linkable to inventory heat rates) and margin — the rollup
  becomes a rate-history entry when agreed. Operation list and rates editable
  (Settings). Details in OPEN_QUESTIONS #1.

## Dependencies
Customers module; Settings (units, operation rates); Inventory (material rates).

## Data model (planned)
`drawing(id, drawing_no, revision, customer_id, description, material_class,
grade, unit, attachments…)` · `drawing_rate(drawing_id, customer_id, date,
kind quoted|agreed|revised, rate, note, costing_id?)` ·
`costing(id, drawing_id, material_cost, margin_pct, total)` ·
`costing_op(costing_id, operation, minutes, rate_per_hr, cost)`.

## What's left (everything)
- [ ] Schema + module package (`backend/modules/parts/`), routes, UI page.
- [ ] Drawing list/detail with revisions + attachments.
- [ ] Rate-history timeline per customer.
- [ ] Costing builder (ops + material + margin → ₹/piece) feeding rate entries.
