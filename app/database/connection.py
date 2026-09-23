from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            future=True,
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        self._configure_sqlite(self.engine)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def initialize(self) -> None:
        # Keep the Phase 0 settings bootstrap intact, then create feature tables
        # owned by later phases without dropping or resetting existing data.
        from sqlalchemy import Column, DateTime, MetaData, String, Table, func

        bootstrap_metadata = MetaData()
        Table(
            "settings",
            bootstrap_metadata,
            Column("key", String(100), primary_key=True),
            Column("value", String(500), nullable=True),
            Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
        )
        bootstrap_metadata.create_all(self.engine)

        # Importing the feature models registers them on the shared declarative
        # metadata. create_all() is additive and therefore safe for existing DBs.
        from app.database.models import Category, Product, StockMovement, Customer, Invoice, InvoiceItem, CustomerLedgerEntry  # noqa: F401

        from app.database.base import Base

        Base.metadata.create_all(self.engine)

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO settings(key, value) "
                    "VALUES (:key, :value)"
                ),
                {"key": "invoice_sequence", "value": "0"},
            )
            conn.execute(text("PRAGMA foreign_keys=ON"))

            # Phase 4 Step 1: additive migration for the historical invoice
            # customer-name snapshot. Existing databases are preserved; no
            # tables/data are dropped or rewritten.
            invoice_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(invoices)")).fetchall()
            }
            if "customer_name" not in invoice_columns:
                conn.execute(text("ALTER TABLE invoices ADD COLUMN customer_name VARCHAR(200)"))

            product_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(products)")).fetchall()}
            if "purchase_price" not in product_columns:
                conn.execute(text("ALTER TABLE products ADD COLUMN purchase_price NUMERIC(18, 2) NOT NULL DEFAULT 0.00"))
            if "wholesale_price" not in product_columns:
                conn.execute(text("ALTER TABLE products ADD COLUMN wholesale_price NUMERIC(18, 2) NOT NULL DEFAULT 0.00"))
            if "purchase_date" not in product_columns:
                # Freeform text ("2 Sep 2026", "15/08/2026", ...), so a plain
                # nullable VARCHAR rather than a strict DATE column.
                conn.execute(text("ALTER TABLE products ADD COLUMN purchase_date VARCHAR(100)"))

            # Inventory dual-state search/accordion: additive migration for the
            # self-referential brand-variant relationship. NULL stays a
            # standalone product or a group header; existing rows are
            # untouched and simply remain standalone until grouped by hand.
            if "parent_id" not in product_columns:
                conn.execute(text("ALTER TABLE products ADD COLUMN parent_id INTEGER REFERENCES products(id) ON DELETE SET NULL"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_parent_id ON products (parent_id)"))

            # Edit Invoice support: additive migration for the voided flag.
            # Existing databases are preserved; no rows are dropped or rewritten.
            if "voided" not in invoice_columns:
                conn.execute(text("ALTER TABLE invoices ADD COLUMN voided BOOLEAN NOT NULL DEFAULT 0"))
            if "voided_at" not in invoice_columns:
                conn.execute(text("ALTER TABLE invoices ADD COLUMN voided_at DATETIME"))

        # Product master rows are removable, while historical invoice/stock records
        # retain their own snapshots. Existing databases created before this rule
        # used RESTRICT/non-null product foreign keys, so migrate those child tables
        # in-place without touching any historical rows.
        self._migrate_product_references()

    def _migrate_product_references(self) -> None:
        """Make historical product references nullable with ON DELETE SET NULL.

        SQLite cannot alter a foreign-key action in-place, so only legacy child
        tables with the old RESTRICT/non-null definition are rebuilt. Data is copied
        verbatim; no invoice, invoice-item, or stock-movement row is deleted.
        """
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                conn.exec_driver_sql("BEGIN")
                self._rebuild_invoice_items_if_needed(conn)
                self._rebuild_stock_movements_if_needed(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    @staticmethod
    def _product_fk_needs_migration(conn, table_name: str) -> bool:
        foreign_keys = conn.exec_driver_sql(f"PRAGMA foreign_key_list({table_name})").fetchall()
        product_fk = next((row for row in foreign_keys if row[2] == "products"), None)
        columns = {row[1]: row for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()}
        product_col = columns.get("product_id")
        if product_fk is None or product_col is None:
            return False
        # PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, ...
        return product_fk[6].upper() != "SET NULL" or bool(product_col[3])

    @staticmethod
    def _rebuild_invoice_items_if_needed(conn) -> None:
        if not conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_items'").fetchone():
            return
        if not DatabaseManager._product_fk_needs_migration(conn, "invoice_items"):
            return
        conn.exec_driver_sql("""
            CREATE TABLE invoice_items__new (
                id INTEGER NOT NULL PRIMARY KEY,
                invoice_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name VARCHAR(200) NOT NULL,
                unit VARCHAR(20) NOT NULL,
                quantity NUMERIC(24, 6) NOT NULL,
                unit_price NUMERIC(18, 2) NOT NULL,
                item_discount NUMERIC(18, 2) NOT NULL,
                line_total NUMERIC(18, 2) NOT NULL,
                FOREIGN KEY(invoice_id) REFERENCES invoices (id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE SET NULL
            )
        """)
        conn.exec_driver_sql("""
            INSERT INTO invoice_items__new
            (id, invoice_id, product_id, product_name, unit, quantity, unit_price, item_discount, line_total)
            SELECT id, invoice_id, product_id, product_name, unit, quantity, unit_price, item_discount, line_total
            FROM invoice_items
        """)
        conn.exec_driver_sql("DROP TABLE invoice_items")
        conn.exec_driver_sql("ALTER TABLE invoice_items__new RENAME TO invoice_items")
        conn.exec_driver_sql("CREATE INDEX ix_invoice_items_invoice_id ON invoice_items (invoice_id)")
        conn.exec_driver_sql("CREATE INDEX ix_invoice_items_product_id ON invoice_items (product_id)")

    @staticmethod
    def _rebuild_stock_movements_if_needed(conn) -> None:
        if not conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_movements'").fetchone():
            return
        if not DatabaseManager._product_fk_needs_migration(conn, "stock_movements"):
            return
        conn.exec_driver_sql("""
            CREATE TABLE stock_movements__new (
                id INTEGER NOT NULL PRIMARY KEY,
                product_id INTEGER,
                quantity_change NUMERIC(24, 6) NOT NULL,
                movement_type VARCHAR(30) NOT NULL,
                note TEXT,
                reference VARCHAR(120),
                created_at DATETIME NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE SET NULL
            )
        """)
        conn.exec_driver_sql("""
            INSERT INTO stock_movements__new
            (id, product_id, quantity_change, movement_type, note, reference, created_at)
            SELECT id, product_id, quantity_change, movement_type, note, reference, created_at
            FROM stock_movements
        """)
        conn.exec_driver_sql("DROP TABLE stock_movements")
        conn.exec_driver_sql("ALTER TABLE stock_movements__new RENAME TO stock_movements")
        conn.exec_driver_sql("CREATE INDEX ix_stock_movements_product_id ON stock_movements (product_id)")
        conn.exec_driver_sql("CREATE INDEX ix_stock_movements_movement_type ON stock_movements (movement_type)")
        conn.exec_driver_sql("CREATE INDEX ix_stock_movements_created_at ON stock_movements (created_at)")

    def health_check(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar_one() == 1

    def dispose(self) -> None:
        self.engine.dispose()
