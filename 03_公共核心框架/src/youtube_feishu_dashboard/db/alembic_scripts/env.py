from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool
from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.db import models  # noqa: F401
from youtube_feishu_dashboard.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = config.attributes.get("settings") or Settings.load()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url.replace("%", "%%"))
target_metadata = Base.metadata


def ensure_sqlite_parent_exists() -> None:
    url = make_url(settings.sqlalchemy_url)
    database_path = url.database
    if url.drivername == "sqlite" and database_path and database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    ensure_sqlite_parent_exists()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
