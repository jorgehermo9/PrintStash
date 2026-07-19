from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class TaskEnvelope:
    job_id: str
    kind: str
    payload: dict[str, Any]


@runtime_checkable
class TaskQueue(Protocol):
    async def enqueue(self, task: TaskEnvelope) -> None: ...

    async def dequeue(self) -> TaskEnvelope: ...


class LocalTaskQueue:
    """Local-first task transport; persistence lives in JobRegistry repository."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[TaskEnvelope] = asyncio.Queue()

    async def enqueue(self, task: TaskEnvelope) -> None:
        await self._queue.put(task)

    async def dequeue(self) -> TaskEnvelope:
        return await self._queue.get()


task_queue: TaskQueue = LocalTaskQueue()
