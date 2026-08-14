# Quotations & Invoices

**Status: ✅ built** (2026-08-14)

## Purpose
The paperwork that leaves the workshop. A quotation prices work and expires; an
invoice bills for it and is usually raised straight from an order.

## User flows
- One list, filtered by kind, searchable by number / customer / code / reference.
- **+ Quotation / + Invoice** — optionally "start from an order", which fills the
  customer and copies its items as lines (nobody retypes quantities and rates).
  Picking a drawing on a line fills description, unit and its latest rate. GST %
  and totals update live while typing.
- Status chips: draft → sent → accepted / paid (or cancelled). Paid and cancelled
  documents are locked against editing.
- **Print / Save as PDF** opens a clean A4 page — the browser saves it as PDF.
  The same button appears on the customer's Business tab, so any past document
  can be reprinted at any time.

## Implemented (file paths)
`backend/modules/quotations.py` (data + `/api/quotations/*`, grant `quotations`;
`render_print` builds the printable HTML) · UI `frontend/quotations/index.html`
+ `quotations.js` · numbering via `modules/settings.py` (`doc_seq` table,
formats `QUO-{FY}-{SEQ}` / `INV-{FY}-{SEQ}`) · spec `tests/test_workshop.py`
(QuotationsAndInvoices).

## Data model
`document(kind, doc_no UNIQUE, customer_id, order_id?, doc_date, valid_until,
reference, tax_pct, notes, terms, status)` · `document_line(document_id,
drawing_id?, description, qty, unit, rate)` · `doc_seq(kind, fy, seq)`.
Subtotal/tax/total are always derived, never stored.

## Printing
Deliberately dependency-free: `/api/quotations/{id}/print` returns styled HTML
with a Print button; the browser's "Save as PDF" does the rest. No PDF library
to install, works offline, and the layout is editable by anyone who reads HTML.

## What's left
- [ ] Company address/GSTIN on the printout come from settings keys
      (`company_address`, `company_gstin`) that Settings doesn't expose yet.
- [ ] Payment tracking against invoices (ROADMAP).
