"""Turning Jobs into engine executions, and nudging the reconciler.

Hot paths never submit a specific job. They record intent in the domain (and,
for request-originated work, a queued Job) in the same transaction as the
domain change, then call ``nudge``: a request to run one definition's
reconcile pass soon. If the nudge is lost, the next tick finds the same work;
nothing is lost with it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from sqlmodel import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    ACTIVE_JOB_STATES,
    Job,
    JobState,
    ReconcileCursor,
    WorkPriority,
)
from app.db.session import get_session_factory

from . import catalog as catalog_module
from .contracts import Submission, SubmitOutcome

logger = get_logger(__name__)

# Engine priority: lower runs first. Interactive work always overtakes backfill.
PRIORITY_RANK = {WorkPriority.INTERACTIVE: 1, WorkPriority.BACKFILL: 1000}


def execution_id(job_id: str, attempt: int) -> str:
    """The engine key of one attempt: exactly once per (Job, attempt)."""
    return f"{job_id}:{attempt}"


def dedupe_key(definition: str, subject_key: str) -> str:
    """At most one active execution per subject, whatever its Job id."""
    return f"{definition}|{subject_key}"


def submit(job_id: str, *, now: datetime | None = None) -> SubmitOutcome | None:
    """Submit the next attempt of an active Job. ``None`` when nothing to do.

    The attempt number is committed only after the engine accepted it. A crash
    in between re-derives the same attempt, so the same execution id, and the
    engine returns the execution it already has.
    """
    now = now or utcnow()
    engine = catalog_module.get_engine()
    catalog = catalog_module.get_catalog()
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return None
        definition = catalog.definition(row.kind)
        lane = catalog.lanes[definition.lane]
        attempt = row.attempts + 1
        submission = Submission(
            execution_id=execution_id(row.id, attempt),
            job_id=row.id,
            definition=row.kind,
            subject_key=row.subject_key,
            lane=lane.name,
            priority=row.priority,
            dedupe_key=None
            if lane.partitioned
            else dedupe_key(row.kind, row.subject_key),
            partition_key=definition.partition(row.subject_key)
            if lane.partitioned and definition.partition is not None
            else None,
            attempt=attempt,
        )
    outcome = engine.submit(submission)
    if outcome is SubmitOutcome.DEDUPLICATED:
        # An older attempt of this subject is still active in the engine. The
        # reconciler cancels or settles it; this attempt is not recorded.
        return outcome
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is not None and row.attempts < attempt:
            row.attempts = attempt
            if row.state == JobState.INTERRUPTED:
                row.state = JobState.QUEUED
            row.updated_at = now
            session.add(row)
            session.commit()
    return outcome


def _cursor(session: Session, source: str) -> ReconcileCursor:
    cursor = session.get(ReconcileCursor, source)
    if cursor is None:
        cursor = ReconcileCursor(source=source)
        session.add(cursor)
    return cursor


def nudge(
    source: str,
    *,
    now: datetime | None = None,
    delay: float | None = None,
    priority: WorkPriority = WorkPriority.INTERACTIVE,
) -> None:
    """Ask for ``source``'s reconcile pass soon. Never raises into the caller.

    The dirty stamp is written first. A pass is enqueued only when none of at
    least this priority is already queued; a pass that is running will see the
    stamp when it tries to release its claim and run again. A hot path's nudge
    is interactive; the tick's sweep of every source is backfill. So an upload
    arriving just after startup gets its own interactive pass rather than
    waiting behind the backfill pass the sweep queued for the same source.
    """
    if not catalog_module.bound():
        return
    now = now or utcnow()
    try:
        catalog_module.get_catalog().definition(source)
        stamp = now + timedelta(seconds=delay) if delay else now
        with get_session_factory().scoped_session() as session:
            cursor = _cursor(session, source)
            state = json.loads(cursor.state_json or "{}")
            if delay is None:
                cursor.nudged_at = now
                grace = timedelta(seconds=settings.jobs_submit_grace_seconds)
                queued = cursor.pass_queued_at is not None and (
                    ensure_utc(cursor.pass_queued_at) > now - grace
                )
                covered = queued and (
                    priority is WorkPriority.BACKFILL
                    or state.get("queued_priority") == WorkPriority.INTERACTIVE.value
                )
                if covered:
                    session.add(cursor)
                    session.commit()
                    return
                cursor.pass_queued_at = now
                state["queued_priority"] = priority.value
                cursor.state_json = json.dumps(state, separators=(",", ":"))
            session.add(cursor)
            session.commit()
        catalog_module.get_engine().submit(
            Submission(
                execution_id=f"{catalog_module.RECONCILE_DEFINITION}:{source}:"
                f"{uuid.uuid4().hex}",
                job_id="",
                definition=catalog_module.RECONCILE_DEFINITION,
                subject_key=source,
                lane=catalog_module.RECONCILE,
                priority=priority,
                delay_seconds=delay,
                metadata={"due_at": stamp.isoformat()},
            )
        )
    except Exception:  # noqa: BLE001 - the tick recovers a lost nudge
        logger.exception("reconciler nudge failed", extra={"source": source})


def nudge_after_commit(session: Session, source: str) -> None:
    """Nudge ``source`` once ``session``'s transaction commits (never before).

    For intent recorded inside a caller's transaction (a transactional
    outbox): nudging before the commit could run a pass that cannot see the
    row yet, and nudging after a rollback would be noise.
    """
    from sqlalchemy import event

    # One nudge per source per transaction, however many rows it recorded, and
    # one pair of listeners per session, however many transactions it runs.
    pending: set[str] | None = session.info.get(_PENDING_NUDGES)
    if pending is None:
        pending = session.info[_PENDING_NUDGES] = set()
        event.listen(session, "after_commit", _nudge_committed)
        event.listen(session, "after_soft_rollback", _forget_rolled_back)
    pending.add(source)


_PENDING_NUDGES = "work_nudges_after_commit"


def _nudge_committed(session: Session) -> None:
    # A released savepoint is reported as a commit too. Its transaction still
    # holds the write lock that the nudge's own session would wait on.
    if session.get_nested_transaction() is not None:
        return
    pending: set[str] = session.info.get(_PENDING_NUDGES, set())
    sources = sorted(pending)
    pending.clear()
    for source in sources:
        nudge(source)


def _forget_rolled_back(session: Session, previous_transaction) -> None:
    if previous_transaction.nested:
        return
    session.info.get(_PENDING_NUDGES, set()).clear()


def forget_queued_passes() -> int:
    """Clear every cursor's queued-pass mark; returns how many were set.

    The mark suppresses duplicate nudges while a pass waits in the engine. A
    process that died (or an older version whose executions a startup sweep
    cancelled) can leave marks for passes that will never run, which would
    hold back this process's startup nudges for the whole grace window. The
    pass claim is single-flight, so at worst this costs one extra pass.
    """
    from sqlalchemy import update
    from sqlmodel import col

    from app.db.affected import affected

    with get_session_factory().scoped_session() as session:
        cleared = affected(
            session,
            update(ReconcileCursor)
            .where(col(ReconcileCursor.pass_queued_at).is_not(None))
            .values(pass_queued_at=None),
        )
        session.commit()
    return cleared


def nudge_all(*, now: datetime | None = None) -> None:
    """The tick: nudge every definition. Each pass is cheap when idle."""
    if not catalog_module.bound():
        return
    for name in sorted(catalog_module.get_catalog().definitions):
        nudge(name, now=now, priority=WorkPriority.BACKFILL)
