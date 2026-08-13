"""The module registry — single source of truth for the launcher tiles.

Every module the product knows about, built or not. The shell renders one tile
per module the signed-in account is granted (admins implicitly hold every
grant); unbuilt modules open a placeholder page. Adding a future module =
one entry here + its backend/modules/<key>/ package + frontend page.
"""

from __future__ import annotations

MODULES = [
    {"key": "salary", "label": "Salary & Attendance", "icon": "₹",
     "desc": "Attendance entry, payroll calculation, advances and salary slips",
     "path": "/payroll.html", "built": True},
    {"key": "inventory", "label": "Raw Material Inventory", "icon": "⛭",
     "desc": "Heat register: rod stock, usage log, mill certificates",
     "path": "/inventory.html?back=1", "built": True},
    {"key": "employees", "label": "Employee Management", "icon": "👥",
     "desc": "Profiles, documents, leave bank and employee records",
     "path": "/employees.html", "built": True},
    {"key": "orders", "label": "Order Tracking", "icon": "🗂",
     "desc": "Enquiry → quote → PO → production → QC → dispatch → payment",
     "path": None, "built": False},
    {"key": "parts", "label": "Parts & Pricing", "icon": "📐",
     "desc": "Drawing master with revisions and rate history per customer",
     "path": None, "built": False},
    {"key": "customers", "label": "Customers", "icon": "🏢",
     "desc": "Customer master: GSTIN, contacts, payment terms",
     "path": None, "built": False},
    {"key": "settings", "label": "Settings", "icon": "⚙",
     "desc": "Payroll rules, units, order-number format, sync & updates",
     "path": None, "built": False},
]

ALL_KEYS = [m["key"] for m in MODULES]
