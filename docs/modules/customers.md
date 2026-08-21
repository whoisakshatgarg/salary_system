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
- Each customer gets a **code** on creation: **one letter + a 2-digit serial**
  (`T04`, `E01`), the scheme printed on the company's own documents and the
  owner's one customer code (CONVENTIONS §2). `abbreviate()` takes the first
  letter of the first meaningful word — "M/s"/"Messrs" is stripped *before*
  tokenising or it splits into M + s and steals the initial, and Pvt/Ltd-style
  noise words are dropped; `next_code()` then appends the next free serial for
  that letter, inside the caller's transaction, with `customer.code UNIQUE` as
  the backstop. A typed code wins (`clean_code`, upper-cased, letters and
  digits only) and the form previews the result live.
- **`recode_legacy_codes(conn)`** moved existing customers off the old
  two-letter scheme (AC01 → A01) at startup, oldest first. It is idempotent — a
  code already matching `^[A-Z][0-9]{2,}$` is never touched — so it is safe to
  run on every start, and three digits past the 99th customer under one letter
  is the same scheme overflowing, not a violation.
- **`country`** on the customer is not decoration: `documents/payloads` prints
  it in the customer block on the quotation and the export invoice, and
  `currency_for(country)` defaults the document's currency from it (UK → GBP,
  else USD — CONVENTIONS §9-D). `split_country()` will also lift a trailing
  country line out of a pasted address.
- **`customer_contact.fax`** is its own column beside `phone`: the ack, the
  invoice and the packing list all print a fax in their CONTACTS block, and
  `payloads.primary_contact()` reads it.
- Delete only while the customer has no orders or drawings (else deactivate).

## Implemented (file paths)
`backend/modules/customers.py` (data + `/api/customers/*` routes, grant
`customers`) · UI `frontend/customers/index.html` + `customers.js` · spec
`tests/test_workshop.py` (CustomersSpec).

## Data model
`customer(id, code UNIQUE, name UNIQUE, gstin, address_billing, address_shipping,
country, payment_terms, notes, active)` · `customer_contact(customer_id, name,
phone, fax, email, role)` (cascade) · `customer_operation_rate(customer_id,
operation, rate_per_hour, extra_rate, note, UNIQUE(customer_id, operation))`
(cascade). `country` and `fax` are additive and default to `''`.

## Screens
guide-images: ws-customer, ws-customer-business, ws-customer-rates.

## What's left
- [ ] Receivables (invoiced vs paid) once invoice payments are tracked.
- [ ] **The code is editable in the API but not in the UI.** `save_customer`
      honours a typed code on update (`typed or existing or next_code(…)`) and
      CONVENTIONS §2 says every code stays editable, but the edit form only
      offers the box on create. Either expose it on Edit or write the rule down
      as create-only — today the two disagree.
