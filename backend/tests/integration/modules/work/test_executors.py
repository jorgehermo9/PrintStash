"""Which processes run work, and whether each is still alive.

A dead executor is recognised by its heartbeat going stale; that is what lets
the reconciler rerun work stranded on it. The heartbeat also carries the
process's in-flight write count, which is how a restore drains work running in
other processes.
"""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session

from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import WorkExecutor
from app.modules.work import executors


class TestExecutorId:
    def test_is_stable_within_a_process(self) -> None:
        assert executors.executor_id() == executors.executor_id()

    def test_names_this_processs_role(self) -> None:
        assert executors.executor_id().startswith(f"{settings.process_role}-")

    def test_an_operator_can_pin_it(self) -> None:
        # A worker container that restarts keeps its id.
        _overlay["executor_id"] = "worker-pinned"
        executors.reset_executor_id()

        assert executors.executor_id() == "worker-pinned"


class TestRegister:
    def test_records_this_process(self, db_session: Session) -> None:
        executors.register(role="worker", lanes=["ingest", "network"])

        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert (row.role, row.lanes, row.active_mutations) == (
            "worker",
            "ingest,network",
            0,
        )
        assert row.app_version == settings.app_version

    def test_registering_again_refreshes_the_row(self, db_session: Session) -> None:
        executors.register(role="worker", lanes=["ingest"])
        later = utcnow() + timedelta(minutes=1)

        executors.register(role="api", lanes=[], now=later)

        db_session.expire_all()
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert (row.role, row.lanes) == ("api", "")


class TestHeartbeat:
    def test_reports_liveness_and_in_flight_writes(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])
        later = utcnow() + timedelta(seconds=30)

        executors.heartbeat(active_mutations=3, now=later)

        db_session.expire_all()
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert row.active_mutations == 3
        assert row.heartbeat_at.replace(tzinfo=None) == later.replace(tzinfo=None)

    def test_never_reports_a_negative_count(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])

        executors.heartbeat(active_mutations=-2)

        db_session.expire_all()
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None and row.active_mutations == 0

    def test_a_forgotten_executor_is_not_recreated(self, db_session: Session) -> None:
        executors.heartbeat(active_mutations=1)

        assert db_session.get(WorkExecutor, executors.executor_id()) is None


class TestLiveness:
    def test_a_silent_executor_is_stale(self, make_work_executor) -> None:
        dead = make_work_executor("dead", stale=True)
        alive = make_work_executor("alive")

        assert executors.stale_ids() == {dead.executor_id}
        assert [row.executor_id for row in executors.live()] == [alive.executor_id]

    def test_forgets_executors_silent_for_ten_stale_windows(
        self, make_work_executor, db_session: Session
    ) -> None:
        long_gone = make_work_executor("long-gone", stale=True)
        long_gone.heartbeat_at = utcnow() - timedelta(
            seconds=settings.jobs_executor_stale_seconds * 11
        )
        db_session.add(long_gone)
        db_session.commit()
        make_work_executor("recently-dead", stale=True)

        assert executors.forget_stale() == 1

        db_session.expire_all()
        assert db_session.get(WorkExecutor, "long-gone") is None
        assert db_session.get(WorkExecutor, "recently-dead") is not None

    def test_deregistering_forgets_this_executor(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])

        executors.deregister()

        db_session.expire_all()
        assert db_session.get(WorkExecutor, executors.executor_id()) is None
