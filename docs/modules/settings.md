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
operations table with per-row rate save + add/remove; departments add/remove.
Reads need the `settings` grant; every change is admin-only.

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
Orders).

## Screens
guide-images: ws-settings.

## Known bugs
- **Adding an operation that already exists silently overwrites its rate**
  (`INSERT … ON CONFLICT(name) DO UPDATE`), with no warning and no toast —
  every costing built afterwards prices at the new rate. See
  [QA-FINDINGS.md](../QA-FINDINGS.md).
- Clearing an operation's rate box and saving stores 0/hour and reports success.

## What's left
- [ ] Absorb the payroll rules editor as friendly forms (ROADMAP Next).
- [ ] Sync/update config (`config/*.json`) surfaced here (hand-edited today).
