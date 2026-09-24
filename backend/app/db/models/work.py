"""Background work: Jobs, reconciler cursors, fences, executors and lane overrides.

The application database records *intent*; the execution engine only executes.
A ``Job`` is the user-visible record of work on one subject, and it is the only
job state that survives a restore or an engine swap. Its input is never
serialized: every attempt rebuilds what it needs from the subject row.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, text
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel
from .types import JobState, WorkPriority

# Non-terminal states. At most one Job per (definition, subject) may be in one of
# these at a time; the partial unique index below is the claim that enforces it,
# so two reconciler passes racing on the same subject cannot both create work.
ACTIVE_JOB_STATES = ("queued", "running", "interrupted")


class Job(SQLModel, table=True):
    """One unit of background work on one subject, across its attempts.

    Staging leases, Pending Imports and upload sessions point at it through
    ``job_id``: the Job, not an engine execution, owns their staged bytes.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_active_subject",
            "kind",
            "subject_key",
            unique=True,
            sqlite_where=text("state IN ('queued', 'running', 'interrupted')"),
            postgresql_where=text("state IN ('queued', 'running', 'interrupted')"),
        ),
        Index(
            "ix_jobs_owner_state_updated",
            "owner_user_id",
            "state",
            "updated_at",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    # The job definition's registered name, e.g. ``ingest.upload``.
    kind: str = Field(max_length=64, index=True)
    subject_key: str = Field(max_length=255, index=True)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    priority: WorkPriority = Field(
        default=WorkPriority.INTERACTIVE,
        sa_column=Column(String(16), nullable=False),
    )
    state: JobState = Field(
        default=JobState.QUEUED,
        sa_column=Column(String(16), nullable=False, index=True),
    )
    # Display-safe progress, counts and result. Never replayed as input.
    status_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    # Executions submitted so far; the engine execution id is ``<id>:<attempts>``.
    attempts: int = Field(default=0)
    # Consecutive interrupted executions; bounded by ``jobs_max_resubmits``.
    resubmits: int = Field(default=0)
    app_version: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = Field(default=None, index=True)


class ReconcileCursor(SQLModel, table=True):
    """Per-definition reconciler bookkeeping: the pass claim and the dirty mark.

    Every nudge stamps ``nudged_at`` *before* deciding whether a pass needs
    enqueueing. A pass claims ``holder`` for its duration and releases it only
    while ``nudged_at`` is not newer than its own start; otherwise it runs again.
    A nudge can therefore never be lost between a pass's last read and its
    release, which engine-level deduplication alone cannot guarantee (it treats
    a finishing pass as still active and would drop the nudge).
    """

    __tablename__ = "reconcile_cursors"

    source: str = Field(primary_key=True, max_length=64)
    nudged_at: Optional[datetime] = None
    pass_queued_at: Optional[datetime] = None
    holder: Optional[str] = Field(default=None, max_length=128)
    holder_expires_at: Optional[datetime] = None
    last_pass_started_at: Optional[datetime] = None
    last_pass_finished_at: Optional[datetime] = None
    last_pass_submitted: int = Field(default=0)
    last_pass_deferred: int = Field(default=0)
    # For schedule sources: the newest occurrence already turned into a Job.
    # Kept here rather than derived from Job rows, which retention prunes.
    last_occurrence_at: Optional[datetime] = None
    # Source-owned scan bookkeeping (a derivative source's rotating window).
    state_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))


class WorkFence(SQLModel, table=True):
    """A database lease that stops new steps from starting while it is held.

    Replaces the process-local maintenance counters, which cannot protect
    anything once a worker runs in another process. A holder heartbeats; a
    crashed holder's fence expires on its own after ``expires_at``.
    """

    __tablename__ = "work_fences"

    name: str = Field(primary_key=True, max_length=64)
    holder: str = Field(max_length=128)
    reason: str = Field(max_length=64)
    acquired_at: datetime = Field(default_factory=utcnow)
    heartbeat_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)


class WorkExecutor(SQLModel, table=True):
    """A process that runs jobs, with its role and last heartbeat."""

    __tablename__ = "work_executors"

    executor_id: str = Field(primary_key=True, max_length=128)
    role: str = Field(max_length=16)
    hostname: str = Field(max_length=255)
    pid: int
    app_version: str = Field(max_length=64)
    lanes: str = Field(default="", sa_column=Column(Text, nullable=False))
    # Write-capable operations in flight at the last heartbeat. A restore
    # drains every live executor by waiting for each to report zero after the
    # restore fence was taken.
    active_mutations: int = Field(default=0)
    started_at: datetime = Field(default_factory=utcnow)
    heartbeat_at: datetime = Field(default_factory=utcnow, index=True)


class WorkLaneOverride(SQLModel, table=True):
    """An administrator's runtime concurrency for one lane.

    The application database owns it rather than the engine, whose state is
    disposable: it is reapplied to the engine on every launch.
    """

    __tablename__ = "work_lane_overrides"

    lane: str = Field(primary_key=True, max_length=32)
    concurrency: int = Field(sa_column=Column(Integer, nullable=False))
    updated_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    updated_at: datetime = Field(default_factory=utcnow)
