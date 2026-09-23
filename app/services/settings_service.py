from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

from app.repositories.settings_repository import SettingsRepository


@dataclass(frozen=True)
class SettingsData:
    shop_name: str
    shop_address: str
    shop_phone: str
    invoice_footer: str
    currency_code: str
    currency_symbol: str
    invoice_prefix: str
    default_invoice_format: str
    thermal_paper_width: str
    low_stock_highlighting: bool
    allow_negative_stock: bool
    typography_font: str = "Segoe UI"
    typography_size: str = "Comfortable"


DEFAULT_SETTINGS: Final[SettingsData] = SettingsData(
    shop_name="Mughal Electric Store",
    shop_address="",
    shop_phone="",
    invoice_footer="Thank you for your business.",
    currency_code="PKR",
    currency_symbol="Rs.",
    invoice_prefix="INV",
    default_invoice_format="A4",
    thermal_paper_width="180mm",
    low_stock_highlighting=True,
    allow_negative_stock=False,
    typography_font="Segoe UI",
    typography_size="Comfortable",
)

_FORMATS = {"A4", "Thermal"}
_WIDTHS = {"180mm"}
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
_INVOICE_PREFIX = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
# System fonts kept for backward compatibility, followed by the premium
# typography options bundled under app/resources/fonts.
TYPOGRAPHY_FONTS = (
    "Segoe UI",
    "Aptos",
    "Arial",
    "Tahoma",
    "Verdana",
    "Cinzel",
    "Playfair Display",
    "Cormorant Garamond",
    "Montserrat",
    "Poppins",
    "Inter",
)
TYPOGRAPHY_SIZES = ("Comfortable", "Large", "Extra Large")


class SettingsService:
    def __init__(self, db, repository: SettingsRepository | None = None):
        self.db = db
        self.repository = repository or SettingsRepository()

    @staticmethod
    def _defaults_dict() -> dict[str, str]:
        return {
            "shop_name": DEFAULT_SETTINGS.shop_name,
            "shop_address": DEFAULT_SETTINGS.shop_address,
            "shop_phone": DEFAULT_SETTINGS.shop_phone,
            "invoice_footer": DEFAULT_SETTINGS.invoice_footer,
            "currency_code": DEFAULT_SETTINGS.currency_code,
            "currency_symbol": DEFAULT_SETTINGS.currency_symbol,
            "invoice_prefix": DEFAULT_SETTINGS.invoice_prefix,
            "default_invoice_format": DEFAULT_SETTINGS.default_invoice_format,
            "thermal_paper_width": DEFAULT_SETTINGS.thermal_paper_width,
            "low_stock_highlighting": "1",
            "allow_negative_stock": "0",
            "typography_font": DEFAULT_SETTINGS.typography_font,
            "typography_size": DEFAULT_SETTINGS.typography_size,
        }

    @staticmethod
    def _parse_bool(value: str | None, field: str) -> bool:
        if value == "1":
            return True
        if value == "0":
            return False
        raise ValueError(f"{field} must be enabled or disabled")

    @classmethod
    def validate(cls, settings: SettingsData) -> SettingsData:
        if not isinstance(settings, SettingsData):
            raise ValueError("Invalid settings data")

        shop_name = settings.shop_name.strip()
        if not shop_name:
            raise ValueError("Shop name is required")
        if len(shop_name) > 200:
            raise ValueError("Shop name must be 200 characters or fewer")

        address = settings.shop_address.strip()
        phone = settings.shop_phone.strip()
        footer = settings.invoice_footer.strip()
        if len(address) > 500:
            raise ValueError("Shop address must be 500 characters or fewer")
        if len(phone) > 100:
            raise ValueError("Shop phone must be 100 characters or fewer")
        if len(footer) > 500:
            raise ValueError("Invoice footer must be 500 characters or fewer")

        code = settings.currency_code.strip().upper()
        if not _CURRENCY_CODE.fullmatch(code):
            raise ValueError("Currency code must be a 3-letter ISO-style code")

        symbol = settings.currency_symbol.strip()
        if not symbol:
            raise ValueError("Currency symbol is required")
        if len(symbol) > 8:
            raise ValueError("Currency symbol must be 8 characters or fewer")

        prefix = settings.invoice_prefix.strip()
        if not _INVOICE_PREFIX.fullmatch(prefix):
            raise ValueError("Invoice prefix must be 1-20 letters, numbers, '_' or '-'")

        if settings.default_invoice_format not in _FORMATS:
            raise ValueError("Invoice format must be A4 or Thermal")
        if settings.thermal_paper_width not in _WIDTHS:
            raise ValueError("Thermal paper width must be 180mm")
        if not isinstance(settings.low_stock_highlighting, bool):
            raise ValueError("Low-stock highlighting must be enabled or disabled")
        if not isinstance(settings.allow_negative_stock, bool):
            raise ValueError("Negative stock policy must be enabled or disabled")
        if settings.typography_font not in TYPOGRAPHY_FONTS:
            raise ValueError("Unsupported typography font")
        if settings.typography_size not in TYPOGRAPHY_SIZES:
            raise ValueError("Unsupported typography size")

        return replace(
            settings,
            shop_name=shop_name,
            shop_address=address,
            shop_phone=phone,
            invoice_footer=footer,
            currency_code=code,
            currency_symbol=symbol,
            invoice_prefix=prefix,
        )

    def get_settings(self) -> SettingsData:
        with self.db.SessionLocal.begin() as session:
            self.repository.ensure_defaults(session, self._defaults_dict())
            values = self.repository.get_all(session)
        return self._from_values(values)

    def save_settings(self, settings: SettingsData) -> SettingsData:
        validated = self.validate(settings)
        values = {
            "shop_name": validated.shop_name,
            "shop_address": validated.shop_address,
            "shop_phone": validated.shop_phone,
            "invoice_footer": validated.invoice_footer,
            "currency_code": validated.currency_code,
            "currency_symbol": validated.currency_symbol,
            "invoice_prefix": validated.invoice_prefix,
            "default_invoice_format": validated.default_invoice_format,
            "thermal_paper_width": validated.thermal_paper_width,
            "low_stock_highlighting": "1" if validated.low_stock_highlighting else "0",
            "allow_negative_stock": "1" if validated.allow_negative_stock else "0",
            "typography_font": validated.typography_font,
            "typography_size": validated.typography_size,
        }
        with self.db.SessionLocal.begin() as session:
            self.repository.set_values(session, values)
        return validated

    def low_stock_highlighting_enabled(self) -> bool:
        return self.get_settings().low_stock_highlighting

    def allow_negative_stock(self) -> bool:
        return self.get_settings().allow_negative_stock

    @staticmethod
    def _from_values(values: dict[str, str | None]) -> SettingsData:
        defaults = DEFAULT_SETTINGS
        return SettingsData(
            shop_name=values.get("shop_name") or defaults.shop_name,
            shop_address=values.get("shop_address") or "",
            shop_phone=values.get("shop_phone") or "",
            invoice_footer=values.get("invoice_footer") or "",
            currency_code=values.get("currency_code") or defaults.currency_code,
            currency_symbol=values.get("currency_symbol") or defaults.currency_symbol,
            invoice_prefix=values.get("invoice_prefix") or defaults.invoice_prefix,
            default_invoice_format=values.get("default_invoice_format") or defaults.default_invoice_format,
            # Coerce legacy persisted widths (e.g. an existing DB still holding
            # "58mm"/"80mm") to the single supported 180mm format so a later
            # validate() can never fail on a stored value from before this change.
            thermal_paper_width=(values.get("thermal_paper_width") if values.get("thermal_paper_width") in _WIDTHS else defaults.thermal_paper_width),
            low_stock_highlighting=SettingsService._parse_bool(values.get("low_stock_highlighting"), "Low-stock highlighting"),
            allow_negative_stock=SettingsService._parse_bool(values.get("allow_negative_stock"), "Negative stock policy"),
            typography_font=values.get("typography_font") or defaults.typography_font,
            typography_size=values.get("typography_size") or defaults.typography_size,
        )
