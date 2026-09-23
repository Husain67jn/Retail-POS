from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.backup_restore_service import BackupRestoreError, BackupRestoreService
from app.services.settings_service import SettingsData, SettingsService, TYPOGRAPHY_FONTS, TYPOGRAPHY_SIZES
from app.ui.widgets import enable_smooth_scrolling


class SettingsPage(QWidget):
    settings_saved = pyqtSignal()
    data_cleared = pyqtSignal()

    def __init__(self, service: SettingsService, backup_service: BackupRestoreService | None = None, parent=None):
        super().__init__(parent)
        self.service = service
        self.backup_service = backup_service
        self._build_ui()
        self.load_settings()

    def _build_ui(self) -> None:
        # The page itself only hosts a scroll area (Known Issue: Settings
        # content overflowing/overlapping on shorter windows). Every group
        # box below lives inside the scrollable inner widget so the page
        # scrolls as one column instead of squeezing or clipping content.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        enable_smooth_scrolling(scroll)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("settingsScrollContent")
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 28, 24)
        layout.setSpacing(14)

        self.error_label = QLabel()
        self.error_label.setObjectName("validationError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        shop_box = QGroupBox("Shop Information")
        shop_form = QFormLayout(shop_box)
        self.shop_name = QLineEdit()
        self.shop_address = QLineEdit()
        self.shop_phone = QLineEdit()
        shop_form.addRow("Shop name *", self.shop_name)
        shop_form.addRow("Address", self.shop_address)
        shop_form.addRow("Phone", self.shop_phone)
        layout.addWidget(shop_box)

        invoice_box = QGroupBox("Invoice")
        invoice_form = QFormLayout(invoice_box)
        self.currency_code = QLineEdit()
        self.currency_code.setMaxLength(3)
        self.currency_symbol = QLineEdit()
        self.currency_symbol.setMaxLength(8)
        self.invoice_prefix = QLineEdit()
        self.invoice_prefix.setMaxLength(20)
        self.invoice_footer = QPlainTextEdit()
        self.invoice_footer.setMaximumHeight(90)
        self.invoice_format = QComboBox()
        self.invoice_format.addItems(["A4", "Thermal"])
        invoice_form.addRow("Currency code *", self.currency_code)
        invoice_form.addRow("Currency symbol *", self.currency_symbol)
        invoice_form.addRow("Invoice prefix *", self.invoice_prefix)
        invoice_form.addRow("Invoice footer", self.invoice_footer)
        invoice_form.addRow("Default format", self.invoice_format)
        layout.addWidget(invoice_box)

        typography_box = QGroupBox("Typography")
        typography_form = QFormLayout(typography_box)
        self.typography_font = QComboBox()
        self.typography_font.addItems(TYPOGRAPHY_FONTS)
        self.typography_size = QComboBox()
        self.typography_size.addItems(TYPOGRAPHY_SIZES)
        typography_form.addRow("Font", self.typography_font)
        typography_form.addRow("Size", self.typography_size)
        layout.addWidget(typography_box)

        receipt_box = QGroupBox("Receipt")
        receipt_form = QFormLayout(receipt_box)
        self.thermal_width = QComboBox()
        self.thermal_width.addItems(["180mm"])
        receipt_form.addRow("Thermal paper width", self.thermal_width)
        layout.addWidget(receipt_box)

        save = QPushButton("Save Settings")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save_settings)
        layout.addWidget(save)

        danger_box = QGroupBox("Danger Zone")
        danger_layout = QVBoxLayout(danger_box)
        danger_info = QLabel(
            "Permanently deletes all invoices, product catalogs, and customer "
            "records. Shop information and settings are preserved. This cannot be "
            "undone — a backup from today is required before it will run."
        )
        danger_info.setWordWrap(True)
        danger_info.setObjectName("muted")
        danger_layout.addWidget(danger_info)
        self.clear_data_button = QPushButton("Clear All Data (Factory Reset)")
        self.clear_data_button.setObjectName("destructiveButton")
        self.clear_data_button.clicked.connect(self.clear_all_data)
        danger_layout.addWidget(self.clear_data_button)
        layout.addWidget(danger_box)

        layout.addStretch()

    def load_settings(self) -> None:
        settings = self.service.get_settings()
        self.shop_name.setText(settings.shop_name)
        self.shop_address.setText(settings.shop_address)
        self.shop_phone.setText(settings.shop_phone)
        self.currency_code.setText(settings.currency_code)
        self.currency_symbol.setText(settings.currency_symbol)
        self.invoice_prefix.setText(settings.invoice_prefix)
        self.invoice_footer.setPlainText(settings.invoice_footer)
        self.invoice_format.setCurrentText(settings.default_invoice_format)
        self.thermal_width.setCurrentText(settings.thermal_paper_width)
        self.typography_font.setCurrentText(settings.typography_font)
        self.typography_size.setCurrentText(settings.typography_size)
        self._legacy_low_stock_highlighting = settings.low_stock_highlighting
        self._legacy_allow_negative_stock = settings.allow_negative_stock
        self.error_label.hide()

    def _form_settings(self) -> SettingsData:
        return SettingsData(
            shop_name=self.shop_name.text(),
            shop_address=self.shop_address.text(),
            shop_phone=self.shop_phone.text(),
            invoice_footer=self.invoice_footer.toPlainText(),
            currency_code=self.currency_code.text(),
            currency_symbol=self.currency_symbol.text(),
            invoice_prefix=self.invoice_prefix.text(),
            default_invoice_format=self.invoice_format.currentText(),
            thermal_paper_width=self.thermal_width.currentText(),
            low_stock_highlighting=self._legacy_low_stock_highlighting,
            allow_negative_stock=self._legacy_allow_negative_stock,
            typography_font=self.typography_font.currentText(),
            typography_size=self.typography_size.currentText(),
        )

    def save_settings(self) -> None:
        try:
            self.service.save_settings(self._form_settings())
        except ValueError as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        self.error_label.hide()
        QMessageBox.information(self, "Settings saved", "Settings were saved successfully.")
        self.settings_saved.emit()

    def clear_all_data(self) -> None:
        if self.backup_service is None:
            QMessageBox.critical(
                self,
                "Clear data unavailable",
                "Data clearing is not available because the backup service is not connected.",
            )
            return

        # Safety Lock 1 — a backup must already exist for today (backupDate ==
        # currentDate). If not, block and send the operator to make one first.
        if not self.backup_service.backup_taken_today():
            QMessageBox.warning(
                self,
                "Backup required",
                "No backup has been created today.\n\n"
                "Open the Backup page and create a backup first, then try again. "
                "Clearing data is blocked until today's backup exists.",
            )
            return

        # Safety Lock 2 — require the operator to type RESET verbatim.
        typed, ok = QInputDialog.getText(
            self,
            "Confirm factory reset",
            "This permanently deletes all invoices, product catalogs, and customer "
            "records. Shop information and settings are preserved.\n\n"
            'Type "RESET" to confirm:',
        )
        if not ok:
            return
        if typed.strip() != "RESET":
            QMessageBox.information(
                self,
                "Reset cancelled",
                'Data was not cleared. You must type "RESET" exactly to confirm.',
            )
            return

        try:
            # The service re-checks today's backup as a hard safeguard before
            # wiping, so the two locks cannot be bypassed by UI state alone.
            self.backup_service.clear_all_data()
        except BackupRestoreError as exc:
            QMessageBox.critical(self, "Clear data failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Data cleared",
            "All invoices, product catalogs, and customer records were cleared. "
            "Shop information and settings were preserved.",
        )
        self.data_cleared.emit()
