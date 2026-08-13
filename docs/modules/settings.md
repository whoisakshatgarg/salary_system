# Settings

**Status: ✅ built** (2026-08-14)

## Purpose
Owner-editable configuration: order-number format, the units list, machining
operations with ₹/hour rates (feeds the Parts costing builder), departments.

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
`frontend/settings.html` + `settings.js` · spec `tests/test_workshop.py`
(SettingsSpec). Departments read/write `config/rules.json` via `core/rules.py`.

## Data model
`app_setting(key, value JSON)` · `unit(name UNIQUE)` ·
`operation(name UNIQUE, rate_per_hour)` · `order_seq(fy, seq)` (bumped by
Orders).

## Screens
guide-images: ws-settings.

## What's left
- [ ] Absorb the payroll rules editor as friendly forms (ROADMAP Next).
- [ ] Sync/update config (`config/*.json`) surfaced here (hand-edited today).
