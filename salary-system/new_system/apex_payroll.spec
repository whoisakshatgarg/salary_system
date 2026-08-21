# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds the two Windows apps from one codebase:

    dist/APEX Payroll (Admin).exe       full CEO access
    dist/APEX Payroll (Operator).exe    attendance-only kiosk

Build (on Windows):  pyinstaller --noconfirm apex_payroll.spec
(or just double-click build_windows.bat, which does it for you.)

Each is a single self-contained .exe — the target laptop needs no Python.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Bundle the UI and the default config templates. They land in the frozen
# bundle root; backend/core/paths.py reads them from there (sys._MEIPASS) and copies
# config into a writable per-user folder on first run.
datas = [
    ("frontend", "frontend"),
    ("config", "config"),
    # the SOP document templates: engine.py resolves them relative to its own
    # __file__, which under PyInstaller lands in sys._MEIPASS — so the files
    # must be bundled at the same relative path or every paper render 500s
    ("backend/documents/templates", "backend/documents/templates"),
]

hiddenimports = collect_submodules("uvicorn") + [
    "backend",
    "backend.main",
]

# pywebview pulls in platform backends dynamically; collect everything it needs.
wv_datas, wv_binaries, wv_hidden = collect_all("webview")
datas += wv_datas
hiddenimports += wv_hidden


def _app(entry, name):
    a = Analysis(
        [entry],
        pathex=["."],
        binaries=list(wv_binaries),
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=["tkinter", "matplotlib", "numpy", "pandas"],
        noarchive=False,
    )
    pyz = PYZ(a.pure)
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,          # no terminal window
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        # icon="build/app.ico",  # drop a .ico here and uncomment for a custom icon
    )
    return exe


exe_admin = _app("run_admin.py", "APEX Payroll (Admin)")
exe_operator = _app("run_operator.py", "APEX Payroll (Operator)")
