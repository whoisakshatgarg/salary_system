"""Shared FastAPI dependencies (DB connection, auth) — used by main.py and any
feature router (e.g. inventory.py) without import cycles."""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException

from . import auth, db, edition


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
