"""Shared FastAPI dependencies (DB connection, auth, module grants) — used by
main.py and every feature router without import cycles."""

from __future__ import annotations

import json

from fastapi import Cookie, Depends, HTTPException

from . import auth, db, edition
from .registry import ALL_KEYS


def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def current_user(session: str | None = Cookie(default=None)) -> dict:
    user = auth.read_token(session)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    # Belt-and-braces: in the Operator app, admin routes are dead no matter what
    # token is presented — the admin half of the system simply isn't reachable.
    if edition.is_operator_edition() or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_module(key: str):
    """Route dependency: the signed-in account must hold the `key` grant.

    Admins implicitly hold every grant. The Operator edition stays locked to
    the salary/attendance module regardless of grants (it's the kiosk laptop).
    """
    if key not in ALL_KEYS:  # fail at import time, not per request
        raise ValueError(f"Unknown module key: {key}")

    def dep(user: dict = Depends(current_user), conn=Depends(get_db)) -> dict:
        if edition.is_operator_edition() and key != "salary":
            raise HTTPException(status_code=403, detail="Not available in the Operator app")
        if user["role"] == "admin":
            return user
        row = conn.execute(
            "SELECT grants FROM app_user WHERE username=?", (user["username"],)
        ).fetchone()
        try:
            grants = json.loads(row["grants"] or "[]") if row else []
        except ValueError:
            grants = []
        if key not in grants:
            raise HTTPException(status_code=403,
                                detail="Your account doesn't have access to this module")
        return user

    return dep
