"""Operations routes and domain modules call on background work.

A Job is visible to its owner and to administrators; to anyone else it does
not exist (404, never 403, so ids cannot be probed). Cancelling withdraws the
subject's intent first, so the reconciler cannot resurrect it, then stops
whatever the engine has in flight. Retrying returns a failed or cancelled
subject to pending and queues the same Job again, unless another Job already
owns the subject or the subject is gone.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

import app.modules.work.service as service
from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    Job,
    JobKind,
    JobState,
    WorkPriority,
)
from app.modules.derivatives.source import subject_key
from app.modules.work.jobs import status_of
from app.modules.work.submission import execution_id
from app.runtime.engine.inline import EngineStatus


@pytest.fixture
def owner(make_user):
    return make_user()


@pytest.fixture
def admin(make_user):
    return make_user(superuser=True)


@pytest.fixture
def mesh(make_model, make_file):
    return make_file(make_model(), filename="part.stl")


@pytest.fixture
def nudged(monkeypatch) -> list[str]:
    names: list[str] = []
    monkeypatch.setattr(service, "nudge", lambda name, **_: names.append(name))
    return names


def _state(session: Session, job_id: str) -> JobState:
    session.expire_all()
    job = session.get(Job, job_id)
    assert job is not None
    return job.state


def _refused(call) -> OperationError:
    with pytest.raises(OperationError) as refused:
        call()
    return refused.value


class TestRequest:
    def test_records_a_queued_job_in_the_callers_transaction(
        self, db_session: Session, owner
    ) -> None:
        job_id = service.request(
            db_session,
            definition=JobKind.SOURCES_SCAN,
            subject_key="library/1",
            owner_user_id=owner.id,
            priority=WorkPriority.BACKFILL,
        )
        db_session.commit()

        job = db_session.get(Job, job_id)
        assert job is not None
        assert (job.state, job.owner_user_id, job.priority) == (
            JobState.QUEUED,
            owner.id,
            WorkPriority.BACKFILL,
        )

    def test_nothing_is_recorded_if_the_caller_rolls_back(
        self, db_session: Session
    ) -> None:
        # The Job and the intent it describes commit together or not at all.
        job_id = service.request(
            db_session,
            definition=JobKind.SOURCES_SCAN,
            subject_key="library/1",
            owner_user_id=None,
        )
        db_session.rollback()

        assert db_session.get(Job, job_id) is None


class TestVisibleTo:
    def test_the_owner_sees_their_job(self, make_job, owner) -> None:
        assert service.visible_to(status_of(make_job(owner=owner)), owner)

    def test_an_administrator_sees_every_job(self, make_job, owner, admin) -> None:
        assert service.visible_to(status_of(make_job(owner=owner)), admin)
        assert service.visible_to(status_of(make_job()), admin)

    def test_another_user_does_not(self, make_job, make_user, owner) -> None:
        assert not service.visible_to(status_of(make_job(owner=owner)), make_user())

    def test_a_system_job_is_for_administrators_only(self, make_job, owner) -> None:
        assert not service.visible_to(status_of(make_job()), owner)


class TestCancel:
    def test_withdraws_the_subjects_intent(
        self, db_session: Session, make_job, owner, mesh
    ) -> None:
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )

        status = service.cancel(job.id, actor=owner)

        assert status.state == JobState.CANCELLED.value
        states = {
            row.kind: row.state
            for row in db_session.exec(
                select(ArtifactDerivative).where(ArtifactDerivative.file_id == mesh.id)
            ).all()
        }
        assert states[DerivativeKind.THUMBNAIL] == DerivativeState.CANCELLED

    def test_stops_the_attempt_the_engine_is_running(
        self, work_engine, make_job, owner
    ) -> None:
        from app.modules.work.submission import submit

        job = make_job(kind=JobKind.SOURCES_SCAN, owner=owner)
        submit(job.id)

        service.cancel(job.id, actor=owner)

        execution = work_engine.executions[execution_id(job.id, 1)]
        assert execution.status is EngineStatus.CANCELLED

    def test_a_job_never_submitted_needs_no_engine_cancel(
        self, db_session: Session, work_engine, make_job, owner, monkeypatch
    ) -> None:
        def unexpected(_execution_id):
            raise AssertionError("nothing was submitted")

        monkeypatch.setattr(work_engine, "cancel", unexpected)
        job = make_job(kind=JobKind.SOURCES_SCAN, owner=owner)

        service.cancel(job.id, actor=owner)

        assert _state(db_session, job.id) == JobState.CANCELLED

    def test_an_unreachable_engine_still_settles_the_job(
        self, db_session: Session, work_engine, make_job, owner, monkeypatch
    ) -> None:
        # The subject is already withdrawn; the reconciler interrupts whatever
        # the engine still runs once it is reachable again.
        from app.modules.work.submission import submit

        job = make_job(kind=JobKind.SOURCES_SCAN, owner=owner)
        submit(job.id)

        def unreachable(_execution_id):
            raise RuntimeError("engine down")

        monkeypatch.setattr(work_engine, "cancel", unreachable)

        assert service.cancel(job.id, actor=owner).state == JobState.CANCELLED.value

    def test_another_users_job_does_not_exist_for_them(
        self, make_job, make_user, owner
    ) -> None:
        job = make_job(owner=owner)

        error = _refused(lambda: service.cancel(job.id, actor=make_user()))

        assert (error.code, error.kind) == ("job_not_found", ErrorKind.NOT_FOUND)

    def test_a_missing_job_does_not_exist(self, admin) -> None:
        error = _refused(lambda: service.cancel("no-such-job", actor=admin))

        assert error.kind is ErrorKind.NOT_FOUND

    def test_a_settled_job_cannot_be_cancelled(self, make_job, owner) -> None:
        job = make_job(owner=owner, state=JobState.COMPLETED)

        error = _refused(lambda: service.cancel(job.id, actor=owner))

        assert (error.code, error.kind) == ("job_not_active", ErrorKind.CONFLICT)


class TestCancelQueued:
    def test_withdraws_every_queued_job_of_one_definition(
        self, db_session: Session, make_job, admin
    ) -> None:
        queued = [make_job(kind=JobKind.SOURCES_SCAN) for _ in range(2)]
        running = make_job(
            kind=JobKind.SOURCES_SCAN, state=JobState.RUNNING, attempts=1
        )
        other = make_job(kind=JobKind.BACKUPS_CREATE)

        assert service.cancel_queued(JobKind.SOURCES_SCAN, actor=admin) == 2

        assert {_state(db_session, job.id) for job in queued} == {JobState.CANCELLED}
        assert _state(db_session, running.id) == JobState.RUNNING
        assert _state(db_session, other.id) == JobState.QUEUED

    def test_is_for_administrators_only(self, make_job, owner) -> None:
        make_job(kind=JobKind.SOURCES_SCAN)

        error = _refused(
            lambda: service.cancel_queued(JobKind.SOURCES_SCAN, actor=owner)
        )

        assert (error.code, error.kind) == ("admin_required", ErrorKind.FORBIDDEN)

    def test_a_definition_this_process_lacks_is_refused(
        self, admin, work_engine
    ) -> None:
        from app.modules.work import catalog as catalog_module
        from app.modules.work.catalog import WorkCatalog

        catalog_module.bind(work_engine, WorkCatalog())

        with pytest.raises(LookupError):
            service.cancel_queued(JobKind.SEARCH_CAPTION, actor=admin)


class TestSupersedeRestored:
    def test_cancels_a_backup_the_restored_database_shows_running(
        self, db_session: Session, make_job
    ) -> None:
        # The archive captured its own backup Job mid-run.
        snapshot = make_job(
            kind=JobKind.BACKUPS_CREATE, state=JobState.RUNNING, attempts=1
        )

        assert service.supersede_restored() == 1

        db_session.expire_all()
        row = db_session.get(Job, snapshot.id)
        assert row is not None and row.state == JobState.CANCELLED
        assert status_of(row).error == "superseded_by_restore"

    def test_leaves_work_the_restore_still_owes(
        self, db_session: Session, make_job
    ) -> None:
        scan = make_job(kind=JobKind.SOURCES_SCAN, state=JobState.RUNNING, attempts=1)

        assert service.supersede_restored() == 0

        assert _state(db_session, scan.id) == JobState.RUNNING

    def test_leaves_a_finished_backup_alone(
        self, db_session: Session, make_job
    ) -> None:
        done = make_job(kind=JobKind.BACKUPS_CREATE, state=JobState.COMPLETED)

        assert service.supersede_restored() == 0

        assert _state(db_session, done.id) == JobState.COMPLETED


class TestRetry:
    def test_queues_the_same_job_again(
        self, db_session: Session, make_job, owner, mesh, make_derivative, nudged
    ) -> None:
        make_derivative(
            mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED, exhausted=True
        )
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(mesh.id),
            owner=owner,
            state=JobState.FAILED,
            resubmits=3,
        )

        status = service.retry(job.id, actor=owner)

        assert status.state == JobState.QUEUED.value
        db_session.expire_all()
        row = db_session.get(Job, job.id)
        assert row is not None
        assert (row.resubmits, row.finished_at) == (0, None)
        assert nudged == [JobKind.DERIVATIVES_MESH]

    def test_returns_the_subject_to_pending(
        self, db_session: Session, make_job, owner, mesh, make_derivative, nudged
    ) -> None:
        make_derivative(
            mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED, exhausted=True
        )
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(mesh.id),
            owner=owner,
            state=JobState.FAILED,
        )

        service.retry(job.id, actor=owner)

        assert (
            db_session.exec(
                select(ArtifactDerivative).where(ArtifactDerivative.file_id == mesh.id)
            ).all()
            == []
        )

    def test_keeps_the_progress_it_had_but_not_the_error(
        self, make_job, owner, mesh, nudged
    ) -> None:
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(mesh.id),
            owner=owner,
            state=JobState.CANCELLED,
            status_json=json.dumps(
                {"result": {"scanned": 3}, "total": 10, "error": "boom"}
            ),
        )

        status = service.retry(job.id, actor=owner)

        assert (status.result, status.total, status.error) == ({"scanned": 3}, 10, None)

    @pytest.mark.parametrize("state", [JobState.QUEUED, JobState.COMPLETED])
    def test_only_a_failed_or_cancelled_job_can_be_retried(
        self, make_job, owner, state: JobState
    ) -> None:
        job = make_job(owner=owner, state=state)

        error = _refused(lambda: service.retry(job.id, actor=owner))

        assert (error.code, error.kind) == ("job_not_retryable", ErrorKind.CONFLICT)

    def test_a_subject_another_job_owns_cannot_be_retried(
        self, make_job, owner
    ) -> None:
        failed = make_job(
            kind=JobKind.SOURCES_SCAN,
            subject="library/1",
            owner=owner,
            state=JobState.FAILED,
        )
        make_job(kind=JobKind.SOURCES_SCAN, subject="library/1")

        error = _refused(lambda: service.retry(failed.id, actor=owner))

        assert (error.code, error.kind) == ("job_subject_busy", ErrorKind.CONFLICT)

    def test_a_subject_that_is_gone_cannot_be_retried(
        self, db_session: Session, make_job, make_model, make_file, owner
    ) -> None:
        trashed = make_file(make_model(), filename="part.stl", trashed=True)
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(trashed.id),
            owner=owner,
            state=JobState.FAILED,
        )

        error = _refused(lambda: service.retry(job.id, actor=owner))

        assert (error.code, error.kind) == ("job_subject_gone", ErrorKind.GONE)
        assert _state(db_session, job.id) == JobState.FAILED

    def test_another_users_job_does_not_exist_for_them(
        self, make_job, make_user, owner
    ) -> None:
        job = make_job(owner=owner, state=JobState.FAILED)

        error = _refused(lambda: service.retry(job.id, actor=make_user()))

        assert error.kind is ErrorKind.NOT_FOUND
