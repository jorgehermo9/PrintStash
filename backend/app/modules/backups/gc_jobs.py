"""The ``backups.trash_gc`` schedule: expire trash and collect unreferenced storage."""

from __future__ import annotations

from app.modules.work.sources import scheduled, when_configured

from .gc_planner import run_scheduled_gc

GC_DEFINITION = "backups.trash_gc"


def definitions():
    return [
        scheduled(
            GC_DEFINITION,
            # Hourly, only once setup configured the vault: an unconfigured
            # vault has no storage for garbage collection to reason about.
            cron=when_configured("5 * * * *"),
            run=run_scheduled_gc,
            label="Trash and storage garbage collection",
        )
    ]
