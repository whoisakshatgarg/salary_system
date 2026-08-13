"""Users & Access — the owner manages accounts and what each one can open.

Model: every account has a role ('admin' = full access to everything including
this screen; 'operator' = a normal account) and a grants list of module keys
(see core/registry.py). The launcher shows one tile per grant; the server
enforces the same grants on module routes via core.deps.require_module.
Admin-only, and dead in the Operator edition (require_admin already blocks it).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import edition
from ..core.auth import hash_password
from ..core.deps import current_user, get_db, require_admin
from ..core.registry import ALL_KEYS, MODULES

router = APIRouter()


def user_grants(row) -> list[str]:
    """Parse a user row's grants column (admins implicitly hold every key)."""
    if row["role"] == "admin":
        return list(ALL_KEYS)
    try:
        grants = json.loads(row["grants"] or "[]")
    except ValueError:
        grants = []
    return [g for g in grants if g in ALL_KEYS]


def _user_out(row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "grants": user_grants(row)}


def _count_admins(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM app_user WHERE role='admin'").fetchone()["n"]


class UserIn(BaseModel):
    username: str
    password: str = ""
    role: str = "operator"
    grants: list[str] = []


class UserUpdateIn(BaseModel):
    role: str | None = None
    grants: list[str] | None = None
    password: str | None = None


def _validate(body_role: str | None, grants: list[str] | None):
    if body_role is not None and body_role not in ("admin", "operator"):
        raise HTTPException(status_code=400, detail="Role must be admin or operator")
    if grants is not None:
        bad = [g for g in grants if g not in ALL_KEYS]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown module(s): {', '.join(bad)}")


# --------------------------------------------------------------------------- #
# The launcher's data: which tiles does THIS account see?
# --------------------------------------------------------------------------- #
@router.get("/api/modules")
def my_modules(user: dict = Depends(current_user), conn=Depends(get_db)):
    row = conn.execute(
        "SELECT * FROM app_user WHERE username=?", (user["username"],)).fetchone()
    grants = user_grants(row) if row else []
    if edition.is_operator_edition():
        grants = [g for g in grants if g == "salary"]  # kiosk laptop = attendance only
    return {
        "username": user["username"],
        "role": user["role"],
        "is_admin": user["role"] == "admin",
        "modules": [dict(m, granted=(m["key"] in grants)) for m in MODULES],
    }


# --------------------------------------------------------------------------- #
# Account management (admin)
# --------------------------------------------------------------------------- #
@router.get("/api/users")
def list_users(user: dict = Depends(require_admin), conn=Depends(get_db)):
    return [_user_out(r) for r in conn.execute("SELECT * FROM app_user ORDER BY id")]


@router.post("/api/users")
def create_user(body: UserIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    _validate(body.role, body.grants)
    if conn.execute("SELECT 1 FROM app_user WHERE username=?", (username,)).fetchone():
        raise HTTPException(status_code=400, detail=f"'{username}' already exists")
    conn.execute(
        "INSERT INTO app_user (username, password_hash, role, grants) VALUES (?,?,?,?)",
        (username, hash_password(body.password), body.role, json.dumps(body.grants)),
    )
    conn.commit()
    return [_user_out(r) for r in conn.execute("SELECT * FROM app_user ORDER BY id")]


@router.put("/api/users/{uid}")
def update_user(uid: int, body: UserUpdateIn, user: dict = Depends(require_admin),
                conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM app_user WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    _validate(body.role, body.grants)
    if (body.role == "operator" and row["role"] == "admin" and _count_admins(conn) <= 1):
        raise HTTPException(status_code=400,
                            detail="This is the only admin account — make another admin first")
    if body.role is not None:
        conn.execute("UPDATE app_user SET role=? WHERE id=?", (body.role, uid))
    if body.grants is not None:
        conn.execute("UPDATE app_user SET grants=? WHERE id=?", (json.dumps(body.grants), uid))
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        conn.execute("UPDATE app_user SET password_hash=? WHERE id=?",
                     (hash_password(body.password), uid))
    conn.commit()
    return [_user_out(r) for r in conn.execute("SELECT * FROM app_user ORDER BY id")]


@router.delete("/api/users/{uid}")
def delete_user(uid: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM app_user WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    if row["username"] == user["username"]:
        raise HTTPException(status_code=400, detail="You can't delete the account you're signed in with")
    if row["role"] == "admin" and _count_admins(conn) <= 1:
        raise HTTPException(status_code=400, detail="This is the only admin account")
    conn.execute("DELETE FROM app_user WHERE id=?", (uid,))
    conn.commit()
    return [_user_out(r) for r in conn.execute("SELECT * FROM app_user ORDER BY id")]
