from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy.exc import SQLAlchemyError

from app.config.paths import writable_dirs
from app.database.base import Base

logger = logging.getLogger(__name__)


class BackupRestoreError(RuntimeError):
    """Raised when a RetailPOS backup or restore operation cannot be completed."""


class BackupRestoreService:
    """Safe SQLite backup/restore operations for the existing RetailPOS database."""

    def __init__(self, db, backup_dir: Path | None = None, now_provider=None):
        self.db = db
        self.backup_dir = Path(backup_dir) if backup_dir else writable_dirs()["backups"]
        self._now_provider = now_provider or datetime.now

    @staticmethod
    def _sidecars(path: Path) -> Iterable[Path]:
        yield Path(f"{path}-wal")
        yield Path(f"{path}-shm")

    @classmethod
    def _remove_sidecars(cls, path: Path) -> None:
        for sidecar in cls._sidecars(path):
            try:
                sidecar.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        try:
            return sqlite3.connect(str(path), timeout=5)
        except (sqlite3.Error, OSError) as exc:
            raise BackupRestoreError(f"Could not open SQLite database: {exc}") from exc

    @classmethod
    def validate_sqlite(cls, path: Path) -> None:
        path = Path(path)
        if not path.is_file():
            raise BackupRestoreError("The selected backup file does not exist.")
        try:
            with closing(cls._connect(path)) as conn:
                header = conn.execute("PRAGMA schema_version").fetchone()
                if header is None:
                    raise BackupRestoreError("The selected file is not a valid SQLite database.")
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if not result or str(result[0]).lower() != "ok":
                    detail = result[0] if result else "unknown integrity error"
                    raise BackupRestoreError(f"SQLite integrity check failed: {detail}")
        except BackupRestoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise BackupRestoreError("The selected file is not a valid, readable SQLite database.") from exc

    @classmethod
    def _required_schema(cls) -> dict[str, set[str]]:
        # Build requirements from the actual current SQLAlchemy models rather
        # than maintaining a copied list of historical Phase 0-3 schemas.
        from app.database.models import (  # noqa: F401
            Category,
            Customer,
            CustomerLedgerEntry,
            Invoice,
            InvoiceItem,
            Product,
            Setting,
            StockMovement,
        )

        return {
            table.name: {column.name for column in table.columns}
            for table in Base.metadata.sorted_tables
        }

    @classmethod
    def validate_compatible_schema(cls, path: Path) -> None:
        """Confirm a backup file is a genuine, restorable RetailPOS database.

        Missing *tables* are still fatal: every table this app knows about is
        additive and never dropped, so an entirely absent table is a strong
        signal the file isn't a RetailPOS backup at all (or is corrupted).

        Missing *columns*, however, are expected and allowed: every schema
        change this app has ever made is a purely additive
        ``ALTER TABLE ... ADD COLUMN`` (see DatabaseManager.initialize),
        which restore() already runs immediately after swapping the
        restored file into place. Rejecting an otherwise-valid older backup
        just because it predates a newly added column (e.g. the "voided"
        columns added for Edit Invoice) would block a legitimate restore
        that the app is fully able to upgrade in place.
        """
        cls.validate_sqlite(path)
        required = cls._required_schema()
        try:
            with closing(cls._connect(Path(path))) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                missing_tables = sorted(set(required) - tables)
                if missing_tables:
                    raise BackupRestoreError(
                        "The selected backup is not a compatible RetailPOS database. "
                        f"Missing table(s): {', '.join(missing_tables)}."
                    )
                for table, columns in required.items():
                    actual = {
                        row[1]
                        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    }
                    missing_columns = sorted(columns - actual)
                    if missing_columns:
                        # Older backup, newer app: these columns will be added
                        # automatically by DatabaseManager.initialize() right
                        # after restore, so this is logged for visibility only
                        # and never blocks the restore.
                        logger.info(
                            "Backup %s predates column(s) %s on table '%s'; "
                            "these will be added automatically after restore.",
                            path, ", ".join(missing_columns), table,
                        )
        except BackupRestoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise BackupRestoreError("Could not inspect the selected RetailPOS database schema.") from exc

    @staticmethod
    def _timestamped_name(prefix: str, now: datetime, suffix: str = ".db") -> str:
        return f"{prefix}_{now:%Y%m%d_%H%M%S}{suffix}"

    def _unique_path(self, directory: Path, filename: str) -> Path:
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        index = 1
        while True:
            candidate = directory / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _backup_live_database(self, destination: Path) -> Path:
        live = Path(self.db.db_path)
        if not live.exists():
            raise BackupRestoreError("The live RetailPOS database does not exist.")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise BackupRestoreError("A file already exists at the selected backup location.")

        temp = destination.with_name(f".{destination.name}.tmp")
        try:
            self._remove_sidecars(temp)
            if temp.exists():
                temp.unlink()
            # SQLite's Online Backup API copies a consistent snapshot of the
            # live database without needing exclusive file access. Every
            # connection is wrapped in contextlib.closing so it is fully closed
            # (a plain ``with sqlite3.connect(...)`` only ends the transaction,
            # leaving the handle open) before any file is replaced or unlinked.
            # That open handle was the cause of Windows PermissionError
            # [WinError 32] during backup.
            with closing(sqlite3.connect(str(live), timeout=5)) as source, \
                    closing(sqlite3.connect(str(temp), timeout=5)) as target:
                source.backup(target)
                target.commit()
            # The standalone backup file needs no WAL/SHM sidecars; removing
            # them also releases any last handle before os.replace on Windows.
            self._remove_sidecars(temp)
            self.validate_sqlite(temp)
            os.replace(temp, destination)
            return destination
        except (sqlite3.Error, OSError, BackupRestoreError) as exc:
            logger.exception("Database backup failed while writing %s", destination)
            self._remove_sidecars(temp)
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            if isinstance(exc, BackupRestoreError):
                raise
            raise BackupRestoreError(f"Database backup failed: {exc}") from exc

    def create_backup(self, destination: Path | None = None) -> Path:
        if destination is None:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            destination = self._unique_path(
                self.backup_dir,
                self._timestamped_name("RetailPOS_Backup", self._now_provider()),
            )
        else:
            destination = Path(destination)
        result = self._backup_live_database(destination)
        self.validate_sqlite(result)
        return result

    def create_pre_restore_backup(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        destination = self._unique_path(
            self.backup_dir,
            self._timestamped_name("RetailPOS_PreRestore", self._now_provider()),
        )
        return self.create_backup(destination)

    _DATE_IN_NAME = re.compile(r"(\d{8})")

    def backup_taken_today(self) -> bool:
        """True if at least one backup file in the backup directory is dated today.

        The check reads the filesystem only — it never writes to the database —
        so it cannot alter table counts or otherwise disturb existing data. The
        date is read from the ``YYYYMMDD`` stamp embedded in each backup name
        (see ``_timestamped_name``), falling back to the file's modification
        time when a name has no parseable stamp.
        """
        today = self._now_provider().date()
        try:
            candidates = list(self.backup_dir.glob("*.db"))
        except OSError:
            return False
        for path in candidates:
            match = self._DATE_IN_NAME.search(path.name)
            if match:
                try:
                    if datetime.strptime(match.group(1), "%Y%m%d").date() == today:
                        return True
                    continue
                except ValueError:
                    pass
            try:
                if datetime.fromtimestamp(path.stat().st_mtime).date() == today:
                    return True
            except OSError:
                continue
        return False

    # Child-before-parent order that respects every foreign key (RESTRICT keys
    # on the ledger/invoices must be cleared before their parents).
    _WIPE_ORDER = (
        "CustomerLedgerEntry",
        "InvoiceItem",
        "Invoice",
        "StockMovement",
        "Product",
        "Category",
        "Customer",
    )

    def clear_all_data(self, require_backup_today: bool = True) -> None:
        """Factory reset: wipe invoices, products, and records after a safe backup.

        As a hard safeguard the reset refuses to run unless a backup was already
        taken today, so a fresh copy of the data always exists before it is
        destroyed. Settings (including shop info and typography) are preserved;
        only the invoice sequence is reset to zero.
        """
        if require_backup_today and not self.backup_taken_today():
            raise BackupRestoreError(
                "A backup from today is required before clearing data. "
                "Create a fresh backup first."
            )
        from app.database.models import (  # noqa: F401
            Category,
            Customer,
            CustomerLedgerEntry,
            Invoice,
            InvoiceItem,
            Product,
            Setting,
            StockMovement,
        )

        models = {
            "CustomerLedgerEntry": CustomerLedgerEntry,
            "InvoiceItem": InvoiceItem,
            "Invoice": Invoice,
            "StockMovement": StockMovement,
            "Product": Product,
            "Category": Category,
            "Customer": Customer,
        }
        try:
            with self.db.SessionLocal.begin() as session:
                for name in self._WIPE_ORDER:
                    session.query(models[name]).delete(synchronize_session=False)
                seq = session.get(Setting, "invoice_sequence")
                if seq is None:
                    session.add(Setting(key="invoice_sequence", value="0"))
                else:
                    seq.value = "0"
        except SQLAlchemyError as exc:
            raise BackupRestoreError(f"Clearing data failed: {exc}") from exc

    def restore(self, selected_backup: Path) -> Path:
        selected_backup = Path(selected_backup)
        # Validate everything while the live database is untouched.
        self.validate_compatible_schema(selected_backup)

        safety_backup = self.create_pre_restore_backup()
        live = Path(self.db.db_path)
        live.parent.mkdir(parents=True, exist_ok=True)

        # Build an independent restored database through SQLite's Online Backup
        # API before touching the live database file.
        fd, temp_name = tempfile.mkstemp(prefix="retailpos_restore_", suffix=".db", dir=str(live.parent))
        os.close(fd)
        restored_temp = Path(temp_name)
        original_temp = live.with_name(f".{live.name}.pre_restore_original")
        try:
            with closing(sqlite3.connect(str(selected_backup), timeout=5)) as source, \
                    closing(sqlite3.connect(str(restored_temp), timeout=5)) as target:
                source.backup(target)
                target.commit()
            self._remove_sidecars(restored_temp)
            self.validate_compatible_schema(restored_temp)

            # No application session may keep a handle to the old database.
            self.db.dispose()
            self._remove_sidecars(live)
            if original_temp.exists():
                original_temp.unlink()
            os.replace(live, original_temp)
            try:
                os.replace(restored_temp, live)
                # Bring the restored file up to the current schema: this runs
                # the same additive ALTER TABLE migrations that live
                # databases get on every normal startup (see
                # DatabaseManager.initialize / connection.py), so an older
                # backup that was missing newer columns (e.g. "voided",
                # "voided_at") is seamlessly upgraded immediately after
                # restore rather than staying on its old schema.
                self.db.initialize()
                if not self.db.health_check():
                    raise BackupRestoreError("The restored database failed its health check.")
            except Exception as restore_exc:
                self.db.dispose()
                self._remove_sidecars(live)
                try:
                    if live.exists():
                        live.unlink()
                    os.replace(original_temp, live)
                    self.db.initialize()
                    if not self.db.health_check():
                        raise BackupRestoreError("The original database could not be recovered after restore failure.")
                except Exception as recovery_exc:
                    raise BackupRestoreError(
                        "Restore failed and automatic recovery of the original database also failed. "
                        f"The pre-restore safety backup remains available at: {safety_backup}"
                    ) from recovery_exc
                raise BackupRestoreError(
                    f"Restore failed. The original database was restored. Safety backup: {safety_backup}"
                ) from restore_exc
            finally:
                try:
                    original_temp.unlink()
                except FileNotFoundError:
                    pass
            return safety_backup
        except BackupRestoreError:
            raise
        except (sqlite3.Error, OSError) as exc:
            # If failure occurred before the live file was replaced, it remains untouched.
            raise BackupRestoreError(f"Restore failed safely: {exc}") from exc
        finally:
            try:
                restored_temp.unlink()
            except FileNotFoundError:
                pass
