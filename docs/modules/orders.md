# Order Tracking

**Status: ✅ built** (2026-08-14)

## Purpose
The operational spine: every order from enquiry to payment, what steel fed it,
and how it shipped.

## User flows
- Stage chips (with counts) + list (search order no / customer PO / customer).
- New order: customer, PO number, skippable starting stage, item rows —
  picking a drawing prefills unit + latest recorded rate; order number is
  generated from the Settings format, sequenced per financial year (atomic).
- Record: stage progress row (click any stage; every move logged with a note),
  items with shipped-vs-ordered, **material used** (inventory issues whose
  Order ID equals this order number — heat numbers for traceability),
  consignments, stage history. Delete only while nothing has shipped.
- **🚚 Ship** → consignment: date, transporter, LR no., e-way bill, invoice,
  vehicle, freight + line quantities (partial fine; pull in further orders —
  one truck, several orders). Over-shipping an item is refused atomically.
  Consignments tab: global list, delivered toggle, delete (frees quantities).

## Material availability (optional)
Creating a order opens a **full page** now, not a modal; editing one still
opens the modal. On that page a **☐ Check material availability** checkbox is
off by default and changes nothing about how a order is written or saved —
tick it and a panel asks for material, grade, part length/diameter, tolerance
and the quantity needed (prefilled from the order lines), then reports what the
rack could actually yield, heat by heat, with any shortfall.

It is advisory: nothing is reserved, and the check never blocks Save. It calls
the shared `POST /api/material/check` (see
[modules/inventory.md](inventory.md)), which is grant-shared with Inventory so a
orders-only account can use it. The point is to see a shortage BEFORE
committing to it.

## Implemented (file paths)
`backend/modules/orders.py` (data + `/api/orders/*` routes incl.
`/api/orders/consignments*`, grant `orders`) · UI `frontend/orders/index.html` + `orders.js` · numbering via `modules/settings.py` (`order_seq` table) · spec
`tests/test_workshop.py` (OrdersSpec).

## Data model
`customer_order(order_no UNIQUE, customer_id, customer_po, stage, dates…)` ·
`order_item(order_id, drawing_id?, description, qty, unit, rate)` ·
`order_stage_log` · `order_seq(fy, seq)` · `consignment(GST fields, delivered)`
· `consignment_line(consignment_id, order_item_id, qty)` — shipped/pending
always derived.

## Screens
guide-images: ws-order-detail, ws-consignment.

## What's left
- [ ] Delivery-challan / order printouts (ROADMAP Next).
- [ ] Stage-change audit belongs to the app-wide audit trail (ROADMAP Now).
