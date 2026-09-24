"""The ``similarity.analyze`` Job: drain claimable similarity work in bounded slices.

Similarity runs already keep durable checkpoints and their own work-unit
leases (``runs.claim``). The Job therefore has one subject, the analysis
queue: whenever any run is claimable (or a scheduled run is due), one Job
drains work units for a bounded time slice and completes. Its completion
nudges the source, so a long analysis continues as a chain of short Jobs, and
each slice is resumable from the last committed checkpoint.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import SimilarityRun, WorkPriority
from app.db.session import get_session_factory
from app.modules.work.catalog import SIMILARITY
from app.modules.work.contracts import JobContext, JobDefinition, Step, WorkItem
from app.modules.work.sources import clear_idle, idle_window, mark_idle

from . import runs
from .configuration import read_settings

DEFINITION = "similarity.analyze"
SUBJECT = "similarity/queue"
SLICE_SECONDS = 50.0
IDLE_SECONDS = 30.0


class AnalysisSource:
    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        if limit <= 0:
            return []
        if read_settings(session).enabled:
            runs.schedule_due(session)
        parked = idle_window(session, DEFINITION)
        if parked is not None and now < parked[1]:
            return []
        claimable = session.exec(
            select(SimilarityRun.id)
            .where(
                col(SimilarityRun.state).not_in(runs.TERMINAL),
                (col(SimilarityRun.lease_token).is_(None))
                | (col(SimilarityRun.lease_expires_at) <= now),
            )
            .limit(1)
        ).first()
        if claimable is None:
            return []
        return [WorkItem(subject_key=SUBJECT, priority=WorkPriority.BACKFILL)]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        parked = idle_window(session, DEFINITION)
        if parked is not None and now < parked[1]:
            return parked[1]
        config = read_settings(session)
        if not config.enabled or config.schedule_hours == 0:
            return None
        return now + timedelta(hours=config.schedule_hours)


def _drain(ctx: JobContext) -> None:
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.runtime import maintenance

    from .processing import SimilarityProcessor

    processor = SimilarityProcessor(
        get_session_factory(),
        get_backend(),
        retain_storage=maintenance.retain_storage_objects,
    )
    deadline = time.monotonic() + SLICE_SECONDS
    units = 0
    while time.monotonic() < deadline and not ctx.cancelled():
        if not processor.work_one():
            break
        units += 1
    if units == 0:
        # Claimable work that would not move (compute busy, leases held
        # elsewhere): wait before trying again rather than spin.
        mark_idle(DEFINITION, seconds=IDLE_SECONDS)
    else:
        clear_idle(DEFINITION)
    ctx.update(result={"units": units, "sliced_at": utcnow().isoformat()})


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=DEFINITION,
            lane=SIMILARITY,
            steps=(Step(f"{DEFINITION}.drain", _drain),),
            source=AnalysisSource(),
            retry=lambda _session, _subject: True,
            label="Similarity analysis",
        )
    ]
