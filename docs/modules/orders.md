# Order Tracking

**Status: ❌ not started** (tile + placeholder exist; scoped 2026-08-13)

## Purpose
The operational spine: every customer order from first contact to payment,
including what steel fed it and how it shipped.

## Scope (decided)
- **7 skippable stages:** Enquiry → Quote → PO received → Production → QC →
  Dispatch → Payment. An order may start at PO; stage history is kept.
- **Order numbers:** auto per financial year, format configurable in Settings
  (e.g. `ORD-{FY}-{SEQ}`); customer's own PO number is a separate searchable
  field.
- **Items** reference the drawing master (Parts & Pricing): drawing ID +
  revision, qty, rate snapshot.
- **Material linkage:** inventory's usage-log `order_id` becomes a real
  reference — from an order, see the heats (and mill certificates) that fed it.
- **Consignments (shipping lives here, not a separate module):** own entity with
  date, transporter, LR number, e-way bill no., invoice no., delivery-confirmed
  flag (+ optional vehicle no., freight ₹); consignment lines reference order
  items with quantities → partial shipments and multi-order consignments work.
  A global consignments list is a tab inside this module.

## Dependencies
Customers module (who ordered), Parts & Pricing (what part, at what rate),
Inventory (heat links exist already), Settings (number format).

## Data model (planned)
`customer_order(id, order_no, customer_id, customer_po, stage, dates…)` ·
`order_item(order_id, drawing_id, revision, qty, rate, amount)` ·
`order_stage_log(order_id, stage, at, note)` ·
`consignment(id, date, transporter, lr_no, eway_no, invoice_no, vehicle_no,
freight, delivered)` · `consignment_line(consignment_id, order_item_id, qty)`.

## What's left (everything)
- [ ] Schema + module package (`backend/modules/orders/`), routes, UI page.
- [ ] Stage board/list + order detail with items, stage history, heats, consignments.
- [ ] Consignment entry with GST fields; delivery confirmation.
- [ ] Order-number generator (FY-aware, configurable format).
- [ ] Wire inventory issue entries to order records.
