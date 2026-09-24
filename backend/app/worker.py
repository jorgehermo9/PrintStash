"""``python -m app.worker``: a process that executes background Jobs and nothing else.

A worker serves no HTTP and supervises no printer or folder connections; it
binds the vault's configuration and storage, launches the job engine on every
lane, heartbeats, and exits cleanly on SIGTERM/SIGINT. The API owns the schema:
a worker waits until the database is migrated to the version it was built
for, so workers can start before or alongside the API.

Split topologies need PostgreSQL and storage every process can reach; see
``docs/architecture/background-work.md``.
"""

from __future__ import annotations

import signal
import threading
import time

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.worker")

_SCHEMA_WAIT_S = 2.0
_SCHEMA_WAIT_MAX_S = 600.0


def _schema_current() -> bool:
    from app.db.migrate import schema_is_current

    return schema_is_current(settings.db_url)


def wait_for_schema(*, timeout: float = _SCHEMA_WAIT_MAX_S) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if _schema_current():
                return
        except Exception:  # noqa: BLE001 - the database may still be starting
            logger.info("waiting for the database")
        if time.monotonic() >= deadline:
            raise RuntimeError("database schema not at this build's migration head")
        time.sleep(_SCHEMA_WAIT_S)


def main() -> int:
    if settings.process_role != "worker":
        raise SystemExit("python -m app.worker requires VAULT_PROCESS_ROLE=worker")
    from app.bootstrap import work
    from app.bootstrap.lifecycle import (
        bind_search,
        close_inference,
        prepare_process,
        restore_search,
    )
    from app.runtime.realtime import build_event_bus

    work.validate_topology()
    wait_for_schema()
    prepare_process(owner=False)
    # A worker changes the library too (ingest commits), so what search must
    # re-project is recorded here as well.
    previous_search = bind_search()
    stop = threading.Event()

    def _signal(signum, _frame) -> None:
        logger.info("worker stopping on signal %s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    work.start(publisher=build_event_bus(listen=False))
    try:
        stop.wait()
    finally:
        try:
            work.stop()
        finally:
            close_inference()
            restore_search(previous_search)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
