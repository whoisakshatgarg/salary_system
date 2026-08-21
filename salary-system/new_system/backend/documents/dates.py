"""Per-document date renderers (CONVENTIONS.md §4).

Wave 1 is adding ``backend/core/numbering.py`` with exactly these four
renderers.  This module prefers that implementation when it exists and only
falls back to the local copy below, so there is never a second source of
truth once wave 1 lands.

    ordinal_apostrophe   04th Aug' 2026   quotation, ack, COC
    ddmmyyyy             14/08/2026       invoice header date
    dtd_ddmmyy           Dtd. 06.03.26    buyer PO dates on invoice / packing list
    us_mmddyy            05-10-24         test certificate (Excel mm-dd-yy)

``date_value`` is the fifth "renderer" used by the engine for cells whose
template number format already prints the date (work order ``dd/mm/yyyy``,
test certificate ``mm-dd-yy``): it hands Excel a real ``datetime.date``.
"""

from __future__ import annotations

import datetime as _dt

__all__ = [
    "parse_iso", "date_value", "ordinal_apostrophe", "ddmmyyyy",
    "dtd_ddmmyy", "us_mmddyy", "RENDERERS", "LOCAL_FALLBACK",
]

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_iso(value):
    """ISO string / date / datetime -> ``datetime.date`` (``None`` passes through)."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value)[:10])


def _suffix(day: int) -> str:
    if 11 <= day <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _local_ordinal_apostrophe(value) -> str:
    """``04th Aug' 2026`` - two-digit day + ordinal, straight apostrophe."""
    d = parse_iso(value)
    if d is None:
        return ""
    return f"{d.day:02d}{_suffix(d.day)} {_MONTHS[d.month - 1]}' {d.year}"


def _local_ddmmyyyy(value) -> str:
    d = parse_iso(value)
    return "" if d is None else f"{d.day:02d}/{d.month:02d}/{d.year}"


def _local_dtd_ddmmyy(value) -> str:
    d = parse_iso(value)
    return "" if d is None else f"Dtd. {d.day:02d}.{d.month:02d}.{d.year % 100:02d}"


def _local_us_mmddyy(value) -> str:
    d = parse_iso(value)
    return "" if d is None else f"{d.month:02d}-{d.day:02d}-{d.year % 100:02d}"


def _blank_safe(fn):
    """A paper may legitimately carry an empty date field; render it blank."""
    def wrapped(value):
        if value is None or value == "":
            return ""
        return fn(value)
    wrapped.__name__ = getattr(fn, "__name__", "renderer")
    wrapped.__doc__ = fn.__doc__
    return wrapped


# --- prefer wave 1's canonical implementation when it exists -----------------
LOCAL_FALLBACK = True
try:
    from backend.core import numbering as _numbering      # type: ignore
    ordinal_apostrophe = _blank_safe(_numbering.ordinal_apostrophe)
    ddmmyyyy = _blank_safe(_numbering.ddmmyyyy)
    dtd_ddmmyy = _blank_safe(_numbering.dtd_ddmmyy)
    us_mmddyy = _blank_safe(_numbering.us_mmddyy)
    LOCAL_FALLBACK = False
except Exception:                                         # ImportError or missing names
    ordinal_apostrophe = _local_ordinal_apostrophe
    ddmmyyyy = _local_ddmmyyyy
    dtd_ddmmyy = _local_dtd_ddmmyy
    us_mmddyy = _local_us_mmddyy


def date_value(value):
    """Real ``datetime.date`` for cells whose number format prints the date."""
    return parse_iso(value)


RENDERERS = {
    "ordinal_apostrophe": ordinal_apostrophe,
    "ddmmyyyy": ddmmyyyy,
    "dtd_ddmmyy": dtd_ddmmyy,
    "us_mmddyy": us_mmddyy,
    "date_value": date_value,
}
