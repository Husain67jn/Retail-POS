from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database.connection import DatabaseManager


def make_manager(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "data" / "pos.db")
    manager.initialize()
    return manager


def test_foreign_keys_are_enabled_on_every_connection(tmp_path: Path):
    manager = make_manager(tmp_path)
    try:
        with manager.engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

        # Force a second pooled connection and verify the per-connection hook.
        with manager.engine.connect() as conn1, manager.engine.connect() as conn2:
            assert conn1.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert conn2.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        manager.dispose()


def test_wal_mode_and_busy_timeout_are_configured(tmp_path: Path):
    manager = make_manager(tmp_path)
    try:
        with manager.engine.connect() as conn:
            assert str(conn.execute(text("PRAGMA journal_mode")).scalar_one()).lower() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
    finally:
        manager.dispose()


def test_settings_persist_across_manager_restart(tmp_path: Path):
    db_path = tmp_path / "data" / "pos.db"

    first = DatabaseManager(db_path)
    first.initialize()
    with first.engine.begin() as conn:
        conn.execute(
            text("UPDATE settings SET value = :value WHERE key = 'invoice_sequence'"),
            {"value": "42"},
        )
    first.dispose()

    second = DatabaseManager(db_path)
    second.initialize()
    try:
        with second.engine.connect() as conn:
            value = conn.execute(
                text("SELECT value FROM settings WHERE key = 'invoice_sequence'")
            ).scalar_one()
        assert value == "42"
    finally:
        second.dispose()


def test_restart_does_not_destroy_existing_database_data(tmp_path: Path):
    db_path = tmp_path / "data" / "pos.db"

    first = DatabaseManager(db_path)
    first.initialize()
    with first.engine.begin() as conn:
        conn.execute(
            text("UPDATE settings SET value = :value WHERE key = 'invoice_sequence'"),
            {"value": "123"},
        )
    first.dispose()

    second = DatabaseManager(db_path)
    second.initialize()
    try:
        with second.engine.connect() as conn:
            assert conn.execute(
                text("SELECT value FROM settings WHERE key = 'invoice_sequence'")
            ).scalar_one() == "123"
            assert conn.execute(text("SELECT COUNT(*) FROM settings")).scalar_one() == 1
    finally:
        second.dispose()


def test_transaction_rollback_leaves_database_unchanged(tmp_path: Path):
    manager = make_manager(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            with manager.engine.begin() as conn:
                conn.execute(
                    text("UPDATE settings SET value = :value WHERE key = 'invoice_sequence'"),
                    {"value": "999"},
                )
                raise RuntimeError("forced rollback")

        with manager.engine.connect() as conn:
            assert conn.execute(
                text("SELECT value FROM settings WHERE key = 'invoice_sequence'")
            ).scalar_one() == "0"
    finally:
        manager.dispose()


def test_foreign_key_constraint_is_enforced(tmp_path: Path):
    manager = make_manager(tmp_path)
    try:
        with manager.engine.begin() as conn:
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            conn.execute(
                text(
                    "CREATE TABLE child ("
                    "id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER NOT NULL REFERENCES parent(id)"
                    ")"
                )
            )

            with pytest.raises(IntegrityError):
                conn.execute(text("INSERT INTO child(parent_id) VALUES (999)"))
    finally:
        manager.dispose()
