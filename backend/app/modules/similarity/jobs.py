"""``similarity.analyze``: each unfinished similarity run is a Job subject.

A run is the intent and its resumable checkpoint (``runs``). Every run that is
not finished is owed work, so the source offers each one; the engine runs one
execution per run at a time, and that execution is the run's fenced writer.
A slice advances the run unit by unit for a bounded time and completes; the
completion nudges the source, so a long analysis is a chain of short Jobs that
resume from the last committed checkpoint, and runs take turns on the lane.

A run the user asked for is interactive; a scheduled or system-triggered one
is backfill. While similarity is disabled only cancellations are offered, so a
cancelled run still settles. A slice that could not move its run (a
fingerprint another process is still computing) parks the source briefly
rather than spinning.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import JobKind, LaneName, SimilarityRun, WorkPriority
from app.db.session import get_session_factory
from app.modules.work.contracts import JobContext, JobDefinition, Step, WorkItem
from app.modules.work.sources import clear_idle, idle_window, mark_idle

from . import runs
from .configuration import read_settings

SLICE_SECONDS = 50.0
IDLE_SECONDS = 30.0


def subject_key(run_id: int) -> str:
    return f"similarity_run/{run_id}"


def _run_id(subject: str) -> int:
    return int(subject.split("/", 1)[1])


class AnalysisSource:
    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        if limit <= 0:
            return []
        enabled = read_settings(session).enabled
        if enabled:
            runs.schedule_due(session)
        parked = idle_window(session, JobKind.SIMILARITY_ANALYZE)
        if parked is not None and now < parked[1]:
            return []
        query = select(SimilarityRun.id, SimilarityRun.trigger).where(
            col(SimilarityRun.state).not_in(runs.TERMINAL)
        )
        if not enabled:
            query = query.where(col(SimilarityRun.cancel_requested).is_(True))
        rows = session.exec(query.order_by(col(SimilarityRun.id)).limit(limit)).all()
        return [
            WorkItem(
                subject_key=subject_key(run_id),
                priority=WorkPriority.INTERACTIVE
                if trigger == "manual"
                else WorkPriority.BACKFILL,
            )
            for run_id, trigger in rows
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        parked = idle_window(session, JobKind.SIMILARITY_ANALYZE)
        if parked is not None and now < parked[1]:
            return parked[1]
        config = read_settings(session)
        if not config.enabled or config.schedule_hours == 0:
            return None
        return now + timedelta(hours=config.schedule_hours)


def _advance(ctx: JobContext) -> None:
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.runtime import maintenance

    from .processing import SimilarityProcessor

    processor = SimilarityProcessor(
        get_session_factory(),
        get_backend(),
        retain_storage=maintenance.retain_storage_objects,
    )
    run_id = _run_id(ctx.subject_key)
    deadline = time.monotonic() + SLICE_SECONDS
    units = 0
    while time.monotonic() < deadline and not ctx.cancelled():
        if not processor.work_one(run_id, ctx.execution_id):
            break
        units += 1
    if units == 0:
        mark_idle(JobKind.SIMILARITY_ANALYZE, seconds=IDLE_SECONDS)
    else:
        clear_idle(JobKind.SIMILARITY_ANALYZE)
    ctx.update(result={"units": units, "sliced_at": utcnow().isoformat()})


def _cancel(session: Session, subject: str) -> None:
    """Cancelling the Job withdraws the run: it settles as cancelled."""
    run = session.get(SimilarityRun, _run_id(subject))
    if run is not None and run.state not in runs.TERMINAL:
        run.cancel_requested = True
        run.state = "cancelling"
        session.add(run)


def _on_failure(session: Session, subject: str, reason: str) -> None:
    run = session.get(SimilarityRun, _run_id(subject))
    if run is not None and run.state not in runs.TERMINAL:
        run.state = "failed"
        run.failure_code = "analysis_failed"
        run.finished_at = utcnow()
        run.active_scope_key = None
        run.writer = None
        session.add(run)
    del reason


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=JobKind.SIMILARITY_ANALYZE,
            lane=LaneName.SIMILARITY,
            steps=(Step(f"{JobKind.SIMILARITY_ANALYZE.value}.advance", _advance),),
            source=AnalysisSource(),
            cancel=_cancel,
            on_failure=_on_failure,
            retry=lambda _session, _subject: True,
            label="Similarity analysis",
            drain=True,
        )
    ]
