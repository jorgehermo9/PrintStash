"""Public shapes of background work: Jobs, Artifact derivatives and lanes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

JobStateName = Literal[
    "queued", "running", "interrupted", "completed", "failed", "cancelled"
]
PriorityName = Literal["interactive", "backfill"]
ImportStage = Literal[
    "resolving",
    "downloading",
    "inspecting",
    "extracting",
    "hashing",
    "ingesting",
    "completed",
]
JobCompletion = Literal["complete", "partial"]
DerivativeStateName = Literal[
    "pending", "queued", "running", "ready", "skipped", "failed", "cancelled"
]


class JobFailedItem(BaseModel):
    name: str
    reason: str
    retryable: bool = False


class JobStatus(BaseModel):
    """One background Job: what it is doing, and what it did.

    ``kind`` is the job definition. Counts and ``failed_items`` are filled by
    definitions that process several items (an archive, a collection); a
    single-item job reports ``processed``/``total`` of 1. Nothing here is ever
    read back as the job's input.
    """

    job_id: str
    kind: str
    owner_user_id: Optional[int] = Field(default=None, exclude=True)
    state: JobStateName
    priority: PriorityName = "interactive"
    attempts: int = 0
    resubmits: int = 0
    model_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    retryable: bool = False
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    step: Optional[int] = None
    total_steps: Optional[int] = None
    label: Optional[str] = None
    progress: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    stage: Optional[ImportStage] = None
    current_item: Optional[str] = None
    processed: int = 0
    total: Optional[int] = None
    succeeded: int = 0
    deduplicated: int = 0
    skipped: int = 0
    failed: int = 0
    completion: Optional[JobCompletion] = None
    failed_items: list[JobFailedItem] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled"}


class JobAccepted(BaseModel):
    """Returned by every endpoint that accepts background work."""

    job_id: str
    state: JobStateName = "queued"
    message: str = "queued"


class DerivativeRead(BaseModel):
    """One derivative kind of one Artifact at the current recipe.

    ``pending`` means no attempt exists yet at the current recipe; every value
    the derivative would supply is unknown until it is ``ready``.
    """

    kind: str
    recipe_version: int
    state: DerivativeStateName
    attempts: int = 0
    failure_reason: Optional[str] = None
    updated_at: Optional[datetime] = None
    retryable: bool = False


class LaneRead(BaseModel):
    name: str
    concurrency: int
    default_concurrency: int
    overridden: bool
    scope: Literal["worker", "global"]
    partitioned: bool
    queued: int
    running: int


class DefinitionRead(BaseModel):
    name: str
    label: str
    lane: str
    queued: int
    running: int
    interrupted: int
    failed: int
    completed: int
    derivative_kinds: list[str] = Field(default_factory=list)
    next_due_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None


class ExecutorRead(BaseModel):
    executor_id: str
    role: str
    hostname: str
    app_version: str
    lanes: list[str]
    started_at: datetime
    heartbeat_at: datetime
    stale: bool


class WorkOverview(BaseModel):
    lanes: list[LaneRead]
    definitions: list[DefinitionRead]
    executors: list[ExecutorRead]
    failed_jobs: list[JobStatus]
    failed_derivatives: int


class LaneUpdate(BaseModel):
    concurrency: Optional[int] = Field(default=None, ge=1, le=64)


class DerivativeRegenerate(BaseModel):
    """``missing`` fills gaps; ``all`` re-derives every Artifact of the kind."""

    mode: Literal["missing", "all"] = "missing"


class CancelQueued(BaseModel):
    definition: str
