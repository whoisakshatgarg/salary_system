# APEX Payroll — packaging & deployment

Two standalone Windows apps are built from this one codebase:

| App | File | Who | Access |
|-----|------|-----|--------|
| **Admin** | `APEX Payroll (Admin).exe` | CEO | Everything — attendance, salary, advances, exports, rules, sync |
| **Operator** | `APEX Payroll (Operator).exe` | Attendance operator | Attendance entry only. Admin sign-in and every admin route are blocked **on the server** — the operator cannot reach CEO data even with the admin password |

Each `.exe` is fully self-contained: **the laptop needs nothing installed** — no Python, no VSCode, no browser setup. The user double-clicks it and a normal app window opens. It works **offline** (Tailwind/Alpine are bundled, not loaded from the internet).

The two-machine model maps cleanly: **operator laptop → Operator app, CEO laptop → Admin app**, exchanging data through the shared cloud folder exactly as before.

---

## How it runs

- Double-clicking the `.exe` starts a private local web server (on `127.0.0.1`, a random port) and opens it in a **native window** (no browser address bar). Closing the window quits everything.
- **Operator app** signs in automatically (kiosk) straight into attendance — no login prompt.
- **Admin app** shows the CEO login (default `admin` / `admin123` — change it after first sign-in).

### Where data is stored
The database, the editable config, backups, and the session secret live in a normal per-user folder (created on first run, survives app updates):

- **Windows:** `%APPDATA%\APEX Payroll\`  (e.g. `C:\Users\<you>\AppData\Roaming\APEX Payroll\`)
- macOS: `~/Library/Application Support/APEX Payroll/`

To **back up**, copy `salary.db` from there (or use the in-app backup button). To **reset**, delete that folder — the app reseeds on next launch.

---

## Building the Windows apps

You're on a Mac; the `.exe` must be built **on Windows** (PyInstaller can't cross-compile). Two ways:

### Option A — one-click on a Windows PC (simplest)
1. On any Windows machine, install **Python 3.11+** from <https://www.python.org/downloads/> — on the first installer screen **tick “Add python.exe to PATH”**.
2. Copy this `new_system` folder onto that machine.
3. **Double-click `build_windows.bat`.** It sets up an isolated environment, installs the build tools, and produces both apps in the **`dist`** folder.
4. Copy `dist\APEX Payroll (Admin).exe` and `dist\APEX Payroll (Operator).exe` to the respective laptops. Done.

(You only need Python on the *build* machine, once. The laptops you hand the `.exe` to need nothing.)

### Option B — build in the cloud (no Windows machine needed)
Use the included GitHub Actions workflow at `.github/workflows/build-windows.yml`:
1. Push this project to a GitHub repo (move the `.github` folder to the repo root — see the note at the top of that file).
2. GitHub → **Actions → “Build Windows apps” → Run workflow**.
3. When it finishes, download the **`apex-payroll-windows`** artifact — it contains both `.exe` files.

---

## Notes
- **WebView2 runtime:** the native window uses Microsoft’s WebView2, which ships with Windows 11 and current Windows 10. On the rare machine without it, install it once from <https://developer.microsoft.com/microsoft-edge/webview2/> (the “Evergreen Standalone Installer”), or the app falls back to opening in the default browser.
- **Antivirus / SmartScreen:** unsigned PyInstaller `.exe`s can trigger a “Windows protected your PC” prompt → *More info → Run anyway*. Code-signing removes this if you have a certificate.
- **Custom icon:** drop a `build/app.ico` next to the spec and uncomment the `icon=` line in `apex_payroll.spec`.

## Running from source (development, unchanged)
```
cd new_system
../venv/bin/uvicorn backend.main:app --reload      # http://127.0.0.1:8000
```
Force an edition while developing with an env var, e.g. `SALARY_EDITION=operator`.
To run the packaged-style window locally: `pip install pywebview` then `python run_admin.py` (or `run_operator.py`).
