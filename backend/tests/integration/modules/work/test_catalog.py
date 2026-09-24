"""The catalog of lanes and definitions one process runs, and its binding.

A catalog refuses definitions that could not run: a duplicate name, a lane it
does not have, or the reserved reconcile name. Runtime lane overrides replace
the configured concurrency and are reapplied on every launch, because the
engine's copy is disposable.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import WorkLaneOverride
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import (
    INGEST,
    RECONCILE,
    RECONCILE_DEFINITION,
    WorkCatalog,
    default_lanes,
)
from app.modules.work.contracts import JobDefinition, Step


def _definition(name: str = "probe.one", lane: str = INGEST) -> JobDefinition:
    return JobDefinition(
        name=name, lane=lane, steps=(Step(f"{name}.run", lambda _: None),)
    )


class TestDefaultLanes:
    def test_reconciles_on_its_own_global_lane(self) -> None:
        lane = default_lanes()[RECONCILE]

        assert lane.scope == "global"

    def test_notifications_are_partitioned_and_rate_limited(self) -> None:
        lane = default_lanes()["notify"]

        assert lane.partitioned is True
        assert lane.rate_limit is not None

    def test_native_derivation_follows_the_render_budget(self) -> None:
        _overlay["jobs_derive_native_concurrency"] = 0
        _overlay["max_render_jobs"] = 3

        assert default_lanes()["derive.native"].concurrency == 3

    def test_an_explicit_native_concurrency_wins(self) -> None:
        _overlay["jobs_derive_native_concurrency"] = 5

        assert default_lanes()["derive.native"].concurrency == 5


class TestWorkCatalog:
    def test_looks_a_definition_up_by_name(self) -> None:
        catalog = WorkCatalog([_definition()])

        assert catalog.definition("probe.one").lane == INGEST
        assert catalog.lane_for("probe.one").name == INGEST

    def test_an_unknown_definition_is_a_lookup_error(self) -> None:
        with pytest.raises(LookupError, match="unknown_job_definition"):
            WorkCatalog().definition("missing")

    def test_refuses_a_duplicate_definition(self) -> None:
        with pytest.raises(ValueError, match="duplicate_job_definition"):
            WorkCatalog([_definition(), _definition()])

    def test_refuses_a_definition_on_an_unknown_lane(self) -> None:
        with pytest.raises(ValueError, match="unknown_lane"):
            WorkCatalog([_definition(lane="nowhere")])

    def test_refuses_the_reserved_reconcile_name(self) -> None:
        with pytest.raises(ValueError, match="reserved_job_definition"):
            WorkCatalog([_definition(name=RECONCILE_DEFINITION)])

    def test_applies_runtime_overrides_over_configured_concurrency(
        self, db_session: Session
    ) -> None:
        db_session.add(WorkLaneOverride(lane=INGEST, concurrency=9))
        db_session.commit()
        catalog = WorkCatalog()

        catalog.apply_overrides(db_session)

        assert catalog.lanes[INGEST].concurrency == 9

    def test_a_removed_override_restores_the_configured_value(
        self, db_session: Session
    ) -> None:
        catalog = WorkCatalog()
        db_session.add(WorkLaneOverride(lane=INGEST, concurrency=9))
        db_session.commit()
        catalog.apply_overrides(db_session)
        db_session.delete(db_session.get(WorkLaneOverride, INGEST))
        db_session.commit()

        catalog.apply_overrides(db_session)

        assert catalog.lanes[INGEST].concurrency == default_lanes()[INGEST].concurrency


class TestBinding:
    def test_an_unbound_process_has_no_catalog(self) -> None:
        catalog_module.bind(None, None)

        assert catalog_module.bound() is False
        with pytest.raises(RuntimeError, match="work_catalog_unbound"):
            catalog_module.get_catalog()
        with pytest.raises(RuntimeError, match="job_engine_unbound"):
            catalog_module.get_engine()

    def test_binding_makes_both_available(self, work_engine, work_catalog) -> None:
        catalog_module.bind(work_engine, work_catalog)

        assert catalog_module.bound() is True
        assert catalog_module.get_engine() is work_engine
