"""work_order.xlsx  <-  "Apex Work Order (1).xlsx".

Provenance: the shop-floor work order, a native .xlsx.  Only the variable
cells are tokenised; the grid is an *open box* (rows carry left/right rules
only) and no horizontal rules are added.  Its header logo is a normal
openpyxl-readable picture and round-trips untouched.
"""

from __future__ import annotations

from openpyxl import load_workbook

from . import common

SHEET = "Sheet1"

CELL_TOKENS = {
    "B6": "wo_no_short",
    "G6": "client_code",
    "B8": "cust_po",
    "G8": "wo_date",          # real date, template numfmt dd/mm/yyyy
}

ITEM_COLUMNS = {
    "A": "sno", "B": "part_no", "C": "item", "D": "qty",
    "E": "material", "F": "marking", "G": "remarks",
}
ITEMS_FIRST, ITEMS_LAST = 12, 38


def build():
    src = common.sample("Apex Work Order (1).xlsx")
    wb = load_workbook(src)
    ws = wb[SHEET]

    common.tokenise(ws, CELL_TOKENS)
    common.fill_slots(ws, ITEMS_FIRST, ITEMS_LAST, ITEM_COLUMNS)

    dst = common.out("work_order.xlsx")
    wb.save(dst)
    return dst


if __name__ == "__main__":
    print(build())
