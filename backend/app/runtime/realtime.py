"""Best-effort event publication, independent from HTTP connections.

Consumers subscribe an async message sink. The HTTP adapter supplies its socket
sender; other consumers can subscribe without constructing a WebSocket. A slow
or failed sink is dropped after the existing bounded send timeout.

Events are notices, not state: each carries ids and a hint, delivery may drop
it, and a client that reconnects is told to ``resync`` and refetch through the
authorized endpoints. Two transports:

- ``InProcessBus`` for a single process (SQLite always is, and so is any
  deployment where the API runs its own jobs): publishers and every websocket
  share the process, so fan-out is a function call.
- ``PostgresNotifyBus`` for split topologies: a worker has no websockets, so
  it publishes with ``pg_notify``; the API ``LISTEN``s and delivers to its
  local sockets. Payloads are ids only, well below NOTIFY's 8000-byte limit.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Protocol, Set

from app.core.logging import get_logger

logger = get_logger(__name__)

MessageSink = Callable[[Dict[str, Any]], Awaitable[None]]

_SEND_TIMEOUT_S = 2.0
NOTIFY_CHANNEL = "printstash_events"
_MAX_NOTIFY_BYTES = 7900
_RECONNECT_MAX_S = 30.0


class EventPublisher(Protocol):
    async def publish(self, channel: str, payload: Dict[str, Any]) -> None: ...

    def publish_threadsafe(self, channel: str, payload: Dict[str, Any]) -> None: ...


class RealtimeBus(EventPublisher, Protocol):
    async def subscribe(self, channel: str, ws: MessageSink) -> None: ...

    async def unsubscribe(self, channel: str, ws: MessageSink) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class InProcessBus:
    """Single-process fan-out. Subscriber sends run concurrently so one slow
    or dead socket can't delay delivery to the rest of a channel."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[MessageSink]] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def stop(self) -> None:
        self._loop = None

    async def subscribe(self, channel: str, ws: MessageSink) -> None:
        async with self._lock:
            self._subscribers.setdefault(channel, set()).add(ws)

    async def unsubscribe(self, channel: str, ws: MessageSink) -> None:
        async with self._lock:
            subs = self._subscribers.get(channel)
            if subs and ws in subs:
                subs.remove(ws)

    async def publish(self, channel: str, payload: Dict[str, Any]) -> None:
        await self._deliver(channel, payload)

    def publish_threadsafe(self, channel: str, payload: Dict[str, Any]) -> None:
        """Publish from a worker thread; dropped when the bus is not running."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._deliver(channel, payload), loop)

    async def _deliver(self, channel: str, payload: Dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subscribers.get(channel, ()))
        if not subs:
            return

        async def _send(ws: MessageSink) -> MessageSink | None:
            try:
                async with asyncio.timeout(_SEND_TIMEOUT_S):
                    await ws(payload)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(_send(ws) for ws in subs))
        dead = [r for r in results if r is not None]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subscribers.get(channel, set()).discard(ws)


def _envelope(channel: str, payload: Dict[str, Any]) -> str | None:
    body = json.dumps({"c": channel, "p": payload}, separators=(",", ":"), default=str)
    if len(body.encode()) > _MAX_NOTIFY_BYTES:
        logger.warning(
            "realtime event over NOTIFY limit dropped", extra={"channel": channel}
        )
        return None
    return body


class PostgresNotifyBus(InProcessBus):
    """Cross-process delivery over PostgreSQL LISTEN/NOTIFY (psycopg 3).

    ``listen=True`` (the API) holds one dedicated connection that receives
    every process's notices and delivers them locally; after a reconnect it
    broadcasts ``resync`` so clients refetch whatever they may have missed.
    Every process publishes with ``pg_notify`` on a short transaction.
    """

    def __init__(self, database_url: str, *, listen: bool) -> None:
        super().__init__()
        self._url = database_url
        self._listen = listen
        self._listener: asyncio.Task | None = None
        self._stopping = threading.Event()

    def _conninfo(self) -> str:
        from sqlalchemy.engine import make_url

        url = make_url(self._url).set(drivername="postgresql")
        return url.render_as_string(hide_password=False)

    async def start(self) -> None:
        await super().start()
        if self._listen and self._listener is None:
            self._stopping.clear()
            self._listener = asyncio.create_task(
                self._listen_forever(), name="realtime-listener"
            )

    async def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except (asyncio.CancelledError, Exception):
                pass
            self._listener = None
        await super().stop()

    async def publish(self, channel: str, payload: Dict[str, Any]) -> None:
        await asyncio.to_thread(self._notify, channel, payload)

    def publish_threadsafe(self, channel: str, payload: Dict[str, Any]) -> None:
        self._notify(channel, payload)

    def _notify(self, channel: str, payload: Dict[str, Any]) -> None:
        body = _envelope(channel, payload)
        if body is None:
            return
        try:
            from sqlalchemy import text

            from app.db.session import get_session_factory

            with get_session_factory().scoped_session() as session:
                session.execute(
                    text("SELECT pg_notify(:channel, :body)"),
                    {"channel": NOTIFY_CHANNEL, "body": body},
                )
                session.commit()
        except Exception:  # noqa: BLE001 - realtime delivery is best effort
            logger.warning("realtime notify failed", extra={"channel": channel})

    async def _listen_forever(self) -> None:
        import psycopg

        delay = 1.0
        first = True
        while not self._stopping.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo(), autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    delay = 1.0
                    if not first:
                        await self._deliver("*", {"type": "resync"})
                    first = False
                    async for notice in conn.notifies():
                        try:
                            envelope = json.loads(notice.payload)
                            await self._deliver(envelope["c"], envelope["p"])
                        except (ValueError, KeyError, TypeError):
                            logger.warning("malformed realtime notice ignored")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect with backoff
                logger.warning("realtime listener disconnected; reconnecting")
            if self._stopping.is_set():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_S)

    async def subscribe(self, channel: str, ws: MessageSink) -> None:
        await super().subscribe(channel, ws)
        if channel != "*":
            await super().subscribe("*", ws)

    async def unsubscribe(self, channel: str, ws: MessageSink) -> None:
        await super().unsubscribe(channel, ws)
        await super().unsubscribe("*", ws)


def build_event_bus(*, listen: bool = True):
    """The bus this process publishes (and, for the API, delivers) through.

    PostgreSQL gets the NOTIFY bus whenever another process may publish (a
    worker, or an API that does not run its own jobs); every other deployment
    is one process and delivers in memory.
    """
    from sqlalchemy.engine import make_url

    from app.core.config import settings

    split = settings.process_role == "worker" or not settings.api_runs_jobs
    if make_url(settings.db_url).get_backend_name() == "postgresql" and split:
        return PostgresNotifyBus(settings.db_url, listen=listen)
    return InProcessBus()
