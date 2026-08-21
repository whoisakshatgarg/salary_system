# Quotations & Invoices

**Status: ✅ built** (2026-08-14)

## Purpose
The paperwork that leaves the workshop. A quotation prices work and expires; an
invoice bills for it and is usually raised straight from an order.

## User flows
- **Add AND edit open the full page** (owner's call, 2026-08-15 — "a popup is
  disruptive" for line-item editing). The form markup serves both; `editDoc()`
  sets `addPage = true`. `/quotations/?open=<id>` deep-links onto a document
  (the order record's Documents segment links here).
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
- **Rates carry 3 decimals** (`step="0.001"`, CONVENTIONS §5): export unit
  prices are quoted that way (`£0.534`) and rounding one to 2dp moves a
  120,000-piece line by £60. Line totals stay at 2.

## Revisions
A customer who asks for a better price gets a **revision**, not a second
document and not an overwrite. **Revise** (`POST /api/quotations/{id}/revise` →
`revise_doc`) copies the row to a new draft whose `doc_no` is the base number
plus ` Rev-A`, ` Rev-B`… and sets `superseded_by` on the parent.

- Only the **tip** of a chain can be revised: a superseded document is history,
  and branching it would leave two documents claiming to be the latest.
- `revision_chain(conn, doc_id)` walks to the root and back down, oldest first,
  with `active` marking the tip — the UI draws it as a rail on the open
  document and dims superseded rows in the list.
- The revision marker is a placeholder scheme (CONVENTIONS §9-B): no revision
  appears anywhere in the reference documents.
- A quotation's **paper** revises in the same breath — `documents/service.revise`
  calls `revise_doc` so the money row and the export sheet keep one number. See
  [papers.md](papers.md).

## The export paper
**Export paper** on an open document is one button for both cases
(`exportPaper()` in quotations.js): it searches `/api/papers` for a paper whose
number is this document's and deep-links to `/papers/?open=<id>`, or creates one
(`kind` = `quotation`/`invoice`, `opts.document_id` = this row) and lands on it.

The ledger stays the **money** truth — what was quoted, to whom, for how much,
and the customer stats built off it. The paper holds the export-format fields
and renders the file. A document with no `order_id` cannot have one: a paper
hangs off an order, and the button says so rather than guessing.

## Material availability (optional)
Creating a quotation opens a **full page** now, not a modal; editing one still
opens the modal. On that page a **☐ Check material availability** checkbox is
off by default and changes nothing about how a quotation is written or saved —
tick it and a panel asks for material, grade, part length/diameter, tolerance
and the quantity needed (prefilled from the quotation lines), then reports what the
rack could actually yield, heat by heat, with any shortfall.

It is advisory: nothing is reserved, and the check never blocks Save. It calls
the shared `POST /api/material/check` (see
[modules/inventory.md](inventory.md)), which is grant-shared with Inventory so a
quotations-only account can use it. The point is to see a shortage BEFORE
quoting to it.

## Implemented (file paths)
`backend/modules/quotations.py` (data + `/api/quotations/*`, grant `quotations`;
`render_print` builds the printable HTML) · UI `frontend/quotations/index.html`
+ `quotations.js` · spec `tests/test_workshop.py` (QuotationsAndInvoices).

**Numbering moved to `backend/core/numbering.py`** (CONVENTIONS §3). A NEW
quotation takes the real per-client export number — `T04/AT/210826/317`, counted
under `qtn:{client}` — and a new invoice `AT/EI/26-27/169` under `inv:{fy}`.
`client_code(conn, customer_id)` resolves the code inside the numbering
transaction, **assigning one if the customer has none**, because the number is
built from it and the two have to be decided together. Old rows keep their
`QUO-`/`INV-` numbers forever; `format_for()` and the `doc_seq` table survive
only for those and for MRQ requisitions. Seeds and edits live in
Settings → Numbering ([settings.md](settings.md)).

## Data model
`document(kind, doc_no UNIQUE, customer_id, order_id?, doc_date, valid_until,
reference, tax_pct, notes, terms, status, revises_document_id?,
superseded_by?)` · `document_line(document_id, drawing_id?, description, qty,
unit, rate, os_item_id?)` · `doc_seq(kind, fy, seq)` (legacy + requisitions).
Subtotal/tax/total are always derived, never stored. The two revision columns
and `os_item_id` are additive and nullable — `os_item_id` lets a line price a
bought-out part instead of a drawing ([outsourcing.md](outsourcing.md)).

## Printing
Deliberately dependency-free: `/api/quotations/{id}/print` returns styled HTML
with a Print button; the browser's "Save as PDF" does the rest. No PDF library
to install, works offline, and the layout is editable by anyone who reads HTML.

## Screens
guide-images: ws-invoice-form, ws-quote-material-check, ws-invoice-print.

## What's left
- [ ] Company address/GSTIN on the printout come from settings keys
      (`company_address`, `company_gstin`) that Settings doesn't expose yet.
- [ ] Payment tracking against invoices (ROADMAP).
