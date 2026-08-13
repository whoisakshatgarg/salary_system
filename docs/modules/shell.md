# App shell (login · launcher · Users & Access)

**Status: ✅ built** (2026-08-13)

## Purpose
One front door: every user signs in once, lands on Home, and sees one tile per
module their account is granted. The owner manages accounts and grants without
touching code.

## User flows
- Sign in → Home → click a tile → module page (same window; "⌂ All modules"
  returns). Unbuilt modules open a placeholder page.
- Operator edition (kiosk laptop): auto sign-in, straight to `/payroll.html` —
  no launcher ceremony.
- Admin → Users & Access: add account (username, password, admin flag or module
  checkboxes), edit grants, reset password, delete. Last-admin and self-delete
  are refused.
- Update popup appears on Home when a newer GitHub Release exists.

## Implemented (file paths)
- UI: `salary-system/new_system/frontend/index.html` + `frontend/shell.js`.
- Tiles: `backend/core/registry.py` (single source of truth) served by
  `GET /api/modules` in `backend/modules/users.py`.
- Accounts: `backend/modules/users.py` (`/api/users` CRUD, guards).
- Enforcement: `backend/core/deps.py` `require_module(key)` — admins pass all;
  Operator edition locked to `salary`. Inventory routes use it; payroll admin
  routes still use `require_admin` (financial = admin-only by design).
- Session/auth routes stay in `backend/main.py` (login, kiosk, logout, me, meta).

## Data model
`app_user(id, username, password_hash, role 'admin'|'operator', grants JSON)` —
grants ignored for admins. Seeded accounts: `admin` (all), `operator`
(`["salary"]`) — `backend/modules/employees/seed.py`.

## Screens
`docs/guide-images/`: shell-login, shell-home, shell-home-restricted,
shell-users, shell-user-form, shell-placeholder.

## Known bugs
None known. (Pre-existing console noise belongs to payroll.html, not the shell.)

## What's left
- [ ] Self-service password change (only admin resets today) — ROADMAP Next.
- [ ] Audit trail of account/grant changes — ROADMAP Next.
- [ ] Tiles for new modules as they ship (one registry entry each).
