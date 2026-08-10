"""The app's version — the single source of truth for the self-updater.

Bump this before every release. The GitHub Actions build refuses to publish a
release whose tag doesn't match this string (tag ``v1.2.3`` ↔ ``1.2.3`` here),
which is what keeps the packaged apps' update check honest: an app compares
its own ``__version__`` against the newest GitHub Release tag.
"""

__version__ = "1.0.0"
