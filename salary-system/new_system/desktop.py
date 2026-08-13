"""Desktop launcher — turns the FastAPI app into a double-click window app.

Starts uvicorn on a private localhost port in a background thread, waits for it
to answer, then opens a native window (pywebview) pointing at it. Closing the
window stops the server and quits. If pywebview can't start (rare), it falls
back to the user's default browser.

This module is started by ``run_admin.py`` / ``run_operator.py``, which set the
``SALARY_EDITION`` environment variable first.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until_up(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/edition"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)  # noqa: S310 (localhost only)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def run() -> None:
    # Imported here so SALARY_EDITION (set by the entry script) is already in
    # the environment before the app and its modules initialise.
    import uvicorn

    from backend.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    # uvicorn skips signal handlers off the main thread, so this is safe.
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait_until_up(port):
        sys.stderr.write("APEX Payroll: the local server did not start in time.\n")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    is_op = os.environ.get("SALARY_EDITION", "admin").lower() == "operator"
    title = "APEX Payroll — " + ("Operator" if is_op else "Admin")

    try:
        import webview

        class JsApi:
            """Bridge for the UI (window.pywebview.api): window.open doesn't
            work inside pywebview, so pages ask the shell for a real second
            window. Same WebView2 profile => same session."""

            def open_inventory(self):
                webview.create_window(
                    title + " — Inventory", f"{url}inventory.html",
                    width=1320, height=860, min_size=(1024, 720),
                )

            def open_path(self, path: str):
                """Open any same-app path (a document, an attachment, a page)
                in its own window. Only local paths — never external URLs."""
                path = str(path)
                if not path.startswith("/") or path.startswith("//"):
                    return
                webview.create_window(
                    title, f"{url}{path.lstrip('/')}",
                    width=1100, height=800, min_size=(800, 600),
                )

        webview.create_window(title, url, width=1320, height=860,
                              min_size=(1024, 720), js_api=JsApi())
        webview.start()  # blocks until the window is closed
    except Exception:
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        server.should_exit = True
        time.sleep(0.3)


if __name__ == "__main__":
    run()
