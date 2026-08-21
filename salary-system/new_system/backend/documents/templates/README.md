# Document templates

Token-marked copies of APEX THERMOCON's own paperwork. Each file is **generated
programmatically** from its reference document by the script named below, so a
swapped reference can simply be re-tokenized:

```bash
cd new_system
../venv/bin/python -m backend.documents.templates.build.build_all
```

Rules that hold for every template:

* the reference's static layout is **never** touched — only variable cells gain
  a `{{token}}`, so `format_spec.py` can prove fidelity cell by cell
  (`tests/test_documents_engine.py`);
* every token is authored as a **single run** (.docx) or as the **whole cell
  value** (.xlsx), so the engine never has to stitch a token back together;
* item slots carry per-item tokens (`{{item.<field>}}`) in *every* row of the
  region; the engine clears the unused ones and keeps their styling;
* the reference documents under `../../../../sample docs/` are **read only** and
  are never modified.

Pure Python only: openpyxl + python-docx. Nothing is rendered, ever.

## Permitted deviations from the references

These are the **only** differences, and each one is asserted explicitly by
`tests/test_documents_engine.py` (`DEVIATIONS`):

| # | Deviation | Where |
|---|---|---|
| 1 | `Total Quotation Vaue` → `Total Quotation Value` (CONVENTIONS §8) | quotation A50 |
| 1 | `nas been tested` → `has been tested` (CONVENTIONS §8) | test_cert A33 |
| 1 | `Payment. Terms` → `Payment Terms` (CONVENTIONS §8) | ack E15 |
| 2 | straight `'` in generated date strings where Word used curly `’` | coc, ack, quotation |
| 3 | explicit print setup (portrait A4, fitToWidth=1, fitToHeight=0) — the BIFF `.xls` conversion carries none at all | ack |
| 4 | the two Test Certificate pictures come back as openpyxl images (the reference spec lists none because openpyxl cannot read the reference's drawing container) | test_cert |
| 5 | the Buyer's-Order line is regenerated in canonical `PO#### Dtd. DD.MM.YY, …` form — CONVENTIONS §8 typo-normalisation **policy** (owner-confirmed); the reference's copy was hand-typed with irregular separators and the `{po, date_iso}` list stays the editable source | packing_list, invoice |
| 6 | example rows 9/10 are normalised to the plain black, non-italic font of slot rows 11–32 — real BOM lines must not print grey (owner, 2026-08-21). Font only; the grey PLACEHOLDER footnote at A40 is untouched | bom |

---

## ack.xlsx

* **Provenance** `PO Acknowledgement Template (converted).xlsx` — the blank house
  acknowledgement, converted from BIFF `.xls` by Phase 0's pure-Python
  `tools/xls2xlsx.py`. Sheet `01`.
* **Build** `build/build_ack.py`
* **Reference spec** `reference_specs/PO_Acknowledgement_Template_(converted).spec.json`
  (structure) + `E01.04.08.26.252.26_(converted).spec.json` (values). The two
  real acks sit at different total-row positions because rows were inserted by
  hand over the years (CONVENTIONS §7), so structure is diffed against the blank
  template and values field by field against the filled E01 document.

| Token | Cell | Payload |
|---|---|---|
| `bill_to_1..5` | A7..A11 | `bill_to_lines[0..4]` |
| `ship_to_1..5` | G7..G11 | `ship_to_lines[0..4]` |
| `cust_po` | F6 | `cust_po` |
| `po_date` | F7 | `po_date_iso` → *ordinal_apostrophe* |
| `quotation_ref` | F9 | `quotation_ref` |
| `client_code` | C12 | `client_code` |
| `ack_ref` | F12 | `ack_ref` |
| `ack_date` | F13 | `ack_date_iso` → *ordinal_apostrophe* |
| `price_basis` | F14 | `price_basis` |
| `payment_terms` | F15 | `payment_terms` |
| `ship_date` | F16 | `ship_date_iso` → *ordinal_apostrophe* |
| `wo_no_long` | F17 | `wo_no_long` |
| `contact_name/email/tel/fax` | B14..B17 | `contacts.*` |
| `currency_header` | I18 | `currency_header` (default `Prices (U.S.D.)`) |
| `total` | I38 | `total` |
| `remit_intro` | A44 | default = CONVENTIONS §1 |
| `remittance_block` | A45 | default = **current 2026 wording** |
| `beneficiary_label` | A48 | default `BENEFICIARY : ` |
| `beneficiary_address` | A49 | default = company address |
| `beneficiary_account` | A50 | default = A/C 508505010000249 |

**Items** rows 20–37 (18 slots) — `A` sno · `B` code · `C` description ·
`F` material · `G` qty · `H` unit · `I` unit_price · `J` total.
Money cells `I20:J37` and `I38` follow the payload currency symbol.
`FORMAT: AT/ACK/EXP/01` (G51) stays static.
**Download name** `{wo_no_long}.xlsx` (the company names the file after the WO).

---

## work_order.xlsx

* **Provenance** `Apex Work Order (1).xlsx` (native). Sheet `Sheet1`.
* **Build** `build/build_work_order.py`
* **Reference spec** `Apex_Work_Order_(1).spec.json` — reproduced exactly.

| Token | Cell | Payload |
|---|---|---|
| `wo_no_short` | B6 | `wo_no_short` |
| `client_code` | G6 | `client_code` |
| `cust_po` | B8 | `cust_po` |
| `wo_date` | G8 | `date_iso` → *date_value* (real date, template numfmt `dd/mm/yyyy`) |

**Items** rows 12–38 (27 slots) — `A` sno · `B` part_no · `C` item · `D` qty ·
`E` material · `F` marking · `G` remarks. The grid is an **open box**: rows carry
left/right rules only and no horizontal rules are added. Despatch and Manager
QA / Manager Production lines are static. The header logo (F1) round-trips
through openpyxl and is asserted.
**Download name** `Apex-Work-Order-160-23.xlsx`.

---

## test_cert.xlsx

* **Provenance** `Test Certiticate PO59812-EI-047.xlsx` (native, landscape).
  Sheet `Sheet2`. The same template serves `PO60543-EI-100` — both references
  carry byte-identical picture parts (asserted).
* **Build** `build/build_test_cert.py`
* **Reference spec** `Test_Certiticate_PO59812-EI-047.spec.json` — reproduced
  exactly bar the A33 typo fix.

| Token | Cell | Payload |
|---|---|---|
| `cert_no` | Q3 | `cert_no` |
| `cert_date` | Q4 | `cert_date_iso` → *date_value* (numfmt `mm-dd-yy`) |
| `customer_line` | C5 | `customer_line` |
| `po` | C6 | `po` |
| `po_date` | F6 | `po_date_iso` → *date_value* |
| `invoice_no` | K6 | `invoice_no` |
| `invoice_date` | Q6 | `invoice_date_iso` → *date_value* |
| `spare_head_1..5` | P8..T8 | `extra_elements[0..4]`, default `-` |

**Items** rows 9–28 (20 slots) — `A` sno · `B` item · `C` size · `D` qty ·
`E` component · `F` heat_no · `G` material · `H..O` `chem.{C,Mn,Si,P,S,Cr,Ni,Mo}` ·
`P..T` `spare_1..5` (default `-`). Writing `extra_elements` puts an element
symbol into the spare **header** and the matching `spare_n` into the body.

**Images** the reference keeps its logo + ISO badges in a drawing container that
also holds a freeform shape; openpyxl reads none of it and drops both pictures
on save. `build_test_cert.py` lifts `xl/media/image1.png` / `image2.png` out of
the zip into `media/apex_logo.png` / `media/apex_iso.png` and re-anchors them as
openpyxl images at A1 / K1 with the reference's own EMU extents
(973455×842010 and 933450×476250 — asserted). The engine re-anchors anything
still missing after load.
**Download name** `Test Certificate PO59812-EI-047.xlsx` (filename typo fixed
per CONVENTIONS §8).

---

## bom.xlsx

* **Provenance** `Apex_BOM_Template_PLACEHOLDER.xlsx` — **ours**, a placeholder
  (CONVENTIONS §9-G); there is no official company BOM format yet. Sheet `BOM`.
* **Build** `build/build_bom.py`
* **Reference spec** `Apex_BOM_Template_PLACEHOLDER.spec.json` — reproduced exactly.

| Token | Cell | Payload |
|---|---|---|
| `bom_no` | Q3 | `bom_no` |
| `bom_date` | Q4 | `date_iso` → *date_value* |
| `customer_line` | C5 | `customer_line` |
| `po` | C6 | `po` |
| `po_date` | F6 | `po_date_iso` → *date_value* |
| `wo_no` | K6 | `wo_no` |
| `part_assy` | Q6 | `part_assy` |

**Items** rows 9–32 (24 slots) — `A` sno · `B` part_no · `C` description ·
`F` size · `G` material · `H` heat_or_os · `J` source · `L` qty_per ·
`N` total_qty · `P` unit · `Q` remarks.

The two example rows lost their **values** and their example **font**: rows 9/10
are normalised to the plain black, non-italic Calibri 9 of slot rows 11–32
(22 cells — the 11 value columns × 2 rows), because real BOM lines must not
print grey. Borders, alignment, wrap and number formats are untouched, and the
grey PLACEHOLDER footnote at **A40 is kept verbatim** until the owner supplies
the official format. Its two pictures round-trip through openpyxl and are
declared in the registry so a regression is caught.
**Download name** `Apex-BOM-26-27-001.xlsx`.

---

## packing_list.docx

* **Provenance** `Apex-Export Packing List-EI-168.docx` (native). One 16×9 table
  holds both the header block and the box-wise goods grid.
* **Build** `build/build_packing_list.py`
* **Reference spec** `Apex-Export_Packing_List-EI-168.spec.json` — sections,
  paragraphs, table topology and values reproduced (deviation 5 aside).

Header tokens (`t<table>.r<row>.c<cell>.p<paragraph>`):
`invoice_no_date` (r0c1p1) · `buyer_po_block` (r0c2p1) · `iec`, `ad_code`
(r1c1) · `consignee_1..5` (r2c0) · `buyer_1..4` (r2c1) · `pre_carriage`,
`place_receipt`, `origin_country`, `country_final_destination` (r3) ·
`vessel`, `port_loading`, `terms` (r4) · `port_discharge`, `final_destination`
(r5) · `marks_1..3` (r7c0) · `hts_line` (r7c1) · `total_weight_line`,
`totals_qty`, `totals_net_wt`, `totals_gross_wt` (r9).

Only the **value** text is tokenised: labels, tab characters and the
reference's surrounding whitespace (`{{terms}}\t `, `{{country_final_destination}} `)
survive untouched.

**Items** table 0, marker row **8**. The engine deep-copies that `<w:tr>` per
item row and removes the marker. `registry._packing_list_rows` flattens
`boxes[]`: the Box No. / Box Size / Net Wt / Gross Wt cells (indices 3,4,5,6)
`vMerge` **restart** on a box's first line and **continue** on the rest; a
single-line box gets *no* `vMerge` at all — exactly what the reference does for
its one-line Box No. 2. The Marks cell (index 0) restarts on the HTS row and
continues through every item row. Signature row static.
**Download name** `Apex-Export Packing List-EI-168.docx`.

---

## invoice.docx — *pending owner visual sign-off*

* **Provenance** no native reference: `Apex-Export Invoice-EI-168.doc` is legacy
  Word that no pure-Python library can read (owner confirmed this provenance
  2026-08-21). Built **from the packing-list skeleton** — identical page setup,
  company header paragraphs and header-table topology through the Terms row —
  with the goods grid rebuilt from `phase0/dumps/invoice_textutil.txt` and the
  owner's render.
* **Build** `build/build_invoice.py` (calls `build_packing_list.build()` first)
* **Reference spec** none. Its test asserts structural self-consistency:
  section size equal to the packing list's, all tokens consumed, column counts,
  and that no `vMerge` exists outside the marks / terms cells.

Row plan of the single table (13 template rows → 18 filled):

```
0-5   header block, identical to the packing list
6     Marks | Description(2) | Order No. | Qty. (Nos.) | Net Weight (Kgs.)(2)
      | Rate in {{currency_head}} | Amount in {{currency_head}}
7     HTS line, centred over the description column (marks cell vMerge restart)
8     ITEM MARKER row  -> {{item.code_desc|po|qty|net_wt|rate|amount}}
9     GSP duty line, centred in the description column
10    {{total_weight_line}} (span 3) | Total | {{totals_qty}} |
      {{totals_net_wt}} (span 2) | – | {{totals_amount}}
11    Amount Chargeable {{amount_words}} (in words) | Total in {{currency_head}}
      | {{totals_amount_words_value}}
12    {{declaration}} (left)  |  FOR APEX THERMOCON PVT. LTD. / AUTHORISED
      SIGNATORY (right)
```

Title paragraph `INVOICE` centred. Rate/Amount accept `-` for replacement lines.
**Download name** `Apex-Export Invoice-EI-168.docx`.

---

## coc.docx

* **Provenance** `COC-PO-02940-EI-122.docx` (native, no tables — a tab-aligned
  label/value ladder).
* **Build** `build/build_coc.py`
* **Reference spec** `COC-PO-02940-EI-122.spec.json` — sections and paragraphs
  reproduced modulo the apostrophe normalisation.

| Token | Where | Payload |
|---|---|---|
| `customer_caps` | certifying sentence | `customer_caps` (upper-cased) |
| `customer_short` | `{{customer_short}} P.O. NUMBER` label prefix | `customer_short` |
| `po` | P.O. NUMBER value | `po` |
| `invoice_no` | INVOICE NUMBER value | `invoice_no` |
| `invoice_date` | after the static `Dtd. ` | `invoice_date_iso` → *ordinal_apostrophe* |
| `part_desc` | PART NO. / DESCRIPTION | `part_desc` |
| `material` | MATERIAL | `material` |
| `plating` | PLATING PROCESS | `plating` (default `NA`) |
| `finishing` | FINISHING PROCESS | `finishing` (default `NA`) |
| `qty_shipped` | `{{qty_shipped}} Nos.` | `qty_shipped` |
| `authenticator` | AUTHENTICATOR'S NAME | `authenticator` (default `Q.A. MANAGER`) |
| `date_shipped` | DATE SHIPPED | `date_shipped_iso` → *ordinal_apostrophe* |

Tokens replace only the value text **after** the tabs, so every tab stop and the
exact run of spaces the reference uses are preserved (asserted).
**Download name** `COC-PO-02940-EI-122.docx`.

---

## quotation.xlsx — *pending owner visual sign-off*

* **Provenance** rebuilt from `Thermosense-Quotation-316.pdf`; no editable
  original survives, so there is no reference spec. Every ruling line and word
  position was measured in points (`phase0/dumps/quotation_geometry.txt`,
  page 595.32 × 841.92 = A4 portrait) and reproduced as an Excel grid.
* **Build** `build/build_quotation.py`. Sheet `Quotation`.

**pt → Excel character widths.** Excel stores a column width in characters of
the workbook's normal font and renders it `px = round(chars × MDW + 5)`, where
MDW = 7 px for the Calibri 11 normal font openpyxl writes, and 1 pt = 96/72 px.
Inverting that — and keeping the 5 px cell padding out of the proportion so the
column *boundaries* land exactly where the PDF has them:

```
chars = (pt × 96/72 − 5) / 7      →  effective ≈ 5.55 pt per character
```

The seven columns sum to **86.71 chars = 642 px = 481.5 pt**, exactly the
measured frame width (asserted). Column boundaries in pt:
`38.9 | 58.0 | 107.2 | 357.3 | 393.1 | 420.0 | 465.1 | 520.4`.

Row heights are the measured y-deltas of the PDF's text lines and rules (Excel's
row-height unit is also points); the 30 body slots share the measured
255.3 → 585.3 band evenly at 10.9 pt.

| Token | Cell | Payload |
|---|---|---|
| `customer_name` | A6 | `customer.name` |
| `customer_line_2/3` | A7, A8 | `customer.address_lines[0..1]` |
| `customer_country` | A9 | `customer.country` |
| `rfq_ref` | F6 | `rfq_ref` |
| `rfq_date` | F7 | `rfq_date_iso` → *ordinal_apostrophe* |
| `quotation_date` | F8 | `date_iso` → *ordinal_apostrophe* |
| `number` | F9 | `number` |
| `client_code` | C10 | `client_code` |
| `attn` / `tel` / `fax` / `email` | C11..C14 | `customer.contact.*` (email underlined) |
| `intro` | A15 (A15:G16) | `intro`, default = the standard two-line sentence |
| `currency_header` | F17 | `currency.header` |
| `validity_line` | C49 | default `THIS QUOTATION IS VALID FOR 30 DAYS` |
| `total` | G50 | `total` |
| `price_basis` … `note` | C51..C56 | `price_basis`, `lead_time`, `payment_terms`, `guarantee`, `taxes_duties`, `note` |
| `approved_by` | A59 (`Approved by : {{approved_by}}`) | `approved_by` |
| `prepared_by` | D59 (`Prepared by {{prepared_by}}`) | `prepared_by` |

**Items** rows 19–48 (30 slots) — `A` sno · `B` code · `C` description ·
`D` qty · `E` unit · `F` unit_price · `G` total.

* Fonts Arial: titles 11.5, labels/body 8, item rows 7.5 (nearest Excel sizes to
  the measured 11.4 / 8.2 / 7.3). `APEX THERMOCON Pvt. Ltd.` is bold RGB `1F497D`.
* The quotation/ack phone variant **4167651** is used (CONVENTIONS §1, §9-E).
* Qty column carries the **Indian digit-grouping** number format
  `[>=10000000]##\,##\,##\,##0;[>=100000]##\,##\,##0;#,##0` → `1,20,000`.
* **Currency symbol rides on the number format**, not on the value: unit prices
  `"$"#,##0.000`, totals `"$"#,##0.00`, and the engine rewrites `"$"` to the
  payload's `currency.symbol` when it is not USD (so the Thermosense quotation
  prints `£0.534` / `£64,080.00` while the value stays a real number).
* Borders: medium outer frame + thin internal rules, one per RECT in the
  geometry dump. `THIS QUOTATION IS VALID FOR 30 DAYS` sits **inside** the table
  (column C of row 49) with the column rules running past it, as in the PDF.
* `Format : AT/QTN/EXP/01` (D60) static, right of the divider at x=357.3.
* Print setup portrait A4, fitToWidth=1, fitToHeight=0.

**Download name** `Thermosense-Ltd-Quotation-316.xlsx`.

---

## media/

`apex_logo.png` and `apex_iso.png` are extracted verbatim from the reference
workbooks' `xl/media/` by the build scripts (both Test Certificates and the BOM
placeholder carry byte-identical copies). They exist so the engine can
re-anchor pictures openpyxl would otherwise drop.
