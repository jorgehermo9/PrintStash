"""The process-local gate on opening application database sessions.

A restore that activates new database names closes the gate so no request
opens a session against a half-switched database. It lives here, below the
session layer, so the session factory can check it without depending on the
maintenance coordinator that closes it (which itself depends on sessions).
"""

from __future__ import annotations

import threading

from app.core.errors import ErrorKind, OperationError

_fenced = threading.Event()


def fence() -> None:
    """Reject fresh application sessions while database names are activated."""
    _fenced.set()


def release() -> None:
    _fenced.clear()


def require() -> None:
    if _fenced.is_set():
        raise OperationError("database_activation_in_progress", kind=ErrorKind.BUSY)
