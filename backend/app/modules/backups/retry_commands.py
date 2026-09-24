"""Retry a failed backup destination using its persisted execution state.

A retry is a ``backups.retry_destination`` Job whose subject is the
destination result, so one retry of a destination runs at a time anywhere.
Requesting it records the attempt (the intent) with its Job and returns; the
Job publishes the exact archive from verified surviving bytes, never a
rebuild. Whatever ends the Job (success, a refusal, a crash, a cancel) settles
the attempt, the destination and the run.
"""

from __future__ import annotations

from contextlib import ExitStack

from sqlmodel import Session, col, select

import app.runtime.maintenance as backup_maintenance
from app.core.time import utcnow
from app.db.models import (
    BackupDestinationResult,
    BackupRetryAttempt,
    BackupRun,
)
from app.db.session import get_session_factory

from .backup_runs import finish_run, update_result

RETRY_DEFINITION = "backups.retry_destination"
_OPEN = ("queued", "running")


def subject_key(result_id: str) -> str:
    return f"backup_result/{result_id}"


def result_id_of(subject: str) -> str:
    return subject.split("/", 1)[1]


def request_retry(
    session: Session, result_id: str, *, owner_user_id: int | None
) -> str:
    """Record a retry of one failed destination with its queued Job.

    The attempt's id is its Job's id. The caller commits both together, then
    nudges. A destination already being retried is refused.
    """
    import uuid

    from app.modules.backups.backup_replica_retry import RetryRefused
    from app.modules.work import service as work_service
    from app.modules.work.jobs import ActiveJobExists

    result = session.get(BackupDestinationResult, result_id)
    if result is None:
        raise LookupError("backup_destination_result_not_found")
    run = session.get(BackupRun, result.run_id)
    if run is None or result.outcome != "failed":
        raise RetryRefused("backup_retry_not_failed")
    if run.outcome == "running":
        # The backup's own Job still publishes its other destinations and
        # settles the run; a retry ending first would settle it under that Job.
        raise RetryRefused("backup_retry_backup_running")
    attempt_id = uuid.uuid4().hex
    try:
        work_service.request(
            session,
            definition=RETRY_DEFINITION,
            subject_key=subject_key(result_id),
            owner_user_id=owner_user_id,
            job_id=attempt_id,
        )
    except ActiveJobExists as exc:
        raise RetryRefused("backup_retry_in_progress") from exc
    session.add(
        BackupRetryAttempt(
            id=attempt_id,
            destination_result_id=result_id,
            archive_sha256=run.archive_sha256,
            outcome="queued",
        )
    )
    return attempt_id


def _settle(attempt_id: str, outcome: str, error_code: str | None) -> None:
    with get_session_factory().scoped_session() as session:
        attempt = session.get(BackupRetryAttempt, attempt_id)
        assert attempt is not None
        attempt.outcome, attempt.error_code, attempt.finished_at = (
            outcome,
            error_code,
            utcnow(),
        )
        session.add(attempt)
        session.commit()


def run_retry(attempt_id: str) -> dict:
    """Publish one queued retry; returns the destination as it now stands."""
    from app.modules.backups.backup_replica_retry import (
        RetryRefused,
        publish_retry,
        reconcile_result,
        verified_survivor,
    )

    with backup_maintenance.backup_operation_lock:
        with get_session_factory().scoped_session() as session:
            attempt = session.get(BackupRetryAttempt, attempt_id)
            if attempt is None or attempt.outcome not in _OPEN:
                raise LookupError("backup_retry_attempt_not_open")
            result = session.get(BackupDestinationResult, attempt.destination_result_id)
            assert result is not None
            run = session.get(BackupRun, result.run_id)
            assert run is not None
            if result.outcome == "completed":
                # An earlier attempt of this Job published before it was lost.
                pass
            else:
                # "publishing" can only be this Job's own lost attempt: the
                # engine runs one retry of a destination at a time.
                result.outcome, result.updated_at = "publishing", utcnow()
                session.add(result)
            attempt.outcome = "running"
            session.add(attempt)
            session.commit()
            session.refresh(run)
            session.refresh(result)
            run = BackupRun.model_validate(run.model_dump())
            result = BackupDestinationResult.model_validate(result.model_dump())
            survivors = [
                BackupDestinationResult.model_validate(row.model_dump())
                for row in session.exec(
                    select(BackupDestinationResult).where(
                        BackupDestinationResult.run_id == run.id,
                        BackupDestinationResult.outcome == "completed",
                        BackupDestinationResult.id != result.id,
                    )
                ).all()
            ]
        if result.outcome != "completed":
            try:
                published = (
                    reconcile_result(result, run)
                    if result.target_identity_json
                    else False
                )
                if published:
                    _record_source(attempt_id, result.id)
                for survivor in [] if published else survivors:
                    with ExitStack() as resources:
                        try:
                            path = resources.enter_context(
                                verified_survivor(survivor, run)
                            )
                        except Exception:
                            continue
                        _record_source(attempt_id, survivor.id)
                        publish_retry(result, run, path)
                        published = True
                        break
                if not published:
                    raise RetryRefused("backup_retry_new_backup_required")
            except BaseException as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, RetryRefused)
                    else "backup_retry_publication_failed"
                )
                update_result(result.id, outcome="failed", error_code=reason)
                _settle(attempt_id, "failed", reason)
                finish_run(run.id)
                if isinstance(exc, Exception):
                    raise RetryRefused(reason) from exc
                raise
        _settle(attempt_id, "completed", None)
        finish_run(run.id)
        return next(row for row in _destinations(run.id) if row["id"] == result.id)


def _record_source(attempt_id: str, source_result_id: str) -> None:
    with get_session_factory().scoped_session() as session:
        attempt = session.get(BackupRetryAttempt, attempt_id)
        assert attempt is not None
        attempt.source_result_id = source_result_id
        session.add(attempt)
        session.commit()


def _destinations(run_id: str) -> list[dict]:
    from .backup_runs import run_detail

    return run_detail(run_id)["destinations"]


def settle_open_retries(session: Session, result_id: str, reason: str) -> None:
    """End a destination's open retry: its Job failed or was cancelled.

    A destination left publishing by it is failed with ``reason``; one an
    attempt did publish stays completed. The run is re-summarised. Runs in
    the caller's transaction, which commits it.
    """
    from .backup_runs import summarise_run

    attempts = session.exec(
        select(BackupRetryAttempt).where(
            BackupRetryAttempt.destination_result_id == result_id,
            col(BackupRetryAttempt.outcome).in_(_OPEN),
        )
    ).all()
    result = session.get(BackupDestinationResult, result_id)
    published = result is not None and result.outcome == "completed"
    for attempt in attempts:
        attempt.outcome = "completed" if published else "failed"
        attempt.error_code = None if published else reason
        attempt.finished_at = utcnow()
        session.add(attempt)
    if result is not None and result.outcome == "publishing":
        result.outcome, result.error_code = "failed", reason
        result.updated_at = utcnow()
        session.add(result)
    if result is not None:
        session.flush()
        summarise_run(session, result.run_id)
