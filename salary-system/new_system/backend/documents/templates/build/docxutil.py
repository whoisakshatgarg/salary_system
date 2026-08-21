"""Low-level .docx surgery used by the template build scripts.

Tokens are always authored as **a single run** so the engine never has to
stitch a token back together, and only the *value* text is swapped - labels,
tabs and the surrounding whitespace of the reference stay byte-identical.
Runs left empty by a swap are kept (``format_spec`` ignores empty runs), so
the reference's run/formatting structure survives intact.
"""

from __future__ import annotations

import copy

from docx.oxml.ns import qn


def paragraphs(el):
    """Direct ``<w:p>`` children of a body / table-cell element."""
    return el.findall(qn("w:p"))


def cells(tr):
    return tr.findall(qn("w:tc"))


def rows(tbl):
    return tbl.findall(qn("w:tr"))


def para_text(p):
    return "".join(t.text or "" for t in p.iter(qn("w:t")))


def runs(p):
    return p.findall(qn("w:r"))


def set_run_text(r, text):
    ts = r.findall(qn("w:t"))
    if not ts:
        t = r.makeelement(qn("w:t"), {})
        r.append(t)
        ts = [t]
    ts[0].text = text
    ts[0].set(qn("xml:space"), "preserve")
    for extra in ts[1:]:
        r.remove(extra)


def replace_text(p, old, new, required=True):
    """Swap ``old`` for ``new`` inside one paragraph, across run boundaries.

    ``new`` lands in the first ``<w:t>`` the match touches (so it inherits
    that run's formatting); the rest of the match is cut from the following
    ``<w:t>`` elements.
    """
    text = para_text(p)
    start = text.find(old)
    if start < 0:
        if required:
            raise AssertionError(f"{old!r} not found in {text!r}")
        return False
    end = start + len(old)

    pos, placed = 0, False
    for t in list(p.iter(qn("w:t"))):
        s = t.text or ""
        lo, hi = pos, pos + len(s)
        pos = hi
        if hi <= start or lo >= end:
            continue
        head = s[:max(0, start - lo)]
        tail = s[end - lo:] if end <= hi else ""
        mid = "" if placed else new
        placed = True
        t.text = head + mid + tail
        t.set(qn("xml:space"), "preserve")
    return True


def set_para_text(p, text, donor=None):
    """Replace a paragraph's whole text, keeping the first run's formatting.

    Empty paragraphs in the references carry no run at all; ``donor`` supplies
    the run formatting to clone for those (typically a sibling *value*
    paragraph, so the filled cell matches its neighbours).
    """
    rs = runs(p)
    if not rs:
        src = runs(donor)[0] if donor is not None and runs(donor) else None
        r = copy.deepcopy(src) if src is not None else p.makeelement(qn("w:r"), {})
        for t in r.findall(qn("w:t")):
            r.remove(t)
        p.append(r)
        rs = [r]
    set_run_text(rs[0], text)
    for extra in rs[1:]:
        p.remove(extra)


def tokenize_para(p, token, old=None, donor=None):
    """Turn a paragraph (or just its value substring) into ``{{token}}``."""
    marker = "{{%s}}" % token
    if old is None:
        set_para_text(p, marker, donor=donor)
    else:
        replace_text(p, old, marker)
    return marker


def clone(el):
    return copy.deepcopy(el)


def delete_row(tr):
    tr.getparent().remove(tr)


def set_vmerge(tc, mode):
    tcPr = tc.get_or_add_tcPr()
    tcPr._remove_vMerge()
    if mode is None:
        return
    v = tcPr.get_or_add_vMerge()
    if mode == "restart":
        v.set(qn("w:val"), "restart")
    elif v.get(qn("w:val")) is not None:
        del v.attrib[qn("w:val")]
