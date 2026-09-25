"""A reconcile pass against the real database: repair, discover, and bound.

``decide`` (unit-tested) says what a Job needs; this file defends what a pass
*does* about it, on real rows and a real engine: submitting first attempts,
interrupting and resubmitting lost ones, failing exhausted ones and calling
their failure hook, finishing ones whose terminal write was lost. Discovery
creates one Job per pending subject, never more than the batch or the lane's
headroom allows, and holds back a subject that keeps coming straight back.

A pass is single-flight per definition, re-runs itself when a nudge lands
while it runs, and asks for a delayed pass at its source's next due time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import (
    Job,
    JobKind,
    JobState,
    LaneName,
    ReconcileCursor,
    WorkPriority,
)
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.modules.work.contracts import (
    EngineStatus,
    JobDefinition,
    Lane,
    PassSubmission,
    SkipReason,
    Step,
    WorkItem,
)
from app.modules.work.reconciler import (
    run_pass,
    sweep_foreign_versions,
    sweep_lost_passes,
)
from app.modules.work.submission import execution_id, nudge
from app.runtime.engine.inline import InlineJobEngine

# Probe definitions borrow real kinds and a real lane: the catalog each test
# binds holds only these two, so nothing else answers to those names.
REQUESTED = JobKind.INGESTION_UPLOAD
SOURCED = JobKind.SOURCES_SCAN
PROBE_LANE = LaneName.MAINTENANCE


@dataclass
class Probe:
    items: list[WorkItem] = field(default_factory=list)
    due: datetime | None = None
    calls: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    on_pending: object | None = None


PROBE = Probe()


class _Source:
    def pending(self, session, *, now, limit):
        PROBE.calls += 1
        if callable(PROBE.on_pending):
            PROBE.on_pending()
        return PROBE.items[:limit]

    def next_due(self, session, *, now):
        return PROBE.due


def _noop(_ctx) -> None:
    return None


def _failed(_session, subject: str, reason: str) -> None:
    PROBE.failures.append((subject, reason))


@pytest.fixture
def engine() -> InlineJobEngine:
    PROBE.items = []
    PROBE.due = None
    PROBE.calls = 0
    PROBE.failures = []
    PROBE.on_pending = None
    lanes = {**default_lanes(), PROBE_LANE: Lane(PROBE_LANE, 1)}
    catalog = WorkCatalog(
        [
            JobDefinition(
                name=REQUESTED,
                lane=PROBE_LANE,
                steps=(Step(f"{REQUESTED}.run", _noop),),
                label="Requested probe",
                on_failure=_failed,
            ),
            JobDefinition(
                name=SOURCED,
                lane=PROBE_LANE,
                steps=(Step(f"{SOURCED}.run", _noop),),
                label="Sourced probe",
                source=_Source(),
            ),
        ],
        lanes=lanes,
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built


def _row(session: Session, job_id: str) -> Job:
    session.expire_all()
    row = session.get(Job, job_id)
    assert row is not None
    return row


def _jobs(session: Session, kind: JobKind = SOURCED) -> list[Job]:
    session.expire_all()
    return list(session.exec(select(Job).where(Job.kind == kind)).all())


def _items(*subjects: str, **kw) -> list[WorkItem]:
    return [WorkItem(subject_key=subject, **kw) for subject in subjects]


def _passes(engine: InlineJobEngine, source: JobKind) -> list:
    return [
        execution
        for execution in engine.executions.values()
        if isinstance(execution.submission, PassSubmission)
        and execution.submission.source == source
    ]


def _stale(make_job, *, attempts: int = 1, **kw) -> Job:
    """A running Job last touched past the submission grace."""
    return make_job(
        kind=REQUESTED,
        state=kw.pop("state", JobState.RUNNING),
        attempts=attempts,
        updated_at=utcnow() - timedelta(seconds=settings.jobs_submit_grace_seconds + 5),
        **kw,
    )


class TestRepair:
    def test_submits_the_first_attempt_of_a_queued_job(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)

        result = run_pass(REQUESTED)

        assert result.submitted == 1
        assert _row(db_session, job.id).attempts == 1
        assert execution_id(job.id, 1) in engine.executions

    def test_resubmits_an_execution_the_engine_lost(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = _stale(make_job)

        result = run_pass(REQUESTED)

        row = _row(db_session, job.id)
        assert (result.interrupted, result.submitted) == (1, 1)
        assert (row.attempts, row.resubmits) == (2, 1)
        assert execution_id(job.id, 2) in engine.executions

    def test_records_why_a_job_was_interrupted(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        import json

        job = _stale(make_job)

        run_pass(REQUESTED)

        assert json.loads(_row(db_session, job.id).status_json)["error"] == (
            "execution_lost"
        )

    def test_fails_a_job_lost_more_often_than_its_budget(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = _stale(make_job, resubmits=settings.jobs_max_resubmits)

        result = run_pass(REQUESTED)

        assert result.failed == 1
        assert _row(db_session, job.id).state == JobState.FAILED
        # The definition withdraws the subject so the source stops offering it.
        assert PROBE.failures == [(job.subject_key, "interrupted_repeatedly")]

    def test_finishes_a_job_whose_terminal_write_was_lost(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = _stale(make_job)
        run_pass(REQUESTED)
        engine.executions[execution_id(job.id, 2)].status = EngineStatus.SUCCEEDED

        result = run_pass(REQUESTED)

        assert result.completed == 1
        assert _row(db_session, job.id).state == JobState.COMPLETED

    def test_fails_a_job_the_engine_failed(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        engine.executions[execution_id(job.id, 1)].status = EngineStatus.FAILED

        result = run_pass(REQUESTED)

        assert result.failed == 1
        assert _row(db_session, job.id).state == JobState.FAILED
        assert PROBE.failures == [(job.subject_key, "engine_failed")]

    def test_reruns_work_stranded_on_a_dead_executor(
        self, engine: InlineJobEngine, make_job, make_work_executor, db_session
    ) -> None:
        dead = make_work_executor("dead-executor", stale=True)
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        stranded = engine.executions[execution_id(job.id, 1)]
        stranded.status = EngineStatus.RUNNING
        stranded.executor_id = dead.executor_id

        result = run_pass(REQUESTED)

        assert result.interrupted == 1
        assert stranded.status is EngineStatus.CANCELLED
        assert _row(db_session, job.id).attempts == 2

    def test_reruns_work_of_another_version(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        old = engine.executions[execution_id(job.id, 1)]
        old.app_version = "0.0.1-previous"

        run_pass(REQUESTED)

        assert old.status is EngineStatus.CANCELLED
        assert _row(db_session, job.id).attempts == 2

    def test_an_engine_that_cannot_cancel_leaves_the_job_for_the_next_pass(
        self, engine: InlineJobEngine, make_job, db_session: Session, monkeypatch
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        engine.executions[execution_id(job.id, 1)].app_version = "0.0.1-previous"

        def unreachable(_execution_id):
            raise RuntimeError("engine down")

        monkeypatch.setattr(engine, "cancel", unreachable)

        result = run_pass(REQUESTED)

        assert result.deferred == 1
        row = _row(db_session, job.id)
        assert (row.state, row.attempts) == (JobState.QUEUED, 1)

    def test_leaves_live_work_alone(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)

        result = run_pass(REQUESTED)

        assert (result.submitted, result.interrupted) == (0, 0)
        assert result.outcomes.get("in_progress") == 1
        assert _row(db_session, job.id).attempts == 1


class TestDiscover:
    def test_submits_one_job_per_pending_subject(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        PROBE.items = _items("a", "b")

        result = run_pass(SOURCED)

        assert result.submitted == 2
        assert sorted(job.subject_key for job in _jobs(db_session)) == ["a", "b"]

    def test_the_job_takes_what_its_item_asked_for(
        self, engine: InlineJobEngine, db_session: Session, make_user
    ) -> None:
        owner = make_user()
        PROBE.items = _items(
            "mine", priority=WorkPriority.INTERACTIVE, owner_user_id=owner.id
        )

        run_pass(SOURCED)

        (job,) = _jobs(db_session)
        assert (job.priority, job.owner_user_id) == (WorkPriority.INTERACTIVE, owner.id)

    def test_a_subject_with_an_active_job_gets_no_second_one(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        make_job(kind=SOURCED, subject="busy", state=JobState.RUNNING, attempts=1)
        PROBE.items = _items("busy")

        result = run_pass(SOURCED)

        assert result.outcomes.get("already_active") == 1
        assert len(_jobs(db_session)) == 1

    def test_never_offers_more_than_the_lanes_headroom(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        # One slot, headroom factor 2: a backfill never floods the engine.
        headroom = engine.catalog.lanes[PROBE_LANE].headroom
        PROBE.items = _items(*[f"s{n}" for n in range(headroom + 3)])

        run_pass(SOURCED)

        assert len(_jobs(db_session)) == headroom

    def test_a_full_lane_offers_nothing(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        PROBE.items = _items(*[f"s{n}" for n in range(10)])
        run_pass(SOURCED)
        PROBE.calls = 0

        result = run_pass(SOURCED)

        assert result.outcomes.get("lane_full") == 1
        assert PROBE.calls == 0

    def test_a_full_batch_with_room_left_continues_in_the_same_pass(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        _overlay["jobs_reconcile_batch"] = 2
        engine.catalog.lanes[PROBE_LANE] = Lane(PROBE_LANE, 10)
        subjects = [f"s{n}" for n in range(5)]
        remaining = list(subjects)

        def drain_as_created():
            created = {job.subject_key for job in _jobs(db_session)}
            PROBE.items = _items(*[s for s in remaining if s not in created])

        PROBE.on_pending = drain_as_created

        result = run_pass(SOURCED)

        assert result.submitted == 5
        assert sorted(job.subject_key for job in _jobs(db_session)) == subjects

    def test_a_batch_of_subjects_already_in_flight_does_not_spin(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        # Regression: a source reporting subjects whose Jobs are still queued
        # filled the batch every loop, so the pass looped to its limit and then
        # nudged itself again, forever.
        _overlay["jobs_reconcile_batch"] = 2
        engine.catalog.lanes[PROBE_LANE] = Lane(PROBE_LANE, 10)
        for subject in ("a", "b"):
            make_job(kind=SOURCED, subject=subject)
        PROBE.items = _items("a", "b")

        result = run_pass(SOURCED)

        assert PROBE.calls == 1
        assert result.outcomes.get("already_active") == 2

    def test_records_a_declined_occurrence_as_a_cancelled_job(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        # A skip is a verdict, not an absence: the admin page shows it.
        occurrence = utcnow().replace(microsecond=0)
        PROBE.items = _items(
            "sched@1",
            skip=SkipReason.PREVIOUS_STILL_RUNNING,
            occurrence_at=occurrence,
        )

        result = run_pass(SOURCED)

        (job,) = _jobs(db_session)
        assert result.skipped == 1
        assert job.state == JobState.CANCELLED
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None and cursor.last_occurrence_at is not None

    def test_holds_back_a_subject_that_keeps_coming_straight_back(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        # Finished work the source still reports would otherwise spin: the
        # burst allowance runs it again a few times, then waits it out.
        for _ in range(settings.jobs_resubmit_burst):
            make_job(kind=SOURCED, subject="loop", state=JobState.COMPLETED)
        PROBE.items = _items("loop")

        result = run_pass(SOURCED)

        assert result.outcomes.get("cooling_down") == 1
        assert result.cooling_until is not None
        assert len(_jobs(db_session)) == settings.jobs_resubmit_burst
        assert any(ex.status is EngineStatus.DELAYED for ex in _passes(engine, SOURCED))

    def test_a_subject_finished_fewer_times_than_the_burst_runs_again(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        make_job(kind=SOURCED, subject="again", state=JobState.COMPLETED)
        PROBE.items = _items("again")

        result = run_pass(SOURCED)

        assert result.submitted == 1


class TestPass:
    def test_a_pass_already_claimed_elsewhere_does_nothing(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source=SOURCED,
                holder="another-executor",
                holder_expires_at=utcnow() + timedelta(minutes=1),
            )
        )
        db_session.commit()
        PROBE.items = _items("a")

        result = run_pass(SOURCED)

        assert result.outcomes == {"claimed_elsewhere": 1}
        assert _jobs(db_session) == []

    def test_an_expired_claim_is_taken_over(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source=SOURCED,
                holder="dead-executor",
                holder_expires_at=utcnow() - timedelta(seconds=1),
            )
        )
        db_session.commit()
        PROBE.items = _items("a")

        assert run_pass(SOURCED).submitted == 1

    def test_releases_its_claim(self, engine: InlineJobEngine, db_session) -> None:
        run_pass(SOURCED)

        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None
        assert (cursor.holder, cursor.last_pass_finished_at is not None) == (None, True)

    def test_a_nudge_during_a_pass_runs_it_again(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        # The dirty mark: work recorded while the pass was already past its
        # discovery must not wait for the next tick.
        def nudge_once():
            if PROBE.calls == 1:
                PROBE.items = _items("late")
                nudge(SOURCED)

        PROBE.on_pending = nudge_once

        run_pass(SOURCED)

        assert PROBE.calls == 2
        assert [job.subject_key for job in _jobs(db_session)] == ["late"]

    def test_a_pass_that_never_settles_hands_over(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        PROBE.on_pending = lambda: nudge(SOURCED)

        run_pass(SOURCED)

        assert PROBE.calls == 20
        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None and cursor.holder is None

    def test_asks_for_a_pass_at_the_sources_next_due_time(
        self, engine: InlineJobEngine
    ) -> None:
        PROBE.due = utcnow() + timedelta(seconds=90)

        run_pass(SOURCED)

        (delayed,) = [
            ex for ex in _passes(engine, SOURCED) if ex.status is EngineStatus.DELAYED
        ]
        assert 80 < (delayed.submission.delay_seconds or 0) <= 90

    def test_a_due_time_beyond_the_tick_is_left_to_the_tick(
        self, engine: InlineJobEngine
    ) -> None:
        PROBE.due = utcnow() + timedelta(
            seconds=settings.jobs_reconcile_interval_seconds + 60
        )

        run_pass(SOURCED)

        assert not [
            ex for ex in _passes(engine, SOURCED) if ex.status is EngineStatus.DELAYED
        ]

    def test_a_failing_source_releases_the_claim(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        def broken():
            raise RuntimeError("source query failed")

        PROBE.on_pending = broken

        with pytest.raises(RuntimeError):
            run_pass(SOURCED)

        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None and cursor.holder is None


def _stranded_pass(engine: InlineJobEngine, source: str, executor: str) -> str:
    """A reconcile pass a process was running when it died."""
    nudge(source)
    (stranded,) = _passes(engine, source)
    stranded.status = EngineStatus.RUNNING
    stranded.executor_id = executor
    return stranded.submission.execution_id


class TestSweepLostPasses:
    """A pass a dead process was running holds a reconcile slot until cancelled.

    Reconcile passes are not Jobs, so no repair ever interrupts them, and the
    lane is global: a process killed while its startup passes ran would keep
    every later pass, on every process, from starting.
    """

    def test_cancels_a_pass_stranded_on_a_dead_executor(
        self, engine: InlineJobEngine, make_work_executor
    ) -> None:
        dead = make_work_executor("dead-executor", stale=True)
        stranded = _stranded_pass(engine, SOURCED, dead.executor_id)

        assert sweep_lost_passes() == 1
        assert engine.executions[stranded].status is EngineStatus.CANCELLED

    def test_leaves_a_live_executors_pass_running(
        self, engine: InlineJobEngine, make_work_executor
    ) -> None:
        live = make_work_executor("live-executor")
        running = _stranded_pass(engine, SOURCED, live.executor_id)

        assert sweep_lost_passes() == 0
        assert engine.executions[running].status is EngineStatus.RUNNING

    def test_leaves_a_dead_executors_jobs_to_repair(
        self, engine: InlineJobEngine, make_job, make_work_executor
    ) -> None:
        # A Job's execution is repair's to interrupt, with its Job's record.
        dead = make_work_executor("dead-executor", stale=True)
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        execution = engine.executions[execution_id(job.id, 1)]
        execution.status = EngineStatus.RUNNING
        execution.executor_id = dead.executor_id

        assert sweep_lost_passes() == 0
        assert execution.status is EngineStatus.RUNNING


class TestSweepForeignVersions:
    def test_cancels_every_execution_of_another_version(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        engine.executions[execution_id(job.id, 1)].app_version = "0.0.1-previous"

        assert sweep_foreign_versions() == 1
        assert (
            engine.executions[execution_id(job.id, 1)].status is EngineStatus.CANCELLED
        )

    def test_leaves_this_versions_work_running(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        make_job(kind=REQUESTED)
        run_pass(REQUESTED)

        assert sweep_foreign_versions() == 0
