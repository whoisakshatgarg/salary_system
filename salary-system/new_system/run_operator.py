"""Entry point for the Operator app (attendance only; auto kiosk sign-in).

This edition refuses admin sign-in and 403s every admin route, so it cannot
reach CEO data even with the admin password.
"""

from __future__ import annotations

import os
import sys

os.environ["SALARY_EDITION"] = "operator"
# Make the project root importable when frozen or run as a loose script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from desktop import run

if __name__ == "__main__":
    run()
