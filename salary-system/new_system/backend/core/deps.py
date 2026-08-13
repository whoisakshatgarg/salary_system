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


def current_user(session: str | None = Cookie(default=None),
                 conn=Depends(get_db)) -> dict:
    user = auth.read_token(session)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    # The cookie lives 7 days — the DB is the authority, not the token: a
    # deleted account dies instantly, a demoted admin loses admin instantly.
    row = conn.execute(
        "SELECT role FROM app_user WHERE username=?", (user["username"],)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="This account no longer exists")
    user["role"] = row["role"]
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    # Belt-and-braces: in the Operator app, admin routes are dead no matter what
    # token is presented — the admin half of the system simply isn't reachable.
    if edition.is_operator_edition() or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_module(*keys: str):
    """Route dependency: the signed-in account must hold ANY of the `keys`
    grants (several keys = routes shared between modules, e.g. the employee
    roster serves both Salary and Employee Management).

    Admins implicitly hold every grant. The Operator edition stays locked to
    the salary/attendance module regardless of grants (it's the kiosk laptop).
    """
    unknown = [k for k in keys if k not in ALL_KEYS]
    if not keys or unknown:  # fail at import time, not per request
        raise ValueError(f"Unknown module key(s): {', '.join(unknown) or '(none given)'}")

    def dep(user: dict = Depends(current_user), conn=Depends(get_db)) -> dict:
        if edition.is_operator_edition() and "salary" not in keys:
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
        if not any(k in grants for k in keys):
            raise HTTPException(status_code=403,
                                detail="Your account doesn't have access to this module")
        return user

    return dep
