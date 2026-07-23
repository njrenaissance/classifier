"""Alembic migration environment.

Wired to the project's own configuration: the connection URL comes from
``DatabaseSettings.url`` (ADR-0013) rather than a hard-coded ``alembic.ini``
value, and ``target_metadata`` is the ORM ``Base.metadata`` from :mod:`db` so
``--autogenerate`` can diff future revisions against the models.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# The models live under ``src/`` (flat layout, ``pythonpath = ["src"]`` in
# pyproject); add it so this env can import the app config and metadata.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import DatabaseSettings  # noqa: E402
from db import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # ``disable_existing_loggers=False`` so running migrations in-process (the
    # app, or the migration-drift test) never disables loggers configured
    # elsewhere — the Alembic default would silently mute them for the rest of
    # the process (breaking, e.g., pytest's caplog in later tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Resolve the connection URL from the database settings at runtime, so migrations,
# the app, and the DatabaseWriter all read the same source. Only the DB section is
# loaded (not the full Settings), so migrations need no ANTHROPIC_API_KEY.
_database = DatabaseSettings()
if _database.url is None:
    raise RuntimeError("CLASSIFIER__DATABASE_URL must be set to run migrations.")
config.set_main_option("sqlalchemy.url", _database.url.get_secret_value())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
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
    """Run migrations in 'online' mode against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
