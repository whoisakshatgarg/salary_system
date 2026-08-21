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
- **Admin app** opens the shared login (default `admin` / `admin123` — change it after first sign-in); any account created in **Users & Access** can sign in and lands on the Home launcher showing only its granted modules.

### Where data is stored
The database, the editable config, backups, and the session secret live in a normal per-user folder (created on first run, survives app updates):

- **Windows:** `%APPDATA%\APEX Payroll\`  (e.g. `C:\Users\<you>\AppData\Roaming\APEX Payroll\`)
- macOS: `~/Library/Application Support/APEX Payroll/`

To **back up**, use the in-app backup button (a `.zip` of the database AND every `*_files/` attachments folder); copying by hand means `salary.db` **plus** `inventory_files/`, `employee_files/` and `drawing_files/`. To **reset**, delete that folder — the app reseeds on next launch.

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
The GitHub Actions workflow lives at the **repository root**: `.github/workflows/build-windows.yml` (GitHub only runs workflows from there; it builds inside `salary-system/new_system`).
1. Push the repo to GitHub.
2. GitHub → **Actions → “Build Windows apps” → Run workflow**.
3. When it finishes, download the **`apex-payroll-windows`** artifact — it contains both `.exe` files.

For **releases** (which also feed the built-in auto-update), don't run it manually — push a version tag instead, see below.

---

## Shipping updates (built-in auto-update)

The apps update themselves from **GitHub Releases** of the repo named in
`config/update.json` (`github_repo`). On every launch each app quietly checks the
newest release; **only if a newer version exists** does the user see an
*“Update available”* popup — one click on **Update now** downloads the new build,
the app closes, swaps itself, and reopens. No popup otherwise, and no data is
touched (the DB/config live in the per-user folder, not in the `.exe`).

### To publish a new version
1. Make your code changes.
2. Bump the version in [`backend/core/version.py`](backend/core/version.py) — e.g. `__version__ = "1.0.1"`.
3. Commit and push to `main`.
4. Tag and push the tag:
   ```bash
   git tag v1.0.1 && git push origin v1.0.1
   ```
5. GitHub Actions builds both `.exe`s and attaches them to a Release automatically
   (the build **fails on purpose** if the tag doesn't match `version.py`, so you
   can't ship a mismatched version). ~10 minutes later, every installed app offers
   the update on its next launch.

### Notes
- The repo must be **public**, or paste a read-only fine-grained personal access
  token into `github_token` in `config/update.json` before building.
- `auto_check_on_start: false` in `config/update.json` disables the launch-time
  popup; the version label at the bottom of the sidebar still checks on demand.
- The very first installs (v1.0.0) are distributed by hand as before; everything
  after that can ride the updater.
- Offline is fine: if GitHub is unreachable the check silently does nothing.

---

## Notes
- **SOP document templates ship inside the exe:** `apex_payroll.spec` bundles
  `backend/documents/templates/` as data (the engine resolves them relative to
  its own module, which lands under `sys._MEIPASS` when frozen). If a build's
  paper downloads 500 with a missing-template error, that datas line was lost.
  The engine's libraries (`openpyxl`, `python-docx`) ride in via normal import
  analysis — they're in `requirements.txt` for source installs.
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
