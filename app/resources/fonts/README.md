# Premium typography for Mughal Electric Store

Drop TrueType/OpenType font files here to make the premium families in
**Settings → Typography** render on machines where they are not already
installed. Every `*.ttf`, `*.otf`, and `*.ttc` file in this folder is
registered with Qt automatically at application startup
(`app/ui/fonts.py::load_bundled_fonts`).

Recommended files (family name → file), matching the Settings dropdown:

- Cinzel → `Cinzel-Regular.ttf`, `Cinzel-Bold.ttf`
- Playfair Display → `PlayfairDisplay-Regular.ttf`, `PlayfairDisplay-Bold.ttf`
- Cormorant Garamond → `CormorantGaramond-Regular.ttf`, `CormorantGaramond-Bold.ttf`
- Montserrat → `Montserrat-Regular.ttf`, `Montserrat-Bold.ttf`
- Poppins → `Poppins-Regular.ttf`, `Poppins-Bold.ttf`
- Inter → `Inter-Regular.ttf`, `Inter-Bold.ttf`

The loader is best-effort: a missing file or unreadable font is logged and
skipped, and never blocks the app from launching. Families already installed
on the system continue to work without any file here.
