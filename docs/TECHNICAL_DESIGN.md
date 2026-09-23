# RetailPOS — Technical Design (Current Master)

## Scope

RetailPOS is an offline, single-user Windows desktop POS. SQLite is the sole business database and SQLAlchemy 2.x is used for ORM/database access. The current master contains the completed implementation through Phase 4 Step 4.

## 1. SQLite and application paths

SQLite is configured with foreign keys enabled, WAL mode, `synchronous=NORMAL`, and a busy timeout. The live database is stored in the application's writable data area (`%LOCALAPPDATA%\\RetailPOS\\data\\pos.db` on Windows). Logs and backups are also kept in writable application-data locations. Bundled/PyInstaller application resources are treated as read-only.

The database connection/bootstrap code is responsible for the existing SQLite settings and SQLAlchemy engine/session infrastructure. The consolidation checkpoint does not introduce a database migration.

## 2. Sales transaction strategy

Sales are finalized through the existing `SalesService`. Invoice creation, invoice items, stock deduction, stock movements, and (for legacy/backend compatibility) customer-ledger effects are committed atomically. The normal Billing UI uses the cash path only; the underlying credit/customer tables are retained for database compatibility and are not exposed as a normal shop workflow. A failure rolls the transaction back so a sale cannot leave partially written business data.

Invoice numbering uses the existing `invoice_sequence` setting and the configured invoice prefix. The invoice number is protected by a database uniqueness constraint and the existing transaction/retry logic.

## 3. Historical invoice data

`InvoiceItem` stores historical product information including product name, unit, quantity, unit price, item discount, and line total. `Invoice` retains the historical optional customer-name snapshot for compatibility with existing records, but the normal Invoice History/Detail UI does not expose customer fields.

## 4. Money and quantity precision

Money is handled with Python `Decimal` and persisted as `NUMERIC(18,2)`. Monetary input with more than two decimal places is rejected; the application does not silently round invalid user input.

Quantities use Python `Decimal` and `NUMERIC(24,6)`. The application validates quantities according to the product unit rules and formats them without meaningless trailing zeros.

## 5. Invoice accounting and output

For newly created invoices, `Invoice.subtotal` is the gross sum of invoice-item amounts before item-level discounts. `Invoice.discount_total` is the sum of all item-level discounts plus the whole-bill discount, and `grand_total = subtotal - discount_total`. The live POS cart uses the same semantics. Historical invoices created under the older subtotal convention are not rewritten; Invoice Detail and invoice output derive the gross subtotal from the immutable invoice-item snapshots and derive the bill discount from the stored total discount, preserving the historical grand total.

`InvoiceOutputService` is independent of the sale transaction. It renders historical invoice data with ReportLab into two layouts: a standard A4 invoice and a single 180mm wide thermal receipt. Output includes configured shop details, currency, invoice footer, items, discounts, totals, paid amount, and remaining amount. Long names wrap and A4 output can span multiple pages.

Printing is an output-only operation. A PDF or printer failure cannot roll back or alter an already-finalized sale.

## 6. Dashboard

`DashboardService` provides read-only SQL/ORM queries for today's sales, invoice count, cash received, active product count, low/out-of-stock products, and recent sales for the normal dashboard presentation. Legacy credit/outstanding aggregates remain available internally for compatibility tests/data, but the normal dashboard does not display Udhaar/Credit cards. It uses date boundaries rather than formatted-date string comparisons and limits recent sales at the database query level. Dashboard refresh never modifies business data.

## 7. Backup and restore

`BackupRestoreService` uses SQLite's Online Backup API (`sqlite3.Connection.backup()`) instead of blindly copying a live WAL database file. Backups are validated with SQLite integrity checking.

Before restore, the selected file is validated as SQLite and checked against the current RetailPOS ORM-derived schema requirements. A retained pre-restore safety backup of the current live database is created and validated before replacement. Restore handles active database connections and SQLite WAL/SHM sidecars, reinitializes the database infrastructure, reapplies the normal SQLite connection settings, and verifies the restored database. If restoration cannot be reopened safely, the original database is recoverable and the safety backup remains available.

A failed backup or validation operation must not be reported as successful. If a pre-restore safety backup cannot be created, restore does not proceed.

## 8. PyInstaller readiness

The application separates bundled/read-only resources from writable runtime data. The path architecture supports PyInstaller's bundled resource location while keeping the SQLite database, logs, backups, and generated output in writable user/application-data locations. No feature requires writing beside the installed executable.

## 9. Verification and scope

The automated suite covers the completed Phase 0–4 functionality. Compile checks use `python -m compileall -q app tests`.

Final Windows GUI verification, physical-printer verification, and Windows EXE build/run are environment-dependent and are not claimed unless actually performed.

The master checkpoint does not include cloud/network backup, reports, barcode, authentication, tax, supplier/purchase management, payroll, accounting, or Phase 5 functionality.
