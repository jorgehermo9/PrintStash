"""The reconciler: the load-bearing path that makes background work converge.

Every guarantee that work eventually happens lives here. A pass over one
definition does two bounded things, in order, over one captured ``now``:

1. **Repair.** For each non-terminal Job, ``decide`` compares the Job with the
   engine's evidence about its current attempt and returns one verdict:
   submit the first attempt, leave it alone, interrupt it (the execution was
   lost, superseded by an upgrade, or stranded on a dead executor) and
   resubmit it, fail it (resubmits exhausted, or the engine failed it), or
   complete it (the engine finished but the terminal write was lost).
2. **Discover.** A definition with a source asks it for pending subjects,
   creates one Job per subject that has no active Job, and submits them, up to
   the smaller of the batch size and the lane's headroom.

A pass is single-flight per definition through the cursor claim, and it re-runs
itself while nudges arrive during it (see ``ReconcileCursor``). A batch that
came back full with lane room to spare continues immediately; a full lane stops,
and each completion nudges its own definition again, so a backfill flows
without ever filling the engine with the whole library.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import func, or_, update
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import ACTIVE_JOB_STATES, Job, JobState, ReconcileCursor
from app.db.session import get_session_factory

from . import catalog as catalog_module
from . import executors
from .contracts import EngineEvidence, EngineStatus, JobDefinition, WorkItem
from .jobs import TERMINAL_STATES, ActiveJobExists, jobs
from .submission import execution_id, nudge, submit

logger = get_logger(__name__)

_MAX_LOOPS = 20


class Verdict(str, Enum):
    NONE = "none"
    SUBMIT = "submit"
    INTERRUPT = "interrupt"
    FAIL = "fail"
    COMPLETE = "complete"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str = ""
    cancel_engine: bool = False


def decide(
    job: Job,
    evidence: EngineEvidence | None,
    *,
    now: datetime,
    app_version: str,
    stale_executors: set[str],
    max_resubmits: int,
    grace: timedelta,
) -> Decision:
    """The pure decision table for one non-terminal Job.

    ``evidence`` is ``None`` when no attempt was ever submitted. Every branch
    names why, and the reason lands on the Job when it is interrupted or failed.
    """
    state = JobState(job.state)
    if state is JobState.INTERRUPTED:
        if job.resubmits > max_resubmits:
            return Decision(Verdict.FAIL, "interrupted_repeatedly")
        return Decision(Verdict.SUBMIT, "resubmit")
    if evidence is None or job.attempts == 0:
        return Decision(Verdict.SUBMIT, "first_attempt")
    if evidence.absent:
        if now - ensure_utc(job.updated_at) < grace:
            return Decision(Verdict.NONE, "submission_in_flight")
        return Decision(Verdict.INTERRUPT, "execution_lost")
    status = evidence.status
    if status is EngineStatus.SUCCEEDED:
        return Decision(Verdict.COMPLETE, "terminal_write_lost")
    if status is EngineStatus.FAILED:
        return Decision(Verdict.FAIL, "engine_failed")
    if status is EngineStatus.CANCELLED:
        return Decision(Verdict.INTERRUPT, "execution_cancelled")
    if evidence.app_version and evidence.app_version != app_version:
        return Decision(Verdict.INTERRUPT, "application_upgraded", cancel_engine=True)
    if (
        status is EngineStatus.RUNNING
        and evidence.executor_id is not None
        and evidence.executor_id in stale_executors
    ):
        return Decision(Verdict.INTERRUPT, "executor_lost", cancel_engine=True)
    return Decision(Verdict.NONE, "in_progress")


@dataclass
class PassResult:
    submitted: int = 0
    deferred: int = 0
    interrupted: int = 0
    failed: int = 0
    completed: int = 0
    skipped: int = 0
    full: bool = False
    # The earliest moment a subject held back by the resubmit cooldown is due.
    cooling_until: datetime | None = None
    outcomes: dict[str, int] = field(default_factory=dict)

    def count(self, outcome: str) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1


def _interrupt(job: Job, reason: str, *, now: datetime) -> None:
    from app.core.metrics import record_resubmit

    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job.id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return
        row.state = JobState.INTERRUPTED
        row.resubmits += 1
        row.updated_at = now
        session.add(row)
        session.commit()
    jobs.update(job.id, error=reason, retryable=True)
    record_resubmit(job.kind)


def _repair(definition: JobDefinition, *, now: datetime, result: PassResult) -> None:
    engine = catalog_module.get_engine()
    with get_session_factory().scoped_session() as session:
        rows = list(
            session.exec(
                select(Job)
                .where(
                    Job.kind == definition.name, col(Job.state).in_(ACTIVE_JOB_STATES)
                )
                .order_by(col(Job.updated_at))
                .limit(settings.jobs_reconcile_batch)
            ).all()
        )
        for row in rows:
            session.expunge(row)
    if not rows:
        return
    ids = [execution_id(row.id, row.attempts) for row in rows if row.attempts > 0]
    evidence = engine.evidence(ids) if ids else {}
    stale = executors.stale_ids(now=now)
    grace = timedelta(seconds=settings.jobs_submit_grace_seconds)
    for row in rows:
        seen = (
            evidence.get(execution_id(row.id, row.attempts)) if row.attempts else None
        )
        decision = decide(
            row,
            seen,
            now=now,
            app_version=settings.app_version,
            stale_executors=stale,
            max_resubmits=settings.jobs_max_resubmits,
            grace=grace,
        )
        result.count(decision.reason)
        if decision.verdict is Verdict.NONE:
            continue
        if decision.cancel_engine and row.attempts:
            try:
                engine.cancel(execution_id(row.id, row.attempts))
            except Exception:  # noqa: BLE001 - the next pass retries the cancel
                logger.warning("engine cancel failed", extra={"job_id": row.id})
                result.deferred += 1
                continue
        if decision.verdict is Verdict.COMPLETE:
            jobs.finish(row.id, state=JobState.COMPLETED)
            result.completed += 1
            continue
        if decision.verdict is Verdict.FAIL:
            jobs.finish(row.id, state=JobState.FAILED, error=decision.reason)
            _call_failure_hook(definition, row.subject_key, decision.reason)
            result.failed += 1
            continue
        if decision.verdict is Verdict.INTERRUPT:
            _interrupt(row, decision.reason, now=now)
            result.interrupted += 1
            with get_session_factory().scoped_session() as session:
                fresh = session.get(Job, row.id)
                exhausted = (
                    fresh is not None and fresh.resubmits > settings.jobs_max_resubmits
                )
            if exhausted:
                jobs.finish(
                    row.id, state=JobState.FAILED, error="interrupted_repeatedly"
                )
                _call_failure_hook(
                    definition, row.subject_key, "interrupted_repeatedly"
                )
                result.failed += 1
                continue
        _submit(row.id, result)


def _call_failure_hook(
    definition: JobDefinition, subject_key: str, reason: str
) -> None:
    with get_session_factory().scoped_session() as session:
        try:
            definition.on_failure(session, subject_key, reason)
            session.commit()
        except Exception:  # noqa: BLE001 - the Job already records the failure
            session.rollback()
            logger.exception("job failure hook failed", extra={"kind": definition.name})


def _submit(job_id: str, result: PassResult) -> None:
    try:
        outcome = submit(job_id)
    except Exception:  # noqa: BLE001 - the Job stays queued for the next pass
        logger.warning("job submission failed", extra={"job_id": job_id})
        result.deferred += 1
        return
    if outcome is None:
        return
    if outcome.value == "deduplicated":
        result.deferred += 1
    else:
        result.submitted += 1


def _headroom(definition: JobDefinition) -> int:
    catalog = catalog_module.get_catalog()
    lane = catalog.lanes[definition.lane]
    depth = catalog_module.get_engine().lane_depth(lane.name)
    return max(0, lane.headroom - depth.queued)


def _discover(definition: JobDefinition, *, now: datetime, result: PassResult) -> None:
    source = definition.source
    if source is None:
        return
    limit = min(settings.jobs_reconcile_batch, _headroom(definition))
    if limit <= 0:
        result.count("lane_full")
        return
    with get_session_factory().scoped_session() as session:
        items: Sequence[WorkItem] = source.pending(session, now=now, limit=limit)
        finished = _recently_finished(
            session, definition.name, [item.subject_key for item in items], now=now
        )
    cooldown = timedelta(seconds=settings.jobs_resubmit_cooldown_seconds)
    for item in items:
        last = finished.get(item.subject_key)
        if last is not None:
            due = last + cooldown
            result.deferred += 1
            result.count("cooling_down")
            if result.cooling_until is None or due < result.cooling_until:
                result.cooling_until = due
            continue
        _create_and_submit(definition, item, now=now, result=result)
    # A batch held back by the cooldown is not a reason to loop straight away:
    # the same subjects would come back and be held back again.
    result.full = len(items) >= limit and result.cooling_until is None


def _recently_finished(
    session: Session, definition: str, subject_keys: list[str], *, now: datetime
) -> dict[str, datetime]:
    """When each of ``subject_keys`` last had a Job of ``definition`` finish,
    for those that did within the resubmit cooldown."""
    window = settings.jobs_resubmit_cooldown_seconds
    if not subject_keys or window <= 0:
        return {}
    since = now - timedelta(seconds=window)
    rows = session.exec(
        select(Job.subject_key, func.max(Job.finished_at))
        .where(
            col(Job.kind) == definition,
            col(Job.subject_key).in_(subject_keys),
            col(Job.state).in_([state.value for state in TERMINAL_STATES]),
            col(Job.finished_at) > since,
        )
        .group_by(col(Job.subject_key))
    ).all()
    return {subject: ensure_utc(at) for subject, at in rows if at is not None}


def _create_and_submit(
    definition: JobDefinition, item: WorkItem, *, now: datetime, result: PassResult
) -> None:
    try:
        job_id = jobs.create(
            definition=definition.name,
            subject_key=item.subject_key,
            owner_user_id=item.owner_user_id,
            priority=item.priority,
        )
    except ActiveJobExists:
        result.count("already_active")
        return
    if item.occurrence_at is not None:
        _record_occurrence(definition.name, item.occurrence_at)
    if item.skip_reason is not None:
        jobs.finish(job_id, state=JobState.CANCELLED, error=item.skip_reason)
        result.skipped += 1
        result.count(item.skip_reason)
        return
    _submit(job_id, result)


def _record_occurrence(source: str, occurrence: datetime) -> None:
    with get_session_factory().scoped_session() as session:
        cursor = session.get(ReconcileCursor, source) or ReconcileCursor(source=source)
        if cursor.last_occurrence_at is None or ensure_utc(
            cursor.last_occurrence_at
        ) < ensure_utc(occurrence):
            cursor.last_occurrence_at = occurrence
        session.add(cursor)
        session.commit()


def _claim(source: str, holder: str, *, now: datetime) -> bool:
    ttl = timedelta(seconds=max(settings.fence_ttl_seconds, 60))
    with get_session_factory().scoped_session() as session:
        if session.get(ReconcileCursor, source) is None:
            session.add(ReconcileCursor(source=source))
            session.commit()
        claimed = affected(
            session,
            update(ReconcileCursor)
            .where(
                col(ReconcileCursor.source) == source,
                or_(
                    col(ReconcileCursor.holder).is_(None),
                    col(ReconcileCursor.holder_expires_at) < now,
                ),
            )
            .values(
                holder=holder,
                holder_expires_at=now + ttl,
                pass_queued_at=None,
                last_pass_started_at=now,
            ),
        )
        session.commit()
    return bool(claimed)


def _release(
    source: str, holder: str, *, started: datetime, result: PassResult
) -> bool:
    """Release the claim unless a nudge arrived after ``started``."""
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        released = affected(
            session,
            update(ReconcileCursor)
            .where(
                col(ReconcileCursor.source) == source,
                col(ReconcileCursor.holder) == holder,
                or_(
                    col(ReconcileCursor.nudged_at).is_(None),
                    col(ReconcileCursor.nudged_at) <= started,
                ),
            )
            .values(
                holder=None,
                holder_expires_at=None,
                last_pass_finished_at=now,
                last_pass_submitted=result.submitted,
                last_pass_deferred=result.deferred,
            ),
        )
        if not released:
            ttl = timedelta(seconds=max(settings.fence_ttl_seconds, 60))
            session.execute(
                update(ReconcileCursor)
                .where(
                    col(ReconcileCursor.source) == source,
                    col(ReconcileCursor.holder) == holder,
                )
                .values(holder_expires_at=now + ttl, last_pass_started_at=now)
            )
        session.commit()
    return bool(released)


def _force_release(source: str, holder: str) -> None:
    with get_session_factory().scoped_session() as session:
        session.execute(
            update(ReconcileCursor)
            .where(
                col(ReconcileCursor.source) == source,
                col(ReconcileCursor.holder) == holder,
            )
            .values(holder=None, holder_expires_at=None, last_pass_finished_at=utcnow())
        )
        session.commit()


def run_pass(source: str, *, holder: str | None = None) -> PassResult:
    """One claimed, bounded, self-continuing pass over one definition."""
    from app.core.metrics import record_reconcile_pass

    definition = catalog_module.get_catalog().definition(source)
    holder = holder or executors.executor_id()
    total = PassResult()
    started_clock = time.monotonic()
    now = utcnow()
    if not _claim(source, holder, now=now):
        total.count("claimed_elsewhere")
        return total
    try:
        for _ in range(_MAX_LOOPS):
            started = now
            result = PassResult()
            _repair(definition, now=now, result=result)
            _discover(definition, now=now, result=result)
            _merge(total, result)
            if result.full:
                now = utcnow()
                continue
            if _release(source, holder, started=started, result=result):
                break
            now = utcnow()
        else:
            _force_release(source, holder)
            nudge(source)
    except Exception:
        _force_release(source, holder)
        raise
    _schedule_next(definition, also=total.cooling_until)
    record_reconcile_pass(
        source,
        time.monotonic() - started_clock,
        {
            "submitted": total.submitted,
            "deferred": total.deferred,
            "interrupted": total.interrupted,
            "failed": total.failed,
            "completed": total.completed,
            "skipped": total.skipped,
        },
    )
    return total


def _merge(total: PassResult, result: PassResult) -> None:
    total.submitted += result.submitted
    total.deferred += result.deferred
    total.interrupted += result.interrupted
    total.failed += result.failed
    total.completed += result.completed
    total.skipped += result.skipped
    total.full = result.full
    if result.cooling_until is not None and (
        total.cooling_until is None or result.cooling_until < total.cooling_until
    ):
        total.cooling_until = result.cooling_until
    for key, value in result.outcomes.items():
        total.outcomes[key] = total.outcomes.get(key, 0) + value


def _schedule_next(definition: JobDefinition, *, also: datetime | None = None) -> None:
    """Ask for a delayed pass at the source's next due time, if it has one.

    ``also`` is a due time the pass itself found (a subject cooling down); the
    earlier of the two wins.
    """
    if definition.source is None:
        return
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        due = definition.source.next_due(session, now=now)
    if also is not None and (due is None or ensure_utc(also) < ensure_utc(due)):
        due = also
    if due is None:
        return
    delay = (ensure_utc(due) - now).total_seconds()
    if 0 < delay <= settings.jobs_reconcile_interval_seconds:
        nudge(definition.name, delay=delay)


def execute_pass(source: str, runner) -> None:
    """Engine entry point for one reconcile execution."""
    from .contracts import NO_RETRY

    runner.run("work.reconcile", lambda: _pass_summary(source), NO_RETRY)


def _pass_summary(source: str) -> int:
    return run_pass(source).submitted


def sweep_foreign_versions() -> int:
    """Cancel executions another application version left behind.

    Their Jobs stay active; the next pass sees the cancellation (or the version
    mismatch) and resubmits them on this version's code.
    """
    engine = catalog_module.get_engine()
    cancelled = 0
    for execution in engine.foreign_version_executions():
        try:
            engine.cancel(execution)
            cancelled += 1
        except Exception:  # noqa: BLE001 - the repair pass settles the rest
            logger.warning("foreign version cancel failed", extra={"id": execution})
    return cancelled


__all__ = [
    "Decision",
    "PassResult",
    "Verdict",
    "decide",
    "execute_pass",
    "run_pass",
    "sweep_foreign_versions",
]
