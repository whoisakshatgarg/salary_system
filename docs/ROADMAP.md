# Roadmap

Everything left to build, grouped by priority. Each item also appears in its
module doc's "What's left". Update both when shipping.

**Every module tile is built** — Salary, Inventory, Employee Management, Order
Tracking, Parts & Pricing, Quotations & Invoices, Customers and Settings, plus
the admin-only Users & Access (2026-08-14). What remains is hardening and
convenience.

## Now (next sessions)

- **Audit trail** — append-only log (who published payroll, issued stock,
  changed a rate/stage); seeds exist (`sync_log`, `pay.published_at`,
  `order_stage_log`).
- **Self-service password change** (only admin resets today).

## Next

- Invoice payment tracking (part-payments, outstanding) → customer receivables.
- Company address/GSTIN fields in Settings for the printed documents — the print
  template already reads `company_address` / `company_gstin`, but nothing writes
  them, so a TAX INVOICE currently goes out with no supplier address.
- Cross-module dashboard/reports (orders by stage, stock value, salary
  outflow, receivables) — the data all exists now.
- Friendlier payroll-rules editor inside Settings (raw JSON today).
- Printable PDF salary slips; bulk CSV attendance import.
- Order/consignment printouts (delivery challan layout).

## Later

- Move attendance ENTRY from Salary into Employee Management (needs the
  operator-kiosk flow + two-machine sync redesigned around it).
- Byte-match the two Excel exports against a real sample sheet
  (OPEN_QUESTIONS #3).
- Per-employee `bonus_eligible` flag (retire hardcoded name exclusions in
  `config/rules.json`).
- LAN multi-user mode if a third machine appears.
- A true vector (SVG) of the corrected logo from the designer — the repaired
  raster is clean at app sizes but won't scale to print/signage.

## Known, not yet fixed

From the 2026-08-14 QA sweep — full list in [QA-FINDINGS.md](QA-FINDINGS.md).
The ones worth picking up first:

- **Double-click creates duplicates** on Save order, Create consignment and Save
  costing — no in-flight guard on the submit buttons. Two FY order numbers get
  burned; a double-clicked consignment doubles the dispatched quantity.
- **Printed quantities go through `%g`** in the document template, so 1200000
  prints as `1.2e+06` and 12345.67 as `12345.7` while the amount column uses the
  true figure — the document contradicts itself.
- **An invoice can carry another customer's order number**: pick an order, then
  change the customer, and `_check_refs` only verifies the order exists.
- **Payroll accepts impossible paid-day counts** (999 days, or negative) and will
  offer the result for publishing with no warning.
- **The EM edit form shows "Overtime-eligible" unchecked for an OT person**
  (x-model treats the API's integer 1 as a value, not a checked state) — saving
  without touching it would silently downgrade them. Found during the
  2026-08-15 restyle, detailed in QA-FINDINGS.
- **Settings "Add" on an existing operation name silently overwrites its rate**
  (`INSERT … ON CONFLICT DO UPDATE`), repricing every costing built afterwards.

