# Outsourcing

**Status: ✅ built** (2026-08-21)

## Purpose
The work that leaves the shop — plating, heat treatment, a bought-out part —
and the stock that comes back. Four nouns in the order the office meets them
(SOP-DESIGN §9): a **vendor** does it, an **os_order** goes out with a
deadline, an **os_receipt** books in what returned, and an **os_item** is the
bought-out stock that results.

Outsourced stock is deliberately its **own world** rather than a heat: it has no
heat number and no chemistry, so filing it under the raw-material register would
be a table of empty columns. It surfaces in `/api/material/search` flagged
`source:"outsourced"` so a costing or a quotation can still pick it.

## Two rules worth knowing before reading the code
- **Status is derived, not typed.** `open → partial → received` follows the
  quantities received (`_recompute_status`); only `closed`/`cancelled`
  (`MANUAL_STATUSES`) are set by hand, and once set a recount never overwrites
  them — a decision is not a derivation.
- **`os_item.qty` is STORED and `os_movement.qty` is the SIGNED change to it**
  (+ receive, − issue, ± adjust), so the movements always sum to the stock on
  hand. Heats do it the other way round — their stock is derived — because a
  heat can only ever be consumed, never adjusted.

## User flows
- **Outgoing** — the deadlines strip (`deadlines()`: late / this week / this
  month off `os_order.deadline`, mirroring the homepage panel but for vendors),
  then the job list filtered by status and searchable by OS no / vendor /
  purpose / part. **+ New outgoing order**: vendor, purpose (a datalist off
  `PURPOSES`, never a constraint), date sent, deadline, and lines
  (description, part code, qty, unit, the vendor's `unit_cost`). Linking a line
  to an `order_item_id` is how **one internal order is split across vendors**.
- **Record receipt** on the job — date, inspection notes, an `accepted` flag,
  and a quantity per line with **Into stock**: a new OS ID or top up an
  existing one. The rate follows the goods into stock on a new item and is
  ignored when topping up (an old shelf is not repriced by a new delivery).
  Over-receiving a line is refused.
- **Reopen** — a Close or a Cancel taken back; see below.
- **Stock** — one row per OS ID with what is on hand, its cost, its vendor and
  the job it came from. **Issue** (order number required, like a heat),
  **Adjust** (signed, reason required) and **Edit**; every movement is listed
  with its date, sign and remark.
- **Vendors** — code `V01`+, contacts, services; deactivate, never delete.
- **Documents** — vendor paperwork, **uploaded and never generated** (owner
  brief: ask before designing an outgoing vendor document).

## Reopen semantics
`reopen_os_order` is the only way out of a terminal state, and what the job
becomes is not chosen either:

1. Refuse unless the status is `closed`/`cancelled` — "already following the
   quantities received".
2. Step out of the terminal state **first** (`status='open'`), because
   `_recompute_status` refuses to overwrite a decision and would otherwise
   leave it exactly as it was.
3. Re-derive, landing precisely where a receipt would have left it.

All of it inside one `BEGIN IMMEDIATE`, so the check and the re-derivation
cannot race.

## Deleting a receipt
`delete_receipt` un-says a delivery: the stock it created goes back out, and a
reversing `adjust` movement is **logged rather than erased**, dated to the
receipt's own date — the delivery is being unsaid, so every balance from that
day on must read as if it never arrived, and the pair sits together in the log.
Refused when the goods have already gone out, which would leave the shelf
negative. The job's status is re-derived afterwards.

The guard can only ask *"does the shelf hold enough?"*, never *"are THESE
pieces still here"*, so undoing an early receipt can succeed against stock a
later one brought in. That is the documented simplification: **stock is a
running quantity, not a set of lots** — per-receipt lot tracking is out of
scope, ratified by the owner.

## Routes
`/api/outsourcing`, all under `require_module("outsourcing")`. Every data
function takes an open connection and raises `ValueError` for a user mistake;
`_400` turns those into HTTP 400.

| Method | Path | Purpose |
|---|---|---|
| GET | `/refs` | vendors, purposes, units, status labels — form reference data |
| GET | `/refs/order-items/{order_id}` | an internal order's items, for partial outsourcing |
| GET | `/deadlines` | late / this week / this month, off `os_order.deadline` |
| GET · POST | `/vendors` | list (`q`, `active`) · create |
| GET · PUT | `/vendors/{id}` | record (jobs, stock, documents) · update |
| POST | `/vendors/{id}/active` | deactivate / reactivate |
| GET · POST | `/orders` | list (`q`, `status`, `vendor_id`) · create |
| GET · PUT | `/orders/{id}` | record · update |
| POST | `/orders/{id}/status` | set `closed`/`cancelled` by hand |
| POST | `/orders/{id}/reopen` | take a terminal state back and re-derive |
| GET · POST | `/receipts` | list (`q`, `os_order_id`) · create |
| GET · DELETE | `/receipts/{id}` | one receipt · undo it |
| GET | `/stock` | os_items (`q`, `active`, `vendor_id`) |
| GET · PUT | `/stock/{id}` | one item · edit its description etc. |
| GET | `/stock/{id}/movements` | the signed ledger |
| POST | `/stock/{id}/adjust` | signed correction, reason required |
| POST | `/stock/{id}/issue` | issue to an order number |
| GET · POST | `/documents` | list (by vendor and/or job) · upload |
| GET · DELETE | `/documents/{id}` | view/download (`?download=`) · delete |

The upload handler is deliberately **sync** like every route here: the sqlite
connection from `get_db` lives in the threadpool, so the handler must run there
too.

## Numbering
All three counters come from `backend/core/numbering.py`, consumed inside the
same `BEGIN IMMEDIATE` that writes the row that owns them:

| fn | scope | yields |
|---|---|---|
| `os_po_no(conn, d)` | `os_po:{fy}` | `AT/OS/26-27/001` |
| `os_item_id(conn)` | `os_item` | `OS-0001` — global, isolated, swappable |
| `vendor_code(conn)` | `vendor` | `V01`, typed codes honoured |

## Picker and BOM integration
- `/api/material/search` (`inventory.material_search`) searches outsourced stock
  alongside the rack: `_outsourced_matches()` returns rows flagged
  `source:"outsourced"` with `source_os_no`, against heats' `source:"heat"`. See
  [inventory.md](inventory.md).
- `costing_material.os_item_id` and `document_line.os_item_id` (both nullable,
  additive) let a costing or a quotation line point at a bought-out part
  instead of a heat.
- The BOM builder (`documents/payloads.build_bom` →
  `_outsourced_materials()`) prints the OS ID in **Heat No. / OS ID** and
  `Outsourced - V01` in **Source**, against `In-House` for a heat — see
  [papers.md](papers.md).

## Files on disk
`data/outsourcing_files/` via `core/paths.outsourcing_files_dir()`, metadata in
`os_document`, the standard uploads pattern (`core/attachments` for validation,
mime and the `Content-Disposition` filename). Included in the backup zip
alongside `inventory_files/`, `employee_files/`, `drawing_files/` and
`order_files/` (`backend/main.py` `_write_backup_zip`).

## Data model
```
vendor(code UNIQUE 'V01', name UNIQUE, contact_name, phone, email, address,
       services, notes, active, created_at)
os_order(os_no UNIQUE 'AT/OS/26-27/001', vendor_id, order_id?, purpose,
         date_sent, deadline, status, notes, created_at)
os_order_item(os_order_id CASCADE, description, part_code, qty, unit,
              unit_cost, order_item_id?)
os_receipt(os_order_id CASCADE, receipt_date, inspection_notes, accepted)
os_receipt_line(os_receipt_id CASCADE, os_order_item_id?, qty, os_item_id?)
os_item(os_id UNIQUE 'OS-0001', description, part_code, material, size_section,
        unit, qty, unit_cost, vendor_id?, os_order_id?, received_date, notes,
        active)
os_movement(os_item_id CASCADE, mv_date, type receive|issue|adjust,
            qty SIGNED, order_id TEXT, remarks)
os_document(vendor_id?, os_order_id?, label, filename, mime, size_bytes,
            stored_name UNIQUE, uploaded_at)
```
Indexed on `os_order(vendor_id)` and `os_order(deadline)`. `os_movement.order_id`
is the order **number** as text, exactly as in `heat_movement` — and carries the
same known weakness (see [inventory.md](inventory.md)).

`os_receipt_line.os_item_id` records which stock row a line became, which is
what makes `delete_receipt` able to take it back out again.

## Implemented (file paths)
`backend/modules/outsourcing.py` (data + `/api/outsourcing/*`, grant
`outsourcing`) · DDL in `backend/core/db.py` · UI
`frontend/outsourcing/index.html` + `outsourcing.js` at `/outsourcing/` · spec
`tests/test_outsourcing.py` (86 tests).

## Screens
guide-images: os-outgoing. User-facing:
[USER_GUIDE.md](../USER_GUIDE.md) section 11.

## What's left
- [ ] Per-receipt lot tracking (see *Deleting a receipt*) — deliberately out of
      scope, but it is what would make an undo exact rather than sufficient.
- [ ] `os_movement.order_id` is free text, so a typo silently breaks the link
      between an issue and its order — the same fix as `heat_movement`.
- [ ] No outgoing vendor document is generated (owner brief). If one is ever
      wanted, it belongs in `backend/documents/` as a ninth kind, not here.
- [ ] Vendor rate history: `unit_cost` lives on the job line and the stock row,
      so "what did this plater charge last time" means reading past jobs.
