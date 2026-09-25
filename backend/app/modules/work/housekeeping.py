"""The work layer's own scheduled upkeep: retention, executors and lane metrics."""

from __future__ import annotations

from datetime import timedelta

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import JobKind, LaneName

from . import catalog as catalog_module
from . import executors
from .contracts import JobContext, JobDefinition, Step
from .jobs import jobs
from .sources import ScheduleSource, fixed

logger = get_logger(__name__)


def _run(ctx: JobContext) -> None:
    from app.core.metrics import set_lane_depth

    now = utcnow()
    pruned = jobs.prune(now=now)
    history = catalog_module.get_engine().prune_history(
        older_than=now - timedelta(days=settings.engine_history_retention_days)
    )
    forgotten = executors.forget_stale(now=now)
    engine = catalog_module.get_engine()
    for lane in catalog_module.get_catalog().lanes:
        depth = engine.lane_depth(lane)
        set_lane_depth(lane, queued=depth.queued, running=depth.running)
    jobs.snapshot_counts()
    ctx.update(
        result={
            "jobs_pruned": pruned,
            "engine_history_pruned": history,
            "executors_forgotten": forgotten,
        }
    )


def definition() -> JobDefinition:
    return JobDefinition(
        name=JobKind.WORK_HOUSEKEEPING,
        lane=LaneName.MAINTENANCE,
        steps=(Step("work.housekeeping", _run),),
        source=ScheduleSource(JobKind.WORK_HOUSEKEEPING, fixed("*/15 * * * *")),
        label="Background work upkeep",
    )
