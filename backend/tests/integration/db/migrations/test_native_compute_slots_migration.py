"""``a0c0a39f9d66`` drops the native compute permits, reversibly.

Native work is bounded by the job engine's lanes and by each embedding
subprocess's own memory kill, so the database permit table goes. Its permits
were leases, never history, so nothing is carried forward. If this goes red,
an installation either keeps a table nothing reads or, on PostgreSQL, cannot
downgrade past this revision at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
)

BEFORE = "a83ea973fad4"
DROPPED = "native_compute_slots"


@dataclass
class Migrated:
    engine: Engine
    config: object

    def tables(self) -> set[str]:
        return set(inspect(self.engine).get_table_names())


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
    ids=str,
)
def migrated(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Migrated]:
    """A database with a permit in use, then upgraded to head."""
    if request.param == "postgres":
        from tests.containers import fresh_postgres_database

        # A database of its own: a downgrade below a merge revision leaves two
        # heads recorded, which would break every later user of a shared one.
        url = fresh_postgres_database("migration")
    else:
        url = f"sqlite:///{tmp_path / 'compute-slots.sqlite'}"
    engine = create_engine(normalize_database_url(url))
    config = _alembic_config(url)
    try:
        if request.param == "postgres":
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, BEFORE)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO native_compute_slots "
                    "(slot_number, lease_token, created_at, updated_at) "
                    "VALUES (1, 'held', :now, :now)"
                ),
                {"now": datetime(2026, 1, 1)},
            )
        command.upgrade(config, "head")
        yield Migrated(engine=engine, config=config)
    finally:
        engine.dispose()


class TestDropNativeComputeSlots:
    def test_the_permit_table_is_gone(self, migrated: Migrated) -> None:
        assert DROPPED not in migrated.tables()

    def test_a_downgrade_restores_an_empty_permit_table(
        self, migrated: Migrated
    ) -> None:
        command.downgrade(migrated.config, BEFORE)

        assert DROPPED in migrated.tables()
        with migrated.engine.connect() as connection:
            count = connection.execute(text(f"SELECT COUNT(*) FROM {DROPPED}"))
            assert count.scalar_one() == 0

    def test_a_downgrade_keeps_one_permit_per_slot_number(
        self, migrated: Migrated
    ) -> None:
        command.downgrade(migrated.config, BEFORE)

        unique = {
            index["name"]: index["unique"]
            for index in inspect(migrated.engine).get_indexes(DROPPED)
        }
        assert unique["ix_native_compute_slots_slot_number"]
