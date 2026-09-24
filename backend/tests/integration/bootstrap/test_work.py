"""Composing background work for one process: catalog, engine, lifecycle.

Every role builds the same catalog and binds one engine; what differs is
which lanes a process executes. A split topology (a worker, or an API that
does not run jobs) is refused unless every process can reach the same
database and the same storage, because otherwise work would run against a
vault only one process can see.

Startup is where recovery happens: executions another application version
left behind are cancelled, queued-pass marks a dead process left are
forgotten, and every definition is reconciled once, so whatever the database
says is owed is resubmitted without waiting for the first tick.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from sqlmodel import Session, select

import app.bootstrap.work as work_bootstrap
from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import ReconcileCursor, WorkExecutor, WorkLaneOverride, WorkPriority
from app.modules.work import catalog as catalog_module
from app.modules.work import executors, fences
from app.modules.work.catalog import INGEST, RECONCILE_DEFINITION, WorkCatalog
from app.modules.work.contracts import EngineStatus, Submission
from app.modules.work.jobs import jobs

POSTGRES = "postgresql://printstash:secret@db/printstash"
# Every test binds the inline engine through this seam; these tests need the
# real one, captured before any fixture replaced it.
_BUILD_ENGINE = work_bootstrap.build_engine


def _passes(engine) -> set[str]:
    return {
        execution.submission.subject_key
        for execution in engine.executions.values()
        if execution.submission.definition == RECONCILE_DEFINITION
    }


class TestDefinitions:
    def test_collects_every_owners_definitions_once(self) -> None:
        names = [definition.name for definition in work_bootstrap.definitions()]

        assert len(names) == len(set(names))
        assert {
            "work.housekeeping",
            "ingest.upload",
            "derive.mesh",
            "notify.deliver",
            "audit.run",
            "backup.create",
            "printing.dispatch",
        } <= set(names)

    def test_includes_similarity_when_its_package_is_installed(self) -> None:
        names = {definition.name for definition in work_bootstrap.definitions()}

        assert "similarity.analyze" in names

    def test_leaves_similarity_out_when_its_package_is_absent(
        self, monkeypatch
    ) -> None:
        import app.bootstrap.optional_features as optional

        monkeypatch.setattr(optional, "similarity_available", lambda: False)

        names = {definition.name for definition in work_bootstrap.definitions()}

        assert not any(name.startswith("similarity.") for name in names)


class TestBuildCatalog:
    def test_applies_an_administrators_lane_override(self, db_session: Session) -> None:
        db_session.add(WorkLaneOverride(lane=INGEST, concurrency=7))
        db_session.commit()

        catalog = work_bootstrap.build_catalog()

        assert catalog.lanes[INGEST].concurrency == 7

    def test_unreadable_overrides_fall_back_to_configured_concurrency(
        self, monkeypatch
    ) -> None:
        def unreadable(self, _session):
            raise RuntimeError("override table missing")

        monkeypatch.setattr(WorkCatalog, "apply_overrides", unreadable)

        catalog = work_bootstrap.build_catalog()

        assert catalog.lanes[INGEST].concurrency == settings.jobs_ingest_concurrency


class TestListenLanes:
    @pytest.mark.parametrize(
        ("role", "runs_jobs", "expected"),
        [
            ("all", True, None),
            ("api", True, None),
            ("api", False, []),
            ("worker", True, None),
        ],
    )
    def test_listens_by_role(
        self, work_catalog, role: str, runs_jobs: bool, expected
    ) -> None:
        _overlay["process_role"] = role
        _overlay["api_runs_jobs"] = runs_jobs

        assert work_bootstrap.listen_lanes(work_catalog) == expected


class TestValidateTopology:
    @pytest.mark.parametrize(
        ("role", "runs_jobs"), [("all", True), ("api", True)], ids=["all", "api"]
    )
    def test_a_single_process_role_runs_on_sqlite(
        self, role: str, runs_jobs: bool
    ) -> None:
        _overlay["process_role"] = role
        _overlay["api_runs_jobs"] = runs_jobs
        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        work_bootstrap.validate_topology()

    @pytest.mark.parametrize(
        ("role", "runs_jobs"),
        [("worker", True), ("api", False)],
        ids=["worker", "api-without-jobs"],
    )
    def test_a_split_topology_refuses_sqlite(self, role: str, runs_jobs: bool) -> None:
        _overlay["process_role"] = role
        _overlay["api_runs_jobs"] = runs_jobs
        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            work_bootstrap.validate_topology()

    def test_a_split_topology_refuses_an_undeclared_local_volume(self) -> None:
        _overlay["process_role"] = "worker"
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "local"
        _overlay["shared_storage"] = False

        with pytest.raises(RuntimeError, match="VAULT_SHARED_STORAGE"):
            work_bootstrap.validate_topology()

    def test_a_split_topology_accepts_a_declared_shared_volume(self) -> None:
        _overlay["process_role"] = "worker"
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "local"
        _overlay["shared_storage"] = True

        work_bootstrap.validate_topology()

    def test_a_split_topology_accepts_object_storage(self) -> None:
        _overlay["process_role"] = "worker"
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "s3"

        work_bootstrap.validate_topology()


class TestBuildEngine:
    def test_keeps_engine_state_beside_a_sqlite_vault(self, work_catalog) -> None:
        from app.runtime.engine.dbos_engine import DbosJobEngine

        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        engine = _BUILD_ENGINE(work_catalog)

        assert isinstance(engine, DbosJobEngine)
        assert engine.url == "sqlite:////data/db/printstash-dbos.sqlite"
        assert engine.schema is None
        assert engine.executor_id == executors.executor_id()

    def test_keeps_engine_state_in_its_own_postgres_schema(self, work_catalog) -> None:
        _overlay["db_url"] = POSTGRES

        engine = _BUILD_ENGINE(work_catalog)

        assert engine.schema == "dbos"  # type: ignore[attr-defined]
        assert engine.url.startswith("postgresql+psycopg://")  # type: ignore[attr-defined]


@pytest.fixture
def started(work_engine, work_catalog):
    runtime = work_bootstrap.start(engine=work_engine, catalog=work_catalog)
    try:
        yield runtime
    finally:
        work_bootstrap.stop()


class TestStart:
    def test_makes_this_process_a_registered_executor(
        self, started, work_engine, db_session: Session
    ) -> None:
        assert work_bootstrap.current() is started
        assert catalog_module.get_engine() is work_engine
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert row.role == settings.process_role
        assert set(row.lanes.split(",")) == set(started.catalog.lanes)

    def test_reconciles_every_definition_once(self, started, work_engine) -> None:
        assert _passes(work_engine) == set(started.catalog.definitions)

    def test_startup_passes_are_backfill(self, started, work_engine) -> None:
        # So an upload arriving during startup is not queued behind them.
        priorities = {
            execution.submission.priority
            for execution in work_engine.executions.values()
            if execution.submission.definition == RECONCILE_DEFINITION
        }

        assert priorities == {WorkPriority.BACKFILL}

    def test_a_pass_a_dead_process_left_queued_does_not_suppress_startup(
        self, work_engine, work_catalog, db_session: Session
    ) -> None:
        # The mark says "a pass is already waiting", but the process that
        # queued it died, so that pass never runs.
        db_session.add(ReconcileCursor(source="library.scan", pass_queued_at=utcnow()))
        db_session.commit()

        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            assert "library.scan" in _passes(work_engine)
        finally:
            work_bootstrap.stop()

    def test_cancels_what_another_application_version_left_running(
        self, work_engine, work_catalog
    ) -> None:
        work_engine.app_version = "0.0.1-previous"
        work_engine.submit(
            Submission(
                execution_id="old-job:1",
                job_id="old-job",
                definition="ingest.upload",
                subject_key="ingest_request/old-job",
                lane=INGEST,
                priority=WorkPriority.INTERACTIVE,
            )
        )
        work_engine.app_version = settings.app_version

        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            evidence = work_engine.evidence(["old-job:1"])["old-job:1"]
        finally:
            work_bootstrap.stop()

        assert evidence.status is EngineStatus.CANCELLED

    def test_publishes_job_changes_to_the_given_publisher(
        self, work_engine, work_catalog
    ) -> None:
        notices: list[tuple[str, dict]] = []

        class Publisher:
            def publish_threadsafe(self, channel, payload):
                notices.append((channel, payload))

        work_bootstrap.start(
            engine=work_engine, catalog=work_catalog, publisher=Publisher()
        )
        try:
            job_id = jobs.create(
                definition="work.housekeeping", subject_key="test/1", owner_user_id=None
            )
        finally:
            work_bootstrap.stop()

        assert ("work:admin", {"type": "job", "job_id": job_id}) == (
            notices[0][0],
            {key: notices[0][1][key] for key in ("type", "job_id")},
        )

    def test_refuses_a_topology_before_binding_anything(
        self, work_engine, work_catalog
    ) -> None:
        catalog_module.bind(None, None)
        _overlay["process_role"] = "worker"
        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        assert not catalog_module.bound()
        assert work_bootstrap.current() is None

    def test_heartbeats_until_stopped(
        self, work_engine, work_catalog, db_session: Session
    ) -> None:
        _overlay["fence_heartbeat_seconds"] = 1
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            first = db_session.get(WorkExecutor, executors.executor_id())
            assert first is not None
            registered = first.heartbeat_at
            deadline = time.monotonic() + 5
            while True:
                db_session.expire_all()
                row = db_session.get(WorkExecutor, executors.executor_id())
                if row is not None and row.heartbeat_at > registered:
                    break
                assert time.monotonic() < deadline, "no heartbeat"
                time.sleep(0.1)
        finally:
            work_bootstrap.stop()


class TestStop:
    def test_releases_the_fences_this_executor_held(
        self, work_engine, work_catalog
    ) -> None:
        # Another process must not wait out a TTL for a fence nobody holds.
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        fences.acquire("backup", holder=executors.executor_id(), reason="test")

        work_bootstrap.stop()

        assert fences.held_by(executors.executor_id()) == []

    def test_forgets_this_executor(
        self, work_engine, work_catalog, db_session: Session
    ) -> None:
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()

        db_session.expire_all()
        assert db_session.get(WorkExecutor, executors.executor_id()) is None

    def test_unbinds_the_engine(self, work_engine, work_catalog) -> None:
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()

        assert not catalog_module.bound()
        assert work_engine.launched is False

    def test_stopping_twice_is_harmless(self, work_engine, work_catalog) -> None:
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()
        work_bootstrap.stop()

        assert work_bootstrap.current() is None

    def test_stops_the_heartbeat(self, work_engine, work_catalog) -> None:
        runtime = work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()

        assert runtime.heartbeat is not None
        runtime.heartbeat.join(timeout=5)
        assert not runtime.heartbeat.is_alive()


class TestAfterRestore:
    def test_rebuilds_engine_state_from_the_restored_vault(
        self, started, work_engine, db_session: Session
    ) -> None:
        work_engine.drain()
        before = set(work_engine.executions)

        work_bootstrap.after_restore()

        # The restored database is authoritative; what it owes is resubmitted.
        assert not before & set(work_engine.executions)
        assert _passes(work_engine) == set(started.catalog.definitions)
        assert db_session.get(WorkExecutor, executors.executor_id()) is not None

    def test_does_nothing_without_running_work(self) -> None:
        assert work_bootstrap.current() is None

        work_bootstrap.after_restore()

        assert work_bootstrap.current() is None


class TestStaleCursorMarks:
    def test_forgetting_queued_passes_clears_only_set_marks(
        self, db_session: Session
    ) -> None:
        from app.modules.work.submission import forget_queued_passes

        db_session.add(
            ReconcileCursor(source="a", pass_queued_at=utcnow() - timedelta(seconds=5))
        )
        db_session.add(ReconcileCursor(source="b"))
        db_session.commit()

        assert forget_queued_passes() == 1

        db_session.expire_all()
        assert all(
            row.pass_queued_at is None
            for row in db_session.exec(select(ReconcileCursor)).all()
        )
