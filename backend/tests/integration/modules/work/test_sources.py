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

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import JobState, ReconcileCursor, WorkPriority
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


def _source(expression: str | None = DAILY) -> ScheduleSource:
    return ScheduleSource("probe.schedule", lambda _session: expression)


class TestScheduleSource:
    def test_offers_the_latest_occurrence_once(self, db_session: Session) -> None:
        (item,) = _source().pending(db_session, now=NOW, limit=5)

        assert item.occurrence_at == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
        assert item.subject_key == "probe.schedule@2026-09-24T03:00:00+00:00"
        assert (item.priority, item.skip_reason) == (WorkPriority.BACKFILL, None)

    def test_an_occurrence_already_recorded_is_not_offered_again(
        self, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source="probe.schedule",
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
                source="probe.schedule",
                last_occurrence_at=NOW - timedelta(days=7),
            )
        )
        db_session.commit()

        (item,) = _source().pending(db_session, now=NOW, limit=5)

        assert item.occurrence_at == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)

    def test_declines_an_occurrence_while_the_previous_one_runs(
        self, db_session: Session, make_job
    ) -> None:
        make_job(kind="probe.schedule", state=JobState.RUNNING, attempts=1)

        (item,) = _source().pending(db_session, now=NOW, limit=5)

        assert item.skip_reason == "previous_still_running"

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
            "probe.periodic", cron=fixed(DAILY), run=lambda: 7, label="Probe"
        )
        catalog = WorkCatalog([definition])
        engine = InlineJobEngine(catalog)
        catalog_module.bind(engine, catalog)
        job = make_job(kind="probe.periodic")

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
            "probe.quiet", cron=fixed(DAILY), run=lambda: None, label="Quiet"
        )
        catalog = WorkCatalog([definition])
        engine = InlineJobEngine(catalog)
        catalog_module.bind(engine, catalog)
        job = make_job(kind="probe.quiet")

        submit(job.id)
        engine.drain()

        db_session.expire_all()
        row = db_session.get(type(job), job.id)
        assert row is not None
        assert "result" not in json.loads(row.status_json)


class TestIdle:
    def test_parks_a_source_for_a_while(self, db_session: Session) -> None:
        now = utcnow()

        mark_idle("probe.drain", seconds=30, now=now)

        window = idle_window(db_session, "probe.drain")
        assert window is not None
        assert window[1] - window[0] == timedelta(seconds=30)

    def test_waking_clears_the_park(self, db_session: Session) -> None:
        mark_idle("probe.drain", seconds=30)

        clear_idle("probe.drain")

        db_session.expire_all()
        assert idle_window(db_session, "probe.drain") is None

    def test_waking_a_source_never_parked_is_harmless(
        self, db_session: Session
    ) -> None:
        clear_idle("probe.never-seen")
        db_session.add(ReconcileCursor(source="probe.unparked"))
        db_session.commit()

        clear_idle("probe.unparked")

        assert idle_window(db_session, "probe.unparked") is None

    def test_parking_keeps_the_cursors_other_state(self, db_session: Session) -> None:
        import json

        db_session.add(
            ReconcileCursor(source="probe.drain", state_json='{"queued_priority":"x"}')
        )
        db_session.commit()

        mark_idle("probe.drain", seconds=30)
        clear_idle("probe.drain")

        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, "probe.drain")
        assert cursor is not None
        assert json.loads(cursor.state_json) == {"queued_priority": "x"}


class TestNoPending:
    def test_offers_nothing_and_is_never_due(self, db_session: Session) -> None:
        source = NoPending()

        assert source.pending(db_session, now=NOW, limit=10) == []
        assert source.next_due(db_session, now=NOW) is None
