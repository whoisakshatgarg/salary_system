"""``/api/papers`` — a thin HTTP skin over ``service.py``.

No logic lives here: every route is a validated call into the service, which
is where the transactions, the numbering and the rules are.  Two house rules
this file has to keep:

* **Literal routes come before ``/{paper_id}``** or ``/refs`` is read as a
  paper id.
* **Every field is declared on the Pydantic model.**  An undeclared field is
  silently dropped by FastAPI rather than rejected, which is how an edit
  quietly loses half a payload — hence ``PaperOptsIn`` naming each option
  instead of taking a free ``dict``.

NOT MOUNTED YET: ``main.py`` is owned by another wave and does not
``include_router`` this one; the wiring wave adds it alongside the four SOP
module keys (see the guard below).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..core.deps import get_db, require_module
from ..core.registry import ALL_KEYS
from . import service

# ---------------------------------------------------------------------------
# Module guard.  SOP-DESIGN §7/§10: the papers backend accepts ANY of the four
# new SOP grants — acks / production_docs / shipping_docs / quality_docs.
# Those keys are added to core/registry.MODULES by a LATER wave, and
# require_module validates its arguments at IMPORT time, so asking for them
# before they exist would make this module unimportable.  Until they land the
# guard falls back to 'orders' (every SOP tile hangs off an order anyway) and
# flips itself the moment the keys appear.
#
# TODO(wiring wave): once acks/production_docs/shipping_docs/quality_docs are
# registered, this resolves to the four keys on its own — delete the fallback.
# ---------------------------------------------------------------------------
SOP_KEYS = ("acks", "production_docs", "shipping_docs", "quality_docs")
GUARD_KEYS = tuple(k for k in SOP_KEYS if k in ALL_KEYS) or ("orders",)
GUARD_IS_FALLBACK = GUARD_KEYS == ("orders",)

router = APIRouter(prefix="/api/papers",
                   dependencies=[Depends(require_module(*GUARD_KEYS))])


class PaperOptsIn(BaseModel):
    """Every option any builder understands, named (see the silent-drop note).

    ``service.KIND_OPTS`` says which ones a kind needs; ``/refs`` publishes
    that so the form asks for the right things.
    """
    document_id: int | None = None          # quotation / invoice ledger row
    quotation_paper_id: int | None = None   # ack: the quotation it answers
    repeat_po: bool = False                 # ack: prints the literal 'Repeat PO'
    invoice_paper_id: int | None = None     # packing list / COC / test cert
    order_ids: list[int] = []               # invoice: several orders, one bill
    item_ids: list[int] = []                # invoice: hand-picked lines
    based_on_id: int | None = None          # attach-existing source paper


class PaperIn(BaseModel):
    kind: str
    order_id: int
    opts: PaperOptsIn = PaperOptsIn()


class PayloadIn(BaseModel):
    payload: dict[str, Any]


class StatusIn(BaseModel):
    status: str


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _opts(body: PaperOptsIn) -> dict:
    """Only the options actually given — a builder must not see order_ids=[]
    and think the caller meant "no orders"."""
    raw = body.model_dump()
    return {k: v for k, v in raw.items() if v not in (None, [], False)}


@router.get("/refs")
def refs():
    """Kind labels and the options each kind needs — this drives the UI."""
    return {
        "kinds": service.kind_refs(),
        "statuses": list(service.STATUSES),
        "transitions": {k: sorted(v) for k, v in service.TRANSITIONS.items()},
    }


@router.get("")
def papers(kind: str = "", order_id: int | None = None, q: str = "",
           status: str = "", conn=Depends(get_db)):
    return service.list_papers(conn, kind=kind, order_id=order_id, q=q,
                               status=status)


@router.post("")
def paper_create(body: PaperIn, conn=Depends(get_db)):
    return _400(service.create_paper, conn, body.kind, body.order_id,
                _opts(body.opts))


@router.get("/{paper_id}")
def paper_detail(paper_id: int, conn=Depends(get_db)):
    try:
        return service.get_paper(conn, paper_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{paper_id}")
def paper_update(paper_id: int, body: PayloadIn, conn=Depends(get_db)):
    return _400(service.update_payload, conn, paper_id, body.payload)


@router.post("/{paper_id}/refill")
def paper_refill(paper_id: int, conn=Depends(get_db)):
    """Re-read the order: an explicit re-sync, never automatic."""
    return _400(service.refill, conn, paper_id)


@router.post("/{paper_id}/status")
def paper_status(paper_id: int, body: StatusIn, conn=Depends(get_db)):
    return _400(service.set_status, conn, paper_id, body.status)


@router.post("/{paper_id}/revise")
def paper_revise(paper_id: int, conn=Depends(get_db)):
    """A fresh draft ' Rev-A'; the parent becomes superseded."""
    return _400(service.revise, conn, paper_id)


@router.get("/{paper_id}/file")
def paper_file(paper_id: int, download: bool = False, conn=Depends(get_db)):
    """The document itself, rendered on demand from payload + template."""
    data, filename, mime = _400(service.render_paper, conn, paper_id)
    disposition = "attachment" if download else "inline"
    safe = filename.replace('"', "")
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f'{disposition}; filename="{safe}"'})


@router.delete("/{paper_id}")
def paper_delete(paper_id: int, conn=Depends(get_db)):
    _400(service.delete_paper, conn, paper_id)
    return {"ok": True}


__all__ = ["router", "GUARD_KEYS", "GUARD_IS_FALLBACK", "SOP_KEYS"]
