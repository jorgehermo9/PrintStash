"""The ``administration.audit`` Job: vault audits, scheduled or requested.

The source reuses the policy's own durable schedule: a pass admits a due
policy (one active audit at a time), offers every pending run, and asks to be
woken at the policy's next slot. A restore defers a due audit rather than
starting it. The Job refuses to audit unreachable storage (a false "everything
is missing" report is worse than none) and backs the policy off instead; an
audit found half-run by a new attempt is failed, not resumed, because its
phases are not checkpoints. Cancelling asks the running audit to stop.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.time import ensure_utc, utcnow
from app.db.models import (
    Job,
    JobKind,
    JobState,
    VaultAuditRun,
    VaultAuditRunState,
    WorkPriority,
)
from app.modules.administration import audit_jobs
from app.modules.administration.audit_jobs import AuditSource, subject_key
from app.modules.work.submission import submit

SOURCE = AuditSource()
DEFINITION = audit_jobs.definitions()[0]


@pytest.fixture
def admin(make_user):
    return make_user(superuser=True)


def _run(session: Session, run_id: int) -> VaultAuditRun:
    session.expire_all()
    run = session.get(VaultAuditRun, run_id)
    assert run is not None
    return run


def _execute(work_engine, make_job, run: VaultAuditRun) -> Job:
    job = make_job(kind=JobKind.ADMINISTRATION_AUDIT, subject=subject_key(run.id))
    submit(job.id)
    work_engine.run_one()
    return job


class TestAuditSource:
    def test_a_due_policy_becomes_a_pending_audit(
        self, db_session: Session, admin, make_audit_policy
    ) -> None:
        # Inside the policy's default overnight window.
        now = datetime(2026, 9, 6, 2, 30, tzinfo=UTC)
        make_audit_policy(admin, enabled=True, next_due_at=now - timedelta(minutes=5))

        (item,) = SOURCE.pending(db_session, now=now, limit=10)

        run = db_session.exec(select(VaultAuditRun)).one()
        assert (item.subject_key, item.priority) == (
            subject_key(run.id),
            WorkPriority.BACKFILL,
        )
        assert run.trigger == "scheduled"

    def test_offers_a_requested_audit(
        self, db_session: Session, admin, make_audit_run
    ) -> None:
        run = make_audit_run(admin, state=VaultAuditRunState.PENDING)

        items = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert [item.subject_key for item in items] == [subject_key(run.id)]

    def test_nothing_due_offers_nothing(
        self, db_session: Session, admin, make_audit_run
    ) -> None:
        make_audit_run(admin, state=VaultAuditRunState.COMPLETED)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_a_restore_defers_a_due_audit(
        self, db_session: Session, admin, make_audit_policy, monkeypatch
    ) -> None:
        from app.runtime import maintenance

        monkeypatch.setattr(maintenance, "restore_in_progress", lambda *_: True)
        policy = make_audit_policy(admin, enabled=True, next_due_at=utcnow())

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

        db_session.refresh(policy)
        assert policy.deferred_reason == "maintenance"

    def test_never_offers_more_than_asked(
        self, db_session: Session, admin, make_audit_run
    ) -> None:
        make_audit_run(admin, state=VaultAuditRunState.PENDING)
        make_audit_run(admin, state=VaultAuditRunState.PENDING)

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=1)) == 1


class TestNextDue:
    def test_wakes_at_the_policys_next_slot(
        self, db_session: Session, admin, make_audit_policy
    ) -> None:
        # A 03:00 audit starts at 03:00, not at the next safety-net tick.
        slot = utcnow() + timedelta(hours=3)
        make_audit_policy(admin, enabled=True, next_due_at=slot)

        due = SOURCE.next_due(db_session, now=utcnow())

        assert due is not None and ensure_utc(due) >= slot

    def test_waits_out_a_launch_retry(
        self, db_session: Session, admin, make_audit_policy
    ) -> None:
        now = utcnow()
        retry = now + timedelta(minutes=20)
        make_audit_policy(
            admin,
            enabled=True,
            next_due_at=now - timedelta(minutes=1),
            retry_after=retry,
        )

        due = SOURCE.next_due(db_session, now=now)

        assert due is not None and ensure_utc(due) >= retry

    @pytest.mark.parametrize(
        "fields", [{"enabled": False}, {"enabled": True, "paused": True}]
    )
    def test_an_inactive_policy_is_never_due(
        self, db_session: Session, admin, make_audit_policy, fields
    ) -> None:
        make_audit_policy(admin, next_due_at=utcnow() + timedelta(hours=1), **fields)

        assert SOURCE.next_due(db_session, now=utcnow()) is None


class TestAuditJob:
    def test_runs_a_pending_audit_to_its_verdict(
        self, db_session: Session, work_engine, make_job, admin, make_audit_run
    ) -> None:
        run = make_audit_run(admin, state=VaultAuditRunState.PENDING)

        job = _execute(work_engine, make_job, run)

        assert _run(db_session, run.id).state == VaultAuditRunState.COMPLETED
        db_session.expire_all()
        row = db_session.get(Job, job.id)
        assert row is not None and row.state == JobState.COMPLETED
        assert json.loads(row.status_json)["result"] == {
            "run_id": run.id,
            "state": "completed",
        }

    def test_refuses_to_audit_unreachable_storage(
        self,
        db_session: Session,
        work_engine,
        make_job,
        admin,
        make_audit_run,
        make_audit_policy,
        monkeypatch,
    ) -> None:
        # Every object would read as missing; a report saying so is worse
        # than no report, so the audit fails and the policy backs off.
        from app.modules.storage.storage_backend import runtime

        class Down:
            def health_probe(self):
                return {"ok": False}

        monkeypatch.setattr(runtime, "get_backend", lambda: Down())
        policy = make_audit_policy(admin, enabled=True, next_due_at=utcnow())
        run = make_audit_run(admin, state=VaultAuditRunState.PENDING)

        _execute(work_engine, make_job, run)

        audited = _run(db_session, run.id)
        assert (audited.state, audited.error_code) == (
            VaultAuditRunState.FAILED,
            "storage_unavailable",
        )
        db_session.refresh(policy)
        assert policy.retry_after is not None

    def test_a_half_run_audit_is_failed_rather_than_resumed(
        self, db_session: Session, work_engine, make_job, admin, make_audit_run
    ) -> None:
        run = make_audit_run(admin, state=VaultAuditRunState.RUNNING)

        _execute(work_engine, make_job, run)

        assert _run(db_session, run.id).state == VaultAuditRunState.FAILED

    def test_a_settled_audit_is_left_alone(
        self, db_session: Session, work_engine, make_job, admin, make_audit_run
    ) -> None:
        run = make_audit_run(admin, state=VaultAuditRunState.COMPLETED)

        _execute(work_engine, make_job, run)

        assert _run(db_session, run.id).state == VaultAuditRunState.COMPLETED


class TestHooks:
    def test_cancelling_asks_the_audit_to_stop(
        self, db_session: Session, admin, make_audit_run
    ) -> None:
        run = make_audit_run(admin, state=VaultAuditRunState.RUNNING)

        DEFINITION.cancel(db_session, subject_key(run.id))
        db_session.commit()

        assert _run(db_session, run.id).cancel_requested is True

    def test_a_failed_job_fails_its_audit(
        self, db_session: Session, admin, make_audit_run
    ) -> None:
        run = make_audit_run(admin, state=VaultAuditRunState.RUNNING)

        DEFINITION.on_failure(db_session, subject_key(run.id), "boom")

        audited = _run(db_session, run.id)
        assert (audited.state, audited.error_code) == (
            VaultAuditRunState.FAILED,
            "audit_job_failed",
        )

    def test_an_audit_is_never_retried_in_place(
        self, db_session: Session, admin, make_audit_run
    ) -> None:
        # A new audit is a new run with a fresh snapshot, not this one again.
        run = make_audit_run(admin, state=VaultAuditRunState.FAILED)

        assert DEFINITION.retry(db_session, subject_key(run.id)) is False
