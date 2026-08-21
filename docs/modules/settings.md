# Settings

**Status: ✅ built** (2026-08-14)

## Purpose
Owner-editable configuration: order-number format, the units list, machining
operations with ₹/hour rates (feeds the Parts costing builder), departments.
These are the shop's **default** rates: a customer with agreed prices
(`customer_operation_rate`, edited on the customer's Rates tab) overrides them
for that customer's drawings.

## User flows
Single page (⚙ tile): format editor with live "next number" preview
({FY}/{YYYY}/{SEQ} tokens, {SEQ} required); searchable units with add/remove;
operations table with per-row rate save + add/remove; departments add/remove;
the **Document numbering** card below. Reads need the `settings` grant; every
change is admin-only.

## Document numbering
Settings also owns the DOCUMENT counters — `doc_counter(scope, next_seq)`, the
one table `backend/core/numbering.py` reads and writes (CONVENTIONS §3). This
card is what makes the real-world serials settable without touching code.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/settings/numbering` | every live counter as `{scope, next_seq, label}`, ordered by scope; `settings` grant |
| PUT | `/api/settings/numbering` | `{scope, next_seq}` — upsert, so a scope that does not exist yet is created; `require_admin` |

- `numbering.label_for(scope)` turns a key into prose for the list —
  `qtn:T04` → *Quotation — T04*, `inv:26-27` → *Export invoice — 26-27*. Scope
  names are **keys, not prose**: the PUT validates the shape and rejects
  anything else as a typo rather than accepting it as a new counter.
- The upsert is what lets the office get **ahead** of a counter — a new export
  customer whose quotations must start at 40 — rather than only correcting one
  that already exists.
- `numbering.ensure_seeds()` plants the reference counts on startup with
  `INSERT OR IGNORE`, so a counter the office has already moved (or corrected
  here) is never clobbered: `qtn:T04`→317, `qtn:E01`→595, `wo:26`→253,
  `inv:26-27`→169.
- `numbering.peek(conn, scope)` reads the next serial WITHOUT consuming it —
  that is what preview endpoints use; `_take` is the only consumer and it joins
  the caller's transaction so a number commits with the row that owns it.

`order_seq` (order numbers) and `doc_seq` (legacy quotation/invoice numbers and
MRQ requisitions) are untouched by all this and stay where they were.

## Implemented (file paths)
`backend/modules/settings.py` (routes `/api/settings*`; first-run seeding of
~50 units + 21 operations + the default format; `fy_label`/`render_order_no`
used by Orders). Form reference data for other modules is served by
grant-gated endpoints in those modules (`/api/orders/refs`, `/api/parts/refs`)
— pricing never leaks past a module's own grant · UI
`frontend/settings/index.html` + `settings.js` · spec `tests/test_workshop.py`
(SettingsSpec). Departments read/write `config/rules.json` via `core/rules.py`.

## Data model
`app_setting(key, value JSON)` · `unit(name UNIQUE)` ·
`operation(name UNIQUE, rate_per_hour)` · `order_seq(fy, seq)` (bumped by
Orders) · `doc_counter(scope PRIMARY KEY, next_seq)` — read/written only through
`core/numbering`.

## Screens
guide-images: ws-settings, set-numbering.

## Known bugs
- **Adding an operation that already exists silently overwrites its rate**
  (`INSERT … ON CONFLICT(name) DO UPDATE`), with no warning and no toast —
  every costing built afterwards prices at the new rate. See
  [QA-FINDINGS.md](../QA-FINDINGS.md).
- Clearing an operation's rate box and saving stores 0/hour and reports success.

## What's left
- [ ] Absorb the payroll rules editor as friendly forms (ROADMAP Next).
- [ ] Sync/update config (`config/*.json`) surfaced here (hand-edited today).
