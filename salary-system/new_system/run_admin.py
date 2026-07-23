"""Entry point for the CEO / Admin app (full access)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SALARY_EDITION", "admin")
# Make the project root importable when frozen or run as a loose script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from desktop import run

if __name__ == "__main__":
    run()
