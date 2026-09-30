"""Run a step's coroutine on this process's long-lived job event loop.

Steps are synchronous (engines run them on worker threads), but some owners'
work is naturally async (streaming downloads, provider clients). One loop per
process, on its own daemon thread, runs those coroutines, so a connection pool
created on it (``get_http_client``) lives across steps instead of being bound to
a loop that ``asyncio.run`` closes after every call.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is not None and _thread is not None and _thread.is_alive():
            return _loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def serve() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(target=serve, name="printstash-job-loop", daemon=True)
        thread.start()
        ready.wait()
        _loop, _thread = loop, thread
        return loop


def run_async(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coroutine`` on the job loop and wait for its result."""
    loop = _ensure_loop()
    return asyncio.run_coroutine_threadsafe(coroutine, loop).result()


def stop() -> None:
    """Stop the job loop (process shutdown and test isolation)."""
    global _loop, _thread
    with _lock:
        loop, thread = _loop, _thread
        _loop = _thread = None
    if loop is None:
        return

    async def close_clients() -> None:
        from app.core.http_client import close_http_client

        await close_http_client()

    try:
        asyncio.run_coroutine_threadsafe(close_clients(), loop).result(timeout=5)
    except Exception:  # noqa: BLE001 - shutdown is best-effort
        pass
    loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(timeout=5)
    loop.close()
