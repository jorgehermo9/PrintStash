"""Short-lived, one-use browser WebSocket authentication tickets.

A ticket is bound to one scope (``printer:<id>``, ``events``) so a ticket
issued for one stream cannot open another. Tickets live in the API process,
which is the only process that serves websockets.
"""

from __future__ import annotations

import secrets
import threading
import time

TTL_SECONDS = 30

_lock = threading.Lock()
_tickets: dict[str, tuple[int, str, float]] = {}


def issue(user_id: int, scope: str) -> str:
    ticket = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        expired = [key for key, (_, _, expiry) in _tickets.items() if expiry <= now]
        for key in expired:
            _tickets.pop(key, None)
        _tickets[ticket] = (user_id, scope, now + TTL_SECONDS)
    return ticket


def consume(ticket: str, scope: str) -> int | None:
    with _lock:
        entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    user_id, expected_scope, expires_at = entry
    if expected_scope != scope or expires_at <= time.monotonic():
        return None
    return user_id


def printer_scope(printer_id: int) -> str:
    return f"printer:{printer_id}"


EVENTS_SCOPE = "events"
