from __future__ import annotations

from functools import lru_cache

from app.config.paths import bundled_resource_dir

# Baseline font-size (px, except PRIMARY_BTN which is pt) for each sized selector
# in theme.qss at the "Comfortable" scale of 1.0. build_theme() multiplies these
# by the active typography scale so a larger Settings size grows headings, the
# total value, and primary buttons together with the rest of the UI.
_FONT_SIZES = {
    "BRAND": 19,
    "SUBTITLE": 11,
    "PAGE_TITLE": 25,
    "SECTION_TITLE": 15,
    "SIDEBAR_VERSION": 10,
    "PRIMARY_BTN": 11,
    "DASHBOARD_VALUE": 20,
    "TOTAL_VALUE": 23,
}


@lru_cache(maxsize=1)
def _raw_theme() -> str:
    theme_path = bundled_resource_dir() / "theme.qss"
    return theme_path.read_text(encoding="utf-8")


def build_theme(font_family: str = "Segoe UI", scale: float = 1.0) -> str:
    """Return the application stylesheet bound to a font family and size scale.

    The family is injected into a global ``*`` rule so every widget adopts it,
    and each sized selector is scaled so the whole interface grows or shrinks
    consistently with the chosen typography size.
    """
    qss = _raw_theme().replace("%%FONT_FAMILY%%", font_family)
    for name, base in _FONT_SIZES.items():
        qss = qss.replace(f"%%FS_{name}%%", str(max(1, round(base * scale))))
    return qss


def load_theme(font_family: str = "Segoe UI", scale: float = 1.0) -> str:
    return build_theme(font_family, scale)
