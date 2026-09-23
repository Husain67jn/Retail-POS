from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from app.config.paths import ensure_runtime_dirs
from app.database.connection import DatabaseManager
from app.ui.main_window import MainWindow
from app.utils.logger import configure_logging


def install_exception_hook(logger, app) -> None:
    """Log uncaught exceptions and show a useful GUI error instead of hiding them."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Unhandled application exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        QMessageBox.critical(
            None,
            "Mughal Electric Store error",
            "An unexpected error occurred. The error was recorded in the application log.",
        )

    sys.excepthook = handle_exception


def main() -> int:
    # Qt6 (PySide6) enabled high-DPI scaling by default; Qt5 (PyQt5) does not.
    # Opt in before any QApplication is constructed so the interface keeps the
    # same crisp scaling on high-DPI displays that it had before the port.
    for _attr in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        if hasattr(Qt, _attr):
            QApplication.setAttribute(getattr(Qt, _attr), True)

    dirs = ensure_runtime_dirs()
    logger = configure_logging(dirs["logs"])
    db = DatabaseManager(dirs["data"] / "pos.db")

    try:
        db.initialize()
        if not db.health_check():
            raise RuntimeError("SQLite health check failed")
        logger.info("SQLite initialized at %s", db.db_path)
    except Exception:
        logger.exception("Application initialization failed")
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None,
            "Mughal Electric Store startup error",
            "The application could not initialize its local database. "
            "Check the log file for details.",
        )
        db.dispose()
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Mughal Electric Store")
    # Register bundled premium fonts (Cinzel, Playfair Display, etc.) so the
    # Settings -> Typography choices resolve to real families instead of a
    # silent system fallback. Must run after the QApplication exists.
    from app.ui.fonts import load_bundled_fonts

    load_bundled_fonts(logger)
    install_exception_hook(logger, app)
    app.setApplicationVersion("0.1.0")
    window = MainWindow(db)
    window.show()
    logger.info("Main window launched")

    exit_code = app.exec_()
    db.dispose()
    logger.info("Application exited with code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
