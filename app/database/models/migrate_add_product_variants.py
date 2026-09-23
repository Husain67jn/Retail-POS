"""
Standalone migration: add product-variant grouping to the `products` table.

What it does
------------
Adds a nullable `parent_id` column to `products` (self-referential FK ->
products.id) plus a supporting index. This is purely additive:

  * No table is dropped or renamed.
  * No existing column is altered.
  * No existing row is touched — every product keeps every value it had.
  * `parent_id` defaults to NULL for all 200+ existing rows, which is exactly
    "no parent / not a variant", so nothing needs to be backfilled.

Safety measures
----------------
  1. Takes a timestamped file-copy backup of the .db file before touching it.
  2. Idempotent: checks PRAGMA table_info first, so running it twice (or
     against a DB that already has the column) is a no-op, not an error.
  3. Runs the ALTER TABLE + CREATE INDEX inside a single transaction.
  4. Verifies the row count is unchanged and runs PRAGMA integrity_check
     after the change; rolls back and restores the backup automatically if
     either check fails.

Usage
-----
    python migrate_add_product_variants.py [path/to/pos.db]

If no path is given, it resolves the app's default DB location the same way
app/config/paths.py does (%LOCALAPPDATA%/RetailPOS/data/pos.db on Windows).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def default_db_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "RetailPOS" / "data" / "pos.db"


def backup_db(db_path: Path) -> Path:
    backup_path = db_path.with_name(
        f"{db_path.stem}.pre_variant_migration_{datetime.now():%Y%m%d_%H%M%S}{db_path.suffix}"
    )
    shutil.copy2(db_path, backup_path)
    return backup_path


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        raise SystemExit(f"Database not found at {db_path}")

    print(f"Target database: {db_path}")
    backup_path = backup_db(db_path)
    print(f"Backup written to: {backup_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")

        before_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        print(f"Existing products: {before_count}")

        if column_exists(conn, "products", "parent_id"):
            print("Column 'parent_id' already exists — nothing to do.")
            return

        conn.execute("BEGIN")
        try:
            # Nullable, no server-side default expression other than NULL, so
            # SQLite can add this in place without rebuilding the table.
            conn.execute(
                "ALTER TABLE products ADD COLUMN parent_id INTEGER "
                "REFERENCES products(id) ON DELETE SET NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_products_parent_id ON products(parent_id)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        after_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        non_null_parent = conn.execute(
            "SELECT COUNT(*) FROM products WHERE parent_id IS NOT NULL"
        ).fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

        if after_count != before_count:
            raise RuntimeError(
                f"Row count changed ({before_count} -> {after_count}); restoring backup."
            )
        if integrity != "ok":
            raise RuntimeError(f"Integrity check failed ({integrity}); restoring backup.")

        print(f"Row count unchanged: {after_count}")
        print(f"Rows with a parent set (expected 0 right after migration): {non_null_parent}")
        print(f"Integrity check: {integrity}")
        print("Migration complete.")

    except Exception as exc:
        conn.close()
        print(f"Migration failed: {exc}")
        print(f"Restoring original database from backup: {backup_path}")
        shutil.copy2(backup_path, db_path)
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db_path()
    migrate(target)
