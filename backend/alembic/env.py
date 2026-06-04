import logging
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from app.core.config import get_settings
from app.db.alembic_logging import should_apply_alembic_file_config
from app.db.base import Base
from app.db import models  # noqa: F401 — load all model mappings onto Base.metadata


config = context.config

# Read DATABASE_URL from app settings rather than alembic.ini so .env wins.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Only apply alembic.ini logging config when running as a *standalone* alembic
# CLI (root logger has no handlers yet). When migration runs in-process under the
# desktop launcher / FastAPI, the root logger already carries the launcher's
# FileHandler — calling fileConfig here would replace it and silence all
# post-migration app logs (fill_poller marker, runtime ERROR/WARN) in the log
# file. Skipping it preserves the launcher's handlers. (2026-06-04)
if should_apply_alembic_file_config(
    config.config_file_name, bool(logging.getLogger().handlers)
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
