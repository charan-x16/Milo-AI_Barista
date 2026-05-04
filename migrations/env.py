"""Alembic migration runner for Milo memory tables."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from cafe.agents.memory.storage import (
    APP_MEMORY_METADATA,
    _normalize_async_database_url,
)
from cafe.config import get_settings


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = APP_MEMORY_METADATA


def _configured_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("database_url")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().memory_database_url
    )


def run_migrations_offline() -> None:
    normalized_url, _ = _normalize_async_database_url(_configured_url())
    context.configure(
        url=normalized_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    normalized_url, kwargs = _normalize_async_database_url(_configured_url())
    connectable = create_async_engine(normalized_url, **kwargs)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
