"""Submitting a Job's next attempt, and nudging the reconciler.

``submit`` hands the next attempt to the engine and records it only once the
engine accepted it, so a crash in between re-derives the same execution id.
``nudge`` is how hot paths ask for work: it stamps the dirty mark and enqueues
a reconcile pass unless one of at least its priority is already waiting. It
never raises into the caller, because the tick recovers a lost nudge.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import JobState, ReconcileCursor, WorkPriority
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import RECONCILE_DEFINITION
from app.modules.work.contracts import SubmitOutcome
from app.modules.work.submission import (
    execution_id,
    forget_queued_passes,
    nudge,
    nudge_after_commit,
    nudge_all,
    submit,
)


def _passes(engine, source: str) -> list:
    return [
        execution
        for execution in engine.executions.values()
        if execution.submission.definition == RECONCILE_DEFINITION
        and execution.submission.subject_key == source
    ]


def _cursor(session: Session, source: str) -> ReconcileCursor:
    session.expire_all()
    cursor = session.get(ReconcileCursor, source)
    assert cursor is not None
    return cursor


class TestSubmit:
    def test_an_accepted_submission_counts_as_the_next_attempt(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind="library.scan")

        outcome = submit(job.id)

        assert outcome is SubmitOutcome.ACCEPTED
        db_session.refresh(job)
        assert job.attempts == 1
        execution = work_engine.executions[execution_id(job.id, 1)]
        assert execution.submission.dedupe_key == f"library.scan|{job.subject_key}"

    def test_an_interrupted_job_is_queued_again_on_resubmission(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind="library.scan", state=JobState.INTERRUPTED, attempts=1)

        submit(job.id)

        db_session.refresh(job)
        assert (job.state, job.attempts) == (JobState.QUEUED, 2)

    @pytest.mark.parametrize(
        "state", [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED]
    )
    def test_a_settled_job_has_nothing_to_submit(
        self, work_engine, make_job, state: JobState
    ) -> None:
        job = make_job(kind="library.scan", state=state)

        assert submit(job.id) is None
        assert work_engine.executions == {}

    def test_a_missing_job_has_nothing_to_submit(self, work_engine) -> None:
        assert submit("no-such-job") is None

    def test_a_deduplicated_attempt_is_not_recorded(
        self, work_engine, make_job, db_session: Session, monkeypatch
    ) -> None:
        job = make_job(kind="library.scan")
        monkeypatch.setattr(
            work_engine, "submit", lambda _submission: SubmitOutcome.DEDUPLICATED
        )

        assert submit(job.id) is SubmitOutcome.DEDUPLICATED
        db_session.refresh(job)
        assert job.attempts == 0

    def test_a_partitioned_lane_is_keyed_by_partition_not_dedupe(
        self, work_engine, make_job
    ) -> None:
        # Engines cannot deduplicate a partitioned queue; the active-subject
        # claim on the Job row is what keeps one delivery single-flight.
        job = make_job(kind="notify.deliver", subject="channel/7/delivery/9")

        submit(job.id)

        submission = work_engine.executions[execution_id(job.id, 1)].submission
        assert (submission.partition_key, submission.dedupe_key) == ("7", None)


class TestNudge:
    def test_a_nudge_queues_a_pass_of_a_dirty_source(
        self, work_engine, db_session: Session
    ) -> None:
        nudge("library.scan")

        cursor = _cursor(db_session, "library.scan")
        assert cursor.nudged_at is not None
        assert cursor.pass_queued_at is not None
        (queued,) = _passes(work_engine, "library.scan")
        assert queued.submission.priority is WorkPriority.INTERACTIVE

    def test_a_second_nudge_rides_on_the_queued_pass(self, work_engine) -> None:
        nudge("library.scan")
        nudge("library.scan")

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_a_backfill_nudge_rides_on_any_queued_pass(self, work_engine) -> None:
        nudge("library.scan")
        nudge("library.scan", priority=WorkPriority.BACKFILL)

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_an_interactive_nudge_does_not_wait_behind_a_backfill_pass(
        self, work_engine
    ) -> None:
        # Regression: an upload just after startup waited behind the startup
        # sweep's backfill pass for the same source.
        nudge("library.scan", priority=WorkPriority.BACKFILL)

        nudge("library.scan")

        priorities = sorted(
            execution.submission.priority.value
            for execution in _passes(work_engine, "library.scan")
        )
        assert priorities == ["backfill", "interactive"]

    def test_a_pass_queued_longer_than_the_grace_is_presumed_lost(
        self, work_engine, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source="library.scan",
                pass_queued_at=utcnow()
                - timedelta(seconds=settings.jobs_submit_grace_seconds + 1),
            )
        )
        db_session.commit()

        nudge("library.scan")

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_a_delayed_nudge_leaves_the_dirty_mark_alone(
        self, work_engine, db_session: Session
    ) -> None:
        nudge("library.scan", delay=45)

        cursor = _cursor(db_session, "library.scan")
        assert (cursor.nudged_at, cursor.pass_queued_at) == (None, None)
        (delayed,) = _passes(work_engine, "library.scan")
        assert delayed.submission.delay_seconds == 45

    def test_does_nothing_without_a_bound_engine(
        self, work_engine, db_session: Session
    ) -> None:
        catalog_module.bind(None, None)

        nudge("library.scan")

        assert db_session.exec(select(ReconcileCursor)).all() == []

    def test_an_unknown_source_never_raises_into_the_caller(
        self, work_engine, db_session: Session
    ) -> None:
        nudge("no.such.definition")

        assert db_session.exec(select(ReconcileCursor)).all() == []

    def test_an_engine_that_refuses_never_raises_into_the_caller(
        self, work_engine, monkeypatch
    ) -> None:
        def refuse(_submission):
            raise RuntimeError("engine down")

        monkeypatch.setattr(work_engine, "submit", refuse)

        nudge("library.scan")


class TestNudgeAfterCommit:
    def test_nudges_once_the_transaction_commits(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, "library.scan")
        assert _passes(work_engine, "library.scan") == []

        db_session.commit()

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_a_rolled_back_transaction_nudges_nothing(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, "library.scan")

        db_session.rollback()

        assert _passes(work_engine, "library.scan") == []

    def test_many_records_in_one_transaction_nudge_once(
        self, work_engine, db_session: Session
    ) -> None:
        # A bulk edit records hundreds of changes; its nudge is still one.
        for _ in range(3):
            nudge_after_commit(db_session, "library.scan")

        db_session.commit()

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_a_released_savepoint_waits_for_the_real_commit(
        self, work_engine, db_session: Session
    ) -> None:
        # SQLAlchemy reports a savepoint's release as a commit. Nudging there
        # writes the cursor on a second connection while this transaction
        # still holds SQLite's write lock: the nudge waits on its own caller.
        nudge_after_commit(db_session, "library.scan")
        with db_session.begin_nested():
            pass
        assert _passes(work_engine, "library.scan") == []

        db_session.commit()

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_a_rolled_back_savepoint_keeps_the_outer_nudge(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, "library.scan")
        nested = db_session.begin_nested()
        nested.rollback()

        db_session.commit()

        assert len(_passes(work_engine, "library.scan")) == 1

    def test_the_next_transaction_nudges_again(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, "library.scan")
        db_session.commit()
        work_engine.drain()

        nudge_after_commit(db_session, "library.scan")
        db_session.commit()

        assert len(_passes(work_engine, "library.scan")) == 2


class TestNudgeAll:
    def test_nudges_every_definition_as_backfill(self, work_engine) -> None:
        nudge_all()

        submitted = {
            execution.submission.subject_key: execution.submission.priority
            for execution in work_engine.executions.values()
        }
        assert set(submitted) == set(work_engine.catalog.definitions)
        assert set(submitted.values()) == {WorkPriority.BACKFILL}


class TestForgetQueuedPasses:
    def test_clears_only_the_marks_that_are_set(self, db_session: Session) -> None:
        db_session.add(
            ReconcileCursor(source="a", pass_queued_at=utcnow() - timedelta(seconds=5))
        )
        db_session.add(ReconcileCursor(source="b"))
        db_session.commit()

        assert forget_queued_passes() == 1

        db_session.expire_all()
        assert all(
            row.pass_queued_at is None
            for row in db_session.exec(select(ReconcileCursor)).all()
        )
