"""The ``similarity.analyze`` Job: similarity work drained in bounded slices.

There is one subject, the analysis queue. It is pending whenever a run has
work nobody holds a live lease on; each Job drains units until its slice runs
out and completes, and its completion nudges the source, so a long analysis is
a chain of short, resumable Jobs rather than one that holds a lane for hours.
A slice that could move nothing (compute busy, leases held elsewhere) parks
the source for a while instead of spinning. With analysis enabled and a
schedule set, the source is due again after the schedule interval.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Job, JobState, WorkPriority
from app.modules.similarity import jobs as similarity_jobs
from app.modules.similarity.configuration import update_settings
from app.modules.similarity.jobs import DEFINITION, SUBJECT, AnalysisSource
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.work.sources import idle_window, mark_idle
from app.modules.work.submission import submit

SOURCE = AnalysisSource()


@pytest.fixture
def admin(make_user):
    return make_user(superuser=True)


def _drain(session: Session, work_engine, make_job) -> dict:
    job = make_job(kind=DEFINITION, subject=SUBJECT)
    submit(job.id)
    work_engine.run_one()
    session.expire_all()
    row = session.get(Job, job.id)
    assert row is not None and row.state == JobState.COMPLETED
    return json.loads(row.status_json)["result"]


def _units(monkeypatch, count: int) -> list[int]:
    """A stand-in queue with `count` units of work."""
    done: list[int] = []

    def work_one(_self) -> bool:
        if len(done) >= count:
            return False
        done.append(len(done))
        return True

    monkeypatch.setattr(SimilarityProcessor, "work_one", work_one)
    return done


class TestAnalysisSource:
    def test_a_run_with_unclaimed_work_needs_the_queue_drained(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        make_similarity_run(admin)

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert (item.subject_key, item.priority) == (SUBJECT, WorkPriority.BACKFILL)

    def test_one_subject_serves_every_run(
        self, db_session: Session, admin, make_user, make_similarity_run
    ) -> None:
        make_similarity_run(admin)
        make_similarity_run(make_user())

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=10)) == 1

    def test_a_run_another_process_holds_is_not_offered(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        make_similarity_run(
            admin,
            lease_token="held-elsewhere",
            lease_expires_at=utcnow() + timedelta(minutes=5),
        )

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_an_expired_lease_is_offered_again(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        # The process holding it died; its checkpoint is where work resumes.
        make_similarity_run(
            admin,
            lease_token="abandoned",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=10)) == 1

    def test_finished_runs_need_nothing(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        make_similarity_run(admin, active=False)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_a_parked_source_offers_nothing(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        make_similarity_run(admin)
        mark_idle(DEFINITION, seconds=30)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_offers_nothing_without_room(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        make_similarity_run(admin)

        assert SOURCE.pending(db_session, now=utcnow(), limit=0) == []


class TestNextDue:
    def test_disabled_analysis_is_never_due(self, db_session: Session) -> None:
        assert SOURCE.next_due(db_session, now=utcnow()) is None

    def test_a_schedule_makes_it_due_after_its_interval(
        self, db_session: Session, admin
    ) -> None:
        update_settings(db_session, admin, {"enabled": True, "schedule_hours": 6})
        db_session.commit()
        now = utcnow()

        assert SOURCE.next_due(db_session, now=now) == now + timedelta(hours=6)

    def test_enabled_without_a_schedule_is_never_due(
        self, db_session: Session, admin
    ) -> None:
        update_settings(db_session, admin, {"enabled": True, "schedule_hours": 0})
        db_session.commit()

        assert SOURCE.next_due(db_session, now=utcnow()) is None

    def test_a_parked_source_is_due_when_its_window_ends(
        self, db_session: Session
    ) -> None:
        now = utcnow()
        mark_idle(DEFINITION, seconds=30, now=now)

        due = SOURCE.next_due(db_session, now=now)

        assert due is not None
        assert abs((due - (now + timedelta(seconds=30))).total_seconds()) < 1


class TestDrain:
    def test_drains_every_unit_in_one_slice(
        self, db_session: Session, work_engine, make_job, monkeypatch
    ) -> None:
        done = _units(monkeypatch, 3)

        result = _drain(db_session, work_engine, make_job)

        assert (len(done), result["units"]) == (3, 3)

    def test_stops_when_its_slice_runs_out(
        self, db_session: Session, work_engine, make_job, monkeypatch
    ) -> None:
        # The next slice is another Job, nudged by this one's completion.
        _units(monkeypatch, 1000)
        clock = iter(range(0, 10_000, 10))
        # Only this module's clock: each unit appears to take ten seconds.
        monkeypatch.setattr(
            similarity_jobs, "time", SimpleNamespace(monotonic=lambda: next(clock))
        )

        result = _drain(db_session, work_engine, make_job)

        assert 0 < result["units"] < 1000

    def test_a_slice_that_moved_nothing_parks_the_source(
        self, db_session: Session, work_engine, make_job, monkeypatch
    ) -> None:
        _units(monkeypatch, 0)

        result = _drain(db_session, work_engine, make_job)

        assert result["units"] == 0
        assert idle_window(db_session, DEFINITION) is not None

    def test_a_slice_that_moved_work_unparks_the_source(
        self, db_session: Session, work_engine, make_job, monkeypatch
    ) -> None:
        mark_idle(DEFINITION, seconds=30)
        _units(monkeypatch, 1)

        _drain(db_session, work_engine, make_job)

        db_session.expire_all()
        assert idle_window(db_session, DEFINITION) is None

    def test_a_cancelled_job_stops_draining(
        self, db_session: Session, work_engine, make_job, monkeypatch
    ) -> None:
        from app.modules.work.jobs import jobs

        job = make_job(kind=DEFINITION, subject=SUBJECT)
        done: list[int] = []

        def work_one(_self) -> bool:
            done.append(1)
            jobs.finish(job.id, state=JobState.CANCELLED)
            return True

        monkeypatch.setattr(SimilarityProcessor, "work_one", work_one)
        submit(job.id)

        work_engine.run_one()

        assert done == [1]
