"""The ``printing.dispatch`` Job: route queued fleet work onto free printers.

Dispatch is one drain over the whole queue, because routing is a fleet-wide
decision (which printer, in what order). The lane is global with concurrency
one, so exactly one dispatcher runs across every process; at the start of each
drain, any job it finds left mid-upload was therefore stranded by a dispatcher
that died, and is settled as ``dispatch_outcome_unknown`` (never retried
automatically: the printer may already be printing it).

A drain that finds nothing it can route (every printer busy) parks the source
briefly instead of resubmitting itself; a printer becoming free, or new work
being queued, wakes it immediately.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import PrintJob, PrintJobState, WorkPriority
from app.db.scopes import live
from app.modules.work.async_steps import run_async
from app.modules.work.catalog import PRINTING
from app.modules.work.contracts import JobContext, JobDefinition, Step, WorkItem
from app.modules.work.sources import clear_idle, idle_window, mark_idle

DISPATCH_DEFINITION = "printing.dispatch"
SUBJECT = "fleet/queue"
SLICE_SECONDS = 30.0
IDLE_SECONDS = 30.0

_provider_builder = None


def bind_provider_builder(builder) -> None:
    """Composition sets the provider client factory this process dispatches with."""
    global _provider_builder
    _provider_builder = builder


def _builder():
    if _provider_builder is not None:
        return _provider_builder
    from functools import partial

    from app.modules.printing.printer_provider import (
        build_provider_registry,
        get_provider_client,
    )

    return partial(get_provider_client, registry=build_provider_registry())


def wake_dispatch() -> None:
    """New or released fleet work: unpark the dispatcher and nudge it."""
    from app.modules.work import nudge

    clear_idle(DISPATCH_DEFINITION)
    nudge(DISPATCH_DEFINITION)


class DispatchSource:
    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        if limit <= 0:
            return []
        queued = select(PrintJob.id).where(
            PrintJob.state == PrintJobState.QUEUED,
            col(PrintJob.dispatch_claimed_at).is_(None),
            live(PrintJob),
        )
        stranded = select(PrintJob.id).where(
            PrintJob.state == PrintJobState.UPLOADING,
            col(PrintJob.dispatch_claimed_at).is_not(None),
            live(PrintJob),
        )
        parked = idle_window(session, DISPATCH_DEFINITION)
        if parked is not None and now < parked[1]:
            # Parked: only work queued since the drain gave up can wake it.
            queued = queued.where(PrintJob.created_at > parked[0])
        if (
            session.exec(queued.limit(1)).first() is None
            and session.exec(stranded.limit(1)).first() is None
        ):
            return []
        return [WorkItem(subject_key=SUBJECT, priority=WorkPriority.INTERACTIVE)]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        parked = idle_window(session, DISPATCH_DEFINITION)
        return parked[1] if parked is not None and now < parked[1] else None


def _drain(ctx: JobContext) -> None:
    from app.modules.printing.printer_jobs import (
        drain_dispatch_queue,
        reconcile_stranded_dispatches,
        scheduler_status,
    )

    stranded = reconcile_stranded_dispatches()
    dispatched = run_async(
        drain_dispatch_queue(_builder(), budget_seconds=SLICE_SECONDS)
    )
    if dispatched == 0:
        mark_idle(DISPATCH_DEFINITION, seconds=IDLE_SECONDS)
    else:
        clear_idle(DISPATCH_DEFINITION)
    ctx.update(
        result={
            "dispatched": dispatched,
            "stranded_settled": stranded,
            "last_dispatch_at": scheduler_status.last_dispatch_at.isoformat()
            if scheduler_status.last_dispatch_at
            else None,
            "last_error": scheduler_status.last_error,
            "sliced_at": utcnow().isoformat(),
        }
    )


def scheduler_snapshot() -> dict[str, object]:
    """The dispatcher's state, read from the database so every process agrees.

    ``running`` means dispatch Jobs can run (the engine is bound); ticks and
    outcomes come from the dispatch definition's cursor and latest Job, not
    from process memory, because the dispatch may run on another worker.
    """
    import json

    from app.db.models import Job, ReconcileCursor
    from app.db.session import get_session_factory
    from app.modules.work import bound

    with get_session_factory().scoped_session() as session:
        cursor = session.get(ReconcileCursor, DISPATCH_DEFINITION)
        latest = session.exec(
            select(Job)
            .where(Job.kind == DISPATCH_DEFINITION)
            .order_by(col(Job.updated_at).desc())
            .limit(1)
        ).first()
        result = json.loads(latest.status_json).get("result") or {} if latest else {}
        return {
            "running": bound(),
            "last_tick_at": cursor.last_pass_finished_at if cursor else None,
            "last_dispatch_at": result.get("last_dispatch_at"),
            "last_error": result.get("last_error"),
        }


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=DISPATCH_DEFINITION,
            lane=PRINTING,
            steps=(Step(f"{DISPATCH_DEFINITION}.drain", _drain),),
            source=DispatchSource(),
            retry=lambda _session, _subject: True,
            label="Fleet dispatch",
        )
    ]
