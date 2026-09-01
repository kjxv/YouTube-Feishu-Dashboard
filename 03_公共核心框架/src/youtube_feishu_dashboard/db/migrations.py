from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.db.database import Database


def alembic_config(settings: Settings) -> Config:
    config = Config(str(settings.project_root / "alembic.ini"))
    config.set_main_option("script_location", "youtube_feishu_dashboard.db:alembic_scripts")
    config.attributes["settings"] = settings
    return config


def upgrade_database(settings: Settings, revision: str = "head") -> None:
    command.upgrade(alembic_config(settings), revision)


def migration_status(settings: Settings, database: Database) -> tuple[str | None, tuple[str, ...]]:
    with database.engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    heads = tuple(ScriptDirectory.from_config(alembic_config(settings)).get_heads())
    return current, heads
