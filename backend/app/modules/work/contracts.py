"""The engine-agnostic vocabulary of background work.

Nothing here knows which engine runs the work. Job definitions, their steps,
lanes and sources are declared with these types by the module that owns the
work; an engine adapter (``app.runtime.engine``) maps them onto its own
primitives. A capability outside this contract is not available to
definitions, which is what keeps the engine an implementation detail.

Two guarantees are named explicitly because each has exactly one owner:

``execution_id``
    Exactly once per key. Submitting an id the engine already knows returns
    that execution instead of starting another.
``dedupe_key``
    At most one *active* execution per key. A second submission while one is
    queued, delayed or running is rejected rather than queued behind it.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.db.models.types import WorkPriority

if TYPE_CHECKING:
    from sqlmodel import Session

__all__ = [
    "ActiveExecution",
    "EngineEvidence",
    "EngineStatus",
    "JobContext",
    "JobDefinition",
    "JobEngine",
    "Lane",
    "LaneDepth",
    "RetryPolicy",
    "Step",
    "StepRunner",
    "SubmitOutcome",
    "Submission",
    "WorkItem",
    "WorkPriority",
    "WorkSource",
    "narrower_priority",
]


def narrower_priority(parent: WorkPriority, requested: WorkPriority) -> WorkPriority:
    """A child may lower its parent's priority, never raise it."""
    if parent is WorkPriority.BACKFILL:
        return WorkPriority.BACKFILL
    return requested


@dataclass(frozen=True)
class RetryPolicy:
    """How a step reacts to an exception before its Job gives up.

    ``max_attempts`` counts the first try, so 1 means "never retry". Only
    exceptions matching ``retry_on`` are retried; anything else fails the step
    immediately, because retrying a deterministic failure only repeats it.
    """

    max_attempts: int = 1
    interval_seconds: float = 1.0
    backoff_rate: float = 2.0
    retry_on: tuple[type[BaseException], ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry_max_attempts_below_one")
        if self.interval_seconds < 0 or self.backoff_rate < 1:
            raise ValueError("retry_interval_invalid")

    def should_retry(self, error: BaseException) -> bool:
        return bool(self.retry_on) and isinstance(error, self.retry_on)

    def delay_before(self, attempt: int) -> float:
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        return self.interval_seconds * self.backoff_rate ** max(attempt - 1, 0)


NO_RETRY = RetryPolicy()


@dataclass(frozen=True)
class Lane:
    """A concurrency class of work.

    ``scope`` says whether ``concurrency`` bounds each worker process or the
    whole deployment. A partitioned lane bounds each partition (one printer, one
    notification channel) separately; engines cannot deduplicate on it, so its
    definitions rely on ``execution_id`` idempotency and the active-subject
    claim instead.
    """

    name: str
    concurrency: int
    scope: Literal["worker", "global"] = "worker"
    partitioned: bool = False
    rate_limit: tuple[int, float] | None = None

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("lane_concurrency_below_one")

    @property
    def headroom(self) -> int:
        """How many queued executions a reconcile pass may leave in this lane."""
        from app.core.config import settings

        return self.concurrency * settings.jobs_lane_headroom_factor


class JobContext(Protocol):
    """What a running step may ask of the engine. Nothing else is available."""

    job_id: str
    definition: str
    subject_key: str
    priority: WorkPriority
    execution_id: str
    attempt: int

    def update(self, **fields: Any) -> None:
        """Merge display-safe progress, counts or result into the Job."""
        ...

    def finish(self, state: str, **fields: Any) -> None:
        """Record the Job's terminal outcome from inside a step."""
        ...

    def cancelled(self) -> bool: ...

    def nudge(self, source: str) -> None:
        """Ask the reconciler to run one source's pass soon."""
        ...


StepFn = Callable[[JobContext], Any]


@dataclass(frozen=True)
class Step:
    """An idempotent unit of a job. It re-reads domain state on every attempt."""

    name: str
    fn: StepFn
    retry: RetryPolicy = NO_RETRY


@dataclass(frozen=True)
class WorkItem:
    """One subject a source reports as needing work, as the source renders it."""

    subject_key: str
    priority: WorkPriority = WorkPriority.BACKFILL
    owner_user_id: int | None = None
    partition_key: str | None = None
    delay_seconds: float | None = None
    # A schedule occurrence the source consciously declines (for example
    # ``previous_still_running``) is recorded as a cancelled Job carrying this
    # reason: a skip is a verdict, not an absence.
    skip_reason: str | None = None
    # A schedule occurrence the reconciler records once its Job exists.
    occurrence_at: datetime | None = None


class WorkSource(Protocol):
    """Computes pending work for one definition from domain state.

    ``pending`` must be one bounded, indexed query. It never scans a whole
    table: a source over Artifacts uses an anti-join on its derivative rows.
    ``next_due`` lets a schedule-shaped source ask for a delayed nudge at its
    earliest next occurrence, so wall-clock work does not wait for a tick.
    """

    def pending(
        self, session: Session, *, now: datetime, limit: int
    ) -> Sequence[WorkItem]: ...

    def next_due(self, session: Session, *, now: datetime) -> datetime | None: ...


FailureHook = Callable[["Session", str, str], None]
CancelHook = Callable[["Session", str], None]
RetryHook = Callable[["Session", str], bool]


def _no_hook(_session: Session, *_args: str) -> None:
    return None


def _no_retry_hook(_session: Session, _subject_key: str) -> bool:
    return True


@dataclass(frozen=True)
class JobDefinition:
    """A named unit of background work and everything the reconciler needs.

    ``cancel`` withdraws intent from the subject so the source stops reporting
    it; a cancel that only stopped the engine would be resurrected by the next
    pass. ``on_failure`` marks the subject failed for the same reason, and
    ``retry`` returns it to pending (``False`` when the subject is gone).
    ``source`` is ``None`` for request-originated work, whose Job row is
    created by the request and is its own pending marker.
    """

    name: str
    lane: str
    steps: tuple[Step, ...]
    source: WorkSource | None = None
    cancel: CancelHook = _no_hook
    on_failure: FailureHook = _no_hook
    retry: RetryHook = _no_retry_hook
    # Sources this definition's completions should nudge (always its own).
    completion_nudges: tuple[str, ...] = ()
    # On a partitioned lane: the partition a subject belongs to (a printer id,
    # a notification channel). Required there, ignored elsewhere.
    partition: Callable[[str], str] | None = None
    # Whether each step is admitted as a write-capable operation (drained by a
    # restore, deferred while one holds the fence). Only work that itself takes
    # the restore fence (a vault migration's cutover) opts out and gates itself.
    mutating: bool = True
    label: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("job_definition_without_steps")
        if len({step.name for step in self.steps}) != len(self.steps):
            raise ValueError("job_definition_duplicate_step")


class EngineStatus(str, Enum):
    """The engine's own view of one execution, reduced to what decisions need."""

    QUEUED = "queued"
    DELAYED = "delayed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EngineEvidence:
    """What the engine knows about one execution id, or that it knows nothing."""

    status: EngineStatus | None
    app_version: str | None = None
    executor_id: str | None = None

    @property
    def absent(self) -> bool:
        return self.status is None


@dataclass(frozen=True)
class ActiveExecution:
    execution_id: str
    definition: str
    status: EngineStatus
    app_version: str | None
    executor_id: str | None


@dataclass(frozen=True)
class LaneDepth:
    queued: int
    running: int


class SubmitOutcome(str, Enum):
    ACCEPTED = "accepted"
    EXISTING = "existing"
    DEDUPLICATED = "deduplicated"


@dataclass(frozen=True)
class Submission:
    """One execution request, fully resolved by the work layer."""

    execution_id: str
    job_id: str
    definition: str
    subject_key: str
    lane: str
    priority: WorkPriority
    dedupe_key: str | None = None
    partition_key: str | None = None
    delay_seconds: float | None = None
    attempt: int = 1
    metadata: dict[str, str] = field(default_factory=dict)


class StepRunner(Protocol):
    """How an engine runs one checkpointed unit inside an execution.

    Everything the execution body decides from changing state (admission,
    cancellation) goes through ``run`` too, so a durable engine replays the
    recorded answer and the body takes the same path on recovery.
    """

    def run(self, name: str, fn: Callable[[], Any], retry: RetryPolicy) -> Any: ...

    def sleep(self, seconds: float) -> None:
        """A durable wait between steps; an engine resumes it after a restart."""
        ...

    def is_cancellation(self, error: BaseException) -> bool:
        """Whether ``error`` is the engine unwinding a cancelled execution."""
        ...


class JobEngine(abc.ABC):
    """An execution engine. Implementations: DBOS and the inline test engine.

    An engine runs two kinds of execution: a Job attempt, whose body is
    ``app.modules.work.runner.execute_job``, and a reconcile pass, whose body
    is ``app.modules.work.reconciler.execute_pass``. Both receive only strings
    and integers; neither carries domain input.
    """

    @abc.abstractmethod
    def launch(self, *, listen_lanes: Sequence[str] | None) -> None:
        """Start executing. ``listen_lanes=None`` listens to every lane."""

    @abc.abstractmethod
    def shutdown(self) -> None: ...

    @abc.abstractmethod
    def submit(self, submission: Submission) -> SubmitOutcome: ...

    @abc.abstractmethod
    def cancel(self, execution_id: str) -> None: ...

    @abc.abstractmethod
    def evidence(self, execution_ids: Sequence[str]) -> dict[str, EngineEvidence]:
        """What the engine knows about each id; unknown ids map to absent."""

    @abc.abstractmethod
    def active(self) -> list[ActiveExecution]: ...

    @abc.abstractmethod
    def lane_depth(self, lane: str) -> LaneDepth: ...

    @abc.abstractmethod
    def set_lane_concurrency(self, lane: str, concurrency: int) -> None: ...

    @abc.abstractmethod
    def foreign_version_executions(self) -> list[str]:
        """Active executions recorded under another application version."""

    @abc.abstractmethod
    def prune_history(self, *, older_than: datetime) -> int: ...

    @abc.abstractmethod
    def reset(self) -> None:
        """Discard all engine state. The application database stays authoritative."""

    @property
    @abc.abstractmethod
    def executor_id(self) -> str: ...
