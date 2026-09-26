"""``594452a753fe`` makes backup runs name the Job that built them.

The next attempt of a Job settles what its previous attempt left open, which
needs the run to name that Job. A run from before the revision names none, so
one an interrupted process left ``running`` would stay running forever: the
upgrade settles it, as the old list-time repair would have. If this goes red,
an upgraded vault shows a backup that never finishes, or a retry that never
ends, and refuses every new retry of that destination as already in progress.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
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

BEFORE = "0906f5b806b5"
AFTER = "594452a753fe"


@dataclass
class Migrated:
    engine: Engine
    config: object

    def value(self, table: str, row_id: str, column: str) -> object:
        with self.engine.connect() as connection:
            return connection.execute(
                text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id}
            ).scalar_one()


def _run(connection, run_id: str, outcome: str) -> None:
    seed_schema_row(
        connection,
        "backup_runs",
        id=run_id,
        trigger="manual",
        outcome=outcome,
        finished_at=None,
    )


def _result(connection, result_id: str, run_id: str, outcome: str) -> None:
    seed_schema_row(
        connection,
        "backup_destination_results",
        id=result_id,
        run_id=run_id,
        outcome=outcome,
        error_code=None,
    )


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
    ids=str,
)
def migrated(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Migrated]:
    """Backups an interrupted process left open, then upgraded."""
    if request.param == "postgres":
        from tests.containers import fresh_postgres_database

        # A database of its own: a downgrade below a merge revision leaves two
        # heads recorded, which would break every later user of a shared one.
        url = fresh_postgres_database("migration")
    else:
        url = f"sqlite:///{tmp_path / 'backup-run-job.sqlite'}"
    engine = create_engine(normalize_database_url(url))
    config = _alembic_config(url)
    try:
        if request.param == "postgres":
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, BEFORE)
        with engine.begin() as connection:
            # Died while publishing its second destination.
            _run(connection, "partial-run", "running")
            _result(connection, "partial-done", "partial-run", "completed")
            _result(connection, "partial-open", "partial-run", "publishing")
            # Died before any destination finished.
            _run(connection, "failed-run", "running")
            _result(connection, "failed-open", "failed-run", "pending")
            # Died after every destination finished, before settling.
            _run(connection, "complete-run", "running")
            _result(connection, "complete-done", "complete-run", "completed")
            # Died before it had any destination.
            _run(connection, "empty-run", "running")
            # A settled run whose failed destination was being retried.
            _run(connection, "retried-run", "partial")
            _result(connection, "retried-done", "retried-run", "completed")
            _result(connection, "retried-open", "retried-run", "publishing")
            for attempt, result in (
                ("attempt-open", "retried-open"),
                ("attempt-done", "retried-done"),
            ):
                seed_schema_row(
                    connection,
                    "backup_retry_attempts",
                    id=attempt,
                    destination_result_id=result,
                    outcome="running",
                    error_code=None,
                    finished_at=None,
                )
        command.upgrade(config, AFTER)
        yield Migrated(engine=engine, config=config)
    finally:
        engine.dispose()


class TestSettleOpenBackups:
    @pytest.mark.parametrize(
        ("run_id", "outcome"),
        [
            ("partial-run", "partial"),
            ("failed-run", "failed"),
            ("complete-run", "completed"),
            ("empty-run", "failed"),
        ],
    )
    def test_a_running_run_settles_from_its_destinations(
        self, migrated: Migrated, run_id: str, outcome: str
    ) -> None:
        assert migrated.value("backup_runs", run_id, "outcome") == outcome
        assert migrated.value("backup_runs", run_id, "finished_at") is not None

    @pytest.mark.parametrize("result_id", ["partial-open", "failed-open"])
    def test_an_open_destination_fails_as_interrupted(
        self, migrated: Migrated, result_id: str
    ) -> None:
        assert (
            migrated.value("backup_destination_results", result_id, "outcome")
            == "failed"
        )
        assert (
            migrated.value("backup_destination_results", result_id, "error_code")
            == "backup_publication_interrupted"
        )

    def test_a_finished_destination_is_kept(self, migrated: Migrated) -> None:
        assert (
            migrated.value("backup_destination_results", "partial-done", "outcome")
            == "completed"
        )

    def test_a_settled_run_keeps_its_outcome(self, migrated: Migrated) -> None:
        assert migrated.value("backup_runs", "retried-run", "outcome") == "partial"

    def test_a_retry_of_an_open_destination_fails(self, migrated: Migrated) -> None:
        assert (
            migrated.value("backup_retry_attempts", "attempt-open", "outcome")
            == "failed"
        )
        assert (
            migrated.value("backup_retry_attempts", "attempt-open", "error_code")
            == "backup_publication_interrupted"
        )

    def test_a_retry_whose_destination_finished_completes(
        self, migrated: Migrated
    ) -> None:
        assert (
            migrated.value("backup_retry_attempts", "attempt-done", "outcome")
            == "completed"
        )
        assert (
            migrated.value("backup_retry_attempts", "attempt-done", "error_code")
            is None
        )

    def test_runs_start_naming_no_job(self, migrated: Migrated) -> None:
        assert migrated.value("backup_runs", "partial-run", "job_id") is None

    def test_a_downgrade_drops_the_job_column(self, migrated: Migrated) -> None:
        command.downgrade(migrated.config, BEFORE)

        columns = {
            column["name"]
            for column in inspect(migrated.engine).get_columns("backup_runs")
        }
        assert "job_id" not in columns
        assert migrated.value("backup_runs", "partial-run", "outcome") == "partial"
