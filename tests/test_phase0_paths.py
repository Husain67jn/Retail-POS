from pathlib import Path

import app.config.paths as paths


def test_windows_localappdata_mapping_returns_filesystem_path():
    result = paths.windows_app_data_dir("C:\\Users\\Test\\AppData\\Local")
    assert isinstance(result, Path)
    assert str(result).replace("\\", "/").endswith("C:/Users/Test/AppData/Local/RetailPOS")


def test_database_path_uses_data_directory():
    assert paths.database_path() == paths.app_data_dir() / "data" / "pos.db"
    assert isinstance(paths.database_path(), Path)


def test_writable_dirs_are_filesystem_paths_and_mkdir_works(tmp_path, monkeypatch):
    root = tmp_path / "RetailPOS"
    monkeypatch.setattr(paths, "app_data_dir", lambda: root)

    dirs = paths.ensure_runtime_dirs()

    assert dirs
    assert all(isinstance(path, Path) for path in dirs.values())
    assert all(path.is_dir() for path in dirs.values())

    # Regression guard for the Windows startup failure: every returned path
    # must be a concrete filesystem Path, not PureWindowsPath.
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
