"""Paperwork engine: token-marked copies of APEX THERMOCON's own documents.

    from backend.documents import render
    data, name = render("coc", payload)
"""

from .engine import TemplateError, DocxFiller, XlsxFiller, render   # noqa: F401
from .registry import ENTRIES, entry, filename, kinds, prepare      # noqa: F401

__all__ = ["render", "entry", "filename", "kinds", "prepare", "ENTRIES",
           "XlsxFiller", "DocxFiller", "TemplateError"]
