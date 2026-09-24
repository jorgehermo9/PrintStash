"""The ``administration.audit`` Job: vault audits, scheduled or requested.

Audit policies already keep their own durable schedule (``next_due_at``,
windows, jitter, launch-retry backoff) and admit a run with a claim that
allows one active run at a time. The source reuses that claim: each pass
admits a due run (``claim_due``) and reports every PENDING run as pending
work, and its ``next_due`` asks for a delayed pass at the policy's next slot,
so a 03:00 audit starts at 03:00 rather than at the next tick.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.db.models import (
    VaultAuditPolicy,
    VaultAuditRun,
    VaultAuditRunState,
    WorkPriority,
)
from app.db.session import get_session_factory
from app.modules.work.catalog import MAINTENANCE
from app.modules.work.contracts import JobContext, JobDefinition, Step, WorkItem

from . import vault_audit, vault_audit_policy
from .vault_audit_observability import prune_details, refresh_metrics

logger = get_logger(__name__)

DEFINITION = "administration.audit"


def subject_key(run_id: int) -> str:
    return f"vault_audit_run/{run_id}"


def _run_id(subject: str) -> int:
    return int(subject.split("/", 1)[1])


class AuditSource:
    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        from app.runtime.maintenance import restore_in_progress

        vault_audit_policy.claim_due(
            session,
            now=now,
            deferred_reason="maintenance" if restore_in_progress() else None,
        )
        rows = session.exec(
            select(VaultAuditRun.id)
            .where(VaultAuditRun.state == VaultAuditRunState.PENDING)
            .order_by(col(VaultAuditRun.id))
            .limit(limit)
        ).all()
        return [
            WorkItem(subject_key=subject_key(run_id), priority=WorkPriority.BACKFILL)
            for run_id in rows
            if run_id is not None
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        candidates: list[datetime] = []
        for policy in session.exec(
            select(VaultAuditPolicy).where(
                col(VaultAuditPolicy.enabled).is_(True),
                col(VaultAuditPolicy.paused).is_(False),
            )
        ).all():
            if policy.next_due_at is None:
                continue
            due = ensure_utc(policy.next_due_at)
            if policy.retry_after is not None:
                due = max(due, ensure_utc(policy.retry_after))
            window = vault_audit_policy.eligible_window(policy, max(due, now))
            if window is not None:
                due = max(
                    due,
                    window[0]
                    + timedelta(seconds=vault_audit_policy.slot_jitter(policy)),
                )
            candidates.append(due)
        return min(candidates) if candidates else None


def _execute(ctx: JobContext) -> None:
    from app.modules.storage.storage_backend.runtime import get_backend

    run_id = _run_id(ctx.subject_key)
    with get_session_factory().scoped_session() as session:
        run = session.get(VaultAuditRun, run_id)
        state = run.state if run is not None else None
    if state is None:
        return
    if state == VaultAuditRunState.RUNNING:
        vault_audit.fail_interrupted_run(run_id)
        return
    if state != VaultAuditRunState.PENDING:
        return
    try:
        healthy = bool(get_backend().health_probe().get("ok", False))
    except Exception:  # noqa: BLE001 - an unreachable backend is not healthy
        healthy = False
    if not healthy:
        vault_audit.fail_interrupted_run(run_id, reason="storage_unavailable")
        with get_session_factory().scoped_session() as session:
            vault_audit_policy.defer_launch_failure(session)
        return
    vault_audit.execute_run(run_id)
    with get_session_factory().scoped_session() as session:
        prune_details(session)
        refresh_metrics(session)
        run = session.get(VaultAuditRun, run_id)
        if run is not None:
            ctx.update(result={"run_id": run_id, "state": run.state.value})


def _cancel(session: Session, subject: str) -> None:
    vault_audit.request_cancel(session, _run_id(subject))


def _on_failure(session: Session, subject: str, reason: str) -> None:
    del session
    vault_audit.fail_interrupted_run(_run_id(subject), reason="audit_job_failed")
    logger.warning("audit job failed", extra={"reason": reason})


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=DEFINITION,
            lane=MAINTENANCE,
            steps=(Step(f"{DEFINITION}.execute", _execute),),
            source=AuditSource(),
            cancel=_cancel,
            on_failure=_on_failure,
            retry=lambda _session, _subject: False,
            label="Vault audits",
        )
    ]
