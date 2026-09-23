# RetailPOS

RetailPOS is an **offline Windows desktop point-of-sale application** for a small retail shop. It stores business data locally in SQLite and does not require cloud/network services or Python on the end-user machine after packaging.

## Current feature set

### Application foundation
- PySide6 desktop application and sidebar navigation
- SQLite + SQLAlchemy database infrastructure
- SQLite WAL mode, foreign keys, and busy timeout
- Writable Windows application-data paths under `%LOCALAPPDATA%\\RetailPOS`
- Application logging

### Settings and theme
- Shop name, address, phone, and invoice footer
- Currency code and symbol
- Invoice prefix
- A4 / Thermal output format
- 180mm thermal receipt width
- Low-stock highlighting
- Allow-negative-stock setting
- Existing application theme

### Products and inventory
- Products and categories
- Selling prices and minimum stock
- Stock movements
- Supported units: `piece`, `meter`, `dozen`, `kg`, `box`
- Decimal quantities where allowed
- Fractional quantities for meter-based products
- Whole-number validation for discrete units
- Human-readable quantity formatting without meaningless trailing zeros

### POS and sales
- Product search and cart
- Visible quantity controls with decimal support where allowed
- Item discounts and bill discount
- Cash-only billing workflow in the normal shop UI
- Atomic invoice creation and stock deduction
- Invoice numbering using the configured prefix and current sequence architecture
- Historical invoice item snapshots (product name, unit, quantity, price, discounts, line total)
- Existing customer/credit database compatibility retained internally; the normal shop UI does not expose Udhaar/Credit workflows

### Invoice history and detail
- Invoice history listing
- Search by invoice number
- Historical invoice detail view
- Historical data is used instead of current product records

### Invoice PDF and printing
- A4 PDF invoices
- 180mm wide thermal receipts
- A dedicated wide-thermal layout that fills the full 180mm printable width rather than scaled A4 output
- Historical invoice data for output
- Configured currency and invoice footer
- Windows operating-system print integration
- User-selected PDF save location

### Dashboard
- Today's sales
- Today's invoice count
- Today's cash received
- Active product count
- Low/out-of-stock products
- Recent sales
- Dashboard refresh

### Database backup and restore
- Manual SQLite Online Backup API backups
- SQLite integrity validation
- Compatibility validation against the current RetailPOS ORM schema
- Pre-restore safety backup of the current live database
- WAL-safe restore handling
- Restore failure recovery
- Backup and Restore controls in the application

## Money and quantity rules

Money uses Python `Decimal` and SQLite `NUMERIC(18,2)`. User-entered money with more than two decimal places is rejected rather than silently rounded. Displayed money uses exactly two decimal places and the configured currency symbol/code.

Quantities use Python `Decimal` and SQLite `NUMERIC(24,6)`. Display removes meaningless trailing zeros, for example `5`, `5.5`, and `5.25`.

## Cash-only shop UI

The normal shop workflow is intentionally cash-only: the Billing page exposes product selection, quantity, discounts, cash received, and Complete Sale. Customer/Udhaar/Credit screens and controls are hidden from the normal UI. Existing customer/credit tables and backend compatibility are retained so historical databases and existing invoice records are not destructively migrated.

## Application data and backups

On Windows, writable application data is stored under `%LOCALAPPDATA%\\RetailPOS`, including the live database, logs, and backup storage. The application does not depend on writing into its installed/PyInstaller directory.

Manual database backups are created with SQLite's Online Backup API so WAL-mode data is captured consistently. Restore validates the selected database and creates a retained pre-restore safety backup before replacement.

## Development setup

The project targets Python `>=3.12,<3.13` and uses the dependencies declared in `requirements.txt` / `pyproject.toml`.

Install dependencies in a virtual environment, then run:

```bash
python -m pytest -q
python -m compileall -q app tests
```

## Packaging

The project is designed for PyInstaller packaging. Runtime writable data is separated from bundled/read-only application resources, and ReportLab/PDF generation and backup paths use the application's existing path architecture.

A Windows EXE build/run was **not** performed in this development environment. Final Windows GUI verification and physical-printer verification remain pending.

## Verification status

This master project contains the cumulative implementation through **Phase 4 Step 4**. Automated tests and compile checks are part of the verified development workflow. Windows GUI, real-printer, and Windows EXE verification must be performed in the appropriate Windows environment.

## Explicit scope exclusions for this checkpoint

This project does not implement:

- Cloud/network synchronization or cloud backup
- Reports module
- Barcode functionality
- Authentication
- Tax
- Supplier management
- Purchase management
- Payroll
- Accounting system
- Phase 5 functionality
