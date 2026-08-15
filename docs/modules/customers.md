# Customers

**Status: ✅ built** (2026-08-14)

## Purpose
The thin master Orders, Parts & Pricing and consignments reference: name,
GSTIN, addresses, contact persons, payment terms.

## User flows
- List (searchable by name, GSTIN **or code**) → the record opens as a **full
  window** with a Back button, not a dialog: a profile, a growth chart, an order
  history and a rate card do not fit in a popup. Editing still opens a modal on
  top of it. Four tabs:
- **Profile**: addresses, terms, contact persons.
- **Business**: lifetime totals (total, orders, average, still-open), the
  month-by-month bar chart (hover shows the running total), and the full
  Quotations & invoices reprint table.
- **Orders** (own tab, 2026-08-15 — long histories were stretching the page):
  **Orders in progress** (stage before Payment received, stage chips) and
  **Past orders** (done & paid) whose Documents column hangs each order's
  quotations/invoices on its row as printable chips (`business()` docs carry
  `order_id`). Every order row navigates to `/orders/?open=<id>` — the record
  lives in Order Tracking; this is a view of it. Both tables scroll inside
  their frames.
- **Rates**: the operations this customer has an agreed price for — standard
  ₹/hour from Settings shown alongside, so you can see what was negotiated. Each
  row is `operation · ₹/hour · additional ₹/hour · note`, one row per operation
  (saving the same operation twice updates it). These feed the costing workspace
  automatically for every drawing assigned to this customer, so a renegotiated
  rate is one edit, not one per part. Rates already snapshotted into a saved
  costing are untouched.
- Each customer gets a **code** on creation: an abbreviation of the name plus a
  serial within it (Acme Castings → AC01, the next AC… → AC02). "M/s"/"Messrs"
  and Pvt/Ltd-style words are ignored when deriving it; the abbreviation can be
  overridden in the form, which previews the result live.
- Delete only while the customer has no orders or drawings (else deactivate).

## Implemented (file paths)
`backend/modules/customers.py` (data + `/api/customers/*` routes, grant
`customers`) · UI `frontend/customers/index.html` + `customers.js` · spec
`tests/test_workshop.py` (CustomersSpec).

## Data model
`customer(id, code UNIQUE, name UNIQUE, gstin, address_billing, address_shipping,
payment_terms, notes, active)` · `customer_contact(customer_id, name, phone,
email, role)` (cascade) · `customer_operation_rate(customer_id, operation,
rate_per_hour, extra_rate, note, UNIQUE(customer_id, operation))` (cascade).

## Screens
guide-images: ws-customer, ws-customer-business, ws-customer-rates.

## What's left
- [ ] Receivables (invoiced vs paid) once invoice payments are tracked.
