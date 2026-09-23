# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for RetailPOS (shop-pos v1.0.0), targeting
# PyInstaller 6.x on Windows / Python 3.11+.
#
#   Build:  python build_exe.py
#     or :  pyinstaller --noconfirm --clean RetailPOS.spec
#
# ---------------------------------------------------------------------------
# WHY THIS APP USES PyQt5 (Qt 5.15) AND NOT PySide6 (Qt 6)
# ---------------------------------------------------------------------------
# The target machine runs Windows 10 build 10240 (the original 1507 release).
# Qt 6 / PySide6 requires Windows 10 1809 or newer and will not even load its
# DLLs on 10240. Qt 5.15 (shipped by the PyQt5 wheel) supports Windows 7+,
# including that old build, so the whole project was ported PySide6 -> PyQt5.
#
# ---------------------------------------------------------------------------
# THE "DLL load failed while importing QtWidgets" FIX (still relevant on Qt5)
# ---------------------------------------------------------------------------
# PyQt5's Qt DLLs (Qt5Core.dll, Qt5Gui.dll, Qt5Widgets.dll, ...) are compiled
# with MSVC and dynamically link against the Visual C++ runtime:
#   vcruntime140.dll, vcruntime140_1.dll, msvcp140.dll (and concrt140.dll).
# Those runtime DLLs physically ship inside PyQt5\Qt5\bin, right next to the Qt
# DLLs -- but PyInstaller treats them as OS-provided "system" DLLs and
# DELIBERATELY EXCLUDES them from the bundle during dependency analysis. On a
# developer machine that already has the VC++ Redistributable installed the app
# still runs; on a *fresh* Windows install that lacks the redistributable,
# loading QtWidgets fails with exactly:
#
#     DLL load failed while importing QtWidgets: The specified module
#     could not be found.
#
# This spec fixes that by explicitly re-adding the MSVC runtime DLLs to the
# bundle (binaries passed to Analysis() bypass the system-DLL exclusion) so they
# sit next to the Qt DLLs in _internal\PyQt5\Qt5\bin. The paired installer
# (installer.iss) *also* runs vc_redist.x64.exe silently, so the machine gets
# the runtime system-wide too -- belt and suspenders, truly plug-and-play.
#
# collect_all('PyQt5') pulls the full PyQt5 payload (every Qt5*.dll, the Qt
# plugins incl. platforms\qwindows.dll, translations and Python submodules) so
# nothing the app touches at runtime can be missing. That is deliberately
# generous: it inflates the bundle with Qt modules the POS never uses, but it is
# the safest, most future-proof way to guarantee a working freeze.

import glob
import os
import sys
from importlib.util import find_spec

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller and points at this spec's directory.
ROOT = os.path.abspath(globals().get("SPECPATH", os.getcwd()))

ENTRY_SCRIPT = os.path.join(ROOT, "app", "main.py")
RESOURCES_SRC = os.path.join(ROOT, "app", "resources")


# ---------------------------------------------------------------------------
# Helpers to locate binaries that must be force-included.
# ---------------------------------------------------------------------------
def _package_dir(name):
    """Absolute path of an installed package's directory, or None."""
    try:
        spec = find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec and spec.submodule_search_locations:
        return spec.submodule_search_locations[0]
    return None


def _pyqt5_qt_bin_dir():
    """Absolute path of PyQt5\\Qt5\\bin (where Qt5Core.dll et al. live), or None."""
    base = _package_dir("PyQt5")
    if not base:
        return None
    candidate = os.path.join(base, "Qt5", "bin")
    return candidate if os.path.isdir(candidate) else None


# The MSVC runtime DLLs Qt links against. PyQt5's own Qt5\bin ships the exact
# versions Qt5 was built against, so prefer those; fall back to System32 and the
# base Python install so a build still works if the wheel layout ever changes.
_RUNTIME_DLLS = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "concrt140.dll",
)

# Where the frozen Qt DLLs land, and therefore where the runtime must sit next
# to them. PyInstaller's PyQt5 hook mirrors the wheel layout: PyQt5\Qt5\bin.
_QT_BIN_DEST = os.path.join("PyQt5", "Qt5", "bin")


def _msvc_runtime_binaries():
    """(src, dest) tuples for every MSVC runtime DLL we can locate.

    Each DLL is dropped both beside the Qt DLLs (_internal\\PyQt5\\Qt5\\bin, which
    is what actually resolves Qt's import table -- same-directory lookup always
    wins) and at the bundle root (_internal\\) as a cheap safety net for anything
    else in the bundle that needs the runtime.
    """
    search_dirs = [
        d
        for d in (
            _pyqt5_qt_bin_dir(),
            _package_dir("PyQt5"),
            sys.base_prefix,
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
        )
        if d and os.path.isdir(d)
    ]

    binaries = []
    for name in _RUNTIME_DLLS:
        for directory in search_dirs:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                binaries.append((candidate, _QT_BIN_DEST))  # -> _internal\PyQt5\Qt5\bin
                binaries.append((candidate, "."))            # -> _internal\
                break
        else:
            # concrt140.dll / msvcp140_1.dll are not required by every Qt build;
            # the core three are. Warn loudly so a build on a stripped machine
            # does not silently ship a bundle that crashes on clean Windows.
            if name not in ("concrt140.dll", "msvcp140_1.dll"):
                print(f"[RetailPOS.spec] WARNING: {name} not found on build "
                      f"machine; the frozen app may fail on clean Windows.")
    return binaries


def _pyqt5_plugin_binaries(subdir):
    """(src, dest) tuples for every DLL in PyQt5\\Qt5\\plugins\\<subdir>.

    Explicitly guarantees platforms\\qwindows.dll and the built-in styles are
    bundled even if PyInstaller's automatic PyQt5 hook ever changes behaviour.
    Destinations mirror the hook's layout (PyQt5\\Qt5\\plugins\\<subdir>) so these
    entries de-duplicate cleanly against the hook's rather than double-bundling.
    """
    base = _package_dir("PyQt5")
    if not base:
        return []
    plugin_dir = os.path.join(base, "Qt5", "plugins", subdir)
    dest = os.path.join("PyQt5", "Qt5", "plugins", subdir)
    return [(dll, dest) for dll in glob.glob(os.path.join(plugin_dir, "*.dll"))]


# ---------------------------------------------------------------------------
# datas / binaries / hiddenimports
# ---------------------------------------------------------------------------
# collect_all pulls the entire PyQt5 payload: Qt5*.dll, every Qt plugin
# (platforms, styles, imageformats, sqldrivers, ...), translations, and the
# Python-side submodules. This is the "--collect-all PyQt5" guarantee.
pyqt5_datas, pyqt5_binaries, pyqt5_hiddenimports = collect_all("PyQt5")

# Bundle app/resources/ (QSS theme, fonts, templates) to _MEIPASS/app/resources.
# Runtime code reads these via app.config.paths.bundled_resource_dir(), which
# resolves sys._MEIPASS when frozen. Writable data (SQLite DB, logs, backups)
# is deliberately NOT bundled here: it lives under %LOCALAPPDATA%\RetailPOS and
# must never be written inside _MEIPASS.
datas = [
    (RESOURCES_SRC, os.path.join("app", "resources")),
]
datas += pyqt5_datas

# The core fix: force-include the MSVC runtime (which PyInstaller would exclude
# as "system" DLLs) and explicitly re-pin the platform + styles plugins next to
# everything collect_all already gathered.
binaries = []
binaries += pyqt5_binaries
binaries += _msvc_runtime_binaries()
binaries += _pyqt5_plugin_binaries("platforms")  # qwindows.dll lives here
binaries += _pyqt5_plugin_binaries("styles")     # native Windows style

# Explicit hidden imports required for a clean frozen build, plus their
# dynamically-discovered submodules (SQLite dialect + ReportLab barcodes).
hiddenimports = [
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "sqlalchemy.dialects.sqlite",
    "reportlab",
    "reportlab.graphics.barcode",
]
hiddenimports += pyqt5_hiddenimports
hiddenimports += collect_submodules("sqlalchemy.dialects.sqlite")
hiddenimports += collect_submodules("reportlab.graphics.barcode")

# Optional Windows icon, used only if dropped in app/resources/app.ico.
_icon = os.path.join(RESOURCES_SRC, "app.ico")
app_icon = _icon if os.path.exists(_icon) else None


a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "PySide6", "shiboken6"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RetailPOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # clean Windows GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RetailPOS",
)
