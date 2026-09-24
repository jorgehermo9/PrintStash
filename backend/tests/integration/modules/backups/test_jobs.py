"""Backups run as Jobs: manual ones on request, automatic ones on their schedule.

A manual backup no longer holds an HTTP request open while the vault is
archived; the route queues ``backup.create`` and the archive's metadata lands in
the Job's result. The daily automatic backup is ``backup.automatic``, whose
schedule comes from the backup configuration and whose domain claim keeps a
resubmitted occurrence from archiving twice in one day. If this goes red, a
backup can silently not happen, happen twice, or report success it did not have.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.db.models import BackupRun, Job, JobState, SystemConfig
from app.modules.backups import jobs as backup_jobs
from app.modules.backups.backup_destination import BackupTrigger
from app.modules.work import service as work_service
from app.modules.work.jobs import jobs
from app.modules.work.submission import nudge
from app.runtime.engine.inline import InlineJobEngine
from tests.factories import build_system_config
from tests.integration._backup_harness import BackupEnv, seed_model_with_blob


def _config(env: BackupEnv, **fields: object) -> None:
    with env.new_session() as session:
        build_system_config(session, **fields)


def _manual_backup(env: BackupEnv, engine: InlineJobEngine) -> str:
    with env.new_session() as session:
        job_id = work_service.request(
            session,
            definition=backup_jobs.CREATE_DEFINITION,
            subject_key="job/manual-backup",
            owner_user_id=None,
        )
        session.commit()
    nudge(backup_jobs.CREATE_DEFINITION)
    engine.drain()
    return job_id


def _runs(env: BackupEnv) -> list[BackupRun]:
    with env.new_session() as session:
        return list(session.exec(select(BackupRun)).all())


class TestCreate:
    def test_archives_a_manual_backup(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid manual\n")

        _manual_backup(backup_env, work_engine)

        assert [run.trigger for run in _runs(backup_env)] == [
            BackupTrigger.MANUAL.value
        ]

    def test_records_the_backup_in_the_job_result(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid result\n")

        job_id = _manual_backup(backup_env, work_engine)

        status = jobs.get(job_id)
        assert status is not None and status.state == "completed"
        assert status.result["backup_id"]

    def test_fails_the_job_without_a_destination(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        _config(backup_env, manual_local_backup_enabled=False)

        job_id = _manual_backup(backup_env, work_engine)

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "backup_destination_required",
        )


class TestAutomatic:
    def _run(self, engine: InlineJobEngine) -> None:
        nudge(backup_jobs.AUTOMATIC_DEFINITION)
        engine.drain()

    def test_archives_with_the_automatic_trigger_when_due(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid auto\n")
        _config(
            backup_env, automatic_backups_enabled=True, automatic_backup_time_utc="00:00"
        )

        self._run(work_engine)

        assert [run.trigger for run in _runs(backup_env)] == [
            BackupTrigger.AUTOMATIC.value
        ]

    def test_archives_once_however_often_the_schedule_is_reconciled(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid once\n")
        _config(
            backup_env, automatic_backups_enabled=True, automatic_backup_time_utc="00:00"
        )

        self._run(work_engine)
        self._run(work_engine)

        assert len(_runs(backup_env)) == 1

    def test_skips_a_day_already_claimed(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        from app.core.time import utcnow

        _config(
            backup_env,
            automatic_backups_enabled=True,
            automatic_backup_time_utc="00:00",
            automatic_backup_last_attempt_at=utcnow(),
        )

        self._run(work_engine)

        assert _runs(backup_env) == []

    def test_queues_nothing_while_automatic_backups_are_off(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        _config(backup_env, automatic_backups_enabled=False)

        self._run(work_engine)

        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(Job).where(Job.kind == backup_jobs.AUTOMATIC_DEFINITION)
                ).all()
                == []
            )

    def test_claims_the_day_even_when_archiving_fails(
        self,
        backup_env: BackupEnv,
        work_engine: InlineJobEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _config(
            backup_env, automatic_backups_enabled=True, automatic_backup_time_utc="00:00"
        )

        def fail_backup(**_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(backup_jobs.backup_creation, "create_backup", fail_backup)

        self._run(work_engine)

        with backup_env.new_session() as session:
            config = session.get(SystemConfig, 1)
            job = session.exec(
                select(Job).where(Job.kind == backup_jobs.AUTOMATIC_DEFINITION)
            ).one()
        assert config is not None and config.automatic_backup_last_attempt_at
        assert job.state == JobState.FAILED
