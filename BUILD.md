# Building & Delivering RetailPOS (Mughal Electric Store)

This app is a **PyQt5 (Qt 5.15)** desktop application frozen with **PyInstaller**.
The notes below make it **plug-and-play on a fresh Windows PC** — no pre-installed
Python, pip, or Visual C++ runtime required.

## Why PyQt5 and not PySide6

The target machine runs **Windows 10 build 10240** (the original 1507 release).
**Qt 6 / PySide6 requires Windows 10 1809 or newer** and will not load its DLLs on
10240. **Qt 5.15 (the PyQt5 wheel) supports Windows 7+**, including that old build,
so the project was ported from PySide6 to PyQt5. The port was mechanical:

- `PySide6.*` imports → `PyQt5.*`
- `Signal` → `pyqtSignal`
- `app.exec()` → `app.exec_()`
- High-DPI scaling opt-in added in `app/main.py` (Qt6 did this automatically; Qt5
  needs `AA_EnableHighDpiScaling` / `AA_UseHighDpiPixmaps` set before the
  `QApplication` is created)
- `QPen`/`QBrush` given a color **string** → wrapped in `QColor(...)`, because
  PyQt5 (unlike PySide6) does not implicitly convert `str` → `QColor`

The full test suite (169 tests) passes under PyQt5, and the frozen exe launches
cleanly (reaches "Main window launched" with no DLL error).

## The bug we fixed (still relevant on Qt5)

On a clean Windows machine the app crashed with:

> DLL load failed while importing QtWidgets: The specified module could not be found.

**Cause:** Qt's DLLs (`Qt5Core.dll`, `Qt5Widgets.dll`, …) link against the MSVC
runtime (`vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`). Those DLLs
ship inside `PyQt5\Qt5\bin`, but PyInstaller treats them as OS "system" DLLs and
**excludes them by default**. A developer machine has the VC++ redistributable
installed, so it works there; a fresh machine does not, so Qt fails to load.

**Fix (two independent layers):**
1. `RetailPOS.spec` force-includes the MSVC runtime DLLs next to the Qt DLLs
   (`_internal\PyQt5\Qt5\bin\`). Binaries passed explicitly to `Analysis()`
   bypass PyInstaller's system-DLL exclusion. It also uses `collect_all('PyQt5')`
   so every Qt5 DLL, plugin (incl. `platforms\qwindows.dll`) and translation is
   bundled.
2. `installer.iss` silently installs `vc_redist.x64.exe` during setup, so the
   runtime is present system-wide too.

Either layer alone fixes the crash; together they make delivery robust.

## Prerequisites (build machine only)

- Windows x64
- The project virtual environment in `.venv\` (already present), or `pip install -r requirements.txt`
- **Inno Setup 6** (for the installer only): https://jrsoftware.org/isdl.php

## Build

```
.venv\Scripts\python.exe build_exe.py
```

This cleans `build/` + `dist/`, freezes the app via `RetailPOS.spec`, then
**verifies** the exe, resources, `qwindows.dll`, `Qt5Core.dll`, and — critically —
that the MSVC runtime DLLs sit **in the same directory as `Qt5Core.dll`**. If any
runtime DLL is missing or misplaced the build fails loudly; it is not
delivery-ready. Add `--skip-redist` if `installer\redist\vc_redist.x64.exe` is
already present (it is) and you want to skip the download step.

Output: `dist\RetailPOS\` (~178 MB, `RetailPOS.exe` + `_internal\`). The size is
larger than a hand-tuned build because `collect_all('PyQt5')` deliberately bundles
the entire PyQt5 payload (including Qt modules the POS never uses, such as QtQml
/ QtQuick / Qt3D) to guarantee nothing is missing at runtime. The benign
"Library not found" warnings during the build (`Qt53DRender.dll`, `LIBPQ.dll`,
`Qt5WebEngine.dll`, …) are those unused optional plugins and can be ignored.

## Deliver — pick one

### Option A — Portable (no install)
Zip the **entire** `dist\RetailPOS\` folder and send it. The client unzips
anywhere and runs `RetailPOS.exe`. The bundled runtime DLLs mean it works even
without the VC++ redistributable installed.

### Option B — Installer (recommended)
Compile the installer after building:

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Produces `installer\Output\MughalElectricStore-Setup-1.0.0.exe`, a single file
that installs to Program Files, silently runs `vc_redist.x64.exe`, and creates
Start Menu / Desktop shortcuts.

## Where the app stores data

Writable data is **never** inside the install folder. It lives under
`%LOCALAPPDATA%\RetailPOS\` (`data\pos.db`, `logs\`, `backups\`, `config\`),
created on first launch. Uninstalling leaves client data intact.

## Notes

- The `.venv` targets Python 3.11; `pyproject.toml` still declares
  `requires-python >=3.12,<3.13`. The build works on 3.11 — align these if you
  standardize the toolchain.
- `pypdf` in `requirements.txt` is a test-only dependency (used by
  `tests/test_phase4_invoice_output.py` to read generated PDFs); it is not needed
  at runtime.
- No app icon is bundled. Drop one at `app\resources\app.ico` and rebuild to
  brand the exe and shortcuts.
