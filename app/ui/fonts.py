from __future__ import annotations

from app.config.paths import bundled_resource_dir

# Premium typography (Cinzel, Playfair Display, Cormorant Garamond, Montserrat,
# Poppins, Inter) is offered in Settings -> Typography, but those families are
# not guaranteed to be installed on a Windows POS terminal. Any font files
# dropped into app/resources/fonts are registered with Qt at startup so the
# family names resolve and actually render; families already installed on the
# machine keep working unchanged. Loading is best-effort: a missing directory
# or an unreadable file never blocks application launch.

_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}


def fonts_dir():
    return bundled_resource_dir() / "fonts"


def load_bundled_fonts(logger=None) -> list[str]:
    """Register every bundled font file with the running QApplication.

    Returns the list of font families that were successfully registered. Safe
    to call once after the QApplication is created and before the main window
    is shown.
    """
    from PyQt5.QtGui import QFontDatabase

    directory = fonts_dir()
    families: list[str] = []
    try:
        candidates = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES
        )
    except (OSError, FileNotFoundError):
        return families

    for path in candidates:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            if logger is not None:
                logger.warning("Could not load bundled font: %s", path.name)
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in families:
                families.append(family)

    if logger is not None and families:
        logger.info("Loaded bundled fonts: %s", ", ".join(families))
    return families
