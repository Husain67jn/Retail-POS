from __future__ import annotations

APP_NAME = "Mughal Electric Store"
APP_VERSION = "1.1.0"

# Minimum window bounds are kept modest so the layout stays usable on small
# laptop panels; the window opens at a screen-relative size (see MainWindow)
# and every page flexes with QSizePolicy/stretch rather than fixed pixels.
WINDOW_MIN_WIDTH = 940
WINDOW_MIN_HEIGHT = 600

# Fraction of the available screen the window claims on first launch, clamped
# between the minimums above and the physical screen so it never overflows.
WINDOW_DEFAULT_WIDTH_FRAC = 0.82
WINDOW_DEFAULT_HEIGHT_FRAC = 0.85

# The sidebar flexes between these bounds instead of a single fixed width so it
# scales with font size and window width without clipping the brand or nav.
SIDEBAR_WIDTH = 248
SIDEBAR_MIN_WIDTH = 200
SIDEBAR_MAX_WIDTH = 264
