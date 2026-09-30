"""Database fences: exclusion that holds across every process.

An exclusive fence (restore, backup) has one live holder; a second holder is
refused, the same holder re-acquiring renews it, and an expired fence is taken
over, because a crashed holder must not block the vault forever. Shared fences
(retentions, destructive operations) are many at once under a prefix, and a
checker asks whether any live one exists, optionally ignoring its own.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import WorkFence
from app.modules.work import fences


class TestAcquire:
    def test_takes_a_free_fence(self, db_session: Session) -> None:
        fence = fences.acquire(fences.RESTORE, holder="me", reason="restore")

        assert (fence.name, fence.holder) == (fences.RESTORE, "me")
        assert ensure_utc(fence.expires_at) > utcnow()

    def test_refuses_a_fence_another_holder_has(self) -> None:
        fences.acquire(fences.BACKUP, holder="them", reason="backup")

        with pytest.raises(fences.FenceHeld) as error:
            fences.acquire(fences.BACKUP, holder="me", reason="backup")

        assert (error.value.name, error.value.holder) == (fences.BACKUP, "them")

    def test_the_same_holder_renews_its_fence(self) -> None:
        first = fences.acquire(fences.BACKUP, holder="me", reason="backup")
        later = utcnow() + timedelta(seconds=5)

        again = fences.acquire(fences.BACKUP, holder="me", reason="backup", now=later)

        assert ensure_utc(again.expires_at) > ensure_utc(first.expires_at)

    def test_takes_over_a_fence_its_holder_let_expire(self, make_work_fence) -> None:
        # A crashed holder stops counting at expires_at.
        make_work_fence(fences.RESTORE, holder="crashed", expired=True)

        fence = fences.acquire(fences.RESTORE, holder="me", reason="restore")

        assert fence.holder == "me"

    def test_bounds_the_reason(self) -> None:
        fence = fences.acquire(fences.BACKUP, holder="me", reason="x" * 200)

        assert len(fence.reason) == 64


class TestShared:
    def test_many_holders_share_a_prefix(self) -> None:
        first = fences.acquire_shared(
            fences.RETENTION_PREFIX, holder="a", reason="snapshot"
        )
        second = fences.acquire_shared(
            fences.RETENTION_PREFIX, holder="b", reason="snapshot"
        )

        assert first != second
        assert fences.any_held(fences.RETENTION_PREFIX)

    def test_a_checker_can_ignore_its_own_fences(self) -> None:
        fences.acquire_shared(fences.DESTRUCTIVE_PREFIX, holder="me", reason="delete")

        assert fences.any_held(fences.DESTRUCTIVE_PREFIX, except_holder="me") is False
        assert fences.any_held(fences.DESTRUCTIVE_PREFIX, except_holder="them") is True

    def test_expired_shared_fences_do_not_count(self, make_work_fence) -> None:
        make_work_fence(f"{fences.RETENTION_PREFIX}old", expired=True)

        assert fences.any_held(fences.RETENTION_PREFIX) is False


class TestRelease:
    def test_releases_a_fence_its_holder_owns(self) -> None:
        fences.acquire(fences.BACKUP, holder="me", reason="backup")

        assert fences.release(fences.BACKUP, holder="me") is True
        assert fences.is_held(fences.BACKUP) is False

    def test_never_releases_someone_elses_fence(self) -> None:
        fences.acquire(fences.BACKUP, holder="them", reason="backup")

        assert fences.release(fences.BACKUP, holder="me") is False
        assert fences.is_held(fences.BACKUP) is True


class TestHeartbeat:
    def test_extends_every_live_fence_of_its_holder(self, db_session: Session) -> None:
        fences.acquire(fences.BACKUP, holder="me", reason="backup")
        shared = fences.acquire_shared(
            fences.RETENTION_PREFIX, holder="me", reason="snapshot"
        )
        later = utcnow() + timedelta(seconds=settings.fence_ttl_seconds / 2)

        assert fences.heartbeat("me", now=later) == 2

        db_session.expire_all()
        row = db_session.get(WorkFence, shared)
        assert row is not None
        assert ensure_utc(row.expires_at) > later + timedelta(
            seconds=settings.fence_ttl_seconds - 1
        )

    def test_does_not_revive_an_expired_fence(self, make_work_fence) -> None:
        make_work_fence(fences.RESTORE, holder="me", expired=True)

        assert fences.heartbeat("me") == 0


class TestGet:
    def test_reads_a_live_fence(self) -> None:
        fences.acquire(fences.RESTORE, holder="me", reason="restore")

        fence = fences.get(fences.RESTORE)

        assert fence is not None and fence.holder == "me"

    def test_an_expired_fence_reads_as_absent(self, make_work_fence) -> None:
        make_work_fence(fences.RESTORE, expired=True)

        assert fences.get(fences.RESTORE) is None

    def test_joins_the_callers_transaction(self, db_session: Session) -> None:
        # Reading on a second connection from inside a write transaction is
        # what deadlocked SQLite; a caller in a transaction passes its session.
        fences.acquire(fences.RESTORE, holder="me", reason="restore")

        fence = fences.get(fences.RESTORE, session=db_session)

        assert fence is not None and fence.holder == "me"

    def test_an_expired_fence_reads_as_absent_in_the_callers_transaction(
        self, db_session: Session, make_work_fence
    ) -> None:
        make_work_fence(fences.RESTORE, expired=True)

        assert fences.get(fences.RESTORE, session=db_session) is None

    def test_lists_what_a_holder_holds(self) -> None:
        fences.acquire(fences.BACKUP, holder="me", reason="backup")
        fences.acquire(fences.RESTORE, holder="them", reason="restore")

        assert fences.held_by("me") == [fences.BACKUP]
