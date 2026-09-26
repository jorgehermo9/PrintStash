"""The work layer's own upkeep, run every quarter hour.

Finished Jobs past retention are pruned, the engine's own history is pruned,
long-dead executors are forgotten, and lane depth and stuck-Job metrics are
refreshed. The Job records what it did, so the admin page shows it.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlmodel import Session

from app.core.config import settings
from app.core.metrics import lane_depth
from app.core.time import utcnow
from app.db.models import Job, JobKind, JobState, LaneName, WorkExecutor
from app.modules.work import housekeeping


def _result(session: Session, job_id: str) -> dict:
    session.expire_all()
    job = session.get(Job, job_id)
    assert job is not None and job.state == JobState.COMPLETED
    return json.loads(job.status_json)["result"]


class TestHousekeeping:
    def test_runs_on_a_fixed_quarter_hour_schedule(self) -> None:
        definition = housekeeping.definition()

        assert definition.lane == LaneName.MAINTENANCE
        assert definition.source is not None
        assert definition.source.cron(None) == "*/15 * * * *"  # type: ignore[attr-defined]

    def test_prunes_jobs_past_retention(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        from app.modules.work.submission import submit

        expired = utcnow() - timedelta(hours=settings.jobs_system_retention_hours + 1)
        old_id = make_job(
            kind=JobKind.DERIVATIVES_MESH, state=JobState.COMPLETED, updated_at=expired
        ).id
        job = make_job(kind=JobKind.WORK_HOUSEKEEPING, subject="work.housekeeping@now")

        submit(job.id)
        work_engine.run_one()

        assert _result(db_session, job.id)["jobs_pruned"] == 1
        assert db_session.get(Job, old_id) is None

    def test_forgets_long_dead_executors(
        self, work_engine, make_job, make_work_executor, db_session: Session
    ) -> None:
        from app.modules.work.submission import submit

        gone = make_work_executor("gone", stale=True)
        gone.heartbeat_at = utcnow() - timedelta(
            seconds=settings.jobs_executor_stale_seconds * 11
        )
        db_session.add(gone)
        db_session.commit()
        job = make_job(kind=JobKind.WORK_HOUSEKEEPING, subject="work.housekeeping@now")

        submit(job.id)
        work_engine.run_one()

        assert _result(db_session, job.id)["executors_forgotten"] == 1
        assert db_session.get(WorkExecutor, "gone") is None

    def test_prunes_the_engines_settled_history(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        from app.modules.work.submission import submit

        earlier = make_job(kind=JobKind.SOURCES_SCAN)
        submit(earlier.id)
        work_engine.drain()
        job = make_job(kind=JobKind.WORK_HOUSEKEEPING, subject="work.housekeeping@now")

        submit(job.id)
        work_engine.drain()

        assert _result(db_session, job.id)["engine_history_pruned"] >= 1

    def test_publishes_every_lanes_depth(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        from app.modules.work.submission import submit

        job = make_job(kind=JobKind.WORK_HOUSEKEEPING, subject="work.housekeeping@now")
        submit(job.id)
        queued = make_job(kind=JobKind.INGESTION_UPLOAD)
        submit(queued.id)

        work_engine.run_one()

        assert lane_depth.labels(lane="ingest", state="queued")._value.get() == 1
