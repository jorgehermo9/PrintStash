"""Storage Jobs: copying a vault migration, and sampling storage inventory.

``storage.migrate`` has one subject per run that is ``copying``, and its single
step copies batch after batch until the run leaves that state, so the storage
retention the copy holds never outlives the process holding it. It pauses
while a restore runs and stops when its Job is cancelled. A run whose batches
keep failing parks the source for a while and fails the Job, instead of
retrying hot; the per-object errors stay on the run for the administrator.
``storage.inventory`` is an hourly sample once the vault is configured.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Job, JobKind, JobState, VaultMigrationRun, WorkPriority
from app.modules.storage import jobs as storage_jobs
from app.modules.storage.jobs import MigrationSource
from app.modules.storage.vault_migration import VaultMigrations
from app.modules.work.sources import idle_window, mark_idle
from app.modules.work.submission import submit

SOURCE = MigrationSource()
DEFINITIONS = {definition.name: definition for definition in storage_jobs.definitions()}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(storage_jobs.time, "sleep", lambda _seconds: None)


def _subject(run: VaultMigrationRun) -> str:
    return f"vault_migration_run/{run.id}"


def _job(session: Session, job_id: str) -> Job:
    session.expire_all()
    job = session.get(Job, job_id)
    assert job is not None
    return job


def _copy(work_engine, make_job, run: VaultMigrationRun) -> str:
    job = make_job(kind=JobKind.STORAGE_MIGRATE, subject=_subject(run))
    submit(job.id)
    work_engine.run_one()
    return job.id


def _advance_until(batches: int, run_id: str):
    """A stand-in copy: `batches` batches, then the run leaves `copying`."""
    from app.db.session import get_session_factory

    calls: list[str] = []

    def advance(_self, requested: str, *, batch_size: int) -> dict:
        assert requested == run_id
        calls.append(requested)
        if len(calls) >= batches:
            with get_session_factory().scoped_session() as session:
                run = session.get(VaultMigrationRun, run_id)
                assert run is not None
                run.state = "verifying"
                session.add(run)
                session.commit()
        return {"copied": batch_size}

    return advance, calls


class TestMigrationSource:
    def test_a_copying_run_is_interactive_work(
        self, db_session: Session, make_vault_migration
    ) -> None:
        run = make_vault_migration(state="copying")

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert (item.subject_key, item.priority) == (
            _subject(run),
            WorkPriority.INTERACTIVE,
        )

    @pytest.mark.parametrize("state", ["planned", "verifying", "completed", "failed"])
    def test_a_run_that_is_not_copying_is_not_offered(
        self, db_session: Session, make_vault_migration, state: str
    ) -> None:
        make_vault_migration(state=state)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_a_parked_source_offers_nothing(
        self, db_session: Session, make_vault_migration
    ) -> None:
        make_vault_migration(state="copying")
        mark_idle(JobKind.STORAGE_MIGRATE, seconds=60)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_a_parked_source_is_due_when_its_window_ends(
        self, db_session: Session
    ) -> None:
        now = utcnow()
        mark_idle(JobKind.STORAGE_MIGRATE, seconds=60, now=now)

        due = SOURCE.next_due(db_session, now=now)

        assert due is not None
        assert abs((due - (now + timedelta(seconds=60))).total_seconds()) < 1

    def test_an_unparked_source_has_no_due_time(self, db_session: Session) -> None:
        assert SOURCE.next_due(db_session, now=utcnow()) is None


class TestCopy:
    def test_copies_batches_until_the_run_leaves_copying(
        self,
        db_session: Session,
        work_engine,
        make_job,
        make_vault_migration,
        monkeypatch,
    ) -> None:
        run = make_vault_migration(state="copying")
        advance, calls = _advance_until(3, run.id)
        monkeypatch.setattr(VaultMigrations, "advance", advance)

        job_id = _copy(work_engine, make_job, run)

        assert len(calls) == 3
        job = _job(db_session, job_id)
        assert job.state == JobState.COMPLETED

    def test_waits_out_a_restore(
        self,
        db_session: Session,
        work_engine,
        make_job,
        make_vault_migration,
        monkeypatch,
    ) -> None:
        from app.runtime import maintenance

        run = make_vault_migration(state="copying")
        advance, calls = _advance_until(1, run.id)
        monkeypatch.setattr(VaultMigrations, "advance", advance)
        restoring = [True, True]
        monkeypatch.setattr(
            maintenance,
            "restore_in_progress",
            lambda *_: restoring.pop(0) if restoring else False,
        )

        _copy(work_engine, make_job, run)

        assert calls == [run.id]
        assert restoring == []

    def test_a_cancelled_job_stops_copying(
        self,
        db_session: Session,
        work_engine,
        make_job,
        make_vault_migration,
        monkeypatch,
    ) -> None:
        from app.modules.work.jobs import jobs

        run = make_vault_migration(state="copying")
        job = make_job(kind=JobKind.STORAGE_MIGRATE, subject=_subject(run))
        calls: list[str] = []

        def advance(_self, requested: str, *, batch_size: int) -> dict:
            calls.append(requested)
            jobs.finish(job.id, state=JobState.CANCELLED)
            return {}

        monkeypatch.setattr(VaultMigrations, "advance", advance)
        submit(job.id)

        work_engine.run_one()

        assert calls == [run.id]
        assert _job(db_session, job.id).state == JobState.CANCELLED

    def test_repeated_failures_park_the_source(
        self,
        db_session: Session,
        work_engine,
        make_job,
        make_vault_migration,
        monkeypatch,
    ) -> None:
        run = make_vault_migration(state="copying")
        calls: list[str] = []

        def failing(_self, requested: str, *, batch_size: int) -> dict:
            calls.append(requested)
            raise OSError("provider said no")

        monkeypatch.setattr(VaultMigrations, "advance", failing)

        job_id = _copy(work_engine, make_job, run)

        assert len(calls) == storage_jobs._MAX_CONSECUTIVE_ERRORS
        assert _job(db_session, job_id).state == JobState.FAILED
        assert idle_window(db_session, JobKind.STORAGE_MIGRATE) is not None

    def test_a_failure_never_shows_the_providers_message(
        self,
        db_session: Session,
        work_engine,
        make_job,
        make_vault_migration,
        monkeypatch,
    ) -> None:
        # An adapter exception can carry credentials.
        run = make_vault_migration(state="copying")

        def failing(_self, _requested: str, *, batch_size: int) -> dict:
            raise OSError("s3://AKIASECRET:hunter2@bucket refused")

        monkeypatch.setattr(VaultMigrations, "advance", failing)

        job_id = _copy(work_engine, make_job, run)

        error = _job(db_session, job_id).status_json
        assert "hunter2" not in error and "AKIASECRET" not in error

    def test_a_retry_is_always_allowed(
        self, db_session: Session, make_vault_migration
    ) -> None:
        run = make_vault_migration(state="copying")

        assert (
            DEFINITIONS[JobKind.STORAGE_MIGRATE].retry(db_session, _subject(run))
            is True
        )


class TestInventory:
    def test_samples_hourly_once_the_vault_is_set_up(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.modules.administration import runtime_config

        monkeypatch.setattr(runtime_config, "is_configured", lambda _session: True)
        source = DEFINITIONS[JobKind.STORAGE_INVENTORY].source
        assert source is not None

        assert source.cron(db_session) == "15 * * * *"  # type: ignore[attr-defined]

    def test_waits_for_setup(self, db_session: Session) -> None:
        # An unconfigured vault has no storage to sample.
        source = DEFINITIONS[JobKind.STORAGE_INVENTORY].source
        assert source is not None

        assert source.cron(db_session) is None  # type: ignore[attr-defined]
