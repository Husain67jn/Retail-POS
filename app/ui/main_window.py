from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QSize, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from app.config.settings import (
    APP_NAME,
    APP_VERSION,
    SIDEBAR_MAX_WIDTH,
    SIDEBAR_MIN_WIDTH,
    WINDOW_DEFAULT_HEIGHT_FRAC,
    WINDOW_DEFAULT_WIDTH_FRAC,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from app.services.backup_restore_service import BackupRestoreService
from app.services.customer_service import CustomerService
from app.services.dashboard_service import DashboardService
from app.services.invoice_output_service import InvoiceOutputService
from app.services.product_service import ProductService
from app.services.sales_service import SalesService
from app.services.settings_service import SettingsService
from app.ui import icons
from app.ui.backup_page import BackupPage
from app.ui.customers_page import CustomersPage
from app.ui.dashboard_page import DashboardPage
from app.ui.invoices_page import InvoicesPage
from app.ui.notes_page import NotesPage
from app.ui.pos_page import POSPage
from app.ui.products_page import ProductsPage
from app.ui.responsive import center_on_screen, size_to_screen
from app.ui.settings_page import SettingsPage
from app.ui.theme import load_theme


class MainWindow(QMainWindow):
    # (label, icon key) — order defines both sidebar order and the stacked page order below.
    NAV_ITEMS = [
        ("Dashboard", "dashboard"),
        ("Products", "products"),
        ("POS", "billing"),
        ("Sales / Invoices", "invoices"),
        ("Customer Udhaar", "customer_udhaar"),
        ("Notes & Docs", "notes"),
        ("Settings", "settings"),
        ("Backup", "backup"),
    ]

    # Collapsed rail shows nav icons only; expanded shows the full labelled
    # sidebar. The toggle animates smoothly between the two widths.
    COLLAPSED_WIDTH = 78
    EXPANDED_WIDTH = SIDEBAR_MAX_WIDTH

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.product_service = ProductService(db)
        self.settings_service = SettingsService(db)
        self.sales_service = SalesService(db, settings_service=self.settings_service)
        # One output pipeline for every printed document. Both the Invoices
        # page (full A4/A5/thermal reprints) and the POS page ("Print/Save
        # Receipt", 80mm thermal) now render through the reportlab
        # InvoiceOutputService, so a POS receipt is the exact same
        # professional layout as an Invoices-section PDF. The legacy
        # QTextDocument/HTML generator (pdf_service.py) that POS used to call
        # produced the old "Receipt-*.pdf" output and is no longer wired in.
        self.invoice_output_service = InvoiceOutputService()
        self.dashboard_service = DashboardService(db)
        self.customer_service = CustomerService(db)
        self.backup_restore_service = BackupRestoreService(db)
        # "Point of Sale" describes what the app does; it no longer repeats
        # "Electric"/"Store" back at the user (Known Issue #2).
        self.setWindowTitle(f"{APP_NAME} — Point of Sale")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self._build_ui()
        # Open at a fraction of the available screen and centre it, so the app
        # fits any laptop/desktop resolution without overflowing or clipping.
        size_to_screen(
            self,
            WINDOW_DEFAULT_WIDTH_FRAC,
            WINDOW_DEFAULT_HEIGHT_FRAC,
            min_width=WINDOW_MIN_WIDTH,
            min_height=WINDOW_MIN_HEIGHT,
        )
        center_on_screen(self)

    def _apply_typography(self):
        # Typography size maps to a base point size and a matching scale factor.
        # The point size is set on the QApplication font so every unstyled
        # widget and native dialog inherits it; the scale is fed to the theme
        # so the QSS-sized headings/buttons/totals grow in step. Re-applying the
        # stylesheet forces a full repolish, guaranteeing a Font Family or Size
        # change reaches EVERY component — product cards and names, category and
        # price labels, search fields, customer fields, and bill-discount inputs.
        settings = self.settings_service.get_settings()
        base_sizes = {"Comfortable": 10, "Large": 11, "Extra Large": 12}
        base_pt = base_sizes.get(settings.typography_size, 10)
        scale = base_pt / 10
        family = settings.typography_font
        app = QApplication.instance()
        if app is not None:
            font = QFont(family)
            font.setPointSize(base_pt)
            app.setFont(font)
        self.setStyleSheet(load_theme(family, scale))

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        self.sidebar = sidebar
        self._collapsed = False
        # Flex between bounds instead of a single fixed width so the sidebar
        # scales with font size and window width without clipping the brand.
        sidebar.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        sidebar.setMaximumWidth(SIDEBAR_MAX_WIDTH)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(14, 16, 14, 14)
        sl.setSpacing(0)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(7)
        self.brand_mark = QLabel()
        # The one deliberately "branded" icon in the app — used once, not
        # repeated on every nav item (Section 1: sparingly).
        self.brand_mark.setPixmap(icons.nav_icon("lightning", "#5b9bff").pixmap(QSize(16, 16)))
        brand_row.addWidget(self.brand_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.brand = QLabel(APP_NAME)
        self.brand.setObjectName("brand")
        # Wrapping (rather than relying on exact pixel-width guesses) is what
        # guarantees the store name can never be silently clipped again
        # (Known Issue #1), regardless of window width or font metrics.
        self.brand.setWordWrap(True)
        brand_row.addWidget(self.brand, 1)
        # Arrow button that collapses the sidebar to icons-only and back.
        self.toggle_button = QPushButton("‹")
        self.toggle_button.setObjectName("sidebarToggle")
        self.toggle_button.setFixedSize(28, 28)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.setToolTip("Collapse sidebar")
        self.toggle_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle_button.clicked.connect(self._toggle_sidebar)
        brand_row.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignTop)
        sl.addLayout(brand_row)

        self.subtitle = QLabel("Electrical Retail Management")
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        sl.addWidget(self.subtitle)
        sl.addSpacing(18)

        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.navigation.setIconSize(QSize(17, 17))
        self.navigation.setSpacing(1)
        # Primary navigation must never need its own scrollbar (Known Issue
        # #3). Turning it off is a hard guarantee, not a pixel guess; giving
        # the list a stretch factor below is what supplies the room to
        # actually fit all seven items without it.
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for label, icon_key in self.NAV_ITEMS:
            item = QListWidgetItem(icons.nav_icon(icon_key), label)
            # Tooltip keeps the label reachable when the rail is collapsed to
            # icons only.
            item.setToolTip(label)
            self.navigation.addItem(item)
        sl.addWidget(self.navigation, 1)

        # User profile card pinned to the bottom of the sidebar.
        self.profile_card = QFrame()
        self.profile_card.setObjectName("profileCard")
        pl = QHBoxLayout(self.profile_card)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(9)
        self.profile_avatar = QLabel("ME")
        self.profile_avatar.setObjectName("profileAvatar")
        self.profile_avatar.setFixedSize(34, 34)
        self.profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pl.addWidget(self.profile_avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        pcol = QVBoxLayout()
        pcol.setSpacing(0)
        self.profile_name = QLabel("Store Owner")
        self.profile_name.setObjectName("profileName")
        self.profile_role = QLabel("Administrator")
        self.profile_role.setObjectName("profileRole")
        pcol.addWidget(self.profile_name)
        pcol.addWidget(self.profile_role)
        pl.addLayout(pcol, 1)
        sl.addSpacing(12)
        sl.addWidget(self.profile_card)
        # Bottom-left store owner profile badge removed from the sidebar.
        self.profile_card.setVisible(False)

        self.sidebar_version = QLabel(f"v{APP_VERSION}")
        self.sidebar_version.setObjectName("sidebarVersion")
        sl.addSpacing(8)
        sl.addWidget(self.sidebar_version)
        return sidebar

    def _toggle_sidebar(self) -> None:
        self._collapsed = not self._collapsed
        target = self.COLLAPSED_WIDTH if self._collapsed else self.EXPANDED_WIDTH
        self.toggle_button.setText("›" if self._collapsed else "‹")
        self.toggle_button.setToolTip("Expand sidebar" if self._collapsed else "Collapse sidebar")
        if self._collapsed:
            # Hide the text before the rail shrinks so labels are never clipped
            # mid-animation; icons stay visible for navigation.
            self._set_sidebar_expanded(False)
        self._animate_sidebar(target)

    def _animate_sidebar(self, target: int) -> None:
        existing = getattr(self, "_sidebar_animation", None)
        if existing is not None:
            existing.stop()
        group = QParallelAnimationGroup(self)
        # Drive minimum and maximum width together so the rail settles at an
        # exact collapsed/expanded width regardless of layout stretch.
        for prop in (b"minimumWidth", b"maximumWidth"):
            anim = QPropertyAnimation(self.sidebar, prop, self)
            anim.setDuration(200)
            anim.setStartValue(self.sidebar.width())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            group.addAnimation(anim)
        group.finished.connect(self._on_sidebar_animation_finished)
        # Keep a reference so the running animation is not garbage-collected.
        self._sidebar_animation = group
        group.start()

    def _on_sidebar_animation_finished(self) -> None:
        # Reveal the labels only once the rail is back to full width.
        if not self._collapsed:
            self._set_sidebar_expanded(True)

    def _set_sidebar_expanded(self, expanded: bool) -> None:
        self.brand.setVisible(expanded)
        self.brand_mark.setVisible(expanded)
        self.subtitle.setVisible(expanded)
        self.sidebar_version.setVisible(expanded)
        self.profile_name.setVisible(expanded)
        self.profile_role.setVisible(expanded)
        for index, (label, _icon_key) in enumerate(self.NAV_ITEMS):
            self.navigation.item(index).setText(label if expanded else "")


    def _build_ui(self):
        root = QWidget()
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        sidebar = self._build_sidebar()

        self.pages = QStackedWidget()
        self.dashboard_page = DashboardPage(self.dashboard_service, self.settings_service)
        self.products_page = ProductsPage(self.product_service, self.settings_service)
        self.pos_page = POSPage(self.product_service, self.sales_service, self.settings_service, self.invoice_output_service)
        self.invoices_page = InvoicesPage(self.sales_service, self.settings_service, self.invoice_output_service)
        self.customer_page = CustomersPage(
            service=self.customer_service,
            sales_service=self.sales_service,
            output_service=self.invoice_output_service,
            settings_service=self.settings_service,
        )
        self.notes_page = NotesPage()
        self.settings_page = SettingsPage(self.settings_service, self.backup_restore_service)
        self.backup_page = BackupPage(self.backup_restore_service)
        for page in [
            self.dashboard_page,
            self.products_page,
            self.pos_page,
            self.invoices_page,
            self.customer_page,
            self.notes_page,
            self.settings_page,
            self.backup_page,
        ]:
            self.pages.addWidget(page)
        # Lazy-refresh bookkeeping — the fix for section-switching lag. A page's
        # (potentially heavy) refresh() runs only when the page is FIRST shown
        # or after something actually invalidated its data, never on every tab
        # switch. Every page starts "dirty" so its first view loads it; the
        # signals wired below re-mark only the pages a given action affects.
        self._page_index = {page: idx for idx, page in enumerate(self.pages.widget(i) for i in range(self.pages.count()))}
        self._dirty_pages = set(self._page_index.values())
        self.dashboard_page.open_invoice = self.invoices_page.open_invoice_by_id
        self.invoices_page.edit_invoice = self._edit_invoice

        rl.addWidget(sidebar)
        rl.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        # _apply_typography applies the stylesheet (family + size scale); no
        # need to set a default theme first and immediately overwrite it.
        self._apply_typography()
        self.navigation.currentRowChanged.connect(self._change_page)
        self.settings_page.settings_saved.connect(self._refresh_settings)
        self.settings_page.data_cleared.connect(self._refresh_all)
        self.backup_page.restored.connect(self._refresh_all)
        # Targeted invalidation: a finalized sale, a product/category edit, or a
        # customer change marks ONLY the views it affects stale, so unrelated
        # tabs (and repeat visits) stay instant.
        self.pos_page.sale_completed.connect(self._on_sale_completed)
        self.products_page.products_changed.connect(self._on_products_changed)
        self.customer_page.customers_changed.connect(self._on_customers_changed)
        # Connect before selecting row 0 so the initial selection drives the
        # same lazy-load path as every later switch; the explicit call is a
        # belt-and-braces guarantee the first page loads even if the row was
        # already current.
        self.navigation.setCurrentRow(0)
        self._change_page(0)

    def _change_page(self, index):
        if index < 0:
            return
        # The visual switch is always instant. The (possibly heavy) data
        # refresh runs only when this page is stale — switching back to an
        # already-loaded, un-invalidated page does zero DB work.
        self.pages.setCurrentIndex(index)
        if index not in self._dirty_pages:
            return
        page = self.pages.widget(index)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        self._dirty_pages.discard(index)

    def _mark_dirty(self, *pages):
        for page in pages:
            idx = self._page_index.get(page)
            if idx is not None:
                self._dirty_pages.add(idx)

    def _refresh_current_page(self):
        index = self.pages.currentIndex()
        page = self.pages.widget(index)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        self._dirty_pages.discard(index)

    def _on_sale_completed(self):
        # A finalized sale changes revenue, stock, receivables and the invoice
        # list; every data-backed view is marked stale so each reloads once on
        # its next visit (the POS page already refreshed its own cart).
        self._mark_dirty(
            self.dashboard_page,
            self.products_page,
            self.invoices_page,
            self.customer_page,
            self.pos_page,
        )

    def _on_products_changed(self):
        # A product/category create/edit/delete changes the POS search list and
        # the Dashboard stock figures.
        self._mark_dirty(self.pos_page, self.dashboard_page)

    def _on_customers_changed(self):
        # A customer payment/adjustment/delete changes Dashboard receivables.
        self._mark_dirty(self.dashboard_page)

    def _edit_invoice(self, invoice):
        # Wired from Invoices' "Edit Invoice" (which already confirmed this
        # with the cashier). Check feasibility BEFORE voiding — voiding is
        # irreversible, so an invoice must only be voided once we already
        # know its items can be reloaded as a replacement bill.
        blocker = self.pos_page.edit_invoice_blockers(invoice)
        if blocker:
            QMessageBox.warning(self, "Cannot edit invoice", blocker)
            return
        try:
            self.sales_service.void_invoice(invoice.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot edit invoice", str(exc))
            return
        loaded = self.pos_page.load_invoice_for_edit(invoice)
        # The void must show on the Sales/Invoices list immediately (the user
        # stays on it if the reload fails), so refresh it now and clear its
        # dirty flag; the remaining data views reload lazily on next visit.
        self.invoices_page.refresh()
        self._dirty_pages.discard(self._page_index[self.invoices_page])
        self._mark_dirty(self.dashboard_page, self.products_page, self.customer_page, self.pos_page)
        if loaded:
            billing_row = next(i for i, (label, _) in enumerate(self.NAV_ITEMS) if label == "POS")
            self.navigation.setCurrentRow(billing_row)

    def _refresh_settings(self):
        # Typography changed: re-apply the global stylesheet immediately (it
        # reaches every widget), and because font metrics drive table column
        # sizing, mark every page stale so each re-measures on next visit. The
        # visible page is refreshed now so the change is seen at once.
        self._apply_typography()
        self._dirty_pages = set(self._page_index.values())
        self._refresh_current_page()

    def _refresh_all(self):
        for page in [
            self.dashboard_page,
            self.products_page,
            self.pos_page,
            self.invoices_page,
            self.customer_page,
            self.notes_page,
            self.settings_page,
        ]:
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()
        # A data wipe / restore is rare and global; everything was just
        # refreshed eagerly, so nothing is left stale.
        self._dirty_pages.clear()
        self.navigation.setCurrentRow(0)
