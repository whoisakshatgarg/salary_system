"""Shared helpers for the template build scripts.

Every template under ``backend/documents/templates/`` is generated
**programmatically** from its reference document by the sibling ``build_*.py``
scripts, so a swapped reference can simply be re-tokenized:

    ../../../../venv/bin/python -m backend.documents.templates.build.build_all

(run from ``new_system/``; see ``templates/README.md``).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE.parent
MEDIA_DIR = TEMPLATE_DIR / "media"
NEW_SYSTEM = TEMPLATE_DIR.parents[2]              # .../new_system
REPO = NEW_SYSTEM.parents[1]                      # .../salary_system
SAMPLES = NEW_SYSTEM.parent / "sample docs"       # .../salary-system/sample docs
REFERENCE_SPECS = REPO / "reference_specs"


def sample(name: str) -> Path:
    p = SAMPLES / name
    if not p.exists():
        raise SystemExit(f"reference document not found: {p}")
    return p


def out(name: str) -> Path:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    return TEMPLATE_DIR / name


def extract_media(xlsx_path: Path, mapping: dict) -> None:
    """Copy ``xl/media/*`` parts out of a reference workbook into templates/media.

    ``mapping`` is ``{"image1.png": "test_cert_logo.png", ...}``.  openpyxl
    silently drops drawing-container pictures on save (see engine
    ``_ensure_images``), so the bytes are lifted out of the zip once, here.
    """
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(xlsx_path) as z:
        for src, dst in mapping.items():
            (MEDIA_DIR / dst).write_bytes(z.read(f"xl/media/{src}"))


def set_print_setup(ws, orientation="portrait", paper="A4",
                    fit_to_width=1, fit_to_height=0):
    from openpyxl.worksheet.properties import PageSetupProperties
    ws.page_setup.orientation = orientation
    ws.page_setup.paperSize = ws.PAPERSIZE_A4 if paper == "A4" else ws.page_setup.paperSize
    ws.page_setup.fitToWidth = fit_to_width
    ws.page_setup.fitToHeight = fit_to_height
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)


def tokenise(ws, mapping: dict) -> None:
    """Write ``{{token}}`` markers into cells, leaving every style untouched."""
    for coord, token in mapping.items():
        ws[coord] = "{{%s}}" % token


def fill_slots(ws, first_row: int, last_row: int, columns: dict,
               prefix: str = "item") -> None:
    """Write per-item tokens into every row of a contiguous item region."""
    for row in range(first_row, last_row + 1):
        for col, field in columns.items():
            ws[f"{col}{row}"] = "{{%s.%s}}" % (prefix, field)
