"""One pooled HTTP client per running event loop.

A client binds its transport to the loop that first uses it, so the API loop
and the job loop must never share one. Within a loop the client is reused
until closed; outside any loop one client serves synchronous construction
sites. Closing a loop's client also closes the loop-less one.
"""

from __future__ import annotations

import asyncio

from app.core import http_client


async def _client_twice():
    return http_client.get_http_client(), http_client.get_http_client()


class TestGetHttpClient:
    def test_a_loop_reuses_its_client(self) -> None:
        first, second = asyncio.run(_client_twice())

        assert first is second

    def test_each_loop_gets_its_own_client(self) -> None:
        first, _ = asyncio.run(_client_twice())
        other, _ = asyncio.run(_client_twice())

        assert first is not other

    def test_outside_a_loop_one_client_is_shared(self) -> None:
        assert http_client.get_http_client() is http_client.get_http_client()

    def test_a_closed_client_is_replaced(self) -> None:
        async def closed_then_fresh():
            client = http_client.get_http_client()
            await client.aclose()
            return client, http_client.get_http_client()

        closed, fresh = asyncio.run(closed_then_fresh())

        assert closed is not fresh and not fresh.is_closed


class TestCloseHttpClient:
    def test_closes_every_client_the_loop_can_reach(self) -> None:
        loopless = http_client.get_http_client()

        async def close():
            client = http_client.get_http_client()
            await http_client.close_http_client()
            return client

        closed = asyncio.run(close())

        assert closed.is_closed and loopless.is_closed
        assert http_client.get_http_client() is not loopless

    def test_closing_with_no_client_is_a_no_op(self) -> None:
        asyncio.run(http_client.close_http_client())
