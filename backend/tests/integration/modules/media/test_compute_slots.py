"""Native compute permits: one host memory budget shared by every process.

No more permits than the limit are ever held; a dead holder's lease expires
and is taken over; a late owner cannot release its successor's permit; and
SQLite contention is retried without admitting extra work.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import NativeComputeSlot
from app.db.session import get_session_factory
from app.modules.media import compute_slots


@pytest.fixture
def locked_permit(threaded_hub_db, monkeypatch):
    monkeypatch.setitem(_overlay, "max_render_jobs", 1)
    factory = get_session_factory()
    with factory.scoped_session() as session:
        permit = compute_slots.acquire(session, "initial")
        assert permit is not None
        compute_slots.release(session, permit.id, "initial")
        session.commit()
    with factory.scoped_session() as blocker:
        blocker.execute(text("UPDATE native_compute_slots SET lease_token = 'held'"))
        yield blocker
        blocker.rollback()


class TestAcquire:
    def test_retries_transient_sqlite_lock(self, locked_permit, monkeypatch):
        monkeypatch.setattr(
            compute_slots.time, "sleep", lambda _: locked_permit.rollback()
        )

        with get_session_factory().scoped_session() as session:
            permit = compute_slots.acquire(session, "recovered")

            assert permit is not None
            assert permit.lease_token == "recovered"
            assert len(session.exec(select(NativeComputeSlot)).all()) == 1

    def test_bounds_persistent_sqlite_lock(self, locked_permit, monkeypatch):
        monkeypatch.setattr(compute_slots.time, "sleep", lambda _: None)

        with get_session_factory().scoped_session() as session:
            with pytest.raises(OperationalError, match="locked"):
                compute_slots.acquire(session, "denied")

        locked_permit.rollback()
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(NativeComputeSlot)).one().lease_token is None

    @pytest.mark.parametrize(
        "token,seconds",
        [
            pytest.param("", 900, id="empty-token"),
            pytest.param("valid", 0, id="zero-lease"),
            pytest.param("valid", 901, id="over-lease-cap"),
        ],
    )
    def test_rejects_invalid_lease(self, db_session, token, seconds):
        with pytest.raises(ValueError, match="invalid_compute_lease"):
            compute_slots.acquire(db_session, token, lease_seconds=seconds)

        assert db_session.exec(select(NativeComputeSlot)).all() == []

    def test_admits_no_more_than_the_limit(self, db_session, monkeypatch):
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)

        assert compute_slots.acquire(db_session, "first") is not None
        assert compute_slots.acquire(db_session, "second") is None

    def test_an_expired_lease_is_taken_over(self, db_session, monkeypatch):
        # Its holder died; the permit must not stay lost until a restart.
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        compute_slots.acquire(db_session, "dead", lease_seconds=1)
        db_session.execute(
            text("UPDATE native_compute_slots SET lease_expires_at = '2000-01-01'")
        )
        db_session.commit()

        permit = compute_slots.acquire(db_session, "successor")

        assert permit is not None and permit.lease_token == "successor"


class TestRelease:
    def test_a_released_permit_admits_the_next(self, db_session, monkeypatch):
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        permit = compute_slots.acquire(db_session, "first")
        assert permit is not None

        compute_slots.release(db_session, permit.id, "first")
        db_session.commit()

        assert compute_slots.acquire(db_session, "second") is not None

    def test_a_late_owner_cannot_release_its_successor(self, db_session, monkeypatch):
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        permit = compute_slots.acquire(db_session, "successor")
        assert permit is not None

        compute_slots.release(db_session, permit.id, "late-owner")
        db_session.commit()

        db_session.expire_all()
        assert db_session.get(NativeComputeSlot, permit.id).lease_token == "successor"

    def test_releasing_no_permit_is_a_no_op(self, db_session):
        compute_slots.release(db_session, None, "never-acquired")


class TestNativeMemory:
    def test_the_budget_is_capped_at_two_gibibytes(self, monkeypatch):
        from app.modules.media import mesh_processing

        monkeypatch.setattr(
            mesh_processing, "_step_memory_budget_bytes", lambda: 64 * 1024**3
        )

        assert compute_slots.native_memory_budget_bytes() == 2 * 1024**3

    def test_an_undetectable_budget_falls_back_to_one_gibibyte(self, monkeypatch):
        from app.modules.media import mesh_processing

        monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: None)

        assert compute_slots.native_memory_budget_bytes() == 1024**3

    def test_reads_a_process_rss(self, monkeypatch):
        from app.modules.media import mesh_processing

        monkeypatch.setattr(mesh_processing, "_process_rss_bytes", lambda pid: pid * 2)

        assert compute_slots.native_process_rss_bytes(21) == 42


class TestRetry:
    def test_propagates_non_lock_database_failure(self, db_session):
        with pytest.raises(OperationalError, match="no such table"):
            compute_slots._retry(
                db_session,
                lambda: db_session.execute(
                    text("SELECT * FROM nonexistent_permit_table")
                ),
            )
