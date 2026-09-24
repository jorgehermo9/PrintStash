"""The catalog of lanes and job definitions, and the process-wide binding.

Definitions live with the module that owns their work (``<module>/jobs.py``)
and are collected here by composition at startup. Nothing registers itself at
import time: the catalog a process runs is exactly the one bootstrap built.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import WorkLaneOverride

from .contracts import JobDefinition, JobEngine, Lane

INGEST = "ingest"
DERIVE_NATIVE = "derive.native"
DERIVE_LIGHT = "derive.light"
SIMILARITY = "similarity"
NETWORK = "network"
NOTIFY = "notify"
PRINTING = "printing"
MAINTENANCE = "maintenance"
RECONCILE = "reconcile"

# The reconciler's own executions. Not a Job: its bookkeeping is the cursor.
RECONCILE_DEFINITION = "work.reconcile"


def _native_default() -> int:
    """One native process per budget share, never fewer than one."""
    configured = settings.jobs_derive_native_concurrency
    if configured:
        return configured
    return max(1, int(settings.max_render_jobs) or 1)


def default_lanes() -> dict[str, Lane]:
    """Every lane with its configured (environment) concurrency."""
    return {
        lane.name: lane
        for lane in (
            Lane(INGEST, settings.jobs_ingest_concurrency),
            Lane(DERIVE_NATIVE, _native_default()),
            Lane(DERIVE_LIGHT, settings.jobs_derive_light_concurrency),
            Lane(SIMILARITY, settings.jobs_similarity_concurrency, scope="global"),
            Lane(NETWORK, settings.jobs_network_concurrency),
            Lane(
                NOTIFY,
                settings.jobs_notify_concurrency,
                partitioned=True,
                rate_limit=(settings.jobs_notify_rate_per_minute, 60.0),
            ),
            # Fleet routing is one fleet-wide decision: one dispatcher, anywhere.
            Lane(PRINTING, settings.jobs_printing_concurrency, scope="global"),
            Lane(MAINTENANCE, settings.jobs_maintenance_concurrency, scope="global"),
            Lane(RECONCILE, 4, scope="global"),
        )
    }


class WorkCatalog:
    """Lanes and definitions one process knows how to run."""

    def __init__(
        self,
        definitions: Iterable[JobDefinition] = (),
        *,
        lanes: dict[str, Lane] | None = None,
    ) -> None:
        self.lanes: dict[str, Lane] = dict(lanes or default_lanes())
        self.definitions: dict[str, JobDefinition] = {}
        for definition in definitions:
            self.add(definition)

    def add(self, definition: JobDefinition) -> None:
        if definition.name == RECONCILE_DEFINITION:
            raise ValueError("reserved_job_definition")
        if definition.name in self.definitions:
            raise ValueError(f"duplicate_job_definition:{definition.name}")
        if definition.lane not in self.lanes:
            raise ValueError(f"unknown_lane:{definition.lane}")
        self.definitions[definition.name] = definition

    def definition(self, name: str) -> JobDefinition:
        try:
            return self.definitions[name]
        except KeyError as exc:
            raise LookupError(f"unknown_job_definition:{name}") from exc

    def lane_for(self, definition: str) -> Lane:
        return self.lanes[self.definition(definition).lane]

    def apply_overrides(self, session: Session) -> None:
        """Replace configured concurrency with administrators' runtime values."""
        defaults = default_lanes()
        overrides = {
            row.lane: row.concurrency
            for row in session.exec(select(WorkLaneOverride)).all()
        }
        for name, lane in list(self.lanes.items()):
            base = defaults.get(name, lane)
            self.lanes[name] = replace(
                lane, concurrency=overrides.get(name, base.concurrency)
            )


_catalog: WorkCatalog | None = None
_engine: JobEngine | None = None


def bind(engine: JobEngine | None, catalog: WorkCatalog | None) -> None:
    """Bind this process's engine and catalog; ``None`` unbinds."""
    global _catalog, _engine
    _engine = engine
    _catalog = catalog


def get_catalog() -> WorkCatalog:
    if _catalog is None:
        raise RuntimeError("work_catalog_unbound")
    return _catalog


def get_engine() -> JobEngine:
    if _engine is None:
        raise RuntimeError("job_engine_unbound")
    return _engine


def bound() -> bool:
    return _engine is not None and _catalog is not None
