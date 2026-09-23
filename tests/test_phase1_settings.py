from pathlib import Path

import pytest
from sqlalchemy import text

from app.database.connection import DatabaseManager
from app.services.settings_service import DEFAULT_SETTINGS, SettingsData, SettingsService


def make_manager(tmp_path: Path) -> DatabaseManager:
    db = DatabaseManager(tmp_path / "data" / "pos.db")
    db.initialize()
    return db


def test_default_settings_are_created(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        settings = SettingsService(db).get_settings()
        assert settings == DEFAULT_SETTINGS
    finally:
        db.dispose()


def test_settings_can_be_saved_and_loaded(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = SettingsService(db)
        saved = service.save_settings(SettingsData(
            shop_name="Mughal Electric Store",
            shop_address="Main Road",
            shop_phone="03001234567",
            invoice_footer="Thank you",
            currency_code="PKR",
            currency_symbol="Rs.",
            invoice_prefix="MES",
            default_invoice_format="Thermal",
            thermal_paper_width="180mm",
            low_stock_highlighting=False,
            allow_negative_stock=True,
        ))
        assert saved.shop_name == "Mughal Electric Store"
        assert service.get_settings() == saved
    finally:
        db.dispose()


def test_settings_persist_after_database_restart(tmp_path: Path):
    db_path = tmp_path / "data" / "pos.db"
    first = DatabaseManager(db_path)
    first.initialize()
    try:
        service = SettingsService(first)
        service.save_settings(SettingsData(
            shop_name="Persistent Shop", shop_address="A", shop_phone="B", invoice_footer="C",
            currency_code="PKR", currency_symbol="Rs.", invoice_prefix="PERSIST",
            default_invoice_format="A4", thermal_paper_width="180mm",
            low_stock_highlighting=False, allow_negative_stock=True,
        ))
    finally:
        first.dispose()

    second = DatabaseManager(db_path)
    second.initialize()
    try:
        settings = SettingsService(second).get_settings()
        assert settings.shop_name == "Persistent Shop"
        assert settings.low_stock_highlighting is False
        assert settings.allow_negative_stock is True
    finally:
        second.dispose()


def test_invalid_settings_are_rejected(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = SettingsService(db)
        base = service.get_settings()
        invalid = [
            ("shop name", base.__class__("", base.shop_address, base.shop_phone, base.invoice_footer, base.currency_code, base.currency_symbol, base.invoice_prefix, base.default_invoice_format, base.thermal_paper_width, base.low_stock_highlighting, base.allow_negative_stock)),
            ("currency", base.__class__(base.shop_name, base.shop_address, base.shop_phone, base.invoice_footer, "US", base.currency_symbol, base.invoice_prefix, base.default_invoice_format, base.thermal_paper_width, base.low_stock_highlighting, base.allow_negative_stock)),
            ("prefix", base.__class__(base.shop_name, base.shop_address, base.shop_phone, base.invoice_footer, base.currency_code, base.currency_symbol, "bad prefix!", base.default_invoice_format, base.thermal_paper_width, base.low_stock_highlighting, base.allow_negative_stock)),
            ("format", base.__class__(base.shop_name, base.shop_address, base.shop_phone, base.invoice_footer, base.currency_code, base.currency_symbol, base.invoice_prefix, "Letter", base.thermal_paper_width, base.low_stock_highlighting, base.allow_negative_stock)),
            ("width", base.__class__(base.shop_name, base.shop_address, base.shop_phone, base.invoice_footer, base.currency_code, base.currency_symbol, base.invoice_prefix, base.default_invoice_format, "100mm", base.low_stock_highlighting, base.allow_negative_stock)),
            ("boolean", base.__class__(base.shop_name, base.shop_address, base.shop_phone, base.invoice_footer, base.currency_code, base.currency_symbol, base.invoice_prefix, base.default_invoice_format, base.thermal_paper_width, "yes", base.allow_negative_stock)),
        ]
        for field, value in invalid:
            with pytest.raises(ValueError):
                service.save_settings(value)
    finally:
        db.dispose()


def test_invalid_settings_do_not_overwrite_valid_persisted_settings(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = SettingsService(db)
        valid = service.save_settings(SettingsData(
            shop_name="Valid Shop", shop_address="Address", shop_phone="Phone", invoice_footer="Footer",
            currency_code="PKR", currency_symbol="Rs.", invoice_prefix="VALID",
            default_invoice_format="A4", thermal_paper_width="180mm",
            low_stock_highlighting=True, allow_negative_stock=False,
        ))
        invalid = SettingsData(
            shop_name="", shop_address="Changed", shop_phone="", invoice_footer="",
            currency_code="PKR", currency_symbol="Rs.", invoice_prefix="VALID",
            default_invoice_format="A4", thermal_paper_width="180mm",
            low_stock_highlighting=False, allow_negative_stock=True,
        )
        with pytest.raises(ValueError):
            service.save_settings(invalid)
        assert service.get_settings() == valid
    finally:
        db.dispose()


def test_invoice_sequence_is_preserved(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE settings SET value='57' WHERE key='invoice_sequence'"))
        service = SettingsService(db)
        service.get_settings()
        service.save_settings(service.get_settings())
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT value FROM settings WHERE key='invoice_sequence'")).scalar_one() == "57"
    finally:
        db.dispose()


def test_low_stock_highlighting_setting_persists(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = SettingsService(db)
        settings = service.get_settings()
        service.save_settings(settings.__class__(
            settings.shop_name, settings.shop_address, settings.shop_phone, settings.invoice_footer,
            settings.currency_code, settings.currency_symbol, settings.invoice_prefix,
            settings.default_invoice_format, settings.thermal_paper_width, False, settings.allow_negative_stock,
        ))
        assert service.get_settings().low_stock_highlighting is False
        assert service.low_stock_highlighting_enabled() is False
    finally:
        db.dispose()


def test_allow_negative_stock_setting_persists(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = SettingsService(db)
        settings = service.get_settings()
        service.save_settings(settings.__class__(
            settings.shop_name, settings.shop_address, settings.shop_phone, settings.invoice_footer,
            settings.currency_code, settings.currency_symbol, settings.invoice_prefix,
            settings.default_invoice_format, settings.thermal_paper_width, settings.low_stock_highlighting, True,
        ))
        assert service.get_settings().allow_negative_stock is True
        assert service.allow_negative_stock() is True
    finally:
        db.dispose()
