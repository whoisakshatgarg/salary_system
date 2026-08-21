"""Rebuild every template from its reference document.

    cd new_system
    ../venv/bin/python -m backend.documents.templates.build.build_all
"""

from __future__ import annotations

from . import (build_ack, build_bom, build_coc, build_invoice,
               build_packing_list, build_quotation, build_test_cert,
               build_work_order)

BUILDERS = [
    ("quotation", build_quotation.build),
    ("ack", build_ack.build),
    ("work_order", build_work_order.build),
    ("test_cert", build_test_cert.build),
    ("bom", build_bom.build),
    ("packing_list", build_packing_list.build),
    ("invoice", build_invoice.build),           # derives from packing_list
    ("coc", build_coc.build),
]


def build_all():
    made = []
    for kind, fn in BUILDERS:
        made.append((kind, fn()))
    return made


if __name__ == "__main__":
    for kind, path in build_all():
        print(f"{kind:14s} {path}")
