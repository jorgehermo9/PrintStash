from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import Connection, engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context
from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.url import normalize_database_url

config = context.config
default_url = "sqlite:///./dev.sqlite"
if config.get_main_option("sqlalchemy.url") == default_url:
    config.set_main_option("sqlalchemy.url", normalize_database_url(settings.db_url))

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would set .disabled on
    # every app.* logger not named in alembic.ini — silently muting application
    # logging for the rest of the process (and breaking caplog in any test that
    # runs a migration before asserting on app logs). Migrations configure only
    # their own logging; they must never hijack the app's. Matches the intent in
    # app/db/migrate.py, which sidesteps fileConfig entirely for the same reason.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render SQLModel's own column types as plain SQLAlchemy ones.

    Autogenerate reaches for the type object it found on the model, which for a
    `str` field is `sqlmodel.sql.sqltypes.AutoString`. Two problems with letting that
    into a migration file: the generated script does not import `sqlmodel`, so it
    fails at `NameError: name 'sqlmodel' is not defined`; and a migration is a
    historical record that should not depend on the ORM layer's internals, which are
    free to move.

    `AutoString` is `sa.String` with a length, so rendering it as one loses nothing.
    """
    if type_ == "type" and type(obj).__module__.startswith("sqlmodel"):
        length = getattr(obj, "length", None)
        return f"sa.String(length={length})" if length else "sa.String()"
    return False


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def _configure_context(
    *,
    connection: Connection | None = None,
    url: str | None = None,
) -> None:
    kwargs = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "render_item": _render_item,
        "render_as_batch": (
            connection.dialect.name == "sqlite"
            if connection
            else _is_sqlite_url(url or "")
        ),
    }
    if connection is not None:
        kwargs["connection"] = connection
    else:
        kwargs["url"] = url
        kwargs["literal_binds"] = True
        kwargs["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**kwargs)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    _configure_context(url=url)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _configure_context(connection=connection)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
