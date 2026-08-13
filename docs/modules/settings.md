# Settings

**Status: ❌ not started as a module** (tile + placeholder exist). The pieces it
will consolidate already exist and work — scattered.

## Purpose
One friendly place for configuration that today lives in three JSON files and
two in-app screens.

## Where config lives today
- Payroll policy: `salary-system/new_system/config/rules.json` — edited as raw
  JSON in the payroll SPA's Rules tab (admin).
- Two-machine sync folder: `config/sync.json` (hand-edited).
- Self-update source: `config/update.json` (hand-edited).
- Inventory dropdown lists: managed in the inventory page's Lists tab.
- Company name / currency / departments: inside `rules.json`.

## Scope (decided 2026-08-13)
- Friendly forms for the above (replace raw-JSON editing).
- **Order-number format** (template like `ORD-{FY}-{SEQ}`, used by Orders).
- **Units:** comprehensive searchable unit list (length/weight/count…), fully
  user-manageable — used by Parts & Pricing and Orders.
- Departments list management; operation list + rates for costing
  (OPEN_QUESTIONS #1).

## What's left (everything as a module)
- [ ] `backend/modules/settings/` + UI page behind the tile.
- [ ] Units table + seed of common units; searchable dropdown component.
- [ ] Order-number format setting (validated template).
- [ ] Move rules editing here from the payroll SPA (form-based, validated).
