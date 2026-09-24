"""Maintenance exclusion and draining of mutating operations, across processes.

The public operations are unchanged from the single-process implementation.
Every exclusion is enforced in-process first (the counters and gates below).
When the database is shared with other processes (PostgreSQL: the split
topologies), each exclusion that must hold across processes is *also* a
database fence (``app.modules.work.fences``). On SQLite exactly one process
runs (the process lock and topology validation guarantee it), so the fences
would guard nothing; worse, a fence written on a second connection from a
request that already holds SQLite's single write lock would wait on itself.
The fences are:

- **restore**: an exclusive fence. While it is held, no process admits a new
  write-capable operation. Taking it drains this process *and* every other
  live executor, each of which reports its in-flight count on its heartbeat.
- **backup**: an exclusive fence, so two processes never build or restore a
  snapshot at once.
- **storage retention / destructive operations**: shared fences. A snapshot
  retains every object generation it captured; a delete, move or replacement
  refuses while any retention is live, and a retention refuses while a
  destructive operation is live. Each side inserts its own fence *before*
  checking for the other, so of two racing processes at least one sees the
  other and backs off.

Storage-configuration activation and database-connection fencing stay
process-local: they only ever run inside the API process that serves the
configuration and restore routes, and they are covered by the restore fence
for every other process.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Callable, Iterator, ParamSpec, TypeVar

from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.db import admission as database_admission

if TYPE_CHECKING:
    from sqlmodel import Session

logger = get_logger(__name__)

_RESTORE_DRAIN_TIMEOUT_S = 30.0
_DRAIN_POLL_S = 0.1

_P = ParamSpec("_P")
_R = TypeVar("_R")

# This process's own view: set while it holds (or is recovering) a restore.
_restore_gate = threading.Event()
_mutation_condition = threading.Condition()
_active_mutations = 0
_mutation_observer: Callable[[], None] | None = None

backup_operation_lock = threading.RLock()
_backup_depth = threading.local()


class RestoreConflictError(Exception):
    """Raised when a restore is refused because write work is still in flight."""


def _holder() -> str:
    from app.modules.work.executors import executor_id

    return executor_id()


def _shared_database() -> bool:
    """Whether other processes can share this database (anything but SQLite)."""
    from app.core.config import settings

    return _backend_name(str(settings.db_url)) != "sqlite"


def _backend_name(url: str) -> str:
    cached = _BACKENDS.get(url)
    if cached is None:
        from sqlalchemy.engine import make_url

        cached = make_url(url).get_backend_name()
        _BACKENDS[url] = cached
    return cached


_BACKENDS: dict[str, str] = {}


def observe_mutations(observer: Callable[[], None] | None) -> None:
    """Composition hook for a durable activation owner's first-write marker."""
    global _mutation_observer
    _mutation_observer = observer


def active_mutations() -> int:
    """Write-capable operations admitted in this process and not yet ended."""
    with _mutation_condition:
        return _active_mutations


def _foreign_restore_fence(session: Session | None = None) -> bool:
    """Whether another process holds the restore fence. Fails closed."""
    from app.modules.work import fences

    if not _shared_database():
        return False
    try:
        fence = fences.get(fences.RESTORE, session=session)
    except Exception:  # noqa: BLE001 - an unreadable fence table is not "free"
        logger.warning("restore fence unreadable; refusing new mutations")
        return True
    return fence is not None and fence.holder != _holder()


def restore_in_progress(session: Session | None = None) -> bool:
    """Whether a restore (here or in another process) holds maintenance.

    Pass ``session`` from inside an open transaction so the fence read joins
    it rather than opening another connection mid-transaction.
    """
    if _restore_gate.is_set():
        return True
    return _foreign_restore_fence(session)


def begin_mutating_operation() -> bool:
    """Register a write-capable operation unless restore maintenance is active."""
    global _active_mutations
    with _mutation_condition:
        if _restore_gate.is_set():
            return False
        _active_mutations += 1
    if _foreign_restore_fence():
        end_mutating_operation()
        return False
    try:
        if _mutation_observer is not None:
            _mutation_observer()
    except Exception:
        end_mutating_operation()
        raise
    return True


def end_mutating_operation() -> None:
    global _active_mutations
    with _mutation_condition:
        if _active_mutations <= 0:
            raise RuntimeError("unbalanced_mutating_operation")
        _active_mutations -= 1
        if _active_mutations == 0:
            _mutation_condition.notify_all()


def _others_drained(acquired_at) -> bool:
    """Every other live executor heartbeated after the fence with nothing active."""
    from app.modules.work import executors

    if not _shared_database():
        return True
    me = _holder()
    for row in executors.live():
        if row.executor_id == me:
            continue
        if ensure_utc(row.heartbeat_at) <= ensure_utc(acquired_at):
            return False
        if row.active_mutations:
            return False
    return True


def begin_restore_maintenance() -> None:
    """Block new mutations everywhere and wait for admitted ones to drain."""
    from app.core.time import utcnow
    from app.modules.work import fences

    acquired_at = utcnow()
    if _shared_database():
        try:
            fence = fences.acquire(fences.RESTORE, holder=_holder(), reason="restore")
        except fences.FenceHeld as exc:
            raise RestoreConflictError("restore_in_progress") from exc
        acquired_at = fence.acquired_at
    with _mutation_condition:
        _restore_gate.set()
    deadline = time.monotonic() + _RESTORE_DRAIN_TIMEOUT_S
    try:
        while True:
            with _mutation_condition:
                local = _active_mutations
            if local == 0 and _others_drained(acquired_at):
                return
            if time.monotonic() >= deadline:
                raise RestoreConflictError(
                    f"{local} local write operation(s) or another process still "
                    "active; retry later"
                )
            with _mutation_condition:
                _mutation_condition.wait(timeout=_DRAIN_POLL_S)
    except BaseException:
        end_restore_maintenance()
        raise


def end_restore_maintenance() -> None:
    from app.modules.work import fences

    with _mutation_condition:
        _restore_gate.clear()
        _mutation_condition.notify_all()
    if not _shared_database():
        return
    try:
        fences.release(fences.RESTORE, holder=_holder())
    except Exception:  # noqa: BLE001 - an unreleased fence expires on its own
        logger.warning("restore fence release failed; it will expire")


def hold_restore_maintenance() -> None:
    """Keep mutations gated while durable recovery evidence remains unresolved.

    The filesystem journal is the authority here, so the local gate is set
    first and unconditionally; the fence only extends the gate to other
    processes and is best-effort while the database may be mid-recovery.
    """
    from app.modules.work import fences

    with _mutation_condition:
        _restore_gate.set()
    if not _shared_database():
        return
    try:
        fences.acquire(fences.RESTORE, holder=_holder(), reason="restore_recovery")
    except Exception:  # noqa: BLE001 - the local gate still holds this process
        logger.warning("restore fence unavailable during recovery maintenance")


def exclusive_backup_operation(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Prevent overlapping backup/restore operations in any process."""

    @wraps(func)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        from app.modules.work import fences

        with backup_operation_lock:
            depth = getattr(_backup_depth, "value", 0)
            fenced = depth == 0 and _shared_database()
            if fenced:
                try:
                    fences.acquire(fences.BACKUP, holder=_holder(), reason="backup")
                except fences.FenceHeld as exc:
                    raise OperationError(
                        "backup_operation_in_progress", kind=ErrorKind.BUSY
                    ) from exc
            _backup_depth.value = depth + 1
            try:
                return func(*args, **kwargs)
            finally:
                _backup_depth.value -= 1
                if fenced:
                    fences.release(fences.BACKUP, holder=_holder())

    return serialized


def backup_in_progress_elsewhere() -> bool:
    """Whether a backup or restore holds the backup fence outside this thread.

    Recovery that settles "running" backup rows may only run when no backup
    is live anywhere; the calling thread's own exclusive operation does not
    count against it.
    """
    from app.modules.work import fences

    if getattr(_backup_depth, "value", 0) > 0:
        return False
    if not _shared_database():
        # One process: a backup elsewhere is another thread holding the lock.
        if backup_operation_lock.acquire(blocking=False):
            backup_operation_lock.release()
            return False
        return True
    try:
        return fences.is_held(fences.BACKUP)
    except Exception:  # noqa: BLE001 - an unreadable fence is not "free"
        return True


_retention_condition = threading.Condition(threading.RLock())
_storage_retentions = 0
_active_destructive = 0
_destructive_fences = threading.local()
_activating_configuration: ContextVar[bool] = ContextVar(
    "vault_configuration_activation", default=False
)
_retained_destination: ContextVar[object | None] = ContextVar(
    "retained_migration_destination", default=None
)


@contextmanager
def retain_storage_objects() -> Iterator[None]:
    """Keep all captured object generations alive until an archive/copy finishes."""
    global _storage_retentions
    from app.modules.work import fences

    with _retention_condition:
        if _active_destructive:
            raise OperationError("storage_cleanup_in_progress", kind=ErrorKind.BUSY)
        _storage_retentions += 1
    name: str | None = None
    try:
        if _shared_database():
            name = fences.acquire_shared(
                fences.RETENTION_PREFIX, holder=_holder(), reason="snapshot"
            )
            if fences.any_held(fences.DESTRUCTIVE_PREFIX, except_holder=_holder()):
                raise OperationError("storage_cleanup_in_progress", kind=ErrorKind.BUSY)
        yield
    finally:
        if name is not None:
            fences.release(name, holder=_holder())
        with _retention_condition:
            _storage_retentions -= 1
            _retention_condition.notify_all()


@contextmanager
def allow_retained_destination_destruction(
    *, source: object, destination: object
) -> Iterator[None]:
    """Permit exact-receipt migration cleanup on this isolated candidate only."""
    if source is destination:
        raise ValueError("migration_destination_must_be_isolated")
    token = _retained_destination.set(destination)
    try:
        yield
    finally:
        _retained_destination.reset(token)


def _fence_stack() -> list[str | None]:
    stack = getattr(_destructive_fences, "stack", None)
    if stack is None:
        stack = []
        _destructive_fences.stack = stack
    return stack


def begin_destructive_operation(*, _backend: object | None = None) -> bool:
    """Admit a delete, move or replacement when no capture is pinned anywhere."""
    global _active_destructive
    from app.modules.work import fences

    isolated = _backend is not None and _retained_destination.get() is _backend
    with _retention_condition:
        if _storage_retentions and not isolated:
            return False
        _active_destructive += 1
    if isolated or not _shared_database():
        # An isolated migration candidate is not what any snapshot retains;
        # on an unshared database the counters above are the whole exclusion.
        _fence_stack().append(None)
        return True
    name = fences.acquire_shared(
        fences.DESTRUCTIVE_PREFIX, holder=_holder(), reason="storage_mutation"
    )
    if fences.any_held(fences.RETENTION_PREFIX, except_holder=_holder()):
        fences.release(name, holder=_holder())
        with _retention_condition:
            _active_destructive -= 1
            _retention_condition.notify_all()
        return False
    _fence_stack().append(name)
    return True


def end_destructive_operation() -> None:
    global _active_destructive
    from app.modules.work import fences

    with _retention_condition:
        if _active_destructive <= 0:
            raise RuntimeError("unbalanced_destructive_operation")
        _active_destructive -= 1
        _retention_condition.notify_all()
    stack = _fence_stack()
    name = stack.pop() if stack else None
    if name is not None:
        fences.release(name, holder=_holder())


@contextmanager
def destructive_operation(*, _backend: object | None = None) -> Iterator[None]:
    if not begin_destructive_operation(_backend=_backend):
        raise OperationError("storage_snapshot_retained", kind=ErrorKind.BUSY)
    try:
        yield
    finally:
        end_destructive_operation()


def guarded_storage_destruction(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Cover the whole storage mutation, including its identity check."""

    @wraps(func)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with destructive_operation(_backend=args[0] if args else None):
            return func(*args, **kwargs)

    return guarded


def guarded_destructive_operation(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Exclude the logical transaction too, before claims or rows are changed."""

    @wraps(func)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with destructive_operation():
            return func(*args, **kwargs)

    return guarded


@contextmanager
def activating_storage_configuration() -> Iterator[None]:
    """Internal atomic-activation scope; never exposed by configuration routes."""
    token = _activating_configuration.set(True)
    try:
        yield
    finally:
        _activating_configuration.reset(token)


def guarded_storage_configuration(func: Callable[_P, _R]) -> Callable[_P, _R]:
    fields = {
        "provider",
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "s3_bucket",
        "s3_endpoint_url",
        "s3_region",
        "s3_access_key",
        "s3_secret_key",
    }

    @wraps(func)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _activating_configuration.get() or not any(
            kwargs.get(field) is not None for field in fields
        ):
            return func(*args, **kwargs)
        with destructive_operation():
            return func(*args, **kwargs)

    return guarded


def fence_database_connections() -> None:
    """Reject fresh application sessions while database names are activated."""
    database_admission.fence()


def release_database_connections() -> None:
    database_admission.release()


def require_database_connection_admission() -> None:
    database_admission.require()


def reset_for_tests() -> None:
    """Return every process-local gate and counter to its initial state."""
    global _active_mutations, _storage_retentions, _active_destructive
    global _mutation_observer
    with _mutation_condition:
        _restore_gate.clear()
        _active_mutations = 0
        _mutation_condition.notify_all()
    with _retention_condition:
        _storage_retentions = 0
        _active_destructive = 0
    _destructive_fences.stack = []
    _backup_depth.value = 0
    database_admission.release()
    _mutation_observer = None


__all__ = [
    "RestoreConflictError",
    "active_mutations",
    "allow_retained_destination_destruction",
    "begin_destructive_operation",
    "begin_mutating_operation",
    "begin_restore_maintenance",
    "destructive_operation",
    "end_destructive_operation",
    "end_mutating_operation",
    "end_restore_maintenance",
    "exclusive_backup_operation",
    "fence_database_connections",
    "guarded_destructive_operation",
    "guarded_storage_configuration",
    "guarded_storage_destruction",
    "hold_restore_maintenance",
    "observe_mutations",
    "release_database_connections",
    "require_database_connection_admission",
    "restore_in_progress",
    "retain_storage_objects",
]
