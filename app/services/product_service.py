from __future__ import annotations

from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from app.database.models.product import Product
from app.database.models.category import Category
from app.database.models.stock_movement import StockMovement
from app.repositories.category_repository import CategoryRepository
from app.repositories.product_repository import ProductRepository

SUPPORTED_UNITS = ("piece", "meter", "dozen", "kg", "box", "packet")
UNIT_LABELS = {"piece": "Piece", "meter": "Meter", "dozen": "Dozen", "kg": "Kg", "box": "Box", "packet": "Packet"}

# Sentinel key for the "no Category set" bucket in search_grouped_by_category,
# distinct from any real category_id (which is always an int) or None (which
# Python dicts would otherwise collapse every uncategorized product onto
# correctly anyway, but an explicit sentinel documents the intent rather than
# relying on that incidentally working).
_UNCATEGORIZED = object()


def decimal_value(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a valid decimal value")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} must be a valid decimal value") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal value")
    return result


def validate_money(value: object, field: str = "Unit price") -> Decimal:
    result = decimal_value(value, field)
    if result.as_tuple().exponent < -2:
        raise ValueError(f"{field} cannot have more than 2 decimal places")
    return result


class CategoryGroup:
    """In-memory grouping of products that share a Category — NOT a database
    model, and NOT related to the old self-referential parent/variant
    grouping (which has been removed; see ProductDialog in products_page.py).

    Built fresh on every :meth:`ProductService.search_grouped_by_category`
    call from the current, real Product rows, so it is always in sync with
    whatever category each product is filed under — there is no separate
    "group" row to keep consistent, unlike the old parent-product design.

    ``id`` is the Category's real id (or ``None`` for the synthetic
    "Uncategorized" bucket), used only to key the accordion row — there is
    nothing to Edit/Delete on a CategoryGroup itself, only the individual
    products in ``.products``.
    """

    def __init__(self, category_id: int | None, name: str, products: list):
        self.id = category_id
        self.name = name
        self.products = products

    @property
    def unit(self) -> str:
        return self.products[0].unit if self.products else ""

    @property
    def purchase_price(self) -> Decimal:
        if not self.products:
            return Decimal("0.00")
        return min(p.purchase_price for p in self.products)

    @property
    def wholesale_price(self) -> Decimal:
        if not self.products:
            return Decimal("0.00")
        return min(p.wholesale_price for p in self.products)

    @property
    def selling_price(self) -> Decimal:
        if not self.products:
            return Decimal("0.00")
        return min(p.selling_price for p in self.products)


class ProductService:
    """Product master-data service. Stock/category/status are legacy DB fields only."""

    def __init__(self, db, product_repository: ProductRepository | None = None, category_repository: CategoryRepository | None = None, movement_repository=None):
        self.db = db
        self.products = product_repository or ProductRepository()
        self.categories = category_repository or CategoryRepository()

    @staticmethod
    def validate_product_data(*, name: str, selling_price: object, unit: str) -> tuple[str, Decimal, str]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Product name is required")
        if len(clean_name) > 200:
            raise ValueError("Product name must be 200 characters or fewer")
        # Field name kept consistent with the "Unit Price" label used across
        # every screen (Known Issue #9) so a validation message never
        # contradicts what the field is actually called on screen.
        price = validate_money(selling_price, "Unit price")
        if price < 0:
            raise ValueError("Unit price cannot be negative")
        if unit not in SUPPORTED_UNITS:
            raise ValueError("Unsupported product unit")
        return clean_name, price, unit

    # ---- Category (the only grouping concept the UI exposes now) ----
    def create_category(self, name: str) -> Category:
        clean = name.strip()
        if not clean:
            raise ValueError("Category name is required")
        with self.db.SessionLocal.begin() as session:
            if self.categories.get_by_name_ci(session, clean):
                raise ValueError("A category with this name already exists")
            category = self.categories.add(session, Category(name=clean))
            session.flush()
            return category

    def update_category(self, category_id: int, name: str) -> Category:
        clean = name.strip()
        if not clean:
            raise ValueError("Category name is required")
        with self.db.SessionLocal.begin() as session:
            category = self.categories.get(session, category_id)
            if category is None:
                raise ValueError("Category not found")
            category.name = clean
            session.flush()
            return category

    def list_categories(self, search: str = "", active_only: bool = False) -> list[Category]:
        with self.db.SessionLocal() as session:
            return self.categories.list(session, search, active_only)

    def get_or_create_category(self, name: str) -> Category | None:
        """Resolve a typed "Category Name" to an existing category
        (case-insensitive match, reusing the same lookup create_category
        already uses to reject duplicates) or create one on the fly if no
        match exists. Unlike create_category, this never raises for an
        existing name — that's the point of a type-to-select-or-create
        field. Returns None for blank input, since Category is optional.
        """
        clean = (name or "").strip()
        if not clean:
            return None
        with self.db.SessionLocal.begin() as session:
            existing = self.categories.get_by_name_ci(session, clean)
            if existing is not None:
                return existing
            category = self.categories.add(session, Category(name=clean))
            session.flush()
            return category

    def create_product(self, *, name: str, selling_price: object, unit: str, stock=None, minimum_stock=None, category_id=None, purchase_price="0.00", wholesale_price="0.00", purchase_date="", parent_id=None) -> Product:
        """Create product; legacy stock keywords remain accepted for compatibility.

        ``parent_id`` is legacy: the old self-referential "Parent Group"
        picker that used to set it has been removed from ProductDialog (see
        products_page.py), so the active UI never passes anything but the
        default ``None`` here any more. The parameter and its DB column are
        left in place — untouched — purely so this method's signature and the
        database schema stay backward compatible with anything else in the
        codebase that might still reference it; grouping in the Inventory
        accordion is entirely Category-based now (see
        search_grouped_by_category below), not parent/variant-based.
        """
        clean_name, price, unit = self.validate_product_data(name=name, selling_price=selling_price, unit=unit)
        purchase = validate_money(purchase_price, "Purchase price")
        if purchase < 0:
            raise ValueError("Purchase price cannot be negative")
        wholesale = validate_money(wholesale_price, "Wholesale price")
        if wholesale < 0:
            raise ValueError("Wholesale price cannot be negative")
        # Freeform — accepted as-is (any text/number mix, e.g. "2 Sep 2026" or
        # "15/08/2026"), with no date parsing or format validation.
        clean_purchase_date = (purchase_date or "").strip()
        with self.db.SessionLocal.begin() as session:
            opening = decimal_value(stock, "Stock") if stock is not None else Decimal("0")
            minimum = decimal_value(minimum_stock, "Minimum stock") if minimum_stock is not None else Decimal("0")
            if opening < 0 or minimum < 0:
                raise ValueError("Stock values cannot be negative")
            if parent_id is not None:
                self._validate_parent_choice(session, parent_id, own_id=None)
            product = self.products.add(session, Product(name=clean_name, purchase_price=purchase, wholesale_price=wholesale, selling_price=price, unit=unit, current_stock=opening, minimum_stock=minimum, category_id=category_id, purchase_date=clean_purchase_date, parent_id=parent_id))
            session.flush()
            if opening != 0:
                session.add(StockMovement(product_id=product.id, quantity_change=opening, movement_type="opening_stock", note="Opening stock"))
            return product

    def update_product(self, product_id: int, *, name: str, selling_price: object, unit: str, minimum_stock=None, category_id=None, purchase_price=None, wholesale_price=None, purchase_date=None, parent_id=-1) -> Product:
        """``parent_id`` is legacy — see the note on create_product above.
        ProductDialog no longer passes it, so it always keeps its ``-1``
        ("not supplied, leave grouping untouched") default in practice.
        """
        clean_name, price, unit = self.validate_product_data(name=name, selling_price=selling_price, unit=unit)
        with self.db.SessionLocal.begin() as session:
            product = self.products.get(session, product_id)
            if product is None:
                raise ValueError("Product not found")
            product.name = clean_name
            product.selling_price = price
            product.unit = unit
            if purchase_price is not None:
                purchase = validate_money(purchase_price, "Purchase price")
                if purchase < 0:
                    raise ValueError("Purchase price cannot be negative")
                product.purchase_price = purchase
            if wholesale_price is not None:
                wholesale = validate_money(wholesale_price, "Wholesale price")
                if wholesale < 0:
                    raise ValueError("Wholesale price cannot be negative")
                product.wholesale_price = wholesale
            if purchase_date is not None:
                # Freeform text field — stored as typed, no parsing/validation.
                product.purchase_date = purchase_date.strip()
            if minimum_stock is not None:
                minimum = decimal_value(minimum_stock, "Minimum stock")
                if minimum < 0: raise ValueError("Minimum stock cannot be negative")
                product.minimum_stock = minimum
            if category_id is not None:
                product.category_id = category_id
            # -1 is the "not supplied" sentinel (None is a real, valid value
            # here — "make this product standalone again") so a caller can
            # omit parent_id entirely and leave grouping untouched.
            if parent_id != -1:
                if parent_id is not None:
                    self._validate_parent_choice(session, parent_id, own_id=product.id)
                elif product.variants:
                    raise ValueError("Cannot change grouping: this is already a parent group with brand variants")
                product.parent_id = parent_id
            session.flush()
            return product

    @staticmethod
    def _validate_parent_choice(session, parent_id: int, own_id: int | None) -> None:
        if own_id is not None and parent_id == own_id:
            raise ValueError("A product cannot be its own parent group")
        parent = session.get(Product, parent_id)
        if parent is None:
            raise ValueError("Selected parent group was not found")
        if parent.parent_id is not None:
            raise ValueError("Cannot nest under a brand variant — pick the top-level group instead")
        if own_id is not None:
            child = session.get(Product, own_id)
            if child is not None and child.variants:
                raise ValueError("Cannot group a product that already has its own brand variants")


    # Legacy backend compatibility: these are intentionally not exposed in the active UI.
    def set_product_active(self, product_id: int, active: bool) -> None:
        with self.db.SessionLocal.begin() as session:
            product=self.products.get(session, product_id)
            if product is None: raise ValueError("Product not found")
            product.is_active=bool(active)

    @staticmethod
    def is_low_stock(product: Product) -> bool:
        return product.current_stock <= product.minimum_stock

    def set_category_active(self, category_id: int, active: bool) -> None:
        with self.db.SessionLocal.begin() as session:
            category=self.categories.get(session, category_id)
            if category is None: raise ValueError("Category not found")
            category.is_active=bool(active)

    def delete_product(self, product_id: int) -> None:
        """Permanently remove a product while preserving historical snapshots.

        InvoiceItem and legacy StockMovement product references use ON DELETE SET NULL,
        so deleting the master product cannot remove or corrupt historical records.
        """
        with self.db.SessionLocal.begin() as session:
            product = self.products.get(session, product_id)
            if product is None:
                raise ValueError("Product not found")
            if product.variants:
                raise ValueError("Remove or reassign its brand variants before deleting this group")
            session.delete(product)

    def list_products(self, search: str = "") -> list[Product]:
        with self.db.SessionLocal() as session:
            return self.products.list(session, search)

    def search_grouped_by_category(self, term: str) -> list[CategoryGroup]:
        """Requirement #1 — Category-based grouping for the Inventory
        accordion, replacing the old parent/brand-variant grouping entirely.

        A Category is included if its own name matches ``term``, or if any of
        its member products' names do — e.g. creating "GFC Dimmer", "China
        Dimmer" and "HFC Dimmer" all with Category = "Dimmer" means searching
        either "Dimmer" (matches the category) or "GFC" (matches one member)
        surfaces the same "Dimmer" group, expandable to its member products.

        Products with no category set are collected into a single synthetic
        "Uncategorized" group (id=None) rather than being silently dropped
        from the accordion.

        This groups in plain Python over every active product rather than
        pushing the grouping into a repository-level query, since the
        product/category relationship is already loaded cheaply via
        ProductRepository.list and this keeps the grouping rule (and its
        "match category name OR a member's name" semantics) in one place,
        readable next to the UI that depends on it.
        """
        needle = (term or "").strip().lower()
        with self.db.SessionLocal() as session:
            products = self.products.list(session, "")
            # Snapshot the plain fields we need onto detached SimpleNamespace
            # rows while the session is still open — Product's `.category`
            # relationship would otherwise raise once this `with` block
            # closes and the session is gone.
            rows = [
                SimpleNamespace(
                    id=p.id,
                    name=p.name,
                    unit=p.unit,
                    purchase_date=p.purchase_date,
                    purchase_price=p.purchase_price,
                    wholesale_price=p.wholesale_price,
                    selling_price=p.selling_price,
                    category_id=p.category_id,
                    category_name=(p.category.name if p.category_id is not None and p.category is not None else None),
                )
                for p in products
            ]

        buckets: dict[object, list] = {}
        order: list = []
        for row in rows:
            key = row.category_id if row.category_id is not None else _UNCATEGORIZED
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(row)

        groups: list[CategoryGroup] = []
        for key in order:
            members = buckets[key]
            name = "Uncategorized" if key is _UNCATEGORIZED else (members[0].category_name or "Uncategorized")
            category_name_matches = bool(needle) and needle in name.lower()
            matching_members = [m for m in members if not needle or needle in m.name.lower()]
            if needle and not category_name_matches and not matching_members:
                continue  # neither the category name nor any member matched — hide it
            shown_members = members if (category_name_matches or not needle) else matching_members
            groups.append(CategoryGroup(None if key is _UNCATEGORIZED else key, name, shown_members))

        groups.sort(key=lambda g: g.name.lower())
        return groups

    def search_grouped_products(self, term: str) -> list[Product]:
        """Legacy parent/brand-variant grouping. No longer called by the
        active UI (products_page.py now calls search_grouped_by_category
        instead) — left in place only in case another part of the codebase
        not covered by this change still depends on it.
        """
        with self.db.SessionLocal() as session:
            return self.products.search_grouped(session, term)

    def list_parent_choices(self, exclude_id: int | None = None) -> list[Product]:
        """Legacy — backed the old "Parent Group" picker that has been
        removed from ProductDialog. Left in place for backward compatibility
        only; the active UI no longer calls this.
        """
        with self.db.SessionLocal() as session:
            return self.products.list_parent_choices(session, exclude_id=exclude_id)

    def get_product(self, product_id: int) -> Product | None:
        with self.db.SessionLocal() as session:
            return self.products.get(session, product_id)
