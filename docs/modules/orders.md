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

## Deadlines, delivery plans and shipment progress
- **Deadline** (`customer_order.due_date`) is a column on the list, tinted amber
  inside a week and rose once overdue, and a chip on the order record.
- **Delivery plan** — long orders ship in instalments, so each ORDER ITEM can
  carry an `order_schedule` of "250 by the 15th, 100 by the 15th, the balance by
  the deadline". Quantities hang off the item because a quantity only means
  something against the item it is a quantity OF. **What is left unplanned is
  derived** (`item qty − Σ lines`), never stored, so the two cannot drift; the
  plan is refused if it adds up to more than the item.
- **Shipments tab** — per-order fulfilment: ordered / sent / remaining with a
  progress bar, filtered by default to orders that still owe something. `sent`
  sums `consignment_line` across every consignment, so an order delivered in six
  instalments over four months reads correctly.
- **Home warning panel** — `GET /api/orders/deadlines` buckets open orders into
  overdue / next 7 days / next 31 days, each with the customer, order number and
  quantity still to send. Two kinds of order drop out — those fully shipped, and
  those already at **Payment received** (`stage NOT IN ('payment')`); neither is
  a deadline any more. The shell fetches it fail-closed, so an account without the orders
  grant simply sees no panel.

## Implemented (file paths)
`backend/modules/orders.py` (data + `/api/orders/*` routes incl.
`/api/orders/consignments*`, grant `orders`) · UI `frontend/orders/index.html` + `orders.js` · numbering via `modules/settings.py` (`order_seq` table) · spec
`tests/test_workshop.py` (OrdersSpec).

## Data model
`customer_order(order_no UNIQUE, customer_id, customer_po, stage, dates…)` ·
`order_item(order_id, drawing_id?, description, qty, unit, rate)` ·
`order_stage_log` · `order_seq(fy, seq)` · `consignment(GST fields, delivered)`
· `consignment_line(consignment_id, order_item_id, qty)` · `order_schedule(order_item_id, due_date, qty, note)` — shipped/pending
always derived.

## Screens
guide-images: ws-order-detail, ws-consignment, ws-delivery-plan, ws-shipments.

## What's left
- [ ] Delivery-challan / order printouts (ROADMAP Next).
- [ ] A consignment is recorded against the ORDER, not against a planned drop, so
      the delivery plan and the actual shipments sit side by side rather than
      ticking each other off.
- [ ] Double-clicking Save order / Create consignment submits twice
      ([QA-FINDINGS.md](../QA-FINDINGS.md)).
- [ ] Stage-change audit belongs to the app-wide audit trail (ROADMAP Now).
