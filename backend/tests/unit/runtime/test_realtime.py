"""Tests for services.realtime.InProcessBus fan-out."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import ProcessRole, _overlay
from app.runtime.realtime import (
    InProcessBus,
    PostgresNotifyBus,
    _envelope,
    build_event_bus,
)


class FakeSocket:
    def __init__(self, *, delay: float = 0.0, raises: bool = False):
        self.delay = delay
        self.raises = raises
        self.received: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise RuntimeError("send failed")
        self.received.append(payload)


class TestPublish:
    def test_publish_to_empty_channel_is_a_noop(self):
        async def _run():
            bus = InProcessBus()
            await bus.publish("printer:999", {"x": 1})  # must not raise

        asyncio.run(_run())

    def test_slow_subscriber_does_not_delay_others(self):
        async def _run():
            bus = InProcessBus()
            slow = FakeSocket(delay=10.0)
            fast = FakeSocket()
            await bus.subscribe("printer:1", slow.send_json)
            await bus.subscribe("printer:1", fast.send_json)

            async with asyncio.timeout(3.0):
                await bus.publish("printer:1", {"hello": "world"})

            assert fast.received == [{"hello": "world"}]
            assert slow.received == []

        asyncio.run(_run())

    def test_dead_subscriber_is_dropped(self):
        async def _run():
            bus = InProcessBus()
            dead = FakeSocket(raises=True)
            alive = FakeSocket()
            await bus.subscribe("printer:1", dead.send_json)
            await bus.subscribe("printer:1", alive.send_json)

            await bus.publish("printer:1", {"x": 1})

            assert dead.send_json not in bus._subscribers["printer:1"]
            assert alive.received == [{"x": 1}]

        asyncio.run(_run())


class TestTimeout:
    def test_slow_subscriber_is_dropped_after_timeout(self):
        async def _run():
            bus = InProcessBus()
            slow = FakeSocket(delay=10.0)
            await bus.subscribe("printer:1", slow.send_json)

            async with asyncio.timeout(3.0):
                await bus.publish("printer:1", {"hello": "world"})

            assert slow.send_json not in bus._subscribers["printer:1"]

        asyncio.run(_run())


class TestThreadsafePublish:
    def test_a_job_thread_delivers_through_the_running_loop(self):
        async def _run():
            bus = InProcessBus()
            socket = FakeSocket()
            await bus.start()
            await bus.subscribe("jobs:1", socket.send_json)

            await asyncio.to_thread(bus.publish_threadsafe, "jobs:1", {"n": 1})
            async with asyncio.timeout(3.0):
                while not socket.received:
                    await asyncio.sleep(0.01)

            assert socket.received == [{"n": 1}]

        asyncio.run(_run())

    def test_is_dropped_when_the_bus_is_not_running(self):
        bus = InProcessBus()

        bus.publish_threadsafe("jobs:1", {"n": 1})  # must not raise

        assert bus._loop is None


class TestEnvelope:
    def test_carries_the_channel_with_the_payload(self):
        assert json.loads(_envelope("jobs:1", {"n": 1})) == {
            "c": "jobs:1",
            "p": {"n": 1},
        }

    def test_a_notice_over_the_notify_limit_is_dropped(self):
        # PostgreSQL refuses a NOTIFY payload over 8000 bytes; a notice carries
        # ids only, so one this large is a bug to drop, not a message to split.
        assert _envelope("jobs:1", {"blob": "x" * 8000}) is None


class TestBuildEventBus:
    @pytest.fixture
    def postgres(self):
        _overlay["db_url"] = "postgresql+psycopg://vault@db/vault"

    def test_a_single_process_delivers_in_memory(self):
        assert type(build_event_bus()) is InProcessBus

    def test_postgres_with_jobs_in_the_api_is_still_one_process(self, postgres):
        assert type(build_event_bus()) is InProcessBus

    def test_a_worker_on_postgres_publishes_over_notify(self, postgres):
        _overlay["process_role"] = ProcessRole.WORKER

        bus = build_event_bus(listen=False)

        assert isinstance(bus, PostgresNotifyBus)
        assert bus._listen is False

    def test_an_api_without_jobs_listens_for_workers(self, postgres):
        _overlay["process_role"] = ProcessRole.API
        _overlay["api_runs_jobs"] = False

        bus = build_event_bus()

        assert isinstance(bus, PostgresNotifyBus)
        assert bus._listen is True
