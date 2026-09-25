"""Storage Jobs: vault migration copying and inventory sampling.

``storage.migrate`` copies objects for a migration run that is ``copying``, as
one long step that holds the run's storage retention in its own process for
the whole copy (a retention must not outlive its holder, and a slice-per-Job
design would scatter it across workers). Its cutover stays a synchronous,
fenced administrator action, like a restore. The Job is not an automatically
admitted mutation: the migration gates itself (it takes the restore fence at
cutover and checks ``restore_in_progress`` between batches).
"""

from __future__ import annotations

import time
from datetime import datetime

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.db.models import JobKind, LaneName, VaultMigrationRun, WorkPriority
from app.db.session import get_session_factory
from app.modules.work.contracts import JobContext, JobDefinition, Step, WorkItem
from app.modules.work.sources import (
    clear_idle,
    idle_window,
    mark_idle,
    scheduled,
    when_configured,
)

logger = get_logger(__name__)

_BATCH = 4
_MAX_CONSECUTIVE_ERRORS = 10
_ERROR_IDLE_SECONDS = 60.0


def _run_id(subject: str) -> str:
    return subject.split("/", 1)[1]


class MigrationSource:
    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        parked = idle_window(session, JobKind.STORAGE_MIGRATE)
        if parked is not None and now < parked[1]:
            return []
        rows = session.exec(
            select(VaultMigrationRun.id).where(VaultMigrationRun.state == "copying")
        ).all()
        return [
            WorkItem(
                subject_key=f"vault_migration_run/{run_id}",
                priority=WorkPriority.INTERACTIVE,
            )
            for run_id in rows[:limit]
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        parked = idle_window(session, JobKind.STORAGE_MIGRATE)
        return parked[1] if parked is not None and now < parked[1] else None


def _copy(ctx: JobContext) -> None:
    from app.modules.storage.vault_migration import VaultMigrations
    from app.runtime.maintenance import restore_in_progress

    run_id = _run_id(ctx.subject_key)
    migrations = VaultMigrations(get_session_factory())
    errors = 0
    batches = 0
    while not ctx.cancelled():
        if restore_in_progress():
            time.sleep(1.0)
            continue
        with get_session_factory().scoped_session() as session:
            run = session.get(VaultMigrationRun, run_id)
            if run is None or run.state != "copying":
                break
        try:
            migrations.advance(run_id, batch_size=_BATCH)
            errors = 0
            batches += 1
        except Exception:  # noqa: BLE001 - per-object errors are persisted by the owner
            # Adapter exceptions may contain credentials: never interpolated.
            errors += 1
            logger.warning("vault migration batch failed; see its progress report")
            if errors >= _MAX_CONSECUTIVE_ERRORS:
                mark_idle(JobKind.STORAGE_MIGRATE, seconds=_ERROR_IDLE_SECONDS)
                raise RuntimeError("vault_migration_paused") from None
            time.sleep(min(2**errors, 30))
        ctx.update(processed=batches)
    clear_idle(JobKind.STORAGE_MIGRATE)


def definitions() -> list[JobDefinition]:
    from app.modules.storage.storage_inventory import refresh_inventory_sample

    return [
        JobDefinition(
            name=JobKind.STORAGE_MIGRATE,
            lane=LaneName.MAINTENANCE,
            steps=(Step(f"{JobKind.STORAGE_MIGRATE.value}.copy", _copy),),
            source=MigrationSource(),
            mutating=False,
            retry=lambda _session, _subject: True,
            label="Vault migrations",
        ),
        scheduled(
            JobKind.STORAGE_INVENTORY,
            cron=when_configured("15 * * * *"),
            run=lambda: refresh_inventory_sample(get_session_factory()),
            label="Storage inventory",
        ),
    ]
