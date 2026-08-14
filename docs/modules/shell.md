# App shell (login · launcher · Users & Access)

**Status: ✅ built** (2026-08-13)

## Purpose
One front door: every user signs in once, lands on Home, and sees one tile per
module their account is granted. The owner manages accounts and grants without
touching code.

## User flows
- Sign in → Home → click a tile → module page at `/<module>/` (same window;
  the ⌂ Home button top-left of every page returns). Unbuilt modules open a placeholder page.
- Operator edition (kiosk laptop): auto sign-in, straight to `/payroll/` —
  no launcher ceremony.
- Admin → Users & Access: add account (username, password, admin flag or module
  checkboxes), edit grants, reset password, delete. Last-admin and self-delete
  are refused.
- Update popup appears on Home when a newer GitHub Release exists.
- **📖 User Guide** tile opens `/help/` — the illustrated manual, generated from
  `docs/USER_GUIDE.md`. Not a module: no registry entry and no grant. It is
  **scoped to the account**: every chapter declares a grant key in the guide
  itself (`<!-- access: inventory -->`), the page reads `/api/modules` on load
  and hides the chapters that account cannot open, with a note saying so.
  `general` chapters always show, `admin` ones only to admins, and signing out
  leaves general-only — logging out is not a way to see everything. Note this
  is PRESENTATIONAL: the HTML holds every chapter, so it declutters rather than
  enforces. The real boundary is the per-route grant check on the data itself.
- **Deadlines panel** under the tiles: orders whose delivery date is close, as
  two lists — *next 7 days* and *next month* — with anything already overdue
  called out above them. Each line is customer · order number · quantity still
  to send. Fed by `GET /api/orders/deadlines`, fetched **fail-closed**
  (`shell.js loadDueSoon()` swallows the error), so an account without the
  `orders` grant simply sees no panel instead of an error. Orders that are fully
  shipped drop out — nothing left to send is not a deadline. The panel is hidden
  entirely when all three buckets are empty.

## Implemented (file paths)
- UI: `salary-system/new_system/frontend/index.html` + `frontend/shell/shell.js`.
- Tiles: `backend/core/registry.py` (single source of truth) served by
  `GET /api/modules` in `backend/modules/users.py`.
- Accounts: `backend/modules/users.py` (`/api/users` CRUD, guards).
- Enforcement: `backend/core/deps.py` `require_module(key)` — admins pass all;
  Operator edition locked to `salary`. Inventory routes use it; payroll admin
  routes still use `require_admin` (financial = admin-only by design).
- Session/auth routes stay in `backend/main.py` (login, kiosk, logout, me, meta).
- Deadlines: `orders.deadlines()` + `GET /api/orders/deadlines`
  (`backend/modules/orders.py`), rendered in `frontend/index.html`.

## Data model
`app_user(id, username, password_hash, role 'admin'|'operator', grants JSON)` —
grants ignored for admins. Seeded accounts: `admin` (all), `operator`
(`["salary"]`) — `backend/modules/employees/seed.py`.

## Screens
guide-images: shell-login, shell-home (now includes the deadlines panel),
shell-home-restricted, shell-users, shell-user-form, shell-placeholder.

## Known bugs
None known.

## What's left
- [ ] Self-service password change (only admin resets today) — ROADMAP Next.
- [ ] Audit trail of account/grant changes — ROADMAP Next.
- [ ] Tiles for new modules as they ship (one registry entry each).
      All nine currently in `registry.py` are built.
