#!/usr/bin/env python3
"""Automated, plug-and-play Windows build for RetailPOS.

Pipeline:
  1. clean          - remove previous build/ and dist/ output
  2. run_pyinstaller- freeze the app via RetailPOS.spec (onedir)
  3. verify         - assert the exe, bundled resources, Qt platform plugin,
                      and the MSVC runtime DLLs are all present
  4. fetch_redist   - best-effort download of vc_redist.x64.exe for installer.iss
  5. summary        - print the delivered directory structure

    python build_exe.py                 # full build + verify + redist fetch
    python build_exe.py --skip-redist   # skip the vc_redist download step

The MSVC runtime check exists because a missing vcruntime140*/msvcp140 is the
exact cause of "DLL load failed while importing QtWidgets" on a clean Windows
machine. If that check fails, the build is NOT delivery-ready.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "RetailPOS.spec"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
APP_NAME = "RetailPOS"

# Assets that MUST be present inside the packaged app for it to run correctly.
REQUIRED_ASSETS = (
    Path("app") / "resources" / "theme.qss",
    Path("app") / "resources" / "fonts",
)

# Qt cannot create a window without its platform plugin; a GUI build without
# this file starts and then dies with "could not find or load the Qt platform
# plugin windows". PyQt5 (Qt5) keeps it under PyQt5\Qt5\plugins\platforms.
REQUIRED_PLUGINS = (
    Path("PyQt5") / "Qt5" / "plugins" / "platforms" / "qwindows.dll",
)

# The runtime DLLs whose absence produces the QtWidgets DLL-load crash on fresh
# Windows. concrt140.dll is not required by every Qt build, so it is not checked.
REQUIRED_RUNTIME_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")

# The Qt core DLL the runtime DLLs must sit beside. The plug-and-play guarantee
# is not just "the runtime is somewhere in the bundle" but "it is in the same
# directory as Qt5Core.dll", because same-directory lookup is what resolves Qt's
# import table on a machine without the VC++ redistributable.
QT_CORE_DLL = "Qt5Core.dll"

# Official Microsoft permalink for the latest x64 VC++ 2015-2022 redistributable.
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
REDIST_DIR = ROOT / "installer" / "redist"
REDIST_EXE = REDIST_DIR / "vc_redist.x64.exe"


def _log(message: str) -> None:
    print(f"[build_exe] {message}", flush=True)


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _log("ERROR: PyInstaller is not installed. Run: pip install pyinstaller")
        raise SystemExit(2)


def clean() -> None:
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            _log(f"Removing {path}")
            shutil.rmtree(path, ignore_errors=True)


def run_pyinstaller() -> None:
    if not SPEC.exists():
        _log(f"ERROR: spec file not found: {SPEC}")
        raise SystemExit(2)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)]
    _log("Running: " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        _log(f"ERROR: PyInstaller exited with code {result.returncode}")
        raise SystemExit(result.returncode)


def _app_dir() -> Path:
    # onedir layout produces dist/RetailPOS/.
    return DIST_DIR / APP_NAME


def _find(relative: Path, search_root: Path) -> Path | None:
    # Known layouts first: files sit either at the app root or, on
    # PyInstaller 6.x, under _internal/.
    for base in (search_root, search_root / "_internal"):
        candidate = base / relative
        if candidate.exists():
            return candidate
    # Fall back to a recursive search so verification stays robust if a future
    # PyInstaller relocates bundled data.
    suffix = str(relative).replace("\\", "/")
    for found in search_root.rglob(relative.name):
        if str(found).replace("\\", "/").endswith(suffix):
            return found
    return None


def _find_anywhere(filename: str, search_root: Path) -> Path | None:
    for found in search_root.rglob(filename):
        return found
    return None


def verify() -> None:
    app_dir = _app_dir()
    if not app_dir.exists():
        _log(f"ERROR: expected output directory missing: {app_dir}")
        raise SystemExit(1)

    exe = app_dir / f"{APP_NAME}.exe"
    if not exe.exists():
        # Non-Windows dev builds emit an extension-less binary.
        alt = app_dir / APP_NAME
        exe = alt if alt.exists() else exe
    if not exe.exists():
        _log(f"ERROR: executable not found under {app_dir}")
        raise SystemExit(1)
    _log(f"OK   executable: {exe}")

    missing: list[str] = []

    # Bundled app resources.
    for asset in REQUIRED_ASSETS:
        found = _find(asset, app_dir)
        if found is None:
            missing.append(str(asset))
        else:
            _log(f"OK   asset: {found}")

    # Qt platform plugin.
    for plugin in REQUIRED_PLUGINS:
        found = _find(plugin, app_dir)
        if found is None:
            missing.append(str(plugin))
        else:
            _log(f"OK   qt plugin: {found}")

    # MSVC runtime DLLs -- the plug-and-play guarantee. It is not enough for them
    # to exist somewhere in the bundle; they must sit in the same directory as
    # Qt5Core.dll, because that same-directory lookup is what lets Qt load on a
    # machine without the VC++ redistributable installed.
    qt_core = _find_anywhere(QT_CORE_DLL, app_dir)
    if qt_core is None:
        missing.append(QT_CORE_DLL)
        _log(f"ERROR: {QT_CORE_DLL} not found in bundle -- Qt itself is missing.")
    else:
        _log(f"OK   qt core: {qt_core}")
        qt_dir = qt_core.parent
        for dll in REQUIRED_RUNTIME_DLLS:
            beside = qt_dir / dll
            if beside.exists():
                _log(f"OK   runtime dll (beside Qt5Core): {beside}")
            elif _find_anywhere(dll, app_dir) is not None:
                # Present in the bundle but not next to Qt5Core.dll -- this is the
                # subtle failure mode that still crashes on a clean machine.
                missing.append(f"{dll} (not beside {QT_CORE_DLL})")
                _log(f"ERROR: {dll} is bundled but NOT next to {QT_CORE_DLL}.")
            else:
                missing.append(dll)

    if missing:
        _log("ERROR: bundle is NOT delivery-ready; missing: " + ", ".join(missing))
        if any(d in missing for d in REQUIRED_RUNTIME_DLLS):
            _log("      -> a missing MSVC runtime DLL is the direct cause of the")
            _log("         'DLL load failed while importing QtWidgets' crash.")
        raise SystemExit(1)

    _log("Build verification passed: bundle is plug-and-play ready.")


def fetch_redist(skip: bool) -> None:
    """Download vc_redist.x64.exe for installer.iss (best-effort)."""
    if skip:
        _log("Skipping vc_redist download (--skip-redist).")
        return
    if REDIST_EXE.exists() and REDIST_EXE.stat().st_size > 1_000_000:
        _log(f"OK   vc_redist already present: {REDIST_EXE}")
        return
    REDIST_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"Downloading vc_redist.x64.exe from {VC_REDIST_URL}")
    try:
        with urllib.request.urlopen(VC_REDIST_URL, timeout=60) as resp:
            data = resp.read()
        REDIST_EXE.write_bytes(data)
        _log(f"OK   vc_redist saved: {REDIST_EXE} ({len(data) // 1024} KiB)")
    except Exception as exc:  # network may be unavailable on a build box
        _log(f"WARNING: could not download vc_redist.x64.exe ({exc}).")
        _log(f"         Download it manually from {VC_REDIST_URL}")
        _log(f"         and place it at {REDIST_EXE} before compiling installer.iss.")


def summary() -> None:
    app_dir = _app_dir()
    _log("Delivery layout:")
    print(f"    {app_dir}\\")
    print(f"      RetailPOS.exe            <- launch this")
    print(f"      _internal\\               <- Qt, Python, resources, runtime DLLs")
    if REDIST_EXE.exists():
        print(f"    {REDIST_EXE}   <- bundled by installer.iss")
    print()
    _log("Next steps:")
    _log("  * Portable delivery : zip the entire dist\\RetailPOS\\ folder.")
    _log("  * Installer delivery: compile installer.iss with Inno Setup 6.")


def main(argv: list[str]) -> int:
    skip_redist = "--skip-redist" in argv
    ensure_pyinstaller()
    clean()
    run_pyinstaller()
    verify()
    fetch_redist(skip_redist)
    summary()
    _log(f"Done. Packaged app at: {_app_dir()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
