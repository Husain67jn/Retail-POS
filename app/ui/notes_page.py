from __future__ import annotations

import csv
import json
from pathlib import Path

from PyQt5.QtCore import QTimer, QUrl, Qt
from PyQt5.QtGui import QDesktopServices, QImage, QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config.paths import writable_dirs
from app.ui.widgets import Card

_AUTOSAVE_DELAY_MS = 800
_ALLOWED_DOC_EXTENSIONS = (".xlsx", ".csv", ".pdf", ".png", ".jpg", ".jpeg")
_DOC_FILTER = "Spreadsheets, PDFs and Images (*.xlsx *.csv *.pdf *.png *.jpg *.jpeg)"
# A CSV/XLSX file with no realistic reason to be larger than this in a POS
# document vault is still capped, so a huge file can never hang the UI
# thread loading it into a QTableWidget with no pagination.
_PREVIEW_ROW_LIMIT = 2000

# Render resolution for the in-app PDF preview. PyMuPDF's default is 72 DPI
# (1.0 zoom == 1 PDF point == 1 px), which looks soft on modern screens, so
# pages are rasterized at 2x for a crisper preview without over-taxing memory
# on long documents (pages are rendered one at a time, not all up front).
_PDF_PREVIEW_ZOOM = 2.0

# Notes & Docs styling, aligned to the app's slate/blue dark design system
# (see app/resources/theme.qss) instead of the generic grays this page used
# before. The two sections are the shared app Card (rounded #1E293B surface +
# a soft drop shadow for luxury depth); everything here refines what sits
# *inside* those cards: a premium inset writing surface, a quiet document
# list, and a restrained three-tier button hierarchy — one accent CTA
# (Attach), a ghost secondary (Preview), and a destructive action (Remove)
# that only reveals its red on hover so it never shouts on a calm page.
_NOTES_QSS = """
QLabel#notesCardTitle {
    font-size: 15px;
    font-weight: 800;
    color: #F8FAFC;
    letter-spacing: 0.3px;
}
QLabel#notesCardHint {
    color: #64748B;
    font-size: 12px;
}
QLabel#notesStatus {
    color: #64748B;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
QLabel#notesStatus[state="saving"] { color: #94A3B8; }
QLabel#notesStatus[state="saved"]  { color: #4ADE80; }
QLabel#notesStatus[state="error"]  { color: #F87171; }

QTextEdit#quickNotepad {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 12px;
    padding: 16px 18px;
    font-size: 15px;
    color: #E2E8F0;
    selection-background-color: #2563EB;
    selection-color: #F8FAFC;
}
QTextEdit#quickNotepad:focus {
    border: 1px solid #2563EB;
}

QListWidget#documentVaultList {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 12px;
    padding: 6px;
    outline: none;
}
QListWidget#documentVaultList::item {
    padding: 12px 14px;
    margin: 2px;
    border-radius: 8px;
    color: #E2E8F0;
}
QListWidget#documentVaultList::item:hover {
    background: #1E293B;
}
QListWidget#documentVaultList::item:selected {
    background: #2563EB;
    color: #F8FAFC;
}

QPushButton#notesPrimaryButton {
    background: #2563EB;
    color: #F8FAFC;
    border: 1px solid #2563EB;
    border-radius: 9px;
    padding: 8px 18px;
    font-weight: 700;
}
QPushButton#notesPrimaryButton:hover   { background: #1D4ED8; border-color: #1D4ED8; }
QPushButton#notesPrimaryButton:pressed { background: #1E40AF; border-color: #1E40AF; }

QPushButton#notesGhostButton {
    background: transparent;
    color: #CBD5E1;
    border: 1px solid #334155;
    border-radius: 9px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton#notesGhostButton:hover    { background: #1E293B; border-color: #475569; color: #F8FAFC; }
QPushButton#notesGhostButton:pressed  { background: #162032; }
QPushButton#notesGhostButton:disabled { color: #475569; border-color: #1E293B; background: transparent; }

QPushButton#notesDangerButton {
    background: transparent;
    color: #94A3B8;
    border: 1px solid #334155;
    border-radius: 9px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton#notesDangerButton:hover    { background: #2A1315; color: #F87171; border-color: #7F1D1D; }
QPushButton#notesDangerButton:pressed  { background: #1F0E10; }
QPushButton#notesDangerButton:disabled { color: #475569; border-color: #1E293B; background: transparent; }
"""


class DocumentPreviewDialog(QDialog):
    """In-app preview for a Document Vault attachment.

    Images and CSV are previewed with plain PyQt5 + the stdlib csv module —
    no extra dependency. XLSX previews use the optional ``openpyxl`` package,
    guarded so a machine without it still opens the dialog (with an
    explanation and a working "Open Externally" fallback) rather than the app
    failing over a missing import.

    PDFs are rendered in-app with the optional ``PyMuPDF`` (``fitz``)
    package, page by page, so a PDF never has to be handed off to
    QDesktopServices/the OS file association — which is what let a PDF
    silently open in a web browser on machines where the OS's default PDF
    handler is a browser rather than a native reader. This intentionally does
    NOT use PyQt5.QtPdf/QtPdfWidgets: those Qt-native PDF widgets don't exist
    in PyQt5 (Qt5) at all — they were added in the Qt6 bindings (PyQt6 /
    PySide6) — so PyMuPDF is the lightweight option that actually works on
    this project's PyQt5 stack. As with the XLSX branch above, a machine
    without PyMuPDF installed still gets a working dialog: an explanation
    plus the "Open Externally" button, rather than a failure.
    """

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(path.name)
        self.resize(820, 640)
        # Only populated by _build_pdf_preview; kept as an attribute (rather
        # than a local) so the page-nav spinbox handler can re-render without
        # reopening the file, and so it can be closed explicitly on dialog
        # close instead of waiting on garbage collection to release the file.
        self._pdf_doc = None
        layout = QVBoxLayout(self)

        suffix = path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg"):
            self._build_image_preview(layout)
        elif suffix == ".csv":
            self._build_csv_preview(layout)
        elif suffix == ".xlsx":
            self._build_xlsx_preview(layout)
        elif suffix == ".pdf":
            self._build_pdf_preview(layout)
        else:
            layout.addWidget(QLabel("There is no in-app preview available for this file type."))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_external = buttons.addButton("Open Externally", QDialogButtonBox.ButtonRole.ActionRole)
        open_external.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path))))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_image_preview(self, layout):
        pixmap = QPixmap(str(self.path))
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if pixmap.isNull():
            label.setText("Could not load this image.")
        else:
            label.setPixmap(pixmap)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(label)
        layout.addWidget(scroll, 1)

    def _build_csv_preview(self, layout):
        try:
            with open(self.path, newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
        except (OSError, csv.Error) as exc:
            layout.addWidget(QLabel(f"Could not read this file: {exc}"))
            return
        self._fill_table(layout, rows)

    def _build_xlsx_preview(self, layout):
        try:
            import openpyxl
        except ImportError:
            layout.addWidget(QLabel(
                "Previewing .xlsx files in-app needs the optional 'openpyxl' "
                "package, which isn't installed.\n\nInstall it with:\n"
                "pip install openpyxl\n\nUntil then, use \"Open Externally\" below."
            ))
            return
        try:
            workbook = openpyxl.load_workbook(str(self.path), read_only=True, data_only=True)
            sheet = workbook.active
            rows = [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(max_row=_PREVIEW_ROW_LIMIT + 1, values_only=True)
            ]
            workbook.close()
        except Exception as exc:
            layout.addWidget(QLabel(f"Could not read this file: {exc}"))
            return
        self._fill_table(layout, rows)

    def _build_pdf_preview(self, layout):
        try:
            import fitz  # PyMuPDF
        except ImportError:
            layout.addWidget(QLabel(
                "Previewing .pdf files in-app needs the optional 'PyMuPDF' "
                "package, which isn't installed.\n\nInstall it with:\n"
                "pip install PyMuPDF\n\nUntil then, use \"Open Externally\" below."
            ))
            return
        try:
            self._pdf_doc = fitz.open(str(self.path))
            page_count = self._pdf_doc.page_count
        except Exception as exc:
            layout.addWidget(QLabel(f"Could not read this file: {exc}"))
            return
        if page_count == 0:
            layout.addWidget(QLabel("This PDF has no pages to show."))
            return

        nav = QHBoxLayout()
        self._pdf_prev_button = QPushButton("< Prev")
        self._pdf_next_button = QPushButton("Next >")
        self._pdf_page_spin = QSpinBox()
        self._pdf_page_spin.setRange(1, page_count)
        self._pdf_page_count_label = QLabel(f"/ {page_count}")
        nav.addWidget(self._pdf_prev_button)
        nav.addWidget(self._pdf_page_spin)
        nav.addWidget(self._pdf_page_count_label)
        nav.addWidget(self._pdf_next_button)
        nav.addStretch()
        layout.addLayout(nav)

        self._pdf_page_label = QLabel()
        self._pdf_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._pdf_page_label)
        layout.addWidget(scroll, 1)

        self._pdf_prev_button.clicked.connect(lambda: self._pdf_page_spin.setValue(self._pdf_page_spin.value() - 1))
        self._pdf_next_button.clicked.connect(lambda: self._pdf_page_spin.setValue(self._pdf_page_spin.value() + 1))
        self._pdf_page_spin.valueChanged.connect(self._render_pdf_page)
        self._render_pdf_page(1)

    def _render_pdf_page(self, page_number: int):
        """Rasterize one page of the open PyMuPDF document to a QPixmap.

        Rendered one page at a time (not the whole document up front) so a
        long PDF doesn't spike memory just to preview it.
        """
        if self._pdf_doc is None:
            return
        self._pdf_prev_button.setEnabled(page_number > 1)
        self._pdf_next_button.setEnabled(page_number < self._pdf_doc.page_count)
        try:
            import fitz  # already confirmed importable in _build_pdf_preview
            page = self._pdf_doc[page_number - 1]
            matrix = fitz.Matrix(_PDF_PREVIEW_ZOOM, _PDF_PREVIEW_ZOOM)
            pix = page.get_pixmap(matrix=matrix)
            image_format = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, image_format)
            self._pdf_page_label.setPixmap(QPixmap.fromImage(image.copy()))
        except Exception as exc:
            self._pdf_page_label.setText(f"Could not render page {page_number}: {exc}")

    def closeEvent(self, event):
        if self._pdf_doc is not None:
            self._pdf_doc.close()
            self._pdf_doc = None
        super().closeEvent(event)

    def _fill_table(self, layout, rows):
        if not rows:
            layout.addWidget(QLabel("This file has no rows to show."))
            return
        truncated = len(rows) > _PREVIEW_ROW_LIMIT + 1
        body_rows = rows[1:_PREVIEW_ROW_LIMIT + 1]
        table = QTableWidget()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        column_count = max((len(r) for r in rows), default=0)
        table.setColumnCount(column_count)
        table.setHorizontalHeaderLabels([str(v) for v in rows[0]] + [""] * (column_count - len(rows[0])))
        table.setRowCount(len(body_rows))
        for r, row in enumerate(body_rows):
            for c in range(column_count):
                value = row[c] if c < len(row) else ""
                table.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)
        if truncated:
            note = QLabel(f"Showing the first {_PREVIEW_ROW_LIMIT:,} rows.")
            note.setObjectName("muted")
            layout.addWidget(note)


class NotesPage(QWidget):
    """Notes & Docs: a quick auto-saving notepad plus a document vault for
    referencing local spreadsheets/PDFs/images, previewed in-app (see
    DocumentPreviewDialog) rather than only via the OS default viewer.

    Notes and the vault index are stored under the app's own writable data
    directory (see app.config.paths.writable_dirs) rather than a relative
    path, since this is a packaged desktop app whose working directory at
    launch isn't guaranteed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        data_dir = writable_dirs()["data"]
        self._notes_path = data_dir / "notes.txt"
        self._vault_path = data_dir / "notes_vault.json"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(18)
        self.setStyleSheet(_NOTES_QSS)

        # -- Page header: title + a calm one-line subtitle, matching the rest
        # of the app's page chrome (QLabel#pageTitle / #muted).
        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("Notes & Docs")
        title.setObjectName("pageTitle")
        subtitle = QLabel("A quiet place for shop notes and the reference files you reach for most.")
        subtitle.setObjectName("muted")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        # -- Split body: notes on the left (70%) and the document vault on the
        # right (30%), side by side, both filling the full height.
        body = QHBoxLayout()
        body.setSpacing(18)

        # -- Quick Notepad card: an elevated surface holding a premium inset
        # writing area, with a live save indicator sitting in the header row.
        notepad_card = Card()
        notepad_header = QHBoxLayout()
        notepad_title = QLabel("Quick Notepad")
        notepad_title.setObjectName("notesCardTitle")
        self.notes_status = QLabel("")
        self.notes_status.setObjectName("notesStatus")
        notepad_header.addWidget(notepad_title)
        notepad_header.addStretch()
        notepad_header.addWidget(self.notes_status)
        notepad_card.add_layout(notepad_header)
        self.editor = QTextEdit()
        self.editor.setObjectName("quickNotepad")
        self.editor.setPlaceholderText("Start typing — your notes save themselves.")
        notepad_card.add(self.editor, 1)
        body.addWidget(notepad_card, 7)

        # -- Document Vault card: a narrower right-hand column. Because 30% is
        # too tight to keep the CTA beside the title, the title sits alone on
        # top, the accent "Attach" button spans the full width beneath the
        # list, and the quieter Preview / Remove actions share the row below.
        vault_card = Card()
        vault_title = QLabel("Document Vault")
        vault_title.setObjectName("notesCardTitle")
        vault_card.add(vault_title)

        vault_hint = QLabel("Spreadsheets, PDFs and images — previewed right inside the app.")
        vault_hint.setObjectName("notesCardHint")
        vault_hint.setWordWrap(True)
        vault_card.add(vault_hint)

        self.vault_list = QListWidget()
        self.vault_list.setObjectName("documentVaultList")
        self.vault_list.setCursor(Qt.CursorShape.PointingHandCursor)
        # A floor so the vault always shows a few rows deliberately; the list
        # still grows to fill a taller window.
        self.vault_list.setMinimumHeight(160)
        vault_card.add(self.vault_list, 1)

        self.attach_button = QPushButton("Attach Document…")
        self.attach_button.setObjectName("notesPrimaryButton")
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        vault_card.add(self.attach_button)

        vault_actions = QHBoxLayout()
        vault_actions.setSpacing(10)
        self.open_button = QPushButton("Preview")
        self.open_button.setObjectName("notesGhostButton")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("notesDangerButton")
        self.remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        vault_actions.addWidget(self.open_button, 1)
        vault_actions.addWidget(self.remove_button, 1)
        vault_card.add_layout(vault_actions)
        body.addWidget(vault_card, 3)

        root.addLayout(body, 1)

        # Debounced auto-save: writes land ~800ms after the cashier stops
        # typing, rather than on every keystroke.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(_AUTOSAVE_DELAY_MS)
        self._autosave_timer.timeout.connect(self._save_notes)
        self.editor.textChanged.connect(self._schedule_autosave)

        self.attach_button.clicked.connect(self._attach_document)
        self.open_button.clicked.connect(self._open_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.vault_list.itemDoubleClicked.connect(lambda _: self._open_selected())

        self._load_notes()
        self._load_vault()

    # -- Notepad -----------------------------------------------------

    def _set_status(self, text: str, state: str = ""):
        """Update the notepad's save indicator and recolour it by state.

        The colour comes from a dynamic ``state`` property matched in
        _NOTES_QSS ("saving" / "saved" / "error"); Qt only restyles on a
        property change after an explicit unpolish/polish, so do that here.
        """
        self.notes_status.setText(text)
        self.notes_status.setProperty("state", state)
        self.notes_status.style().unpolish(self.notes_status)
        self.notes_status.style().polish(self.notes_status)

    def _schedule_autosave(self):
        self._set_status("Saving…", "saving")
        self._autosave_timer.start()

    def _load_notes(self):
        try:
            if self._notes_path.exists():
                self.editor.blockSignals(True)
                self.editor.setPlainText(self._notes_path.read_text(encoding="utf-8"))
                self.editor.blockSignals(False)
                self._set_status("")
        except OSError as exc:
            QMessageBox.warning(self, "Could not load notes", str(exc))

    def _save_notes(self):
        try:
            self._notes_path.write_text(self.editor.toPlainText(), encoding="utf-8")
            self._set_status("✓  Saved", "saved")
        except OSError as exc:
            self._set_status("Save failed", "error")
            QMessageBox.warning(self, "Could not save notes", str(exc))

    # -- Document vault ------------------------------------------------

    def _load_vault(self):
        self.vault_list.clear()
        try:
            if self._vault_path.exists():
                paths = json.loads(self._vault_path.read_text(encoding="utf-8"))
            else:
                paths = []
        except (OSError, ValueError):
            paths = []
        for raw_path in paths:
            self._add_vault_item(raw_path, save=False)

    def _save_vault(self):
        paths = [
            self.vault_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.vault_list.count())
        ]
        try:
            self._vault_path.write_text(json.dumps(paths), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Could not save document vault", str(exc))

    def _add_vault_item(self, raw_path: str, save: bool = True):
        path = Path(raw_path)
        label = path.name or raw_path
        if not path.exists():
            label += "  (file not found)"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, raw_path)
        item.setToolTip(raw_path)
        self.vault_list.addItem(item)
        if save:
            self._save_vault()

    def _attach_document(self):
        path, _ = QFileDialog.getOpenFileName(self, "Attach Document", "", _DOC_FILTER)
        if not path:
            return
        if Path(path).suffix.lower() not in _ALLOWED_DOC_EXTENSIONS:
            QMessageBox.warning(
                self, "Unsupported file type",
                "Only .xlsx, .csv, .pdf, .png, and .jpg/.jpeg files can be attached to the Document Vault.",
            )
            return
        self._add_vault_item(path)

    def _selected_vault_path(self):
        item = self.vault_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _open_selected(self):
        raw_path = self._selected_vault_path()
        if raw_path is None:
            return
        path = Path(raw_path)
        if not path.exists():
            QMessageBox.warning(
                self, "File not found",
                f"{raw_path}\n\nThis file no longer exists at its saved location.",
            )
            return
        # Every supported type — including PDFs — goes through the in-app
        # preview dialog now. PDFs used to be handed straight to the OS via
        # QDesktopServices, which meant a machine with a browser set as the
        # default PDF handler would silently pop the receipt/attachment open
        # in a browser tab instead of a document viewer. Rendering PDFs
        # in-app (see DocumentPreviewDialog._build_pdf_preview) removes the
        # OS file-association lookup from this path entirely; "Open
        # Externally" inside the dialog remains available for anyone who
        # still wants their system's own PDF app.
        DocumentPreviewDialog(path, self).exec_()

    def _open_externally(self, path: Path):
        """Open ``path`` in the OS default application for its type.

        Used for PDFs (the system PDF viewer). Reports a clear message if the
        OS has nothing registered to open the file rather than failing
        silently.
        """
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self, "Could not open file",
                f"{path}\n\nNo application is registered to open this file, "
                "or the system viewer could not be launched.",
            )

    def _remove_selected(self):
        row = self.vault_list.currentRow()
        if row < 0:
            return
        self.vault_list.takeItem(row)
        self._save_vault()

    def refresh(self):
        self._load_notes()
        self._load_vault()
