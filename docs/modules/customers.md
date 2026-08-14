# Customers

**Status: ✅ built** (2026-08-14)

## Purpose
The thin master Orders, Parts & Pricing and consignments reference: name,
GSTIN, addresses, contact persons, payment terms.

## User flows
List with search + active filter → record with contacts (add/delete), edit,
deactivate/reactivate. Delete works only while the customer has no orders or
drawings (otherwise deactivate — history stays).

## Implemented (file paths)
`backend/modules/customers.py` (data + `/api/customers/*` routes, grant
`customers`) · UI `frontend/customers/index.html` + `customers.js` · spec
`tests/test_workshop.py` (CustomersSpec).

## Data model
`customer(id, name UNIQUE, gstin, address_billing, address_shipping,
payment_terms, notes, active)` · `customer_contact(customer_id, name, phone,
email, role)` (cascade).

## Screens
guide-images: ws-customer.

## What's left
- [ ] Nothing pending. (Receivables view belongs to Dashboard — ROADMAP Next.)
