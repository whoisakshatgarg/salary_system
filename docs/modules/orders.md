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

## Bill of materials for an order
An order has no bill of materials of its own — its PARTS do. `order_bom()` walks
each item to its drawing, takes that drawing's **most recent** costing, and
multiplies the costing's per-piece material by the quantity ordered:

    required = qty_per_piece × item qty

then rolls the lines up **by heat number**, because the heat is what has to come
off the rack. Beside each line sits what has actually been issued against this
order number (the usage log), so the record reads *committed · issued · still to
issue*, and any heat issued to the order that no part calls for is flagged.

Derived live, never snapshotted: re-costing a drawing changes what the order
needs, and the costing used is named in the result so a figure can always be
traced. Items with no drawing, with no costing, or whose costing prices material
by hand are listed **with the reason** rather than silently contributing zero —
a BOM that quietly under-reports is worse than none.

`GET /api/orders/{id}/bom`. The order screen loads it after the record itself
(not awaited) and fails closed, so an account without the `parts` grant sees the
order without the section rather than an error.

### Issuing it as a document
**📄 Issue requisition** freezes that rollup into a numbered document
(`material_doc` + `material_doc_line`, numbered from `doc_seq` with
kind='material', format `material_number_format`, default `MRQ-{FY}-{SEQ}`).

Everything is SNAPSHOTTED — heat, quantity, what had already been issued, the
value, and which drawings called for it — because a requisition handed to the
store keeper must not change when someone re-costs a drawing an hour later. You
issue another one instead. `order_no` and `customer_name` are copied too, so the
document survives its order being deleted (`order_id` goes NULL, the paper
record stands).

`POST /api/orders/{id}/bom/issue` · `GET /api/orders/requisitions[/{id}[/print]]`.
The print view is the same dependency-free A4 HTML as quotations and invoices —
the browser's Save-as-PDF is the PDF engine — with Required / Already issued /
To issue now, the committed value, and three signature lines (issued by, store
keeper, received by).

## Deadlines, delivery plans and shipment progress
- **Deadline** (`customer_order.due_date`) is a column on the list, tinted amber
  inside a week and rose once overdue, and a chip on the order record.
- **Delivery plan** — long orders ship in instalments, so each ORDER ITEM can
  carry an `order_schedule` of "250 by the 15th, 100 by the 15th, the balance by
  the deadline". Quantities hang off the item because a quantity only means
  something against the item it is a quantity OF. **What is left unplanned is
  derived** (`item qty − Σ lines`), never stored, so the two cannot drift; the
  plan is refused if it adds up to more than the item.
- **Segments** (`_segments`) — the stretches of an item that can be shipped
  against: its planned drops in date order, then whatever quantity carries no
  promised date yet. That trailing balance matters — a 600-piece item planned as
  250 + 150 still owes 200, and without it the bars would not add up to the
  order. An item with no plan at all comes back as ONE segment for the whole
  quantity, so **every order has something to draw and something to ship
  against**. `_order_drops` does the same for a whole page of the list in three
  queries rather than three per row.
- **A progress bar per delivery, never one averaged per order.** An order that
  ships in three drops is three separate jobs; a single bar hides that drop 1
  closed while drop 3 has not started. So the split is drawn in all three places
  the order appears:
  - *Items & delivery plan* — under each item, one bar per drop, where the plan
    is edited.
  - *Shipments & history* — one card per drop with its own **Ship this** button,
    which opens the consignment form with that part chosen and that drop's
    outstanding quantity filled in.
  - *Shipments tab* — the Deliveries column is a strip of separate bars, each
    segment as wide as the quantity it covers; the chevron expands the row into
    the same drops written out with their dates.
  Colour carries state: complete is green, a promised drop solid brand, quantity
  with no promised date the same colour softened — progress, but never a
  commitment that was made.
- What has actually shipped is poured into the segments in due-date order
  (`_allocate_drops`), so over-shipping one closes it and rolls the surplus into
  the next — **the later drops need less without anyone editing the plan**.
  Nothing records which drop a consignment was *for*, and asking would be a lie:
  a lorry leaves with a quantity, not an intention. Anything left after every
  segment is full is reported as `over_delivered` — that now means beyond the
  whole ordered quantity, since the unpromised balance is a segment too.
- **Shipments tab** (the module-level list) — per-order fulfilment: ordered /
  sent / remaining, filtered by default to orders that still owe something.
  `sent` sums `consignment_line` across every consignment, so an order delivered
  in six instalments over four months reads correctly. The caption counts
  *planned* drops only: two parts with no plan draw two bars, but calling those
  "deliveries" would report a promise nobody made.
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
