"""Alembic environment.

Migrations use a normal (sync) engine even though the app is async, because
Alembic is run by hand from the command line. The database URL is read from
the environment.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import settings
from app.models import Base  # noqa: F401 (registers every table on Base.metadata)

config = context.config
config.set_main_option("sqlalchemy.url", settings.postgres_sync_dsn)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.postgres_sync_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        # Migrations run while the API is serving traffic. An ALTER TABLE that
        # waits for its lock makes every later query on the table wait too, so we
        # give up after 5 s. The migration fails and can be run again.
        connection.execute(text("SET lock_timeout = '5s'"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
