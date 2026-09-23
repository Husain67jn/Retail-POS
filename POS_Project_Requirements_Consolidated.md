# Mughal Electric Store POS — Consolidated Project Requirements

**Merged from all prompts across 14 working sessions (26 Aug – 10 Sep 2026).**
Overlapping and repeated requests have been consolidated by theme; retries and
filler messages omitted.

---

## Project Context
- **Application:** "Mughal Electric Store" POS (a.k.a. *RetailPOS*, shop-pos v1.0.0).
- **Stack:** Python 3.12, **PyQt5** (migrated from PySide6), SQLAlchemy, SQLite, ReportLab.
- **Architecture:** modular — Views / Controllers / Database / Services.

## Standing Working Rules (from the guideline prompts)
- Edit **only** the files explicitly named in a task; do not touch unrelated files.
- Do **not** run heavy builds (`build_exe.py`, Inno Setup, full `pytest`) unless asked.
- Run a quick syntax / offscreen smoke check after edits.
- Never break existing invoice or Udhaar (credit) calculations while changing UI.
- Keep output token-efficient and production-ready.

---

## 1. Packaging & Windows Deployment
- Author a production PyInstaller spec (`RetailPOS.spec`) and build script (`build_exe.py`):
  windowed app (`console=False`), explicit hidden imports, bundle `app/resources/`.
- Keep runtime-writable data (SQLite DB, logs, backups) under `%LOCALAPPDATA%\RetailPOS`,
  never inside `_MEIPASS`; load bundled assets via `sys._MEIPASS`.
- Make the app fully **plug-and-play** on a clean Windows PC: fix `DLL load failed …
  QtWidgets`, `--collect-all` Qt, include platform plugins (`qwindows.dll`) and the
  VC++ runtime DLLs; provide an Inno Setup installer that silently installs `vc_redist`.
- **Migrate PySide6 → PyQt5** for Windows 10 Build 10240 compatibility (`Signal`→`pyqtSignal`,
  etc.) and clean up stale build/dist/`__pycache__`/artifact files.

## 2. Global Theme & Layout
- Remove the large top page-title headings on every page to reclaim vertical space.
- Sidebar: dark navy (`#0F172A`), active item as a rounded blue pill (`#2563EB`), bottom
  profile card, and a smooth collapse/expand-to-icons toggle.
- Canvas light grey (`#F8FAFC`); white cards (`#FFFFFF`) with `1px #E2E8F0` borders,
  rounded corners, and drop shadows; bold numbered card sub-headers.
- Replace fixed `1400x700`-style dimensions with fluid/responsive scaling for any resolution.
- Format all prices as whole integers (no `.00`) across every table, field, and total.
- Universal table alignment: headers and cells left-aligned (`AlignLeft | AlignVCenter`),
  with clear grey row/divider borders.

## 3. Typography
- Add premium font options: **Cinzel, Playfair Display, Cormorant Garamond, Montserrat,
  Poppins, Inter**.
- Font family/size changes must propagate to **every** component (product search, product
  cards, category/price labels, customer fields, bill-discount inputs).

## 4. POS / Billing Page
- Remove the `+`/`-` quantity buttons; fix the Qty field's clipping and multi-digit width.
- High-density tables: ~24px baseline rows, 8–10 visible without scrolling, minimal padding.
- No truncation: `setWordWrap(True)` + `ElideNone`, with `resizeRowsToContents()` so long
  product names wrap and grow the row instead of showing "…".
- Column sizing: tight fixed widths for Purchase/Retail/Unit Price, Qty, Discount, Line Total;
  Product column stretches. Center-align all non-product columns. Trash-icon Remove button.
- **Per-unit discount** logic: Line Discount = per-unit × qty; Net Line Total =
  (Unit Price − per-unit) × qty; flow through subtotals, DB save, and receipts.
- Bill discount inline with the TOTAL card; bright-red **Clear All** button.
- **Multi-bill tabs** (grew from 3 → 5 max), each with its own cart; "+ New Bill" disabled at max.
- Auto-fill "Amount Received" with the bill total when payment is Cash.
- Cart performance: batch redraws with `setUpdatesEnabled(False)` / `blockSignals(True)`
  to remove lag on 50–100+ items.
- Live search highlighting: case-insensitive matched-substring highlight (`#fff2a8`),
  cleared when the box empties (POS + Products).
- **Fast keyboard loop:** Enter in search → results row 0 → Enter adds item and focuses Qty
  (selected) → Enter → Item Discount → Enter → back to search; driven via `installEventFilter`.
- **Toggle switches:** reusable custom `ToggleSwitch` widget for Retail/Wholesale and
  Cash/Credit; clicking **anywhere** on the switch flips the state.
- **Design-mockup overhaul:** slate canvas + elevated white cards; embedded search icon;
  blue `+ Add to Bill`; segmented Retail/Wholesale and Cash/Credit; row-select `#E0F2FE`;
  grey TOTAL card; currency icon on Amount Received; full-height blue **Complete Sale** button.

## 5. Invoices
- **Edit existing invoices:** reload items into the POS cart and allow re-finalizing.
- **Redesign the invoice details viewer:** compact inline header (Invoice #, customer, date,
  time, status), high-density wrapped table (7–10 rows visible), compact financial summary
  (subtotal, discount, net, paid, balance), and a horizontal bottom action toolbar
  (Print Thermal, Save PDF, Void/Delete, Re-open).
- Fix double `#`/S.No numbering in the invoice detail table.
- **Deletion (no stock tracking):** `delete_invoice()` and `delete_udhar_entry()` that adjust
  `Customer.balance`, wrapped in try/except with rollback; UI handlers with confirmation dialogs.

## 6. Notes & Documents Page
- New sidebar page "Notes & Docs": quick notepad (auto-save to `data/notes.txt`) and a
  document vault for `.xlsx`/`.csv`/`.pdf`, opened in the system's default viewer.
- Polish: larger readable editor font (14–15px), modern rounded buttons with hover states,
  subtle card borders and separation.

## 7. Data Safety
- Factory Reset / Clear All Data with **two-step** protection: (1) block unless a backup was
  made **today**; (2) require typing `"RESET"` to confirm wiping invoices/products/records.
- Fix the Windows SQLite **backup file-locking** bug (`WinError 32`): use the `sqlite3` online
  backup API / WAL checkpoint instead of unlinking a locked live DB, with proper logging.

## 8. Robustness — WebEngine / PDF
- Guard `from PyQt5.QtWebEngineWidgets import QWebEngineView` with try/except so missing DLLs
  never crash the app; fall back to `QDesktopServices.openUrl()` / `os.startfile()`.
- Later: **remove the PyQtWebEngine dependency entirely** — generate the PDF/HTML and open it
  in the system's default viewer; optional in-app preview via `QTextBrowser`.

---

*Sessions consolidated: 26 Aug (packaging, typography, reset, backup), 27 Aug (plug-and-play,
PySide6→PyQt5), 29 Aug (theme + per-product discount overhaul), 31 Aug (compact billing tables +
working rules), 1–3 Sep (multi-bill, high-density tables, smooth scroll, per-unit discount,
invoice viewer, wrapping), 4 Sep (deletion features), 7 Sep (keyboard loop, ToggleSwitch, polish),
9 Sep (double-numbering, cart perf, Clear All, search highlight, WebEngine removal), 10 Sep
(design-mockup overhaul, click-anywhere toggles).*
