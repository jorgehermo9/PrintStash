"""Reusable work sources. Domain modules write their own ``StateSource``s.

``ScheduleSource`` turns a code-declared cadence into pending work, the way a
cron would, but through the reconciler: an occurrence is due once, only the
newest occurrence is ever offered (an older one is superseded rather than
recovered), and an occurrence that arrives while the previous one still runs is
recorded as a skip rather than silently dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from croniter import croniter
from sqlmodel import Session, col, select

from app.core.time import ensure_utc
from app.db.models import ACTIVE_JOB_STATES, Job, ReconcileCursor, WorkPriority

from .contracts import WorkItem

CronFn = Callable[[Session], str | None]


def latest_occurrence(expression: str, *, now: datetime) -> datetime:
    """The newest instant at or before ``now`` that ``expression`` fires on."""
    now = ensure_utc(now)
    probe = croniter(expression, now + timedelta(seconds=1))
    return ensure_utc(probe.get_prev(datetime))


def next_occurrence(expression: str, *, now: datetime) -> datetime:
    return ensure_utc(croniter(expression, ensure_utc(now)).get_next(datetime))


def interval_cron(seconds: int) -> str:
    """A cron expression firing every ``seconds`` (minute granularity)."""
    minutes = max(1, seconds // 60)
    if minutes < 60:
        return f"*/{minutes} * * * *"
    hours = max(1, minutes // 60)
    if hours < 24:
        return f"0 */{hours} * * *"
    return "0 0 * * *"


@dataclass(frozen=True)
class ScheduleSource:
    """A schedule declared in code; ``cron`` returns ``None`` while disabled."""

    definition: str
    cron: CronFn
    priority: WorkPriority = WorkPriority.BACKFILL

    def _expression(self, session: Session) -> str | None:
        return self.cron(session)

    def pending(
        self, session: Session, *, now: datetime, limit: int
    ) -> Sequence[WorkItem]:
        expression = self._expression(session)
        if expression is None or limit <= 0:
            return []
        occurrence = latest_occurrence(expression, now=now)
        cursor = session.get(ReconcileCursor, self.definition)
        if (
            cursor is not None
            and cursor.last_occurrence_at is not None
            and ensure_utc(cursor.last_occurrence_at) >= occurrence
        ):
            return []
        running = session.exec(
            select(Job.id).where(
                Job.kind == self.definition, col(Job.state).in_(ACTIVE_JOB_STATES)
            )
        ).first()
        return [
            WorkItem(
                subject_key=f"{self.definition}@{occurrence.astimezone(timezone.utc).isoformat()}",
                priority=self.priority,
                occurrence_at=occurrence,
                skip_reason="previous_still_running" if running else None,
            )
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        expression = self._expression(session)
        if expression is None:
            return None
        return next_occurrence(expression, now=now)


def mark_idle(definition: str, *, seconds: float, now: datetime | None = None) -> None:
    """Park a drain-style source after a pass that could make no progress.

    A drain (fleet dispatch, similarity, migration copying) has one subject
    and reports it pending while any work exists, even work that cannot move
    yet (every printer busy). Its completion nudges its own source, so without
    this gate a no-progress drain would resubmit itself in a tight loop. The
    source stays quiet until ``seconds`` pass or genuinely new work arrives.
    """
    import json

    from app.core.time import utcnow
    from app.db.session import get_session_factory

    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        cursor = session.get(ReconcileCursor, definition) or ReconcileCursor(
            source=definition
        )
        state = json.loads(cursor.state_json or "{}")
        state["idle_since"] = now.isoformat()
        state["idle_until"] = (now + timedelta(seconds=seconds)).isoformat()
        cursor.state_json = json.dumps(state, separators=(",", ":"))
        session.add(cursor)
        session.commit()


def clear_idle(definition: str) -> None:
    import json

    from app.db.session import get_session_factory

    with get_session_factory().scoped_session() as session:
        cursor = session.get(ReconcileCursor, definition)
        if cursor is None:
            return
        state = json.loads(cursor.state_json or "{}")
        if "idle_until" not in state:
            return
        state.pop("idle_since", None)
        state.pop("idle_until", None)
        cursor.state_json = json.dumps(state, separators=(",", ":"))
        session.add(cursor)
        session.commit()


def idle_window(session: Session, definition: str) -> tuple[datetime, datetime] | None:
    """``(idle_since, idle_until)`` of a parked drain, if it is parked."""
    import json

    cursor = session.get(ReconcileCursor, definition)
    if cursor is None:
        return None
    state = json.loads(cursor.state_json or "{}")
    if "idle_until" not in state or "idle_since" not in state:
        return None
    return (
        ensure_utc(datetime.fromisoformat(state["idle_since"])),
        ensure_utc(datetime.fromisoformat(state["idle_until"])),
    )


def fixed(expression: str) -> CronFn:
    """A schedule that is always on at one expression."""

    def cron(_session: Session) -> str:
        return expression

    return cron


def when_configured(expression: str) -> CronFn:
    """A schedule that runs only once first-run setup has configured the vault."""

    def cron(session: Session) -> str | None:
        from app.modules.administration.runtime_config import is_configured

        return expression if is_configured(session) else None

    return cron


def scheduled(
    name: str,
    *,
    cron: CronFn,
    run: Callable[[], object],
    label: str,
    lane: str = "maintenance",
):
    """A code-declared periodic job whose single step calls ``run``.

    The step records what ``run`` returned (a count, a summary) as the Job's
    result, so the admin page shows what each occurrence did.
    """
    from .contracts import JobDefinition, Step

    def step(ctx) -> None:
        outcome = run()
        ctx.update(result={"outcome": outcome} if outcome is not None else None)

    return JobDefinition(
        name=name,
        lane=lane,
        steps=(Step(f"{name}.run", step),),
        source=ScheduleSource(name, cron),
        label=label,
    )


class NoPending:
    """A source for definitions whose Jobs are only ever requested."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        return []

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        return None
