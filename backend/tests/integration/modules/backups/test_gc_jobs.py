"""``backups.trash_gc``: the hourly trash-expiry and storage garbage-collection pass.

An unconfigured vault has no storage for garbage collection to reason about,
so the schedule is off until setup completes. Each occurrence runs the
coordinator, which previews, waits and finalizes but never approves its own
plan (the planner's suites defend that), and records what it found.
"""

from __future__ import annotations

import json

from sqlmodel import Session

from app.db.models import Job, JobState
from app.modules.backups import gc_jobs
from app.modules.work.submission import submit

(GC,) = gc_jobs.definitions()


class TestTrashGc:
    def test_is_off_until_setup_configures_the_vault(self, db_session: Session) -> None:
        assert GC.source is not None
        assert GC.source.cron(db_session) is None  # type: ignore[attr-defined]

    def test_runs_hourly_once_configured(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.modules.administration import runtime_config

        monkeypatch.setattr(runtime_config, "is_configured", lambda _session: True)
        assert GC.source is not None

        assert GC.source.cron(db_session) == "5 * * * *"  # type: ignore[attr-defined]

    def test_an_occurrence_records_what_the_pass_found(
        self, db_session: Session, work_engine, make_job
    ) -> None:
        job = make_job(
            kind=gc_jobs.GC_DEFINITION, subject=f"{gc_jobs.GC_DEFINITION}@now"
        )

        submit(job.id)
        work_engine.run_one()

        db_session.expire_all()
        row = db_session.get(Job, job.id)
        assert row is not None and row.state == JobState.COMPLETED
        result = json.loads(row.status_json)["result"]["outcome"]
        # An empty vault: the pass finds nothing, and says which plan it used.
        assert {
            key: result[key] for key in ("rows", "orphan_blobs", "gc_candidates")
        } == {
            "rows": 0,
            "orphan_blobs": 0,
            "gc_candidates": 0,
        }
        assert "gc_plan_id" in result

    def test_an_occurrence_during_a_restore_plans_nothing(
        self, db_session: Session, work_engine, make_job, monkeypatch
    ) -> None:
        # Deleting while the database is being replaced could delete what the
        # restored catalog still owns.
        from sqlmodel import select

        from app.db.models import GcRun
        from app.runtime import maintenance

        monkeypatch.setattr(maintenance, "restore_in_progress", lambda *_: True)
        job = make_job(
            kind=gc_jobs.GC_DEFINITION, subject=f"{gc_jobs.GC_DEFINITION}@restore"
        )

        submit(job.id)
        work_engine.run_one()

        db_session.expire_all()
        row = db_session.get(Job, job.id)
        assert row is not None
        assert json.loads(row.status_json)["result"]["outcome"] == {
            "rows": 0,
            "orphan_blobs": 0,
            "gc_candidates": 0,
        }
        assert db_session.exec(select(GcRun)).all() == []
