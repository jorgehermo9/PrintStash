"""Backup Jobs: manual backups, automatic daily backups, and retention.

A manual backup no longer holds an HTTP request open while the vault is
archived: the route records a queued ``backup.create`` Job and returns it.
Automatic backups are a schedule declared in code, read from the backup
configuration (``automatic_backup_time_utc``); the domain's own daily claim
(``claim_due_backup``) still guarantees at most one automatic attempt per day,
so a resubmitted occurrence never archives twice.

Restore is deliberately *not* a Job: it replaces the application database
that every Job row lives in, so it runs on the API under the restore fence,
which drains every executor first, and the engine is reset afterwards.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

import app.modules.backups.backup.contracts as backup_contracts
import app.modules.backups.backup.creation as backup_creation
import app.modules.backups.backup.deletion as backup_deletion
from app.db.models import SystemConfig
from app.db.session import get_session_factory
from app.modules.backups.backup_destination import BackupTrigger
from app.modules.backups.backup_schedule import claim_due_backup, parse_backup_time
from app.modules.work.catalog import MAINTENANCE
from app.modules.work.contracts import JobContext, JobDefinition, Step
from app.modules.work.sources import ScheduleSource

CREATE_DEFINITION = "backup.create"
AUTOMATIC_DEFINITION = "backup.automatic"


def _meta(meta: backup_contracts.BackupMeta) -> dict[str, Any]:
    return {
        "run_id": meta.run_id,
        "outcome": meta.outcome,
        "destination_results": meta.destination_results,
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "archive_sha256": meta.archive_sha256,
    }


def _archive(ctx: JobContext, trigger: BackupTrigger) -> None:
    try:
        meta = backup_creation.create_backup(trigger=trigger)
    except backup_contracts.DatabaseBackupNotSupportedError as exc:
        ctx.finish("failed", error=str(exc) or "database_backup_not_supported")
        return
    except RuntimeError as exc:
        detail = str(exc)
        if detail in {"backup_destination_required", "backup_all_destinations_failed"}:
            ctx.finish(
                "failed",
                error=detail,
                result={"run_id": getattr(exc, "run_id", None)},
                retryable=detail == "backup_all_destinations_failed",
            )
            return
        raise
    backup_deletion.purge_old_backups()
    ctx.update(result=_meta(meta), processed=1, total=1, succeeded=1)


def _create(ctx: JobContext) -> None:
    _archive(ctx, BackupTrigger.MANUAL)


def _automatic(ctx: JobContext) -> None:
    with get_session_factory().scoped_session() as session:
        claimed = claim_due_backup(session)
    if not claimed:
        ctx.update(result={"skipped": "not_due"})
        return
    _archive(ctx, BackupTrigger.AUTOMATIC)


def _automatic_cron(session: Session) -> str | None:
    """The daily automatic backup as a cron expression, or ``None`` when off."""
    config = session.get(SystemConfig, 1)
    if config is None or not config.automatic_backups_enabled:
        return None
    scheduled = parse_backup_time(config.automatic_backup_time_utc)
    return f"{scheduled.minute} {scheduled.hour} * * *"


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=CREATE_DEFINITION,
            lane=MAINTENANCE,
            steps=(Step(f"{CREATE_DEFINITION}.archive", _create),),
            label="Backups",
        ),
        JobDefinition(
            name=AUTOMATIC_DEFINITION,
            lane=MAINTENANCE,
            steps=(Step(f"{AUTOMATIC_DEFINITION}.archive", _automatic),),
            source=ScheduleSource(AUTOMATIC_DEFINITION, _automatic_cron),
            retry=lambda _session, _subject: False,
            label="Automatic backups",
        ),
    ]
