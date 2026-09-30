"""``0906f5b806b5`` hands similarity runs to the job engine.

A run keeps what it is (scope, settings, resumable checkpoint, counters); it
loses the lease columns that let processes claim it, because the engine now
runs one execution per run and only a write fence (``writer``) remains. The
fence starts empty: whichever attempt runs next takes it. If this goes red,
an upgrade either loses a scan's progress or, on PostgreSQL, cannot downgrade.
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
    seed_schema_row,
)

BEFORE = "a0c0a39f9d66"
AFTER = "0906f5b806b5"


@dataclass
class Migrated:
    engine: Engine
    config: object

    def columns(self) -> set[str]:
        return {
            column["name"]
            for column in inspect(self.engine).get_columns("similarity_runs")
        }

    def row(self) -> dict:
        with self.engine.connect() as connection:
            return dict(
                connection.execute(text("SELECT * FROM similarity_runs"))
                .mappings()
                .one()
            )


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
    ids=str,
)
def migrated(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Migrated]:
    """A run another process held mid-scan, then upgraded past the handover."""
    if request.param == "postgres":
        from tests.containers import fresh_postgres_database

        # A database of its own: a downgrade below a merge revision leaves two
        # heads recorded, which would break every later user of a shared one.
        url = fresh_postgres_database("migration")
    else:
        url = f"sqlite:///{tmp_path / 'similarity-writer.sqlite'}"
    engine = create_engine(normalize_database_url(url))
    config = _alembic_config(url)
    try:
        if request.param == "postgres":
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, BEFORE)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "similarity_runs",
                id=1,
                scope="library",
                scope_ids_json="[]",
                algorithm_version="geometry-v1",
                state="running",
                phase="candidates",
                checkpoint_json='{"file_id": 42}',
                counters_json='{"ready": 3}',
                lease_token="held-elsewhere",
                lease_expires_at=datetime(2026, 1, 1),
                last_activity_at=datetime(2026, 1, 1),
            )
        command.upgrade(config, AFTER)
        yield Migrated(engine=engine, config=config)
    finally:
        engine.dispose()


class TestHandOverSimilarityRuns:
    def test_a_run_keeps_its_progress(self, migrated: Migrated) -> None:
        row = migrated.row()

        assert (row["state"], row["phase"], row["checkpoint_json"]) == (
            "running",
            "candidates",
            '{"file_id": 42}',
        )

    def test_the_lease_is_gone(self, migrated: Migrated) -> None:
        columns = migrated.columns()

        assert not {"lease_token", "lease_expires_at", "last_activity_at"} & columns

    def test_the_write_fence_starts_empty(self, migrated: Migrated) -> None:
        # The next attempt the engine runs takes it.
        assert migrated.row()["writer"] is None

    def test_a_downgrade_restores_the_lease_columns(self, migrated: Migrated) -> None:
        command.downgrade(migrated.config, BEFORE)

        columns = migrated.columns()
        assert {"lease_token", "lease_expires_at", "last_activity_at"} <= columns
        assert "writer" not in columns
        assert migrated.row()["checkpoint_json"] == '{"file_id": 42}'
