from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.settings import Setting


class SettingsRepository:
    """Persistence-only access to the existing key/value settings table."""

    def get_all(self, session: Session) -> dict[str, str | None]:
        rows = session.scalars(select(Setting)).all()
        return {row.key: row.value for row in rows}

    def ensure_defaults(self, session: Session, defaults: dict[str, str]) -> None:
        existing = set(session.scalars(select(Setting.key)).all())
        missing = [Setting(key=key, value=value) for key, value in defaults.items() if key not in existing]
        if missing:
            session.add_all(missing)
            session.flush()

    def set_values(self, session: Session, values: dict[str, str]) -> None:
        for key, value in values.items():
            setting = session.get(Setting, key)
            if setting is None:
                session.add(Setting(key=key, value=value))
            else:
                setting.value = value
                setting.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.flush()
