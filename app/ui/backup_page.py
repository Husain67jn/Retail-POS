from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.backup_restore_service import BackupRestoreError, BackupRestoreService


class BackupPage(QWidget):
    restored = pyqtSignal()

    def __init__(self, service: BackupRestoreService, parent=None):
        super().__init__(parent)
        self.service = service
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        info = QLabel(
            "Create a complete copy of the Mughal Electric Store database, or restore from a previous backup. "
            "A safety backup of the current database is created automatically before every restore."
        )
        info.setWordWrap(True)
        # Was "mutedLabel", which theme.qss has no rule for, so this text
        # never actually picked up the intended muted styling — corrected
        # to the "muted" object name used everywhere else in the app.
        info.setObjectName("muted")
        layout.addWidget(info)

        box = QGroupBox("Database Safety")
        box_layout = QVBoxLayout(box)
        backup = QPushButton("Backup Database")
        restore = QPushButton("Restore Database")
        # Known Issue #11: both read as plain/secondary buttons today even
        # though Section 4's own button hierarchy lists them as PRIMARY.
        # The actual destructive step is the in-flow "replace the database"
        # confirmation below (QMessageBox.warning, defaulted to No) — this
        # button is what starts that safe, confirmable flow.
        backup.setObjectName("primaryButton")
        restore.setObjectName("primaryButton")
        backup.clicked.connect(self.create_backup)
        restore.clicked.connect(self.restore_database)
        box_layout.addWidget(backup)
        box_layout.addWidget(restore)
        layout.addWidget(box)
        layout.addStretch()

    def create_backup(self) -> None:
        default_name = self.service._timestamped_name("Mughal Electric Store_Backup", self.service._now_provider())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Mughal Electric Store Backup",
            str(self.service.backup_dir / default_name),
            "SQLite Database (*.db);;All Files (*)",
        )
        if not path:
            return
        destination = Path(path)
        if destination.exists():
            QMessageBox.warning(
                self,
                "Backup already exists",
                "A file with that name already exists. Choose a different filename.",
            )
            return
        try:
            result = self.service.create_backup(destination)
        except BackupRestoreError as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))
            return
        QMessageBox.information(self, "Backup complete", f"Backup created successfully:\n{result}")

    def restore_database(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Mughal Electric Store Backup",
            str(self.service.backup_dir),
            "SQLite Database (*.db);;All Files (*)",
        )
        if not path:
            return

        answer = QMessageBox.warning(
            self,
            "Confirm database restore",
            "Restoring a backup will replace the current database.\n\n"
            "A safety backup of the current database will be created first.\n\n"
            "Do you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            safety_backup = self.service.restore(Path(path))
        except BackupRestoreError as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))
            return

        QMessageBox.information(
            self,
            "Restore complete",
            "The database was restored successfully.\n\n"
            f"A safety backup of the previous database was saved at:\n{safety_backup}\n\n"
            "Mughal Electric Store will now refresh its open pages from the restored database.",
        )
        self.restored.emit()
