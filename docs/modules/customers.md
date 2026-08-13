# Customers

**Status: ❌ not started** (tile + placeholder exist; scoped 2026-08-13)

## Purpose
Small customer master that Orders, Parts & Pricing and consignments reference.

## Scope (decided)
Name, GSTIN, billing/shipping addresses, contact persons (name/phone/email),
payment terms, notes, active flag. Thin on purpose — it unblocks the other
modules.

## Data model (planned)
`customer(id, name, gstin, address_billing, address_shipping, payment_terms,
notes, active)` · `customer_contact(customer_id, name, phone, email, role)`.

## What's left (everything)
- [ ] Schema + module (`backend/modules/customers/`), routes, UI page
      (list + form; searchable dropdown for other modules to embed).
