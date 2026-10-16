"""Alembic environment: uses the Jarvis config + Keychain for the connection URL."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from jarvis.config import get_settings
from jarvis.db import database_url
from jarvis.db.models import Base

config = context.config
# Embedded in the running core: keep the core's logging configuration.
if config.config_file_name is not None and not config.attributes.get("embedded"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=database_url(get_settings(), "psycopg"), target_metadata=target_metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(get_settings(), "psycopg"), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
