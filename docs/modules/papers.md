# SOP Documents (the papers suite)

**Status: ✅ built** (2026-08-21)

## Purpose
The company issues real documents at every SOP stage — quotation, PO
acknowledgement, work order, BOM, export invoice, packing list, COC, test
certificate — in house formats that have looked the same for years. This module
generates them. One new noun, the **paper**: a generated document instance
hanging off an order, plus the engine that fills token-marked copies of the
reference files.

Nothing here renders anything. Templates are byte-for-byte copies of the
reference documents with `{{tokens}}` typed into the variable cells, so the
static layout is untouched and fidelity is provable by structural diff. See
[SOP-DESIGN.md](../SOP-DESIGN.md) §2–§5 and
[CONVENTIONS.md](../CONVENTIONS.md) §3–§6, which own every number format and
label.

## The five files
```
documents/engine.py     XlsxFiller / DocxFiller — token replacement, item-slot
                        filling, style-cloned overflow rows, image re-anchoring
documents/registry.py   one entry per kind + the CANONICAL PAYLOAD SCHEMAS in
                        its module docstring (read that first)
documents/payloads.py   build_<kind>(conn, order_id, opts) -> payload
documents/service.py    the lifecycle: create / edit / refill / status /
                        revise / render / delete
documents/router.py     /api/papers — a thin HTTP skin, no logic
documents/dates.py      the four CONVENTIONS §4 date renderers
documents/templates/    the eight token-marked files + media/ + README.md
```

## The eight kinds
`registry.ENTRIES`, `service.KIND_LABELS`, `payloads.BUILDERS` — all keyed the
same, and a kind missing from any of them fails a test.

| kind | label | format · template | number from | items region |
|---|---|---|---|---|
| `quotation` | Quotation | xlsx · `quotation.xlsx` (sheet *Quotation*) | its ledger row's `doc_no` | rows 19–48 |
| `ack` | PO acknowledgement | xlsx · `ack.xlsx` (sheet *01*) | `numbering.ack_ref` + `work_order_no` | rows 20–37 |
| `work_order` | Work order | xlsx · `work_order.xlsx` | the ack's reservation | rows 12–38 |
| `invoice` | Export invoice | docx · `invoice.docx` | `numbering.invoice_no` | table 0, marker row 8 |
| `packing_list` | Packing list | docx · `packing_list.docx` | its invoice paper's number | table 0, marker row 8 |
| `coc` | Certificate of conformance | docx · `coc.docx` | derived — `registry.stem("coc", …)` | no grid |
| `test_cert` | Test certificate | xlsx · `test_cert.xlsx` (sheet *Sheet2*) | derived — `numbering.tc_no` | rows 9–28 |
| `bom` | Bill of materials | xlsx · `bom.xlsx` (sheet *BOM*) | `numbering.bom_no` | rows 9–32 |

`filename` on each entry is a callable `payload -> download name`, mirroring the
office's own names (`COC-PO-02940-EI-169.docx`). `service.download_name` hands
it the **unsuffixed** number and appends ` Rev-A` to the stem — the callables
read the serial off the tail of the number, and `AT/EI/26-27/169 Rev-A` has no
trailing digits.

## The engine
`engine.render(kind, payload) -> (bytes, filename)` picks a filler off the
entry's `format`. Both fillers do the same four things:

- **Resolve** every `{{token}}` through `build_token_map(entry, payload)`. A
  spec is `{"path": <dotted payload path>}` plus optional `index`, `render`
  (a date renderer or `date_value`), `default`, `required`, `const`, `upper`.
- **Assert the coordinate.** An xlsx spec's `cell` pins where the token must
  live and `_verify_coordinates` checks it before filling, so template drift
  fails loudly instead of rendering a blank. `where` is the docx equivalent and
  is documentation only.
- **Fill the item region.** xlsx uses `first_row`/`last_row` + `cells` (column
  letter → spec); docx uses `table`/`marker_row`/`row_builder`. More items than
  slots calls `_grow` → `insert_rows_preserving_merges`, which clones the style
  of a real slot row rather than inventing one.
- **Sweep.** A final pass over cells, paragraphs, table cells, headers and
  footers raises `TemplateError` if any `{{` survives, and `_check_consumed`
  raises if a resolved value was never used.

Two extras worth knowing: `_ensure_images` re-anchors pictures openpyxl drops
on save (the TC logo and ISO badges live in a drawing container it cannot
round-trip — they are extracted once into `templates/media/` and re-attached as
openpyxl images), and `currency_numfmt_cells` swaps a money cell's `"$"` number
format to follow `payload["currency"]` (CONVENTIONS §9-D).

## Payloads
`payloads.build(conn, kind, order_id, opts)` dispatches through `BUILDERS`. The
payload **is** the paper: `registry.py`'s docstring holds the canonical schema
per kind, `payloads.SCHEMAS` is that contract in code, and `check_schema`
validates it on every create, refill and hand edit — every key present, nothing
extra, blanks as `''` and never `None` (a `None` renders the template's default
instead of the blank the office asked for).

Auto-fill happens **once**, at creation. After that the payload is the editable
truth; every default is a starting point, nothing is ever locked (owner brief).

| kind | filled from |
|---|---|
| `quotation` | ledger doc + lines, customer block (+ primary contact), currency defaulted from the customer's country, export fields from the CONVENTIONS §1 constants |
| `ack` | the quotation it answers (`quotation_paper_id`) or the literal `Repeat PO` (`repeat_po`), order (customer PO + date), bill-to/ship-to, ship-date promise |
| `work_order` | the ack paper (client, PO, date) + order items → part no/drawing, qty, material, marking |
| `invoice` | order + `merged_items(order_ids, item_ids)`; `buyer_po_block` is a **list** of `{po, date_iso}` and each item carries its own `po`; totals, amount in words, IEC/AD/HTS/ports constants |
| `packing_list` | its invoice paper's header + `boxes[]` — boxes live only in the payload, one starting box holding everything |
| `coc` | invoice paper + order; plating/finishing default to `NA` |
| `test_cert` | invoice paper + `issued_heats(order_no)` → `heat_composition` per line, C/Mn/Si/P/S/Cr/Ni/Mo in `chem` plus `spare_1..spare_5` |
| `bom` | `orders.order_bom()` rollup + `_outsourced_materials()` — `heat_or_os` takes the heat number or the OS ID, `source` reads `In-House` / `Outsourced - V01` |

Builders deliberately **never consume a number**: a counter must only be burnt
by the transaction that inserts the row, so they leave those fields `''` and
`service.create_paper` injects them. Numbers that are *derived* rather than
counted (the TC's `AT/TC/…`, the COC's `COC-PO-…`) are built here, as is the
quotation's — which is simply its ledger row's, because a paper never re-numbers
a ledger document.

Two documented extensions to the canonical schema, both nested: `currency.head`
(`"USD ($)"` — the invoice prints the currency as its own column head) and the
TC items' `spare_N` beside `chem`, in the same order as the payload's
`extra_elements` heads.

## The `_`-prefixed meta keys
`paper` has no column for the app's own bookkeeping, so it lives in the payload
under `payloads.META_PREFIX` (`_`). Meta keys are excluded from the schema
contract, never rendered as fields, and **survive an edit** — the UI never sends
them back, so `update_payload` re-applies them from the stored row.

| key | meaning |
|---|---|
| `_opts` | the arguments the paper was built from — what Refill replays |
| `_wo_no_short` | (ack only) the shop-floor form of the work-order number the ack reserved |

## Lifecycle (`service.py`)
`STATUSES = draft, final, sent, superseded, void`, and `TRANSITIONS` is the
whole rule: `draft → {final, void}`, `final → {sent, void}`, `sent → {void}`,
and nothing leaves `superseded` or `void`. `superseded` is set by `revise()`
alone.

- **create_paper** — builds the payload, takes the number and INSERTs, all
  inside one `BEGIN IMMEDIATE`: two clicks cannot hand out the same
  acknowledgement reference, and a rollback gives the number back.
  `UNIQUE(kind, paper_no, revision)` is the backstop.
- **update_payload** — drafts only ("final papers are frozen — revise or void").
- **refill** — re-runs the builder with the paper's own `_opts`, then copies the
  kind's `NUMBER_FIELDS` back over the fresh payload: the counter was spent on
  those, so a builder must not blank or re-derive them. Explicit only; auto-fill
  never runs again on its own.
- **revise** — only from `final`/`sent`, and only at the tip of a chain
  (`based_on_id` is checked, so a superseded paper cannot be branched). Copies
  the payload to a new draft with the same `paper_no` and the next revision
  letter, writes the suffixed number into the kind's `PRINTED_NUMBER` field, and
  supersedes the parent. A **quotation also revises its ledger row** in the same
  breath (`quotations.revise_doc`), so money and paperwork keep one number.
- **delete_paper** — drafts only. An issued number stays on the record, voided
  if need be: a number handed out is never handed out twice.
- **render_paper** — `payload + template -> bytes` on demand. Nothing stale on
  disk, so a download always reflects the current payload.

### Numbering integration
Everything goes through `backend/core/numbering.py` (see
[settings.md](settings.md) for the Settings → Numbering editor).
`service._take_number` is the only place that calls a counter, and it runs
inside `create_paper`'s transaction:

- `quotation` — no counter; takes its ledger row's `doc_no`, revision and all.
- `ack` — `ack_ref()` **and** `work_order_no()` together, because the WO number
  embeds the ack's date (CONVENTIONS §7). The short form is stashed as
  `_wo_no_short`.
- `work_order` — `_reserved_wo()` reads that meta key off the order's latest ack
  and reuses it; only an order with no ack burns a fresh serial. Falls back to
  `short_from_long()` for acks written before the meta key existed.
- `invoice` — `invoice_no()` plus a **ledger twin** written here rather than via
  `quotations.create_doc`, which would open its own transaction and consume a
  second number.
- `packing_list` — its invoice's number verbatim.
- `coc` / `test_cert` — derived from the PO + invoice; a missing one is a missing
  identity, so it raises rather than numbering a blank.
- `bom` — `bom_no()`.

## Routes
`/api/papers`, guarded by `require_module(*GUARD_KEYS)` where `GUARD_KEYS` is
the four SOP grants — `acks`, `production_docs`, `shipping_docs`,
`quality_docs` — **any** of which admits, the same pattern as inventory's
shared `/api/material` router. The tuple is built as a comprehension over
`registry.ALL_KEYS` so a key renamed in the registry fails the guard test
instead of failing at import (`GUARD_IS_FALLBACK` flags that).

| Method | Path | Purpose |
|---|---|---|
| GET | `/refs` | kind labels, statuses, transitions, and which opts each kind needs — this drives the form |
| GET | `` | list, filtered by `kind` (comma list), `order_id`, `q`, `status` |
| POST | `` | create — `{kind, order_id, opts}` |
| GET | `/{id}` | one paper + its revision chain |
| PUT | `/{id}` | save a hand-edited draft |
| POST | `/{id}/refill` | explicit re-sync from the order |
| POST | `/{id}/status` | draft → final → sent, or void |
| POST | `/{id}/revise` | a fresh ` Rev-A` draft; parent superseded |
| GET | `/{id}/file?download=` | the rendered file, inline or as an attachment |
| DELETE | `/{id}` | drafts only |

`/refs` comes **before** `/{paper_id}` or it is read as a paper id. Every
option is named on `PaperOptsIn` rather than taken as a free `dict`: FastAPI
silently drops an undeclared field, which is exactly how an edit loses half a
payload. `_opts()` then strips the falsy ones, so a builder never sees
`order_ids=[]` and reads it as "no orders".

## Fidelity tests — and how to swap a template
`tests/test_documents_engine.py` (65 tests) fills each kind with **its own
reference document's data** and diffs the `format_spec.py` dump against
`reference_specs/<file>.spec.json`: merged ranges, column widths, row heights,
page setup, and the fonts/borders/alignment/fills of every static cell must
match exactly, and the values must come back identical. Nothing is ever
rendered — that is the owner's toolchain rule.

The only permitted differences are enumerated in the module's `DEVIATIONS`
string and each one is asserted explicitly, so a deviation cannot creep in
silently: the CONVENTIONS §8 typo fixes, straight vs curly apostrophes in
generated dates, the ack's explicit print setup (the BIFF `.xls` conversion has
none), the TC pictures coming back as openpyxl images, the canonical
`PO#### Dtd. DD.MM.YY` buyer-order line, and the BOM placeholder's two example
rows normalised to real-line styling. `tests/test_papers.py` (64 tests) covers
the lifecycle, the builders and the route models.

**To swap a template** (SOP-DESIGN §4 — this is the whole procedure):

1. Drop the new file in `templates/`, keeping the name or updating
   `entry["template"]`.
2. Type `{{tokens}}` into its variable cells. Do not touch anything else — the
   static layout is what the spec diff pins.
3. Update that entry's `tokens` (`cell` coordinates above all), `items`
   region, `images` and `filename`.
4. Regenerate the reference spec if the format itself changed, or add a line to
   `DEVIATIONS` with its assertion.
5. `python -m unittest tests.test_documents_engine` — **no engine change**.

## Data model
`paper(kind, paper_no, revision, order_id, customer_id, document_id,
based_on_id, status, paper_date, payload JSON, created_at, updated_at,
UNIQUE(kind, paper_no, revision))` — indexed on `(order_id)` and
`(kind, status)`. The file is never stored; `payload` is the whole document.

`document_id` links quotation/invoice papers to their ledger row: the ledger
stays the money truth for customer stats and the Documents tab, the paper holds
the export-format fields. `based_on_id` doubles as the revision parent and the
attach-existing source.

## Screens
`frontend/papers/` at `/papers/` — list + full-page editor per kind, deep-linked
as `?open=<id>` and pre-filtered as `?kind=…` by the four homepage tiles.
guide-images: ws-papers-list, ws-paper-editor. User-facing:
[USER_GUIDE.md](../USER_GUIDE.md) section 10.

## What's left
- [ ] Quotation template awaits the owner's sign-off — it was rebuilt as xlsx
      from the PDF's measured geometry, so `reference_specs` has no entry for it
      and `QuotationStructure` checks geometry rather than diffing a spec.
- [ ] The BOM format is a placeholder (`AT/BOM/{FY}/{n}`, format code
      `AT/BOM/EXP/01` both to confirm) — swap it per the procedure above when
      the official one exists.
- [ ] COC granularity is one per shipment with editable part/material lines;
      whether a multi-part order wants one COC per part is still open
      (CONVENTIONS §9-H).
- [ ] Attach-existing (a past quotation reused verbatim on a new order) is
      carried by `based_on_id` + `opts["based_on_id"]` but has no UI yet.
