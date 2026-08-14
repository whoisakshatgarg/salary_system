# Roadmap

Everything left to build, grouped by priority. Each item also appears in its
module doc's "What's left". Update both when shipping.

**All seven module tiles are built** (2026-08-14). What remains is hardening
and convenience.

## Now (next sessions)

- **Audit trail** — append-only log (who published payroll, issued stock,
  changed a rate/stage); seeds exist (`sync_log`, `pay.published_at`,
  `order_stage_log`).
- **Self-service password change** (only admin resets today).
- **Fix the payroll page's pre-existing console errors** (convert null-model `x-show`
  bindings to `template x-if` — every other page is already clean).

## Next

- Invoice payment tracking (part-payments, outstanding) → customer receivables.
- Company address/GSTIN fields in Settings for the printed documents.
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
- Material-cost link from Inventory heat rates into the Parts costing builder.
