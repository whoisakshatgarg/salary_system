"""The module registry — single source of truth for the launcher tiles.

Each built module is one folder under ``frontend/`` holding its own
``index.html`` + JS, so ``path`` is the folder URL (``/orders/``). A module has
exactly ONE entry point — the tile here — so no page links sideways into
another module; every page's only cross-module link is Home.

Every module the product knows about, built or not. The shell renders one tile
per module the signed-in account is granted (admins implicitly hold every
grant); unbuilt modules open a placeholder page. Adding a future module =
one entry here + its backend/modules/<key>/ package + frontend page.
"""

from __future__ import annotations

MODULES = [
    {"key": "salary", "label": "Salary & Attendance", "icon": "₹",
     "desc": "Attendance entry, payroll calculation, advances and salary slips",
     "path": "/payroll/", "built": True},
    {"key": "inventory", "label": "Raw Material Inventory", "icon": "⛭",
     "desc": "Heat register: rod stock, usage log, mill certificates",
     "path": "/inventory/", "built": True},
    {"key": "employees", "label": "Employee Management", "icon": "👥",
     "desc": "Profiles, documents, leave bank and employee records",
     "path": "/employees/", "built": True},
    {"key": "orders", "label": "Order Tracking", "icon": "🗂",
     "desc": "Enquiry → quote → PO → production → QC → dispatch → payment",
     "path": "/orders/", "built": True},
    {"key": "parts", "label": "Parts & Pricing", "icon": "📐",
     "desc": "Drawing master: revisions, rate history, per-operation costing",
     "path": "/parts/", "built": True},
    {"key": "quotations", "label": "Quotations & Invoices", "icon": "🧾",
     "desc": "Quotations, invoices and printable copies for any customer",
     "path": "/quotations/", "built": True},
    {"key": "customers", "label": "Customers", "icon": "🏢",
     "desc": "Customer master, codes, order history and business growth",
     "path": "/customers/", "built": True},
    {"key": "settings", "label": "Settings", "icon": "⚙",
     "desc": "Order-number format, units, operation rates, departments",
     "path": "/settings/", "built": True},
]

ALL_KEYS = [m["key"] for m in MODULES]
