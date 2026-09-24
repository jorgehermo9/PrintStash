"""The ``Job`` repository: creation, display-safe status, listing and retention.

A Job row is the application's record of background work on one subject. The
engine never owns it, so it survives a restore with a fresh engine database, an
engine swap, and an upgrade that cancels every in-flight execution.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import delete, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import ACTIVE_JOB_STATES, Job, JobState, StagingLease, WorkPriority
from app.db.session import get_session_factory
from app.schemas.jobs import JobFailedItem, JobStatus

logger = get_logger(__name__)

TERMINAL_STATES = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})
_SECRET_QUERY = re.compile(
    r"(?i)(token|key|secret|password|cookie|signature|credential)=([^&\s]+)"
)
_ABS_PATH = re.compile(r"(?<![\w.-])(?:[A-Za-z]:[\\/]|/)[^\s:]+")
_MAX_FAILED_ITEMS = 100
_STATUS_FIELDS = frozenset(JobStatus.model_fields) - {
    "job_id",
    "kind",
    "owner_user_id",
    "state",
    "priority",
    "attempts",
    "resubmits",
    "created_at",
    "started_at",
    "finished_at",
    "updated_at",
}
_COUNT_FIELDS = ("processed", "total", "succeeded", "deduplicated", "skipped", "failed")


class ActiveJobExists(Exception):
    """A non-terminal Job already claims this (definition, subject)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


def safe_item(value: str | None) -> str | None:
    """Return a display-safe filename only; never expose server paths."""
    if not value:
        return None
    clean = value.replace("\\", "/").split("/")[-1].strip()
    clean = "".join(ch for ch in clean if ch.isprintable())
    return clean[:180] or None


def safe_error(value: str | None) -> str | None:
    """Remove paths and secret-bearing query values from user-facing errors."""
    if not value:
        return None
    clean = _SECRET_QUERY.sub(r"\1=[redacted]", value)
    clean = _ABS_PATH.sub("[path]", clean)
    clean = " ".join(clean.split())
    return clean[:500]


def _safe_result(value: Any, key: str | None = None) -> Any:
    """Sanitize user-facing error/name fields without breaking manifest paths.

    ``entries[].name`` (archive manifests) is an archive-relative path, not a
    bare filename: stripping it to a basename would desync the UI from the
    selection it later submits.
    """
    if isinstance(value, dict):
        return {k: _safe_result(v, k) for k, v in value.items()}
    if isinstance(value, list):
        if key == "entries":
            return value
        return [_safe_result(item, key) for item in value]
    if isinstance(value, str) and key in {"error", "errors", "reason"}:
        return safe_error(value)
    if isinstance(value, str) and key == "name":
        return safe_item(value)
    return value


def _state(value: str | JobState) -> JobState:
    return value if isinstance(value, JobState) else JobState(value)


def status_of(row: Job) -> JobStatus:
    payload = json.loads(row.status_json or "{}")
    return JobStatus(
        job_id=row.id,
        kind=row.kind,
        owner_user_id=row.owner_user_id,
        state=_state(row.state).value,
        priority=WorkPriority(row.priority).value,
        attempts=row.attempts,
        resubmits=row.resubmits,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
        **{k: v for k, v in payload.items() if k in _STATUS_FIELDS},
    )


def _merge(payload: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Merge reported fields into a status payload, sanitizing as it goes."""
    for key, value in fields.items():
        if value is None or key not in _STATUS_FIELDS:
            continue
        if key == "error":
            payload[key] = safe_error(str(value))
        elif key == "current_item":
            payload[key] = safe_item(str(value))
        elif key == "result":
            payload[key] = _safe_result(value)
        elif key == "progress":
            proposed = max(0.0, min(99.0, float(value)))
            payload[key] = max(float(payload.get("progress") or 0.0), proposed)
        elif key in _COUNT_FIELDS:
            payload[key] = max(0, int(value))
        elif key == "failed_items":
            payload[key] = [
                JobFailedItem(
                    name=safe_item(
                        item.name
                        if isinstance(item, JobFailedItem)
                        else str(item.get("name", "item"))
                    )
                    or "item",
                    reason=safe_error(
                        item.reason
                        if isinstance(item, JobFailedItem)
                        else str(item.get("reason", "import_failed"))
                    )
                    or "import_failed",
                    retryable=(
                        item.retryable
                        if isinstance(item, JobFailedItem)
                        else bool(item.get("retryable", False))
                    ),
                ).model_dump()
                for item in list(value)[:_MAX_FAILED_ITEMS]
            ]
        elif isinstance(value, datetime):
            payload[key] = ensure_utc(value).isoformat()
        else:
            payload[key] = value
    return payload


class JobStore:
    """Every read and write of Job rows goes through this one owner."""

    def __init__(self) -> None:
        self._listeners: list[Any] = []

    # -- change notification ------------------------------------------------

    def subscribe(self, listener: Any) -> None:
        """Register ``listener(status)``, called after every committed change."""
        self._listeners.append(listener)

    def clear_listeners(self) -> None:
        self._listeners.clear()

    def _changed(self, status: JobStatus) -> None:
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception:  # noqa: BLE001 - delivery never fails the write
                logger.exception("job change listener failed")

    # -- creation -----------------------------------------------------------

    def create(
        self,
        *,
        definition: str,
        subject_key: str,
        owner_user_id: int | None,
        priority: WorkPriority = WorkPriority.INTERACTIVE,
        session: Session | None = None,
        job_id: str | None = None,
        status: dict[str, Any] | None = None,
    ) -> str:
        """Create a queued Job, or raise ``ActiveJobExists`` for a claimed subject.

        With ``session`` the row joins the caller's transaction (the caller
        commits it together with the intent it records). Without one it commits
        on its own session.
        """
        new_id = job_id or uuid.uuid4().hex
        now = utcnow()
        row = Job(
            id=new_id,
            kind=definition[:64],
            subject_key=subject_key[:255],
            owner_user_id=owner_user_id,
            priority=priority,
            state=JobState.QUEUED,
            status_json=json.dumps(_merge({}, status or {}), separators=(",", ":")),
            app_version=settings.app_version,
            created_at=now,
            updated_at=now,
        )
        if session is not None:
            existing = self._active_id(session, definition, subject_key)
            if existing is not None:
                raise ActiveJobExists(existing)
            session.add(row)
            session.flush()
            return new_id
        with get_session_factory().scoped_session() as own:
            existing = self._active_id(own, definition, subject_key)
            if existing is not None:
                raise ActiveJobExists(existing)
            own.add(row)
            try:
                own.commit()
            except IntegrityError as exc:
                own.rollback()
                winner = self._active_id(own, definition, subject_key)
                if winner is None:
                    raise
                raise ActiveJobExists(winner) from exc
            own.refresh(row)
            status_row = status_of(row)
        self._changed(status_row)
        return new_id

    @staticmethod
    def _active_id(session: Session, definition: str, subject_key: str) -> str | None:
        return session.exec(
            select(Job.id).where(
                Job.kind == definition,
                Job.subject_key == subject_key,
                col(Job.state).in_(ACTIVE_JOB_STATES),
            )
        ).first()

    def active_for_subject(self, definition: str, subject_key: str) -> str | None:
        with get_session_factory().scoped_session() as session:
            return self._active_id(session, definition, subject_key)

    # -- status -------------------------------------------------------------

    def update(
        self, job_id: str, *, state: str | JobState | None = None, **fields: Any
    ) -> None:
        """Merge progress into a Job. A terminal Job is immutable."""
        with get_session_factory().scoped_session() as session:
            row = session.get(Job, job_id)
            if row is None or _state(row.state) in TERMINAL_STATES:
                return
            payload = _merge(json.loads(row.status_json or "{}"), fields)
            now = utcnow()
            if state is not None:
                target = JobState.QUEUED if state == "pending" else _state(state)
                if target in TERMINAL_STATES:
                    self._apply_terminal(row, payload, target, now)
                else:
                    if target is JobState.RUNNING and row.started_at is None:
                        row.started_at = now
                    row.state = target
            row.status_json = json.dumps(payload, separators=(",", ":"))
            row.updated_at = now
            session.add(row)
            session.commit()
            session.refresh(row)
            status = status_of(row)
        self._changed(status)

    def finish(self, job_id: str, *, state: str | JobState, **fields: Any) -> None:
        """The single terminal transition for every Job."""
        target = _state(state)
        if target not in TERMINAL_STATES:
            raise ValueError("terminal_state_required")
        self.update(job_id, state=target, **fields)

    @staticmethod
    def _apply_terminal(
        row: Job, payload: dict[str, Any], target: JobState, now: datetime
    ) -> None:
        if row.started_at is None:
            # Even a Job that failed before useful work keeps the only valid
            # sequence: queued -> running -> terminal.
            row.started_at = now
        row.state = target
        row.finished_at = now
        payload["progress"] = 100.0
        payload["stage"] = "completed"
        if target is JobState.COMPLETED and payload.get("completion") is None:
            payload["completion"] = (
                "partial"
                if payload.get("failed") or payload.get("skipped")
                else "complete"
            )
        if target is not JobState.COMPLETED:
            payload["completion"] = None
        from app.core.metrics import record_job_terminal

        duration = (ensure_utc(now) - ensure_utc(row.started_at)).total_seconds()
        record_job_terminal(
            row.kind, payload.get("completion") or target.value, duration
        )
        logger.info(
            "job_terminal job_id=%s kind=%s duration_s=%.3f result=%s",
            row.id,
            row.kind,
            duration,
            payload.get("completion") or target.value,
        )

    def get(self, job_id: str) -> Optional[JobStatus]:
        with get_session_factory().scoped_session() as session:
            row = session.get(Job, job_id)
            return status_of(row) if row is not None else None

    def row(self, session: Session, job_id: str) -> Job | None:
        return session.get(Job, job_id)

    # -- listing ------------------------------------------------------------

    def list_for_user(
        self,
        user_id: int,
        *,
        is_superuser: bool = False,
        terminal_limit: int = 20,
        tracked_job_ids: tuple[str, ...] = (),
        kinds: Iterable[str] | None = None,
        include_system: bool = False,
    ) -> list[JobStatus]:
        """Active Jobs, the most recent terminal ones, and any tracked by id.

        A user sees only Jobs they own. An administrator additionally sees
        system Jobs (no owner) when ``include_system`` asks for them.
        """
        terminal_limit = max(0, min(100, terminal_limit))
        with get_session_factory().scoped_session() as session:
            scope: list[Any] = []
            if is_superuser and include_system:
                pass
            elif is_superuser:
                scope.append(col(Job.owner_user_id).is_not(None))
            else:
                scope.append(Job.owner_user_id == user_id)
            if kinds is not None:
                scope.append(col(Job.kind).in_(tuple(kinds)))
            active = session.exec(
                select(Job)
                .where(*scope, col(Job.state).in_(ACTIVE_JOB_STATES))
                .order_by(col(Job.updated_at).desc())
                .limit(1000)
            ).all()
            terminal = session.exec(
                select(Job)
                .where(*scope, col(Job.state).in_([s.value for s in TERMINAL_STATES]))
                .order_by(col(Job.updated_at).desc())
                .limit(terminal_limit)
            ).all()
            tracked = (
                session.exec(
                    select(Job).where(*scope, col(Job.id).in_(tracked_job_ids))
                ).all()
                if tracked_job_ids
                else []
            )
            rows = {row.id: row for row in [*active, *terminal, *tracked]}
            ordered = sorted(rows.values(), key=lambda r: r.updated_at, reverse=True)
            return [status_of(row) for row in ordered]

    def failed(self, *, limit: int = 50) -> list[JobStatus]:
        with get_session_factory().scoped_session() as session:
            rows = session.exec(
                select(Job)
                .where(Job.state == JobState.FAILED)
                .order_by(col(Job.updated_at).desc())
                .limit(limit)
            ).all()
            return [status_of(row) for row in rows]

    def counts_by_definition(self) -> dict[str, dict[str, int]]:
        with get_session_factory().scoped_session() as session:
            rows = session.exec(
                select(Job.kind, Job.state, func.count(col(Job.id))).group_by(
                    Job.kind, Job.state
                )
            ).all()
        counts: dict[str, dict[str, int]] = {}
        for kind, state, count in rows:
            counts.setdefault(str(kind), {})[_state(state).value] = int(count)
        return counts

    def snapshot_counts(self) -> dict[str, int]:
        """Count Jobs by state, plus a total and the number that look stuck."""
        counts = {state.value: 0 for state in JobState}
        with get_session_factory().scoped_session() as session:
            rows = session.exec(
                select(Job.state, func.count(col(Job.id))).group_by(Job.state)
            ).all()
            for state, count in rows:
                counts[_state(state).value] = int(count)
            stuck = session.exec(
                select(func.count(col(Job.id))).where(
                    col(Job.state).in_(ACTIVE_JOB_STATES),
                    Job.updated_at
                    < utcnow()
                    - timedelta(seconds=settings.jobs_reconcile_interval_seconds * 3),
                )
            ).one()
        from app.core.metrics import set_stuck_jobs

        set_stuck_jobs(int(stuck))
        counts["total"] = sum(counts[state.value] for state in JobState)
        return counts

    # -- retention ----------------------------------------------------------

    def prune(self, *, now: datetime | None = None) -> int:
        """Drop terminal Jobs past retention, never one a staging lease still owns.

        User Jobs are kept ``jobs_retention_days`` and capped per user; system
        Jobs (no owner) are high-volume backfill records and keep a day.
        """
        now = now or utcnow()
        user_cutoff = now - timedelta(days=settings.jobs_retention_days)
        system_cutoff = now - timedelta(hours=settings.jobs_system_retention_hours)
        terminal = [s.value for s in TERMINAL_STATES]
        unleased = ~col(Job.id).in_(
            select(StagingLease.job_id).where(col(StagingLease.job_id).is_not(None))
        )
        with get_session_factory().scoped_session() as session:
            removed = affected(
                session,
                delete(Job).where(
                    col(Job.state).in_(terminal),
                    unleased,
                    or_(
                        (col(Job.owner_user_id).is_(None))
                        & (col(Job.finished_at) < system_cutoff),
                        (col(Job.owner_user_id).is_not(None))
                        & (col(Job.finished_at) < user_cutoff),
                    ),
                ),
            )
            owners = session.exec(
                select(Job.owner_user_id)
                .where(col(Job.owner_user_id).is_not(None))
                .group_by(col(Job.owner_user_id))
                .having(func.count(col(Job.id)) > settings.jobs_retention_per_user)
            ).all()
            for owner in owners:
                keep = (
                    select(Job.id)
                    .where(col(Job.owner_user_id) == owner)
                    .order_by(col(Job.updated_at).desc())
                    .limit(settings.jobs_retention_per_user)
                )
                removed += affected(
                    session,
                    delete(Job).where(
                        col(Job.owner_user_id) == owner,
                        col(Job.state).in_(terminal),
                        unleased,
                        ~col(Job.id).in_(keep),
                    ),
                )
            session.commit()
        return removed


jobs = JobStore()
