"""Maintenance fences across processes that share one database.

On a shared database a restore or backup in another process is only visible
through its fence row, so every gate here reads the fence as well as this
process's own counters. An unreadable fence table is never taken as free: it
fails closed. The database stays SQLite; only the configured URL says the
database is shared, which is all the gates consult.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.core.errors import OperationError
from app.core.time import utcnow
from app.modules.work import fences
from app.modules.work.executors import executor_id
from app.runtime import maintenance


@pytest.fixture(autouse=True)
def shared_database() -> None:
    _overlay["db_url"] = "postgresql+psycopg://vault@db/vault"


def _unreadable(*_args, **_kwargs):
    raise RuntimeError("fence table unreadable")


class TestMutations:
    def test_a_mutation_is_admitted_without_a_restore(self) -> None:
        assert maintenance.begin_mutating_operation() is True
        maintenance.end_mutating_operation()

    def test_another_process_restoring_refuses_new_mutations(
        self, make_work_fence
    ) -> None:
        make_work_fence(fences.RESTORE)

        assert maintenance.begin_mutating_operation() is False
        assert maintenance.active_mutations() == 0

    def test_this_process_restore_fence_is_not_foreign(self, make_work_fence) -> None:
        make_work_fence(fences.RESTORE, holder=executor_id())

        assert maintenance.restore_in_progress() is False

    def test_an_expired_fence_no_longer_blocks(self, make_work_fence) -> None:
        make_work_fence(fences.RESTORE, expired=True)

        assert maintenance.restore_in_progress() is False

    def test_an_unreadable_fence_table_fails_closed(self, monkeypatch) -> None:
        monkeypatch.setattr(fences, "get", _unreadable)

        assert maintenance.begin_mutating_operation() is False

    def test_the_fence_read_can_join_an_open_session(
        self, db_session: Session, make_work_fence
    ) -> None:
        make_work_fence(fences.RESTORE)

        assert maintenance.restore_in_progress(db_session) is True


class TestRestoreMaintenance:
    def test_a_restore_holds_the_fence_until_it_ends(self) -> None:
        maintenance.begin_restore_maintenance()
        held = fences.get(fences.RESTORE)

        maintenance.end_restore_maintenance()

        assert held is not None and held.holder == executor_id()
        assert fences.get(fences.RESTORE) is None
        assert maintenance.restore_in_progress() is False

    def test_a_restore_elsewhere_refuses_another(self, make_work_fence) -> None:
        make_work_fence(fences.RESTORE)

        with pytest.raises(maintenance.RestoreConflictError):
            maintenance.begin_restore_maintenance()

    def test_a_busy_executor_elsewhere_times_the_restore_out(
        self, make_work_executor, monkeypatch
    ) -> None:
        # Another process still has a write in flight: the restore must not
        # replace the database under it, and must give its fence back.
        monkeypatch.setattr(maintenance, "_RESTORE_DRAIN_TIMEOUT_S", 0.2)
        monkeypatch.setattr(maintenance, "_DRAIN_POLL_S", 0.01)
        make_work_executor(active_mutations=1)

        with pytest.raises(maintenance.RestoreConflictError):
            maintenance.begin_restore_maintenance()

        assert fences.get(fences.RESTORE) is None
        assert maintenance.restore_in_progress() is False

    def test_an_executor_that_has_not_heartbeated_since_is_waited_for(
        self, make_work_executor
    ) -> None:
        # Its last beat predates the fence, so it may not have seen it yet.
        make_work_executor()
        fenced_at = utcnow() + timedelta(seconds=5)

        assert maintenance._others_drained(fenced_at) is False

    def test_an_idle_executor_that_saw_the_fence_is_drained(
        self, make_work_executor
    ) -> None:
        make_work_executor()
        make_work_executor(executor_id())

        assert maintenance._others_drained(utcnow() - timedelta(seconds=5)) is True

    def test_a_stale_executor_is_not_waited_for(self, make_work_executor) -> None:
        make_work_executor(stale=True, active_mutations=1)

        maintenance.begin_restore_maintenance()
        maintenance.end_restore_maintenance()

    def test_a_failed_fence_release_is_left_to_expire(self, monkeypatch) -> None:
        maintenance.begin_restore_maintenance()
        monkeypatch.setattr(fences, "release", _unreadable)

        maintenance.end_restore_maintenance()

        assert maintenance.restore_in_progress() is False

    def test_recovery_gates_this_process_and_fences_the_others(self) -> None:
        maintenance.hold_restore_maintenance()

        fence = fences.get(fences.RESTORE)
        assert fence is not None and fence.reason == "restore_recovery"
        assert maintenance.begin_mutating_operation() is False

    def test_recovery_still_gates_locally_without_a_fence(self, monkeypatch) -> None:
        monkeypatch.setattr(fences, "acquire", _unreadable)

        maintenance.hold_restore_maintenance()

        assert maintenance.begin_mutating_operation() is False


@maintenance.exclusive_backup_operation
def _backup() -> bool:
    return maintenance.backup_in_progress_elsewhere()


class TestBackupFence:
    def test_a_backup_holds_the_fence_only_while_it_runs(self) -> None:
        seen: list[bool] = []

        @maintenance.exclusive_backup_operation
        def run() -> None:
            seen.append(fences.is_held(fences.BACKUP))

        run()

        assert seen == [True]
        assert fences.is_held(fences.BACKUP) is False

    def test_a_backup_elsewhere_refuses_another(self, make_work_fence) -> None:
        make_work_fence(fences.BACKUP)

        with pytest.raises(OperationError) as refused:
            _backup()

        assert refused.value.code == "backup_operation_in_progress"

    def test_a_backup_does_not_count_against_itself(self) -> None:
        assert _backup() is False

    def test_a_backup_elsewhere_is_seen(self, make_work_fence) -> None:
        make_work_fence(fences.BACKUP)

        assert maintenance.backup_in_progress_elsewhere() is True

    def test_no_backup_anywhere_is_seen(self) -> None:
        assert maintenance.backup_in_progress_elsewhere() is False

    def test_an_unreadable_backup_fence_is_not_free(self, monkeypatch) -> None:
        monkeypatch.setattr(fences, "is_held", _unreadable)

        assert maintenance.backup_in_progress_elsewhere() is True


class TestStorageRetention:
    def test_a_snapshot_holds_a_retention_fence_while_open(self) -> None:
        with maintenance.retain_storage_objects():
            held = fences.held_by(executor_id())

        assert any(name.startswith(fences.RETENTION_PREFIX) for name in held)
        assert fences.held_by(executor_id()) == []

    def test_a_destruction_elsewhere_refuses_a_snapshot(self, make_work_fence) -> None:
        make_work_fence(f"{fences.DESTRUCTIVE_PREFIX}other")

        with pytest.raises(OperationError) as refused:
            with maintenance.retain_storage_objects():
                pass

        assert refused.value.code == "storage_cleanup_in_progress"
        assert fences.held_by(executor_id()) == []

    def test_a_snapshot_elsewhere_refuses_a_destruction(self, make_work_fence) -> None:
        make_work_fence(f"{fences.RETENTION_PREFIX}other")

        assert maintenance.begin_destructive_operation() is False
        assert fences.held_by(executor_id()) == []

    def test_a_destruction_holds_its_fence_until_it_ends(self) -> None:
        assert maintenance.begin_destructive_operation() is True
        held = fences.held_by(executor_id())

        maintenance.end_destructive_operation()

        assert any(name.startswith(fences.DESTRUCTIVE_PREFIX) for name in held)
        assert fences.held_by(executor_id()) == []
