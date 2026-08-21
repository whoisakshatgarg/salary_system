# SOP-DESIGN.md — pipeline, paperwork engine, outsourcing

The blueprint for the SOP build (designed 2026-08-21, Fable; implemented by Opus
waves). Companion to [CONVENTIONS.md](CONVENTIONS.md), which owns every number
format and label. Status of each piece is tracked in §12 at the bottom.

## 1. The one idea

The company's SOP is already almost in the app: `customer_order.stage` runs
`enquiry → quote → po → production → qc → dispatch → payment`, which maps 1:1
onto Intake → Quotation → PO Ack → Production → Quality → Shipping → Payment.
**No stage machinery changes.** What's missing is the paperwork: at each stage
the company issues real documents (xlsx/docx in their exact house formats), and
nothing generates or files them. So the build adds a single new noun — the
**paper** — a generated document instance hanging off an order, plus the engine
that renders papers from token-marked copies of the reference files, plus the
outsourcing world.

## 2. New table: `paper` (one row per generated document)

```sql
paper(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,              -- quotation|ack|work_order|invoice|packing_list|coc|test_cert|bom
  paper_no TEXT NOT NULL,          -- fully formatted number per CONVENTIONS §3
  revision TEXT NOT NULL DEFAULT '',
  order_id INTEGER NOT NULL REFERENCES customer_order(id),
  customer_id INTEGER REFERENCES customer(id),
  document_id INTEGER REFERENCES document(id),   -- ledger link (quotation/invoice kinds only)
  based_on_id INTEGER REFERENCES paper(id),      -- revision parent OR attach-existing source
  status TEXT NOT NULL DEFAULT 'draft',          -- draft|final|sent|superseded|void
  paper_date TEXT,
  payload TEXT NOT NULL,           -- JSON: every editable field, incl. line items
  created_at TEXT, updated_at TEXT,
  UNIQUE(kind, paper_no, revision)
)
```

Rules:
- **Auto-fill once at creation** from the order/ledger/inventory; a "Refill from
  order" action re-syncs explicitly. Hand edits are never silently clobbered;
  no field is ever locked (owner brief).
- **Draft** papers are editable and renumberable; **final** freezes the payload
  (still viewable/downloadable; a revision or a new paper supersedes it).
- Numbers are **consumed at creation** inside the same `BEGIN IMMEDIATE`
  transaction that inserts the row (same double-click-safe pattern as
  `next_order_no`). Preview endpoints peek without consuming.
- `quotation` and `invoice` papers also create/see their ledger row in the
  existing `document` table (`document_id` link): the ledger stays the money
  truth for customer stats and the Documents tab; the paper holds the
  export-format fields and the rendered file. Other kinds are paper-only.

### Quotation paths (SOP stage 2)
- **New**: existing quotations editor (parts-picker, rates) + new export fields;
  saving creates ledger row + paper.
- **Attach existing**: search past quotations (customer/part/number) → new paper
  on THIS order with `based_on_id` → the original; its number is reused verbatim
  (the ack's Quotation Ref. prints it, or the literal `Repeat PO`).
- **Revise**: copies ledger row + paper; `paper_no` keeps the base number,
  `revision` becomes `A`, `B`…; parent status → `superseded`. Chain is browsable;
  only the newest non-superseded revision is "active" on the order.
  New ledger columns: `document.revises_document_id INTEGER`,
  `document.superseded_by INTEGER` (nullable, additive).

## 3. Numbering — `backend/core/numbering.py` + `doc_counter`

`doc_counter(scope TEXT PRIMARY KEY, next_seq INTEGER NOT NULL)`; bumped by
`_take(conn, scope)` inside the caller's transaction. Scopes and formats are
exactly CONVENTIONS §3:

| fn | scope | yields |
|---|---|---|
| `quotation_no(conn, client, d)` | `qtn:{client}` | `T04/AT/130826/317` |
| `ack_ref(conn, client, d)` | `ack:{client}:{ddmmyy}` | `E01.21.08.26.01` |
| `work_order_no(conn, ack_client, ack_date)` | `wo:{yy}` | long + short forms |
| `invoice_no(conn, d)` | `inv:{fy}` | `AT/EI/26-27/169` |
| `bom_no(conn, d)` | `bom:{fy}` | `AT/BOM/26-27/001` |
| `tc_no(cust_po, invoice_no)` | — derived | `AT/TC/59812/EI-047/24-25` |
| `os_item_id(conn)` | `os_item` | `OS-0001` (isolated; swappable) |
| `os_po_no(conn, d)` | `os_po:{fy}` | `AT/OS/26-27/001` |
| `vendor_code(conn)` | `vendor` | `V01` |

Date renderers live here too: `ordinal_apostrophe` (`04th Aug' 2026`),
`ddmmyyyy`, `dtd_ddmmyy`, `us_mmddyy` — each paper kind declares which it uses.
Seeds: Settings → Numbering (admin-only writes) lists every live scope with its
next value, editable — so real-world counters (T04 at 317, E01 at 595, WO at
253/26, invoice at 169) can be set without touching code. Existing
`order_seq`/`doc_seq` stay untouched for order numbers and MRQ requisitions.
New ledger quotations/invoices take their numbers from here (old rows keep
their `QUO-`/`INV-` numbers forever).

## 4. Document engine — `backend/documents/`

```
documents/engine.py     XlsxFiller / DocxFiller: {{token}} replacement, item-slot
                        filling, checked overflow (insert style-cloned rows),
                        image re-attachment for drawing-container logos
documents/registry.py   one entry per kind: template file, token map, items
                        region (first slot row, slot count), date formats,
                        number function — swap a template file, adjust the
                        entry, zero engine changes
documents/payloads.py   auto-fill builders (see §5)
documents/router.py     /api/papers CRUD + file download
documents/templates/    token-marked copies of the reference files + README
```

- Templates are **copies of the reference files with `{{tokens}}` typed into the
  variable cells** — static layout untouched, which is what makes fidelity
  provable.
- **Fidelity is verified structurally, never by rendering** (owner toolchain
  rule): a unit test per kind generates a sample paper and diffs its
  `format_spec.py` dump against `reference_specs/*.spec.json` — merged ranges,
  column widths, row heights, page setup, and the fonts/borders/alignment of
  every static cell must match exactly; only listed variable coordinates may
  differ. Appearance cross-check against the owner's PNG renders; final visual
  sign-off = owner opens the generated file.
- Files are rendered on demand (`GET /api/papers/{id}/file`) straight from
  payload + template — nothing stale on disk; download name mirrors the
  company's own conventions (e.g. `COC-PO-02940-EI-122.docx`).
- Known template surgeries (from Phase 0):
  - TC logo/ISO badges live in a drawing container openpyxl drops on save →
    extract images from the xlsx zip once, re-anchor as openpyxl images (the
    BOM placeholder already proves this works).
  - Ack templates come from the pure-Python `.xls` conversions
    (`tools/xls2xlsx.py`); print setup (lost in BIFF reading) is set explicitly
    on the template copy.
  - The quotation is rebuilt as xlsx from the PDF's measured geometry
    (`reference_specs` has no entry — the PDF is the reference; geometry dump
    drove the build). **Pending owner sign-off before lock-in.**
  - The invoice `.doc` is unreadable pure-Python; its template derives from the
    packing-list docx skeleton (same header family) + the owner's PNG render +
    the textutil text dump. **Provenance confirmed with owner 2026-08-21**, as
    were: document-scheme client codes (re-code existing, editable), real
    numbering conventions for NEW ledger quotations/invoices, and counter seeds
    from the reference documents (T04→317, E01→595, WO→253/26, EI→169).

## 5. Payload builders (auto-fill sources)

| kind | filled from |
|---|---|
| quotation | ledger doc + lines, customer (+contact), export fields defaulted per customer (currency, terms) |
| ack | accepted/attached quotation + order (customer PO, dates), ship-date promise, WO number reserved in the same txn |
| work_order | ack paper (client, PO, date) + order items (part no/drawing, qty, matl., marking) |
| invoice | order + consignments (or hand-picked lines); many customer POs per line; totals, amount-in-words, IEC/AD/HTS/ports constants (editable) |
| packing_list | its invoice paper + box grouping (box no/size/net/gross per box) — boxes live only in the payload |
| coc | invoice paper + order (part, material, plating/finishing NA defaults, qty, date shipped) |
| test_cert | invoice + material issues → heats → `heat_composition` chemistry per line (C/Mn/Si/P/S/Cr/Ni/Mo + spares) |
| bom | `orders.order_bom()` rollup (costing materials, heat numbers) + outsourced lines with OS IDs & vendor source |

## 6. Orders: intake + pipeline strip

- New `order_attachment` table + `paths.order_files_dir()` (+ backup zip list) —
  the intake email (.eml/pdf/screenshot) and any customer paperwork attach to
  the order; item drawings' files are listed on the order for direct viewing.
- Order page gets a **pipeline strip**: seven stage cells (existing
  `STAGES`/`set_stage`), each showing its papers as status chips
  (missing / draft / final) linking to `/papers/?open=…`, with create buttons
  that pre-fill from the order. `get_order` embeds `papers[]` alongside the
  existing `documents[]`.

## 7. Homepage — tiles read like the SOP

`registry.MODULES` order becomes: **orders, quotations, acks, production_docs,
shipping_docs, quality_docs, outsourcing**, then inventory, parts, customers,
employees, salary, settings. The four new SOP tiles are distinct module keys
(separate grants, admins auto-granted) all opening `/papers/` pre-filtered:
`?kind=ack`, `?kind=work_order,bom`, `?kind=invoice,packing_list`,
`?kind=coc,test_cert`. The papers backend guard accepts ANY of the four keys
(same pattern as inventory's `/api/material` router). Deadlines panel stays.

## 8. Papers frontend — `/papers/`

List view (framed table: number mono, kind + status dot-chips, order/customer,
date, download) filtered by `?kind=`; **full-page editor** (owner: popups are
disruptive) per kind: header fields, items grid, terms — every payload field
editable; actions: Save draft, Refill from order, Finalize (consumes nothing —
number already owned), Download. Deep-links `?open=<id>`; right-click-newtab
per UI-STYLE §4. Revision history rail on quotation papers.

## 9. Outsourcing — `backend/modules/outsourcing.py` + `/outsourcing/`

Tables: `vendor` (code `V01`+, contacts, services),
`os_order` (`os_no = AT/OS/{FY}/{seq}`, vendor, optional `order_id`, purpose,
date_sent, **deadline**, status open|partial|received|closed|cancelled) with
`os_order_item` rows (optional `order_item_id` → partial outsourcing of one
internal order across vendors), `os_receipt` + `os_receipt_line` (inspection
notes, acceptance), `os_item` (**the outsourced inventory**: `os_id = OS-0001`,
no heat number, description/material/size/unit/qty, vendor + source os_order),
`os_movement` (issue/adjust, mirrors heat movements), `os_document` (vendor
paperwork uploads, linked to vendor and/or os_order; standard uploads pattern +
`paths.outsourcing_files_dir()` + backup list). Vendor documents are
**uploaded, never generated** (owner brief; ask before designing an outgoing
vendor doc).

Integration: `/api/material/search` returns os_items flagged
`source:"outsourced"`; `costing_material.os_item_id` and
`document_line.os_item_id` (nullable, additive) let costings/quotes reference
them; the BOM builder prints `OS-0001` in *Heat No. / OS ID* and
`Outsourced - V01` in *Source*. Workspace dashboard mirrors the homepage
deadlines panel (late / this week / this month) off `os_order.deadline`.

## 10. Permissions & registry

Five new module keys: `acks`, `production_docs`, `shipping_docs`,
`quality_docs`, `outsourcing`. Admins get all automatically (`user_grants`);
operators need per-key ticks in Users & Access. Settings → Numbering writes are
`require_admin`. `/help/` stays admin-only (standing owner rule).

## 11. Testing & non-negotiables

- unittest, module-functions-with-conn style; every numbering pattern, FY/day
  rollovers, per-client isolation, seed editing; engine fidelity tests per kind
  vs `reference_specs/`; payload builders (multi-PO invoice, box math, chem
  pull, OS lines in BOM); revision chains; attach-existing; outsourcing flows;
  route-model regression pins (the Pydantic silent-drop trap).
- Migrations are additive only; existing data and numbers untouched.
- New runtime deps: `openpyxl`, `python-docx` (requirements.txt).
  `pdfplumber` stays dev-only. **No system packages, no LibreOffice, no
  rendering of documents by the app or the build.**

## 12. Status (updated as waves land)

| Piece | Status |
|---|---|
| Phase 0: CONVENTIONS.md, reference_specs/, xls conversions | ✅ done |
| format_spec.py at repo root (owner-supplied) | ✅ done |
| Wave 1: numbering + paper/outsourcing schema + migrations | ⏳ pending |
| Wave 2: engine + 8 templates + fidelity tests | ⏳ pending |
| Wave 4: outsourcing module end to end | ⏳ pending |
| Wave 3: papers UI + pipeline strip + homepage reorder | ⏳ pending |
| Final: audits, docs, USER_GUIDE | ⏳ pending |
| Quotation template owner sign-off | ⏳ pending |
| Official BOM format (placeholder in use) | ⏳ awaiting owner |
| reference_renders/ PNGs placed in repo | ⏳ owner to drop in |
