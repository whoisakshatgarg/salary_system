# Roadmap

Everything left to build, grouped by priority. Each item also appears in its
module doc's "What's left". Update both when shipping.

## Now (next sessions)

- **Customers module** — small master (name, GSTIN, addresses, contacts, payment
  terms); blocks Orders and Parts & Pricing. → [modules/customers.md](modules/customers.md)
- **Parts & Pricing (drawing master)** — drawing ID + revision, customer link,
  material spec, drawing file attachments; rate history per drawing+customer with
  dated entries; per-operation costing rolling up to ₹/piece (owner wants the
  extensive model). → [modules/parts-pricing.md](modules/parts-pricing.md)

## Next

- **Order Tracking** — 7 skippable stages (Enquiry→Quote→PO→Production→QC→
  Dispatch→Payment); items by drawing ID; link inventory heat issues (turn the
  free-text order ID into a real reference); consignment entity with GST fields
  (transporter, LR, e-way, invoice, delivery confirmation; partial + multi-order).
  Auto order numbers per FY with configurable format. → [modules/orders.md](modules/orders.md)
- **Settings module** — company profile, order-number format, searchable units
  list, departments, dropdown admin; absorb the raw JSON rules editor from the
  payroll SPA into a friendlier form. → [modules/settings.md](modules/settings.md)
- **Audit trail** — append-only log (who published payroll, issued stock, changed
  a rate); seeds exist (`sync_log`, `pay.published_at`).
- **Self-service password change** (accounts exist now; only admin resets today).
- **Fix payroll.html pre-existing console errors** (convert null-model `x-show`
  bindings to `template x-if`).

## Later

- Move attendance ENTRY from Salary into Employee Management (needs the
  operator-kiosk flow + two-machine sync redesigned around it).

- Cross-module dashboard/reports (WIP orders, stock value, salary outflow,
  receivables) — needs Orders/Parts first.
- Printable PDF salary slips; bulk CSV attendance import.
- Byte-match the two Excel exports against a real sample sheet (waiting on a
  sample file — OPEN_QUESTIONS #3).
- Per-employee `bonus_eligible` flag (retire hardcoded name exclusions in
  `config/rules.json`).
- LAN multi-user mode if a third machine appears.
