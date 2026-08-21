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

# The order below IS the shop's SOP, tile by tile (SOP-DESIGN §7): the order
# comes in, it is quoted, the PO is acknowledged, production and quality raise
# their papers, it ships — then the standing masters. The four SOP paper tiles
# are separate module keys (separate grants, §10) that all open /papers/
# pre-filtered to their kinds, which is why their paths carry a querystring.
MODULES = [
    {"key": "orders", "label": "Order Tracking", "icon": "🗂",
     "desc": "Enquiry → quote → PO → production → QC → dispatch → payment",
     "path": "/orders/", "built": True},
    {"key": "quotations", "label": "Quotations & Invoices", "icon": "🧾",
     "desc": "Quotations, invoices and printable copies for any customer",
     "path": "/quotations/", "built": True},
    {"key": "acks", "label": "PO Acknowledgements", "icon": "✓",
     "desc": "Order confirmations: what we accepted, and when it ships",
     "path": "/papers/?kind=ack", "built": True},
    {"key": "production_docs", "label": "Production — WO & BOM", "icon": "⚒",
     "desc": "Work orders for the shop floor and their bills of materials",
     "path": "/papers/?kind=work_order,bom", "built": True},
    {"key": "shipping_docs", "label": "Shipping — Invoice & Packing", "icon": "✈",
     "desc": "Export invoices and the packing lists that travel with them",
     "path": "/papers/?kind=invoice,packing_list", "built": True},
    {"key": "quality_docs", "label": "Quality — COC & Test Certs", "icon": "✚",
     "desc": "Conformance certificates and heat-wise test certificates",
     "path": "/papers/?kind=coc,test_cert", "built": True},
    {"key": "outsourcing", "label": "Outsourcing", "icon": "🚚",
     "desc": "Vendor jobs, receipts, outsourced stock",
     "path": "/outsourcing/", "built": True},
    {"key": "inventory", "label": "Raw Material Inventory", "icon": "⛭",
     "desc": "Heat register: rod stock, usage log, mill certificates",
     "path": "/inventory/", "built": True},
    {"key": "parts", "label": "Parts & Pricing", "icon": "📐",
     "desc": "Drawing master: revisions, rate history, per-operation costing",
     "path": "/parts/", "built": True},
    {"key": "customers", "label": "Customers", "icon": "🏢",
     "desc": "Customer master, codes, order history and business growth",
     "path": "/customers/", "built": True},
    {"key": "employees", "label": "Employee Management", "icon": "👥",
     "desc": "Profiles, documents, leave bank and employee records",
     "path": "/employees/", "built": True},
    {"key": "salary", "label": "Salary & Attendance", "icon": "₹",
     "desc": "Attendance entry, payroll calculation, advances and salary slips",
     "path": "/payroll/", "built": True},
    {"key": "settings", "label": "Settings", "icon": "⚙",
     "desc": "Order-number format, units, operation rates, departments",
     "path": "/settings/", "built": True},
]

ALL_KEYS = [m["key"] for m in MODULES]
