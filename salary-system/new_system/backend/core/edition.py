"""Which app are we — the full Admin (CEO) app, or the locked-down Operator app?

The two distributed `.exe`s share this one codebase; they differ only by the
``SALARY_EDITION`` environment variable, which each launcher sets before the
app starts (see ``run_admin.py`` / ``run_operator.py``).

The distinction is enforced on the *server*, not just hidden in the UI:

* operator edition  → admin sign-in is refused and every admin-only route 403s,
  so the Operator app physically cannot reach admin data even with the admin
  password. It auto-signs-in as the operator (kiosk style).
* admin edition     → full access to both admin and operator functions.
"""

from __future__ import annotations

import os

ADMIN = "admin"
OPERATOR = "operator"


def edition() -> str:
    """``"operator"`` or ``"admin"`` (the default)."""
    return OPERATOR if os.environ.get("SALARY_EDITION", "").lower() == OPERATOR else ADMIN


def is_operator_edition() -> bool:
    return edition() == OPERATOR
