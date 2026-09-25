"""Code-declared schedules, and parking drain-style sources.

A schedule offers exactly one occurrence: the newest one not yet recorded. An
older missed occurrence is superseded, never replayed (a vault offline for a
week runs its daily backup once, not seven times). An occurrence that comes
due while the previous one still runs is offered as a skip, so the admin page
shows it was declined. A disabled schedule offers nothing.

A drain source (one subject, all the work) parks itself after a pass that made
no progress, so its own completion nudge does not spin it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import JobKind, JobState, ReconcileCursor, WorkPriority
from app.modules.work.contracts import SkipReason
from app.modules.work.sources import (
    NoPending,
    ScheduleSource,
    clear_idle,
    fixed,
    idle_window,
    mark_idle,
    scheduled,
    when_configured,
)

DAILY = "0 3 * * *"
NOW = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)
# Probes borrow real kinds; no catalog here runs them.
SCHEDULE = JobKind.WORK_HOUSEKEEPING
DRAIN = JobKind.SIMILARITY_ANALYZE


def _source(expression: str | None = DAILY) -> ScheduleSource:
    return ScheduleSource(SCHEDULE, lambda _session: expression)


class TestScheduleSource:
    def test_offers_the_latest_occurrence_once(self, db_session: Session) -> None:
        (item,) = _source().pending(db_session, now=NOW, limit=5)

        assert item.occurrence_at == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
        assert item.subject_key == "work.housekeeping@2026-09-24T03:00:00+00:00"
        assert (item.priority, item.skip) == (WorkPriority.BACKFILL, None)

    def test_an_occurrence_already_recorded_is_not_offered_again(
        self, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source=SCHEDULE,
                last_occurrence_at=datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
            )
        )
        db_session.commit()

        assert _source().pending(db_session, now=NOW, limit=5) == []

    def test_supersedes_missed_occurrences_with_the_newest(
        self, db_session: Session
    ) -> None:
        # Offline for a week: one run now, not seven.
        db_session.add(
            ReconcileCursor(
                source=SCHEDULE,
                last_occurrence_at=NOW - timedelta(days=7),
            )
        )
        db_session.commit()

        (item,) = _source().pending(db_session, now=NOW, limit=5)

        assert item.occurrence_at == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)

    def test_declines_an_occurrence_while_the_previous_one_runs(
        self, db_session: Session, make_job
    ) -> None:
        make_job(kind=SCHEDULE, state=JobState.RUNNING, attempts=1)

        (item,) = _source().pending(db_session, now=NOW, limit=5)

        assert item.skip is SkipReason.PREVIOUS_STILL_RUNNING

    def test_a_disabled_schedule_offers_nothing(self, db_session: Session) -> None:
        source = _source(None)

        assert source.pending(db_session, now=NOW, limit=5) == []
        assert source.next_due(db_session, now=NOW) is None

    def test_offers_nothing_without_lane_room(self, db_session: Session) -> None:
        assert _source().pending(db_session, now=NOW, limit=0) == []

    def test_is_next_due_at_its_next_firing(self, db_session: Session) -> None:
        assert _source().next_due(db_session, now=NOW) == datetime(
            2026, 9, 25, 3, 0, tzinfo=UTC
        )


class TestWhenConfigured:
    def test_is_off_until_first_run_setup(self, db_session: Session) -> None:
        assert when_configured(DAILY)(db_session) is None

    def test_is_on_once_the_vault_is_configured(
        self, db_session: Session, make_system_config, make_user
    ) -> None:
        make_system_config(configured_at=utcnow())
        make_user()

        assert when_configured(DAILY)(db_session) == DAILY


class TestScheduled:
    def test_records_what_each_occurrence_did(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        import json

        from app.modules.work import catalog as catalog_module
        from app.modules.work.catalog import WorkCatalog
        from app.runtime.engine.inline import InlineJobEngine

        definition = scheduled(
            JobKind.IDENTITY_RETENTION, cron=fixed(DAILY), run=lambda: 7, label="Probe"
        )
        catalog = WorkCatalog([definition])
        engine = InlineJobEngine(catalog)
        catalog_module.bind(engine, catalog)
        job = make_job(kind=JobKind.IDENTITY_RETENTION)

        from app.modules.work.submission import submit

        submit(job.id)
        engine.drain()

        db_session.expire_all()
        row = db_session.get(type(job), job.id)
        assert row is not None and row.state == JobState.COMPLETED
        assert json.loads(row.status_json)["result"] == {"outcome": 7}

    def test_a_step_that_returns_nothing_records_no_result(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        import json

        from app.modules.work import catalog as catalog_module
        from app.modules.work.catalog import WorkCatalog
        from app.modules.work.submission import submit
        from app.runtime.engine.inline import InlineJobEngine

        definition = scheduled(
            JobKind.NOTIFICATIONS_RETENTION,
            cron=fixed(DAILY),
            run=lambda: None,
            label="Quiet",
        )
        catalog = WorkCatalog([definition])
        engine = InlineJobEngine(catalog)
        catalog_module.bind(engine, catalog)
        job = make_job(kind=JobKind.NOTIFICATIONS_RETENTION)

        submit(job.id)
        engine.drain()

        db_session.expire_all()
        row = db_session.get(type(job), job.id)
        assert row is not None
        assert "result" not in json.loads(row.status_json)


class TestIdle:
    def test_parks_a_source_for_a_while(self, db_session: Session) -> None:
        now = utcnow()

        mark_idle(DRAIN, seconds=30, now=now)

        window = idle_window(db_session, DRAIN)
        assert window is not None
        assert window[1] - window[0] == timedelta(seconds=30)

    def test_waking_clears_the_park(self, db_session: Session) -> None:
        mark_idle(DRAIN, seconds=30)

        clear_idle(DRAIN)

        db_session.expire_all()
        assert idle_window(db_session, DRAIN) is None

    def test_waking_a_source_never_parked_is_harmless(
        self, db_session: Session
    ) -> None:
        clear_idle(JobKind.PRINTING_DISPATCH)
        db_session.add(ReconcileCursor(source=JobKind.STORAGE_MIGRATE))
        db_session.commit()

        clear_idle(JobKind.STORAGE_MIGRATE)

        assert idle_window(db_session, JobKind.STORAGE_MIGRATE) is None

    def test_parking_keeps_the_cursors_other_state(self, db_session: Session) -> None:
        db_session.add(
            ReconcileCursor(
                source=DRAIN,
                pass_queued_at=NOW,
                pass_priority=WorkPriority.INTERACTIVE,
                scan_high_water=9,
            )
        )
        db_session.commit()

        mark_idle(DRAIN, seconds=30)
        clear_idle(DRAIN)

        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, DRAIN)
        assert cursor is not None
        assert (cursor.pass_priority, cursor.scan_high_water) == (
            WorkPriority.INTERACTIVE,
            9,
        )


class TestCursorInvariants:
    @pytest.mark.parametrize(
        "fields",
        [
            {"pass_queued_at": NOW},
            {"pass_priority": WorkPriority.BACKFILL},
            {"holder": "executor-1"},
            {"idle_since": NOW},
            {"idle_until": NOW},
        ],
        ids=[
            "queued-without-priority",
            "priority-without-queue",
            "holder-without-expiry",
            "idle-without-end",
            "idle-without-start",
        ],
    )
    def test_the_database_refuses_half_a_pair(
        self, db_session: Session, fields: dict
    ) -> None:
        # Each pair means one thing only together; half of one is a bug.
        db_session.add(ReconcileCursor(source=DRAIN, **fields))

        with pytest.raises(IntegrityError):
            db_session.commit()


class TestNoPending:
    def test_a_source_without_state_is_inert(self, db_session: Session) -> None:
        source = NoPending()

        assert source.pending(db_session, now=NOW, limit=10) == []
        assert source.next_due(db_session, now=NOW) is None
