# CONVENTIONS.md — the paper trail, decoded

**Single source of truth for every ID, label, number format, and field convention used
anywhere in the app.** Built by reading every reference document in
`salary-system/sample docs/` end to end (2026-08-21). When code and this file disagree,
this file wins; when this file and a reference document disagree, the document wins and
this file must be corrected.

Reference set: Thermosense-Quotation-316.pdf · PO Acknowledgement Template (1).xls ·
E01.04.08.26.252.26.xls · Apex Work Order (1).xlsx · Apex-Export Invoice-EI-168.doc ·
Apex-Export Packing List-EI-168.docx · COC-PO-02940-EI-122.docx ·
Test Certiticate PO59812-EI-047.xlsx · Test Certiticate PO60543-EI-100.xlsx ·
Apex_BOM_Template_PLACEHOLDER.xlsx (placeholder, ours).

---

## 1. Company constants

| Constant | Value (as printed on documents) |
|---|---|
| Legal name | APEX THERMOCON Pvt. Ltd. (all-caps `APEX THERMOCON PVT. LTD.` on WO/TC/COC/Invoice/PL) |
| Address | A-2/15, Sector 17, Kavi Nagar Industrial Area, Ghaziabad 201 002, UP, INDIA |
| ISO line | "An ISO 9001:2015 Certified Company" (TC + BOM headers) |
| Tel/Fax — quotation & ack | Tel. No. 91-120- **4167651** / 91-9810127235 Fax No. 91- 120- 4167561 |
| Tel/Fax — TC, COC, Invoice, PL | Tel: +91-120-**4167561**, Fax: +91-120-4167561 |
| E-mail / web | sales@apexthermocon.com / www.apexthermocon.com |
| IEC Code | 0509008631 |
| AD Code | 0292085 / 2690009 |
| HTS / H.T. Code | 9032.90.6080 — "Parts & Accessories for Automatic Regulating & Controlling Instruments" |
| Ports / mode | Pre-carrier receipt GHAZIABAD · Port of Loading NEW DELHI · Vessel/Flight "AIR" |
| Duty line | "GOODS OF INDIAN ORIGIN ELIGIBLE FOR NIL DUTY UNDER GSP (A)" |
| Jurisdiction note | "Our offer is subject to Delhi, India Jurisdiction." (quotation) |
| Quotation validity | "THIS QUOTATION IS VALID FOR 30 DAYS" |
| Approved by | Sumesh Garg (quotation approval block) |

**Remittance block** (ack footer, current 2026 wording): BANK OF AMERICA, NEWYORK, U.S.A.
SWIFT CODE : BOFAUS3N, ACCOUNT NUMBER : 6550692276 OF "UNION BANK OF INDIA, MUMBAI.
(INDIA)" TO THE CREDIT OF "UNION BANK OF INDIA, S.S.I. BRANCH", H-6, PATEL NAGAR-III,
GHAZIABAD-201001 (U.P), INDIA. FAX : 91-120-2835993 / PHONES: 91-120-2834293 ·
SWIFT CODE : UBININBBGHZ · BENEFICIARY : APEX THERMOCON PVT. LTD. … A/c: 508505010000249.
(The blank template carries an older, longer variant with CHIPS ABA/FedWire routing —
treat the filled 2026 document as current; keep the block editable.)

All of these are **editable defaults**, not hard-coded strings: they live in template
files and/or settings, never inline in code.

## 2. Customers & client codes

Scheme: **first letter of customer name + 2-digit sequence** (`T04`, `E01`, `S01`).
Owner ruling 2026-08-21: this scheme is the app's ONE customer code — legacy
two-letter codes were re-coded at startup, every code stays editable per
customer, and after 99 customers under a letter the serial simply grows
(`A100`). Set the true codes (E01, T04, S01…) on the customer records before
generating real paperwork.

| Code | Customer | Evidence |
|---|---|---|
| T04 | Thermosense Ltd., England (Ryan Davis, ryan.davis@thermosense.co.uk) | Quotation 316 |
| E01 | East Coast Sensors, 20 Hathaway Drive, Stratford, CT 06615, USA (Ed O'Neill) | Ack E01.04.08.26.01 |
| S01 | SELCO Products Company, Reno NV, USA (Cassie Halverson) — inferred: S01 WO carries PO00837, and SELCO POs are `PO0XXXX` | WO 160/23, COC, Invoice/PL EI-168 |
| ? | Reotemp Instrument Corporation, USA — no client code appears in its TCs | TC 047/100 |

SELCO has **two addresses**: Consignee (640 Maestro Drive, Suite 102, Reno NV 89511) vs
Buyer (8780 Technology Way, Reno NV 89521-5908) — customers need distinct
consignee/ship-to and buyer/bill-to addresses.

Customer PO formats vary and must stay free text: SELCO `PO0XXXX` (PO02940, PO03864…),
Reotemp bare 5-digit (59812, 60543), East Coast Sensors 4-digit (2916).

## 3. Document numbering (the registry)

| Document | Pattern | Example | Counter scope |
|---|---|---|---|
| Quotation | `{Client}/AT/{DDMMYY}/{serial}` | `T04/AT/130826/316` | **per client code**, running (see §9-A) |
| Quotation revision | `{base} Rev-{A,B,…}` | `T04/AT/130826/316 Rev-A` | per base quotation (placeholder scheme, §9-B) |
| PO Acknowledgement | `{Client}.{DD}.{MM}.{YY}.{seq}` | `E01.04.08.26.01` | per client **per day**, starts 01 |
| Work Order (long) | `{Client}.{DD}.{MM}.{YY}.{WOserial}.{YY}` | `E01.04.08.26.252.26` | date part = ack date; WOserial per year |
| Work Order (short, shop floor) | `{WOserial}/{YY}` | `160/23`, `252/26` | last two segments of long form |
| Export Invoice | `AT/EI/{FY}/{serial:03d}` | `AT/EI/26-27/168` | per Indian fiscal year (Apr–Mar), resets |
| Packing List | same number as its invoice | `AT/EI/26-27/168` | — |
| COC | no number of its own; references PO + invoice | file: `COC-PO-02940-EI-122` | — |
| Test Certificate | `AT/TC/{CustPO}/EI-{invSerial}/{FY}` | `AT/TC/59812/EI-047/24-25` | derived, no counter |
| Bill of Materials | `AT/BOM/{FY}/{serial}` — **placeholder**, to confirm | `AT/BOM/26-27/001` | per fiscal year (proposed) |
| Outsourced item ID | `OS-{serial:04d}` — ours, new | `OS-0001` | global, isolated module (swappable) |
| Outsourced PO (outgoing job) | `AT/OS/{FY}/{serial:03d}` — ours, new | `AT/OS/26-27/001` | per fiscal year |

**Format codes** printed on documents: quotation `AT/QTN/EXP/01` ("Format :" bottom
right), acknowledgement `AT/ACK/EXP/01` ("FORMAT:" bottom right). No format code appears
on WO/TC/COC/Invoice/PL references. BOM placeholder proposes `AT/BOM/EXP/01`.

Indian fiscal year notation: `24-25` = Apr 2024–Mar 2025. August 2026 ⇒ `26-27`.

## 4. Date formats (per document — they genuinely differ)

| Where | Format | Example |
|---|---|---|
| Quotation (RFQ date, quotation date), Ack (PO date, ack date, ship date), COC (invoice "Dtd.", date shipped) | `{D}th Mon' YYYY` — day ordinal, apostrophe after month | `13th Aug' 2026`, `04th Aug' 2026`, `30th Jun' 2025` |
| Invoice header date | `DD/MM/YYYY` | `14/08/2026` |
| Buyer's PO dates on Invoice/PL | `Dtd. DD.MM.YY` | `PO03864 Dtd. 06.03.26` |
| Test Certificate (cert date, PO date, invoice date) | Excel `mm-dd-yy` (US style) | `05-10-24` |
| Work Order date | Excel `dd/mm/yyyy` | `16/05/2023` |

Day ordinals as seen: `04th` (zero-padded ordinal), `13th`, `31st`, `25th`, `30th`,
`16th`, `11th` — i.e. always two digits + suffix.
Several source cells store dates as raw Excel serials — the app normalizes to ISO in the
DB and renders per-document formats only at generation time.

## 5. Line items, units & figures

- Units: `EA` (quotation/ack), `Nos.` (invoice qty, COC "100 Nos.").
- Quantities print with **Indian digit grouping** on the quotation (`1,20,000`).
- Unit prices to 3 decimals (`£0.534`, `0.675`, `0.320`); ack unit prices 2 decimals
  (`37.29`); line totals 2 decimals (`64,080.00`, `1350.00`).
- Weights in Kgs, 3 decimals (`20.600`, `50.000`); box sizes in inches (`16" x 11" x 10"`).
- Amount in words: `USD Three Thousand Eight Hundred Fifty Eight Only` + "(in words)".
- Free/replacement lines print `-` in Rate and Amount (see invoice line 2 "(Replacements)").
- Materials seen: 304SS, Brass, Copper, Naval Brass Grade 1, HDPE, A479-316L, A479-304L,
  ASTM A276 304L, "S/Steel", "304S/Steel" (invoice prose spellings).
- Size-of-material forms: `1-3/8" Hex.`, `9/8" Hex.`, `28 ø`, `3/4" Hex.` — matches
  inventory stock shapes (Ø / A-F sections).
- Part codes are customer-specific SKUs, free text: `TWB02000750`, `TPS-0353`,
  `ECS-125-688-NB-BR`, `ST6316-1`, `TWS061000`, `TPCR-236-0790`, bare `3200625`.
- Heat numbers: `H` + 4 digits (H4515, H4443, H3803, H4498, H4508, H3257).
- Chemistry columns on TC: C% Mn% Si% P% S% Cr% Ni% Mo% + five spare `-` columns for
  other elements. Same heat ⇒ same analysis repeated per line.
- Marking column on WO (`-` when none); Component column on TC (usually `-`).

## 6. Document anatomy (field checklists for the payload models)

- **Quotation**: to-block (customer, country), RFQ Ref No ("Email"), RFQ Date,
  Quotation Dt., Quotation No, Client Code, Attn./Tel/Fax/E-Mail, intro sentence, items
  (Sr No, Product Code, Description, Qty, Unit, Unit Price, Total), validity line,
  Total Quotation Value, Price basis, Lead Time, Payment Terms, Guarantee,
  Taxes & Duties, Note, "For Apex Thermocon Pvt. Ltd.", Approved by/Prepared by, Format code.
- **Acknowledgement**: Bill To + Ship To blocks, Cust. P.O.No., P.O.Date,
  Quotation Ref. (may read literally `Repeat PO`), Client Code, Ackn. Ref., Ackn. Date,
  CONTACTS (Name/E-Mail/Tel/Fax), Price Basis, Payment Terms, **Ship Date** (promise),
  Work Order No., items (Sl No, Product Code, Description, Matl., Qty., Unit, Unit
  price, Total), TOTAL P.O. VALUE, thanks sentence, signatory, remittance block, format code.
- **Work Order** (internal): W/o.No. (short), Client code, PO No., Date, items
  (S.No., Part No./Drg. No., Item, Qty., Matl., Marking, Remarks), Date of Despatch,
  Manager QA / Manager Production sign lines. Logo image top-right.
- **Invoice**: exporter block, INVOICE NO. & DATE, Buyer's Order No. & Date (many POs
  with Dtd.), IEC/AD codes, Consignee vs Buyer, pre-carriage/vessel/ports/countries,
  Terms (DDU), marks ("1/3 TO 3/3, AS ADDRESS, 3 BOXES"), HTS description line, items
  (numbered `1.CODE,Description, Matl. X` + per-line Order No. + Qty + Net Wt + Rate +
  Amount), GSP duty line, totals (qty/net wt/amount), total weight line, amount in
  words, declaration, signatory.
- **Packing List**: same header block as invoice; goods grid is **box-wise**: per part
  (numbered within its box) Qty Per, Box No., Box Size, per-box Net/Gross Wt (repeated
  on each row of the box), Total Weight line + totals row, signatory.
- **COC**: certifying sentence naming customer in caps, `{CUSTOMER} P.O. NUMBER`,
  `Apex Thermocon INVOICE NUMBER` (+ Dtd.), PART NO. / DESCRIPTION, MATERIAL,
  PLATING PROCESS (NA), FINISHING PROCESS (NA), QUANTITY SHIPPED (n Nos.),
  AUTHENTICATOR'S NAME (Q.A. MANAGER), DATE SHIPPED.
- **Test Certificate**: cert no + date, Customer, PO No + PO Date, Invoice No + Date,
  items (S.No, Item, Size of Matl., Qty., Component, Heat No., Material, chemistry %),
  certify sentence, QC In-Charge + Authorised Signatory. Landscape.
- **BOM (placeholder)**: BOM No + date, Customer, PO, WO No., Part/Assy., items (S.No,
  Part/Drg No, Component/Description, Size, Material, **Heat No. / OS ID**, **Source**
  (In-House / Outsourced - V01), Qty/Assy, Total Qty, Unit, Remarks), certify sentence,
  Prepared By / Authorised Signatory.

## 7. Structural facts the data model must honor

- One invoice covers **multiple customer POs** (EI-168 covers six SELCO POs); the PO
  number is per-line on the invoice.
- One packing list pairs 1:1 with an invoice (same number) and groups items into boxes.
- One test certificate covers **multiple heats and materials**; chemistry comes from
  the heat record.
- The acknowledgement's Quotation Ref. may be a real quotation number or the literal
  `Repeat PO` — quotations are reusable across orders.
- The Work Order number embeds the acknowledgement date — WO is born with/after the ack.
- COC references customer PO + invoice; one part per COC in the reference (multi-part
  orders may need one COC per part or an editable combined list — field stays editable).
- Item-grid capacity is fixed rows in the spreadsheet formats (ack template: 18 slots,
  TC: 20, BOM: 24); the two real acks differ in total-row position because rows were
  inserted/deleted by hand over the years — the engine fills the template's grid and
  only inserts style-cloned rows when items overflow it.

## 8. Typos & normalizations (replicate the layout, not the mistakes)

Fixed in our templates (owner instruction: never propagate template typos):

| Reference says | We generate |
|---|---|
| `Total Quotation Vaue` (quotation PDF) | `Total Quotation Value` |
| `nas been tested` (both TCs) | `has been tested` |
| `Payment. Terms` (ack label) | `Payment Terms` |
| `NEWYORK` (remittance block) | kept as printed — bank instructions are quoted verbatim |
| Filenames "Test Certiticate…" | our files: `Test Certificate …` |

## 9. Open questions & conflicts (owner rulings of 2026-08-21 marked ✓)

- **A. Quotation serial scope. ✓ RESOLVED** — per-client running serial (the two
  documents cannot share one global counter: `E01/…/594` on 03 Aug,
  `T04/…/316` ten days later). Owner confirmed and seeded: T04 → 317,
  E01 → 595; all counters editable in Settings → Numbering.
- **B. Revision marker.** No revision appears in any reference. Chosen placeholder per
  brief: ` Rev-A`, ` Rev-B`… appended to the base number; history kept, only latest
  revision active.
- **C. WO serial scope. ✓ RESOLVED** — 160/23 (May'23) → 167/23 (Jun'23) →
  252/26 (Aug'26): rates only make sense if the serial **resets each calendar
  year** (and the number always carries /YY). Owner confirmed; seeded 253 for /26.
  One WO serial is reserved per acknowledgement (its date is embedded in the
  long form); the shop-floor Work Order paper reuses the ack's reservation.
- **D. Currency header. ✓ RESOLVED** — the header is a **currency field**
  (`Prices (U.S.D.)` / `Prices (G.B.P.)` …) defaulting from the customer's
  country (UK → GBP, else USD), symbols following; the reference's own mislabel
  is not reproduced. Ledger unit rates keep 3 decimals (`£0.534`); line totals 2.
- **E. Phone discrepancy.** Quotation/Ack print Tel **4167651**; TC/COC/Invoice/PL print
  **4167561**. Kept per-document as each reference prints it (fidelity), until the owner
  says which is right.
- **F. Reotemp client code** unknown (likely `R01`+) — set when first quotation/ack for
  Reotemp is created; the field is editable on the customer.
- **G. BOM format** is a placeholder (`Apex_BOM_Template_PLACEHOLDER.xlsx`, marked on
  its face) — swap for the official format when it exists; numbering `AT/BOM/{FY}/{n}`
  and format code `AT/BOM/EXP/01` to confirm then.
- **H. COC granularity** (one per invoice vs one per part) — reference shows a single
  part; default one COC per shipment with editable part/material lines, duplicable per
  part when needed.

## 10. Where this is enforced

- `backend/core/numbering.py` — every pattern above lives there and only there;
  counters + seeds in the `doc_counter` table, editable in Settings → Numbering.
- `backend/documents/` — template registry; template files under
  `backend/documents/templates/` are token-marked copies of the reference files. A new
  official format = swap the file, adjust token coordinates in the registry entry, no
  engine change.
- Date renderers (`ordinal_apostrophe`, `ddmmyyyy`, `dtd_ddmmyy`, `mmddyy`) live beside
  the numbering module; documents pick theirs per §4.
