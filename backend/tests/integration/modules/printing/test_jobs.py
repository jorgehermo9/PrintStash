"""The ``printing.dispatch`` Job: when the fleet queue needs a dispatcher.

Dispatch has one subject (the fleet queue), so its source answers a single
question: is there anything a dispatcher could act on? Queued work that no
dispatcher has claimed is; so is a job stranded mid-upload by a dispatcher that
died, because only a dispatch pass settles it.

A pass that could route nothing (every printer busy) parks the source for a
while, or its own completion nudge would resubmit it in a tight loop. Parked,
it ignores the queue it already failed to route and wakes only for work queued
since; ``wake_dispatch`` (a new job, a printer freed) unparks it at once.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlmodel import Session, select

import app.modules.work as work
from app.core.time import utcnow
from app.db.models import Job, JobState, PrintJobState, ReconcileCursor, WorkPriority
from app.modules.printing import jobs as dispatch_jobs
from app.modules.printing import printer_jobs
from app.modules.work.sources import idle_window, mark_idle
from tests.factories import a_gcode_artifact, build_print_job
from tests.integration.api.v1._ingest_assertions import drain_work

SOURCE = dispatch_jobs.DispatchSource()


def _pending(session: Session, *, now=None, limit: int = 10):
    return SOURCE.pending(session, now=now or utcnow(), limit=limit)


@pytest.fixture
def artifact(db_session: Session):
    return a_gcode_artifact(db_session, "Fleet cube")


class TestDispatchSource:
    def test_an_empty_queue_needs_no_dispatcher(self, db_session: Session) -> None:
        assert _pending(db_session) == []

    def test_queued_work_needs_one_dispatcher(
        self, db_session: Session, artifact
    ) -> None:
        build_print_job(db_session, artifact)
        build_print_job(db_session, artifact)

        (item,) = _pending(db_session)

        assert (item.subject_key, item.priority) == (
            dispatch_jobs.SUBJECT,
            WorkPriority.INTERACTIVE,
        )

    def test_work_a_dispatcher_already_claimed_is_not_pending(
        self, db_session: Session, artifact
    ) -> None:
        build_print_job(db_session, artifact, dispatch_claimed_at=utcnow())

        assert _pending(db_session) == []

    def test_a_job_stranded_mid_upload_needs_a_dispatcher(
        self, db_session: Session, artifact
    ) -> None:
        # Only a dispatch pass settles it as outcome-unknown; nothing else will.
        build_print_job(
            db_session,
            artifact,
            state=PrintJobState.UPLOADING,
            dispatch_claimed_at=utcnow(),
        )

        assert len(_pending(db_session)) == 1

    def test_trashed_work_is_not_pending(self, db_session: Session, artifact) -> None:
        build_print_job(db_session, artifact, deleted_at=utcnow())

        assert _pending(db_session) == []

    def test_no_room_in_the_lane_reports_nothing(
        self, db_session: Session, artifact
    ) -> None:
        build_print_job(db_session, artifact)

        assert _pending(db_session, limit=0) == []

    def test_a_parked_source_ignores_the_queue_it_could_not_route(
        self, db_session: Session, artifact
    ) -> None:
        build_print_job(
            db_session, artifact, created_at=utcnow() - timedelta(minutes=1)
        )
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30)

        assert _pending(db_session) == []

    def test_work_queued_while_parked_wakes_the_source(
        self, db_session: Session, artifact
    ) -> None:
        now = utcnow()
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30, now=now)
        build_print_job(db_session, artifact, created_at=now + timedelta(seconds=1))

        assert len(_pending(db_session, now=now + timedelta(seconds=2))) == 1

    def test_a_parked_source_still_settles_stranded_uploads(
        self, db_session: Session, artifact
    ) -> None:
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30)
        build_print_job(
            db_session,
            artifact,
            state=PrintJobState.UPLOADING,
            dispatch_claimed_at=utcnow(),
        )

        assert len(_pending(db_session)) == 1

    def test_parking_ends_when_its_window_passes(
        self, db_session: Session, artifact
    ) -> None:
        now = utcnow()
        build_print_job(db_session, artifact, created_at=now - timedelta(minutes=1))
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30, now=now)

        assert len(_pending(db_session, now=now + timedelta(seconds=31))) == 1

    def test_a_parked_source_is_next_due_when_its_window_ends(
        self, db_session: Session
    ) -> None:
        now = utcnow()
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30, now=now)

        due = SOURCE.next_due(db_session, now=now)

        assert due is not None
        assert abs((due - (now + timedelta(seconds=30))).total_seconds()) < 1

    def test_an_unparked_source_has_no_due_time(self, db_session: Session) -> None:
        assert SOURCE.next_due(db_session, now=utcnow()) is None

    def test_an_expired_park_has_no_due_time(self, db_session: Session) -> None:
        now = utcnow()
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30, now=now)

        assert SOURCE.next_due(db_session, now=now + timedelta(minutes=1)) is None


class TestWakeDispatch:
    def test_wakes_a_parked_dispatcher(self, db_session: Session, monkeypatch) -> None:
        nudged: list[str] = []
        monkeypatch.setattr(work, "nudge", lambda name, **_: nudged.append(name))
        mark_idle(dispatch_jobs.DISPATCH_DEFINITION, seconds=30)

        dispatch_jobs.wake_dispatch()

        db_session.expire_all()
        assert idle_window(db_session, dispatch_jobs.DISPATCH_DEFINITION) is None
        assert nudged == [dispatch_jobs.DISPATCH_DEFINITION]

    def test_waking_an_unparked_dispatcher_only_nudges(
        self, db_session: Session, monkeypatch
    ) -> None:
        nudged: list[str] = []
        monkeypatch.setattr(work, "nudge", lambda name, **_: nudged.append(name))

        dispatch_jobs.wake_dispatch()

        assert (
            db_session.get(ReconcileCursor, dispatch_jobs.DISPATCH_DEFINITION) is None
        )
        assert nudged == [dispatch_jobs.DISPATCH_DEFINITION]


def _dispatch_job(db_session: Session) -> Job:
    db_session.expire_all()
    job = db_session.exec(
        select(Job).where(Job.kind == dispatch_jobs.DISPATCH_DEFINITION)
    ).first()
    assert job is not None
    return job


class TestDispatchJob:
    def test_a_pass_that_routes_work_leaves_the_source_unparked(
        self, db_session: Session, artifact, monkeypatch
    ) -> None:
        build_print_job(db_session, artifact)
        routed = [11]

        async def dispatch_one(_builder) -> int | None:
            if not routed:
                return None
            job_id = routed.pop()
            printer_jobs.scheduler_status.last_dispatch_at = utcnow()
            with printer_jobs.get_session_factory().scoped_session() as session:
                from app.db.models import PrintJob

                for row in session.exec(select(PrintJob)).all():
                    row.state = PrintJobState.PRINTING
                    session.add(row)
                session.commit()
            return job_id

        monkeypatch.setattr(printer_jobs, "dispatch_next", dispatch_one)
        dispatch_jobs.bind_provider_builder(lambda _printer: None)
        try:
            work.nudge(dispatch_jobs.DISPATCH_DEFINITION)
            drain_work()
        finally:
            dispatch_jobs.bind_provider_builder(None)

        job = _dispatch_job(db_session)
        result = json.loads(job.status_json)["result"]
        assert job.state == JobState.COMPLETED
        assert (result["dispatched"], result["stranded_settled"]) == (1, 0)
        assert idle_window(db_session, dispatch_jobs.DISPATCH_DEFINITION) is None

    def test_a_pass_that_routes_nothing_parks_the_source(
        self, db_session: Session, artifact, monkeypatch
    ) -> None:
        # Nothing can route (no printer), so the queue stays; the park is what
        # stops the completion nudge from resubmitting the pass forever.
        build_print_job(db_session, artifact)

        async def nothing_eligible(_builder) -> int | None:
            return None

        monkeypatch.setattr(printer_jobs, "dispatch_next", nothing_eligible)
        dispatch_jobs.bind_provider_builder(lambda _printer: None)
        try:
            work.nudge(dispatch_jobs.DISPATCH_DEFINITION)
            drain_work()
        finally:
            dispatch_jobs.bind_provider_builder(None)

        job = _dispatch_job(db_session)
        assert json.loads(job.status_json)["result"]["dispatched"] == 0
        assert idle_window(db_session, dispatch_jobs.DISPATCH_DEFINITION) is not None
        assert _pending(db_session) == []

    def test_a_pass_settles_a_stranded_upload_as_outcome_unknown(
        self, db_session: Session, artifact
    ) -> None:
        stranded = build_print_job(
            db_session,
            artifact,
            state=PrintJobState.UPLOADING,
            dispatch_claimed_at=utcnow() - timedelta(minutes=5),
        )
        dispatch_jobs.bind_provider_builder(lambda _printer: None)
        try:
            work.nudge(dispatch_jobs.DISPATCH_DEFINITION)
            drain_work()
        finally:
            dispatch_jobs.bind_provider_builder(None)

        db_session.expire_all()
        job = _dispatch_job(db_session)
        assert json.loads(job.status_json)["result"]["stranded_settled"] == 1
        db_session.refresh(stranded)
        assert stranded.state == PrintJobState.FAILED
