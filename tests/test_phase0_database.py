from pathlib import Path

from sqlalchemy import text

from app.database.connection import DatabaseManager


def test_database_initializes_and_health_checks(tmp_path: Path):
    db_path = tmp_path / "data" / "pos.db"
    manager = DatabaseManager(db_path)
    manager.initialize()

    assert db_path.exists()
    assert manager.health_check() is True

    with manager.engine.connect() as conn:
        settings = conn.execute(
            text("SELECT value FROM settings WHERE key = 'invoice_sequence'")
        ).scalar_one()
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal = conn.execute(text("PRAGMA journal_mode")).scalar_one()

    assert settings == "0"
    assert fk == 1
    assert str(journal).lower() == "wal"
    manager.dispose()


def test_database_creation_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "pos.db"
    manager = DatabaseManager(db_path)
    manager.initialize()
    manager.initialize()

    with manager.engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM settings WHERE key = 'invoice_sequence'")
        ).scalar_one()

    assert count == 1
    manager.dispose()
