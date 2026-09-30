"""The process's long-lived loop for steps whose work is async.

One loop on its own daemon thread runs every async step, so a connection pool
created there (the shared HTTP client) outlives one step instead of dying with
an ``asyncio.run`` loop. Stopping it closes those clients and joins the thread;
the next step starts a fresh loop.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.modules.work import async_steps


@pytest.fixture(autouse=True)
def _fresh_loop():
    async_steps.stop()
    yield
    async_steps.stop()


class TestRunAsync:
    def test_returns_the_coroutines_result(self) -> None:
        async def answer() -> int:
            return 42

        assert async_steps.run_async(answer()) == 42

    def test_raises_the_coroutines_error(self) -> None:
        async def broken() -> None:
            raise ValueError("step failed")

        with pytest.raises(ValueError, match="step failed"):
            async_steps.run_async(broken())

    def test_every_step_shares_one_loop(self) -> None:
        async def which() -> asyncio.AbstractEventLoop:
            return asyncio.get_running_loop()

        assert async_steps.run_async(which()) is async_steps.run_async(which())

    def test_the_loop_runs_off_the_callers_thread(self) -> None:
        async def where() -> str:
            return threading.current_thread().name

        assert async_steps.run_async(where()) == "printstash-job-loop"


class TestStop:
    def test_a_stopped_loop_is_replaced_by_the_next_step(self) -> None:
        async def which() -> asyncio.AbstractEventLoop:
            return asyncio.get_running_loop()

        first = async_steps.run_async(which())

        async_steps.stop()

        second = async_steps.run_async(which())
        assert second is not first
        assert first.is_closed()

    def test_stopping_without_a_loop_is_harmless(self) -> None:
        async_steps.stop()
        async_steps.stop()
