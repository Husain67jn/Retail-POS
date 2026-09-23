from __future__ import annotations

from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.inventory_service import InventoryService
from app.services.product_service import UNIT_LABELS
from app.ui.widgets import enable_smooth_scrolling
from app.services.settings_service import SettingsService
from app.ui.responsive import size_to_screen
from app.utils.formatting import quantity


class StockChangeDialog(QDialog):
    def __init__(self, title: str, allow_negative: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.quantity = QLineEdit("0")
        self.quantity.setPlaceholderText("e.g. 5.5 or -2.0")
        self.note = QLabel("Use a clear reason for manual adjustments when needed.")
        form.addRow("Quantity change", self.quantity)
        form.addRow(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)


class MovementHistoryDialog(QDialog):
    ACTIONS = {
        "opening_stock": "Opening Stock",
        "stock_addition": "Stock Added",
        "manual_adjustment": "Manual Adjustment",
        "sale": "Stock Removed",
    }

    def __init__(self, product_service, inventory_service: InventoryService, parent=None):
        super().__init__(parent)
        self.product_service = product_service
        self.inventory_service = inventory_service
        self.setWindowTitle("Stock Movement History")
        size_to_screen(self, 0.72, 0.7, min_width=900, min_height=560)

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.product_filter = QComboBox()
        self.product_filter.addItem("All products", None)
        for product in self.product_service.list_products():
            self.product_filter.addItem(product.name, product.id)
        self.action_filter = QComboBox()
        self.action_filter.addItem("All actions", None)
        for key, label in self.ACTIONS.items():
            self.action_filter.addItem(label, key)
        self.date_filter = QLineEdit()
        self.date_filter.setPlaceholderText("Date: YYYY-MM-DD")
        clear = QPushButton("Clear Filters")
        filters.addWidget(QLabel("Product"))
        filters.addWidget(self.product_filter, 1)
        filters.addWidget(QLabel("Action"))
        filters.addWidget(self.action_filter, 1)
        filters.addWidget(QLabel("Date"))
        filters.addWidget(self.date_filter, 1)
        filters.addWidget(clear)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Date & Time",
                "Product",
                "Action",
                "Quantity",
                "Unit",
                "Previous Stock",
                "New Stock",
                "Reason / Reference",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        enable_smooth_scrolling(self.table)
        layout.addWidget(self.table, 1)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self.product_filter.currentIndexChanged.connect(self.refresh)
        self.action_filter.currentIndexChanged.connect(self.refresh)
        self.date_filter.textChanged.connect(self.refresh)
        clear.clicked.connect(self._clear_filters)
        self.refresh()

    def _clear_filters(self):
        self.product_filter.setCurrentIndex(0)
        self.action_filter.setCurrentIndex(0)
        self.date_filter.clear()

    @staticmethod
    def _history_rows(movements):
        current_by_product = {}
        rows = []
        for movement in movements:
            product = movement.product
            if product.id not in current_by_product:
                current_by_product[product.id] = Decimal(product.current_stock)
            new_stock = current_by_product[product.id]
            change = Decimal(movement.quantity_change)
            previous_stock = new_stock - change
            rows.append((movement, product, previous_stock, new_stock))
            current_by_product[product.id] = previous_stock
        return rows

    def refresh(self):
        try:
            date_text = self.date_filter.text().strip()
            if date_text:
                from datetime import datetime
                datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            self.table.setRowCount(1)
            self.table.setSpan(0, 0, 1, 8)
            self.table.setItem(0, 0, QTableWidgetItem("Invalid date. Use YYYY-MM-DD."))
            return

        product_id = self.product_filter.currentData()
        action = self.action_filter.currentData()
        date_text = self.date_filter.text().strip()
        movements = self.inventory_service.list_all_movements()
        rows = self._history_rows(movements)
        filtered = []
        for movement, product, previous_stock, new_stock in rows:
            if product_id is not None and product.id != product_id:
                continue
            if action is not None and movement.movement_type != action:
                continue
            if date_text and movement.created_at.strftime("%Y-%m-%d") != date_text:
                continue
            filtered.append((movement, product, previous_stock, new_stock))

        self.table.clearSpans()
        self.table.setRowCount(len(filtered))
        for row, (movement, product, previous_stock, new_stock) in enumerate(filtered):
            action_label = self.ACTIONS.get(movement.movement_type, movement.movement_type.replace("_", " ").title())
            change_text = quantity(movement.quantity_change)
            if Decimal(movement.quantity_change) > 0:
                change_text = "+" + change_text
            reason = " / ".join(x for x in (movement.note, movement.reference) if x) or "—"
            values = [
                movement.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                product.name,
                action_label,
                change_text,
                UNIT_LABELS.get(product.unit, product.unit),
                quantity(previous_stock),
                quantity(new_stock),
                reason,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()


class InventoryPage(QWidget):
    def __init__(self, product_service, inventory_service: InventoryService, settings_service: SettingsService | None = None, parent=None):
        super().__init__(parent)
        self.product_service = product_service
        self.inventory_service = inventory_service
        self.settings_service = settings_service
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Inventory")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        add = QPushButton("Add Stock")
        adjust = QPushButton("Adjust Stock")
        history = QPushButton("Movement History")
        header.addWidget(add)
        header.addWidget(adjust)
        header.addWidget(history)
        layout.addLayout(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Product", "Unit", "Current Stock", "Minimum", "Status", "Active"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        enable_smooth_scrolling(self.table)
        layout.addWidget(self.table)
        add.clicked.connect(self.add_stock)
        adjust.clicked.connect(self.adjust_stock)
        history.clicked.connect(self.show_history)
        self.refresh()

    def refresh(self):
        products = self.product_service.list_products()
        highlight_low_stock = bool(self.settings_service and self.settings_service.low_stock_highlighting_enabled())
        self.table.setRowCount(len(products))
        for row, product in enumerate(products):
            values = [
                product.name,
                UNIT_LABELS.get(product.unit, product.unit),
                quantity(product.current_stock),
                quantity(product.minimum_stock),
                "LOW" if self.product_service.is_low_stock(product) else "OK",
                "Active" if product.is_active else "Inactive",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if highlight_low_stock and self.product_service.is_low_stock(product) and col in (0, 4):
                    item.setBackground(Qt.GlobalColor.yellow)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, product.id)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def add_stock(self):
        product_id = self.selected_id()
        if product_id is None:
            QMessageBox.information(self, "Select product", "Select a product first.")
            return
        dialog = StockChangeDialog("Add Stock", False, self)
        if dialog.exec_() != QDialog.DialogCode.Accepted:
            return
        try:
            self.inventory_service.add_stock(product_id, dialog.quantity.text())
            self.refresh()
        except ValueError as exc:
            QMessageBox.warning(self, "Stock error", str(exc))

    def adjust_stock(self):
        product_id = self.selected_id()
        if product_id is None:
            QMessageBox.information(self, "Select product", "Select a product first.")
            return
        dialog = StockChangeDialog("Manual Stock Adjustment", True, self)
        if dialog.exec_() != QDialog.DialogCode.Accepted:
            return
        try:
            self.inventory_service.adjust_stock(product_id, dialog.quantity.text())
            self.refresh()
        except ValueError as exc:
            QMessageBox.warning(self, "Stock error", str(exc))

    def show_history(self):
        MovementHistoryDialog(self.product_service, self.inventory_service, self).exec_()
