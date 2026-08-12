"""Database URL normalization for the supported SQLAlchemy drivers."""

from __future__ import annotations

from sqlalchemy.engine import make_url

_POSTGRES_DRIVER_NAMES = frozenset(
    {
        "postgres",
        "postgresql",
        "postgresql+asyncpg",
        "postgresql+psycopg2",
        "postgresql+psycopg",
    }
)


def _with_driver(db_url: str, driver_name: str) -> str:
    return (
        make_url(db_url)
        .set(drivername=driver_name)
        .render_as_string(hide_password=False)
    )


def normalize_database_url(db_url: str) -> str:
    """Select Psycopg 3 for every supported PostgreSQL URL spelling."""
    parsed = make_url(db_url)
    if parsed.drivername in _POSTGRES_DRIVER_NAMES:
        return _with_driver(db_url, "postgresql+psycopg")
    return db_url


def normalize_async_database_url(db_url: str) -> str:
    """Select the async-capable driver without changing database semantics."""
    parsed = make_url(db_url)
    if parsed.drivername in _POSTGRES_DRIVER_NAMES:
        return _with_driver(db_url, "postgresql+psycopg")
    if parsed.drivername in {"sqlite", "sqlite+pysqlite", "sqlite+aiosqlite"}:
        return _with_driver(db_url, "sqlite+aiosqlite")
    return db_url
