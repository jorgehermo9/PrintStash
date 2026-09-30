"""Pooled ``httpx.AsyncClient``s, one per running event loop.

A single connection pool per loop, shared by all outbound HTTP on that loop
(Moonraker calls, URL imports). An ``AsyncClient`` binds its transport to the
loop that first uses it, so the API's loop and the job loop each get their own;
sharing one across loops fails the moment the second loop touches it.
"""

from __future__ import annotations

import asyncio
import weakref

import httpx

_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
    weakref.WeakKeyDictionary()
)
# The client used outside any running loop (synchronous construction sites).
_http_client: httpx.AsyncClient | None = None


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if _http_client is None or _http_client.is_closed:
            _http_client = _new_client()
        return _http_client
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = _new_client()
        _clients[loop] = client
    return client


async def close_http_client() -> None:
    """Close the running loop's client (and the loop-less one, if any)."""
    global _http_client
    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


def reset_for_tests() -> None:
    """Drop every cached client without touching loops that may be closed."""
    global _http_client
    _clients.clear()
    _http_client = None
