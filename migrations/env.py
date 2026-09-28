"""Alembic environment: runs migrations with a sync connection.

The URL comes from `sqlalchemy.url` if set (tests) or from DATABASE_URL (.env).
Async drivers in the URL are swapped for their sync counterpart, since
migrations are a one-off script and do not need async.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from doc_extractor_api.adapters.db import Base
from doc_extractor_api.core.config import get_settings

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

SYNC_DRIVERS = {"sqlite+aiosqlite": "sqlite", "postgresql+asyncpg": "postgresql+psycopg"}


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    for async_driver, sync_driver in SYNC_DRIVERS.items():
        if url.startswith(async_driver + "://"):
            return sync_driver + url[len(async_driver) :]
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
