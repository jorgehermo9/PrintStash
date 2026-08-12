"""Deployment-topology guard for process-local coordination primitives."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO

from sqlalchemy.engine.url import make_url

from app.core.config import settings


def _lock_path() -> Path:
    url = make_url(settings.db_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, ":memory:"):
        return Path(str(url.database)).resolve().parent / ".printstash-api.lock"
    return Path(settings.staging_dir).resolve() / ".printstash-api.lock"


def acquire_process_lock() -> IO[str]:
    """Claim the vault's single supported API-process slot or fail fast."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            "PrintStash supports a single API process; vault lock is already held"
        ) from exc
    # The fixed lock path is only an advisory-lock rendezvous. Never truncate,
    # append to, or otherwise claim ownership of pre-existing bytes there.
    return handle


def release_process_lock(handle: IO[str]) -> None:
    if handle.closed:
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
