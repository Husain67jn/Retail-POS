from __future__ import annotations
from pathlib import Path
import os
import sys

APP_NAME = "RetailPOS"  # internal storage identifier; user-facing branding is Mughal Electric Store.

def windows_app_data_dir(local_app_data: str | None = None) -> Path:
    value = local_app_data or os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME

def app_data_dir() -> Path:
    return windows_app_data_dir()

def database_path() -> Path:
    return app_data_dir() / "data" / "pos.db"

def writable_dirs() -> dict[str, Path]:
    root=app_data_dir(); dirs={"root":root,"data":root/"data","backups":root/"backups","logs":root/"logs","config":root/"config"}
    for path in dirs.values(): path.mkdir(parents=True,exist_ok=True)
    return dirs

def ensure_runtime_dirs() -> dict[str, Path]:
    return writable_dirs()

def bundled_resource_dir() -> Path:
    base=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[2])); return base/"app"/"resources"
