"""The body of one Job attempt, identical under every engine.

An engine calls ``execute_job(job_id, attempt, runner)`` for each execution
it runs. The body re-reads the Job, declines a superseded attempt, waits out a
restore fence, runs the definition's steps and records the outcome. Every
decision taken from changing state goes through ``runner.run`` so a durable
engine replays the same path on recovery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import ACTIVE_JOB_STATES, Job, JobState, WorkPriority
from app.db.session import get_session_factory

from . import catalog as catalog_module
from .contracts import NO_RETRY, JobDefinition, Step, StepRunner
from .jobs import TERMINAL_STATES, jobs, safe_error

logger = get_logger(__name__)

_ADMISSION_WAIT_MAX_S = 30.0
_CANCELLED = "__cancelled__"
_DEFERRED = "__deferred__"


@dataclass
class ExecutionContext:
    """The ``JobContext`` a step receives."""

    job_id: str
    definition: str
    subject_key: str
    priority: WorkPriority
    execution_id: str
    attempt: int

    def update(self, **fields: Any) -> None:
        jobs.update(self.job_id, **fields)

    def finish(self, state: str, **fields: Any) -> None:
        jobs.finish(self.job_id, state=state, **fields)

    def cancelled(self) -> bool:
        status = jobs.get(self.job_id)
        return status is None or status.state == JobState.CANCELLED.value

    def nudge(self, source: str) -> None:
        from .submission import nudge

        nudge(source)


def _begin(job_id: str, attempt: int) -> dict[str, str] | None:
    """Claim the attempt: ``None`` when it is superseded or the Job is settled."""
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return None
        if row.attempts > attempt:
            return None
        now = utcnow()
        row.attempts = max(row.attempts, attempt)
        row.state = JobState.RUNNING
        row.started_at = row.started_at or now
        row.updated_at = now
        session.add(row)
        session.commit()
        return {
            "kind": row.kind,
            "subject_key": row.subject_key,
            "priority": WorkPriority(row.priority).value,
        }


def _current_attempt(job_id: str) -> str | None:
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None:
            return _CANCELLED
        if row.state == JobState.CANCELLED:
            return _CANCELLED
        return None


def _run_step(step: Step, context: ExecutionContext, *, mutating: bool) -> Any:
    """One admitted step attempt.

    A step of a mutating definition is a write-capable operation for
    maintenance purposes: a restore drains it like any other mutation, and a
    step that cannot be admitted (a restore holds the fence) is deferred, not
    failed.
    """
    from app.core.metrics import record_step
    from app.runtime.maintenance import begin_mutating_operation, end_mutating_operation

    if _current_attempt(context.job_id) == _CANCELLED:
        return _CANCELLED
    if mutating and not begin_mutating_operation():
        return _DEFERRED
    started = time.monotonic()
    try:
        result = step.fn(context)
    except BaseException:
        record_step(context.definition, step.name, "error", time.monotonic() - started)
        raise
    finally:
        if mutating:
            end_mutating_operation()
    record_step(context.definition, step.name, "ok", time.monotonic() - started)
    return result if _json_small(result) else None


def _json_small(value: Any) -> bool:
    """Only small, plain results are checkpointed; anything else is dropped."""
    return value is None or isinstance(value, (str, int, float, bool))


def _settle(
    job_id: str, definition: JobDefinition, subject_key: str, error: str | None
) -> None:
    """Record the attempt's outcome and nudge whoever waits on completion."""
    status = jobs.get(job_id)
    if status is not None and status.state not in {
        state.value for state in TERMINAL_STATES
    }:
        if error is None:
            jobs.finish(job_id, state=JobState.COMPLETED)
        else:
            jobs.finish(job_id, state=JobState.FAILED, error=error, retryable=True)
    status = jobs.get(job_id)
    if status is not None and status.state == JobState.FAILED.value:
        with get_session_factory().scoped_session() as session:
            try:
                definition.on_failure(session, subject_key, status.error or "failed")
                session.commit()
            except Exception:  # noqa: BLE001 - the Job already records the failure
                session.rollback()
                logger.exception(
                    "job failure hook failed",
                    extra={"job_id": job_id, "kind": definition.name},
                )
    from .submission import nudge

    for source in dict.fromkeys((definition.name, *definition.completion_nudges)):
        nudge(source)


def execute_job(job_id: str, attempt: int, runner: StepRunner) -> None:
    """Run one attempt of ``job_id`` to its recorded outcome."""
    begun = runner.run(
        f"work.begin#{attempt}", lambda: _begin(job_id, attempt), NO_RETRY
    )
    if begun is None:
        return
    definition = catalog_module.get_catalog().definition(begun["kind"])
    subject_key = begun["subject_key"]
    context = ExecutionContext(
        job_id=job_id,
        definition=definition.name,
        subject_key=subject_key,
        priority=WorkPriority(begun["priority"]),
        execution_id=f"{job_id}:{attempt}",
        attempt=attempt,
    )
    error: str | None = None
    try:
        for step in definition.steps:
            wait = 1.0
            while True:
                outcome = runner.run(
                    step.name,
                    lambda step=step: _run_step(
                        step, context, mutating=definition.mutating
                    ),
                    step.retry,
                )
                if outcome != _DEFERRED:
                    break
                runner.sleep(wait)
                wait = min(wait * 2, _ADMISSION_WAIT_MAX_S)
            if outcome == _CANCELLED:
                return
    except BaseException as exc:
        if runner.is_cancellation(exc):
            raise
        if not isinstance(exc, Exception):
            raise
        logger.exception(
            "job step failed", extra={"job_id": job_id, "kind": definition.name}
        )
        error = safe_error(str(exc)) or type(exc).__name__
    runner.run(
        "work.settle",
        lambda: _settle(job_id, definition, subject_key, error),
        NO_RETRY,
    )
